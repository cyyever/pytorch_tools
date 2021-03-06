import datetime
import threading

from cyy_naive_lib.algorithm.sequence_op import split_list_to_chunks
from cyy_naive_lib.log import get_logger

from basic_trainer import BasicTrainer
from hyper_parameter import HyperParameter
from ml_types import MachineLearningPhase
from model_loss import ModelWithLoss
from model_util import ModelUtil
from tensor import get_batch_size
from visualization import EpochWindow, Window


class Trainer(BasicTrainer):
    """
    This trainer is designed to add logging to BasicTrainer
    """

    def __init__(
        self,
        model_with_loss: ModelWithLoss,
        training_dataset,
        hyper_parameter: HyperParameter,
    ):
        super().__init__(
            model_with_loss=model_with_loss,
            training_dataset=training_dataset,
            hyper_parameter=hyper_parameter,
        )
        self.visdom_env = None
        self.add_callback("pre_training_callbacks", self.__pre_training_callback)
        self.add_callback("after_batch_callbacks", Trainer.__after_batch_callback)
        self.add_callback("after_epoch_callbacks", Trainer.__plot_after_epoch)

    def __pre_training_callback(self, trainer):
        self.visdom_env = (
            "training_"
            + str(self.model.__class__.__name__)
            + "_"
            + str(threading.get_native_id())
            + "_{date:%Y-%m-%d_%H:%M:%S}".format(date=datetime.datetime.now())
        )
        model_util = ModelUtil(trainer.model)
        get_logger().info(
            "begin training, hyper_parameter is %s, optimizer is %s ,lr_scheduler is %s, %s, parameter number is %s",
            trainer.hyper_parameter,
            trainer.get_optimizer(),
            trainer.get_lr_scheduler(),
            trainer.model_with_loss,
            len(model_util.get_parameter_list()),
        )

    @staticmethod
    def __after_batch_callback(trainer: BasicTrainer, batch_index, batch, **kwargs):
        training_set_size = trainer.get_data("training_set_size")
        ten_batches = training_set_size // (10 * get_batch_size(batch[0]))
        if ten_batches == 0 or batch_index % ten_batches == 0:
            get_logger().info(
                "epoch: %s, batch: %s, learning rate: %s, batch training loss: %s",
                kwargs["epoch"],
                batch_index,
                trainer.get_data("cur_learning_rates"),
                kwargs["batch_loss"],
            )

    @staticmethod
    def __plot_after_epoch(trainer: BasicTrainer, epoch, **kwargs):
        learning_rates = trainer.get_data("cur_learning_rates")
        assert len(learning_rates) == 1
        EpochWindow("learning rate", env=trainer.visdom_env).plot_learning_rate(
            epoch, learning_rates[0]
        )
        optimizer = kwargs.get("optimizer", None)
        for group in optimizer.param_groups:
            if "momentum" in group:
                momentum = group["momentum"]
                EpochWindow("momentum", env=trainer.visdom_env).plot_scalar(
                    epoch, momentum, y_label="Momentum"
                )

        loss_win = EpochWindow("training & validation loss", env=trainer.visdom_env)
        get_logger().info(
            "epoch: %s, training loss: %s",
            epoch,
            trainer.training_loss[-1],
        )
        loss_win.plot_loss(epoch, trainer.training_loss[-1], "training loss")

        (
            validation_loss,
            accuracy,
            other_data,
        ) = trainer.get_inferencer(phase=MachineLearningPhase.Validation).inference()
        validation_loss = validation_loss.data.item()
        trainer.validation_loss[epoch] = validation_loss
        trainer.validation_accuracy[epoch] = accuracy
        get_logger().info(
            "epoch: %s, learning_rate: %s, validation loss: %s, accuracy = %s",
            epoch,
            learning_rates,
            validation_loss,
            accuracy,
        )
        loss_win = EpochWindow("training & validation loss", env=trainer.visdom_env)
        loss_win.plot_loss(epoch, validation_loss, "validation loss")
        EpochWindow("validation accuracy", env=trainer.visdom_env).plot_accuracy(
            epoch, accuracy, "accuracy"
        )

        plot_class_accuracy = kwargs.get("plot_class_accuracy", False)
        if plot_class_accuracy and "per_class_accuracy" in other_data:
            class_accuracy = other_data["per_class_accuracy"]
            for idx, sub_list in enumerate(
                split_list_to_chunks(list(class_accuracy.keys()), 2)
            ):
                class_accuracy_win = EpochWindow(
                    "class accuracy part " + str(idx), env=trainer.visdom_env
                )
                for k in sub_list:
                    get_logger().info(
                        "epoch: %s, learning_rate: %s, class %s accuracy = %s",
                        epoch,
                        learning_rates,
                        k,
                        class_accuracy[k],
                    )
                    class_accuracy_win.plot_accuracy(
                        epoch,
                        class_accuracy[k],
                        "class_" + str(k) + "_accuracy",
                    )

        test_epoch_interval = int(kwargs.get("test_epoch_interval", 2))
        if trainer.test_dataset is not None and (
            epoch % test_epoch_interval == 0 or epoch == trainer.hyper_parameter.epoch
        ):
            (test_loss, accuracy, _) = trainer.get_inferencer(
                phase=MachineLearningPhase.Test
            ).inference(per_class_accuracy=False)
            test_loss = test_loss.data.item()
            trainer.test_loss[epoch] = test_loss
            trainer.test_accuracy[epoch] = accuracy
            EpochWindow("test accuracy", env=trainer.visdom_env).plot_accuracy(
                epoch, accuracy, "accuracy"
            )
            get_logger().info(
                "epoch: %s, learning_rate: %s, test loss: %s, accuracy = %s",
                epoch,
                learning_rates,
                test_loss,
                accuracy,
            )
        Window.save_envs()
