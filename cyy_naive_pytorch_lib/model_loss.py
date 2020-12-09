from typing import Optional
import torch
import torch.nn as nn
from torchvision.models.detection.generalized_rcnn import GeneralizedRCNN
from local_types import MachineLearningPhase
from local_types import ModelType


class ModelWithLoss:
    def __init__(
        self,
        model: torch.nn.Module,
        loss_fun: torch.nn.modules.loss._Loss = None,
        model_type: ModelType = None,
    ):
        self.__model = model
        self.__loss_fun = loss_fun
        if self.__loss_fun is None:
            self.__loss_fun = self.__choose_loss_function()
        self.__model_type = model_type

    def set_model(self, model: torch.nn.Module):
        self.__model = model

    @property
    def model(self):
        return self.__model

    @property
    def model_type(self):
        return self.__model_type

    @property
    def loss_fun(self):
        return self.__loss_fun

    def set_model_mode(self, phase: MachineLearningPhase):
        if isinstance(self.__model, GeneralizedRCNN):
            if phase == MachineLearningPhase.Training:
                self.model.train()
            else:
                self.model.eval()
            return

        if phase == MachineLearningPhase.Training:
            self.model.train()
            return
        self.model.eval()

    def __call__(self, inputs, target, phase: MachineLearningPhase) -> dict:
        if isinstance(self.__model, GeneralizedRCNN):
            loss_dict: dict = None
            if phase in (MachineLearningPhase.Training,):
                loss_dict = self.__model(inputs, target)
                return {"loss": sum(loss for loss in loss_dict.values())}
            loss_dict, detection = self.__model(inputs, target)
            return {
                "loss": sum(loss for loss in loss_dict.values()),
                "detection": detection,
            }

        assert self.__loss_fun is not None

        output = self.__model(inputs)
        loss = self.__loss_fun(output, target)
        return {"loss": loss, "output": output}

    def __choose_loss_function(self) -> Optional[torch.nn.modules.loss._Loss]:
        if isinstance(self.__model, GeneralizedRCNN):
            return None
        last_layer = list(self.__model.modules())[-1]
        if isinstance(last_layer, nn.LogSoftmax):
            return nn.NLLLoss()
        if isinstance(last_layer, nn.Linear):
            return nn.CrossEntropyLoss()
        raise NotImplementedError()

    def is_averaged_loss(self) -> bool:
        if hasattr(self.loss_fun, "reduction"):
            if self.loss_fun.reduction in ("mean", "elementwise_mean"):
                return True
        return False

    def __str__(self):
        return "model: {}, loss_fun: {}".format(
            self.model.__class__.__name__, self.loss_fun
        )