import pytorch_lightning as pl
import torch
import torch.nn as nn
from data.transform import *
from metrics import masked_mae, masked_rmse, masked_mape
import functools
from utils import load_pkl

from utils.registry import SCALER_REGISTRY


class BaseRunner(pl.LightningModule):
    def __init__(
        self,
        learning_rate,
        weight_decay,
        model: nn.Module,
        input_len,
        output_len,
        dataset_name,
        forward_features,
        target_features,
        **kwargs
    ) -> None:
        super(BaseRunner,self).__init__()
        self.learning_rate = learning_rate
        self.weight_decay = weight_decay
        self.model = model
        self.forward_features = forward_features
        self.target_features = target_features
        self.prediction_list = []
        self.real_value_list = []
        self.scaler = load_pkl("./datasets/" + dataset_name + "/output/in{0}_out{1}/scaler_in{0}_out{1}.pkl".format(input_len,output_len))
        self.metrics = {"MAE": masked_mae, "RMSE": masked_rmse, "MAPE": masked_mape}

    def select_input_features(self, data: torch.Tensor) -> torch.Tensor:
        if self.forward_features is not None:
            data = data[:, :, :, self.forward_features]
        return data

    def select_target_features(self, data: torch.Tensor) -> torch.Tensor:
        data = data[:, :, :, self.target_features]
        return data

    def forward(self, data):
        future_data, history_data = data
        batch_size, length, num_nodes, _ = future_data.shape

        history_data = self.select_input_features(history_data)
        prediction_data = self.model(history_data)
        assert list(prediction_data.shape)[:3] == [batch_size, length, num_nodes], \
            "error shape of the output, edit the forward function to reshape it to [B, L, N, C]"

        prediction = self.select_target_features(prediction_data)
        real_value = self.select_target_features(future_data)
        return prediction, real_value #([32, 12, 307,1])


    def metric_forward(self, metric_func, args):
        if isinstance(metric_func, functools.partial) and list(metric_func.keywords.keys()) == ["null_val"]:
            metric_item = metric_func(*args)
        elif callable(metric_func):
            metric_item = metric_func(*args, null_val=0.0)
        else:
            raise TypeError("Unknown metric type: {0}".format(type(metric_func)))
        return metric_item
    def shared_step(self, batch, batch_idx):
        prediction, real_value = self(batch)
        prediction_rescaled = SCALER_REGISTRY.get(self.scaler["func"])(prediction, **self.scaler["args"])
        real_value_rescaled = SCALER_REGISTRY.get(self.scaler["func"])(real_value, **self.scaler["args"])
        return prediction_rescaled, real_value_rescaled

    def training_step(self, batch, batch_idx):
        prediction, real_value = self.shared_step(batch, batch_idx)
        loss = self.metric_forward(masked_mae, [prediction,real_value])
        self.log("train_loss", loss)
        return loss

    def validation_step(self, batch, batch_idx):
        prediction, real_value = self.shared_step(batch, batch_idx)
        metrics = {}
        for metric_name, metric_func in self.metrics.items():
            metric_item = self.metric_forward(metric_func, [prediction, real_value])
            metrics[metric_name] = metric_item
        self.log_dict(metrics)

    def configure_optimizers(self):
        return torch.optim.Adam(
            self.parameters(),
            lr=self.learning_rate,
            weight_decay=self.weight_decay
        )