import uuid
import copy
import os
import shutil
import torch
import torch.nn.utils.prune as prune
import cyy_pytorch_cpp

from .log import get_logger
from .util import model_parameters_to_vector, get_model_sparsity, get_pruning_mask


class HyperGradientTrainer:
    def __init__(self, trainer, cache_size, save_dir):
        self.trainer = trainer
        mask = None
        gradient_shape = None
        if prune.is_pruned(trainer.model):
            get_logger().info("use pruned model")
            sparsity, none_zero_parameter_num, parameter_count = get_model_sparsity(
                trainer.model)
            get_logger().info("model sparsity is %s%%", sparsity)
            get_logger().info(
                "none_zero_parameter_num %s parameter_count %s",
                none_zero_parameter_num,
                parameter_count,
            )

            parameters = model_parameters_to_vector(trainer.model)
            gradient_shape = parameters.shape

            mask = get_pruning_mask(trainer.model)
            assert len(mask) == len(parameters)
        else:
            get_logger().info("use unpruned model")

        self.hyper_gradient_matrix = self.__create_gradient_matrix(
            cache_size, mask, gradient_shape
        )
        self.save_dir = save_dir
        self.hyper_gradient_matrix.set_storage_dir(
            os.path.join(save_dir, "hyper_gradient_matrix", str(uuid.uuid4()),)
        )
        self.mom_gradient_matrix = self.__create_gradient_matrix(
            cache_size, mask, gradient_shape
        )
        self.mom_gradient_matrix.set_storage_dir(
            os.path.join(save_dir, "mom_gradient_matrix", str(uuid.uuid4()),)
        )
        self.delayed_computations = dict()
        for k in range(len(trainer.training_dataset)):
            self.delayed_computations[k] = []
        self.batch_gradients = dict()

    def train(self):
        get_logger().info("begin train")

        self.trainer.train(
            pre_batch_callback=self.__pre_batch_callback,
            per_instance_gradient_callback=self.__per_instance_gradient_callback,
            after_batch_callback=self.__after_batch_callback,
            after_epoch_callback=self.__after_epoch_callback,
        )
        get_logger().info("begin do do_delayed_computation")
        self.do_delayed_computation()
        get_logger().info("end do do_delayed_computation")
        self.trainer.save(self.save_dir)
        self.hyper_gradient_matrix.flush_all()
        self.hyper_gradient_matrix.release()
        self.mom_gradient_matrix.release()

    def do_delayed_computation(self, index=None):
        if index is None:
            unfinished_keys = []
            for k, v in self.delayed_computations.items():
                if v:
                    unfinished_keys.append(str(k))

            self.hyper_gradient_matrix.prefetch(unfinished_keys)
            self.mom_gradient_matrix.prefetch(unfinished_keys)

            for k in unfinished_keys:
                self.do_delayed_computation(int(k))
            return


        old_mom_gradient = None
        if str(index) in self.mom_gradient_matrix:
            old_mom_gradient = self.mom_gradient_matrix[str(index)]
        old_hypergradient = None
        if str(index) in self.hyper_gradient_matrix:
            old_hypergradient = self.hyper_gradient_matrix[str(index)]

        for coefficents in self.delayed_computations[index]:
            momentum, weight_decay, learning_rate = coefficents
            if old_mom_gradient is not None:
                old_mom_gradient *= momentum

            if old_hypergradient is not None:
                if old_mom_gradient is not None:
                    old_mom_gradient += weight_decay * old_hypergradient
                else:
                    old_mom_gradient = weight_decay * old_hypergradient
            if old_mom_gradient is not None:
                if old_hypergradient is not None:
                    old_hypergradient -= learning_rate * old_mom_gradient
                else:
                    old_hypergradient = -learning_rate * old_mom_gradient

        if old_mom_gradient is not None:
            self.mom_gradient_matrix[str(index)] = old_mom_gradient

        if old_hypergradient is not None:
            self.hyper_gradient_matrix[str(index)] = old_hypergradient
        self.delayed_computations[index] = []

    def __create_gradient_matrix(self, cache_size, mask, gradient_shape):
        m = None
        if mask is not None:
            m = cyy_pytorch_cpp.data_structure.SyncedSparseTensorDict(
                mask, gradient_shape
            )
        else:
            m = cyy_pytorch_cpp.data_structure.SyncedTensorDict()
        m.set_permanent_storage()
        m.set_in_memory_number(cache_size)
        m.set_fetch_thread_number(10)
        m.enable_debug_logging(False)
        return m

    def __pre_batch_callback(
            self,
            model,
            batch,
            batch_index,
            cur_learning_rates):
        get_logger().debug("batch %s", batch_index)
        batch_gradient_indices = batch[2]
        self.hyper_gradient_matrix.prefetch(
            [str(i.data.item()) for i in batch_gradient_indices]
        )
        self.mom_gradient_matrix.prefetch(
            [str(i.data.item()) for i in batch_gradient_indices]
        )
        self.batch_gradients.clear()

    def __per_instance_gradient_callback(
        self,
        trainer,
        instance_index,
        instance_gradient,
        cur_learning_rates,
        real_batch_size,
        **kwargs,
    ):
        self.batch_gradients[instance_index] = instance_gradient

    def __after_batch_callback(
        self,
        trainer,
        epoch,
        batch_index,
        batch_size,
        batch_loss,
        cur_learning_rates,
        **kwargs,
    ):

        optimizer = kwargs["optimizer"]
        if not isinstance(optimizer, torch.optim.SGD):
            raise RuntimeError("not SGD")

        cur_learning_rate = cur_learning_rates[0]

        momentums = [group["momentum"] for group in optimizer.param_groups]
        if len(momentums) != 1:
            raise RuntimeError("unsupported momentums")

        momentum = momentums[0]
        weight_decay = trainer.get_hyper_parameter().weight_decay

        training_set_size = len(trainer.training_dataset)
        for idx in set(range(training_set_size)) - \
                set(self.batch_gradients.keys()):
            self.delayed_computations[idx].append(
                (momentum, weight_decay, cur_learning_rate)
            )

        for instance_index, instance_gradient in self.batch_gradients.items():
            self.do_delayed_computation(instance_index)
            if str(instance_index) in self.hyper_gradient_matrix:
                old_hyper_gradient = self.hyper_gradient_matrix[str(
                    instance_index)]
            else:
                old_hyper_gradient = None

            if str(instance_index) in self.mom_gradient_matrix:
                old_mom_gradient = self.mom_gradient_matrix[str(
                    instance_index)]
            else:
                old_mom_gradient = None

            instance_gradient = instance_gradient.detach().clone()
            instance_gradient = instance_gradient * training_set_size / batch_size

            if old_mom_gradient is not None:
                instance_gradient += momentum * old_mom_gradient
            if old_hyper_gradient is not None:
                instance_gradient += weight_decay * old_hyper_gradient
            mom_gradient = instance_gradient

            self.mom_gradient_matrix[str(instance_index)] = mom_gradient

            hyper_gradient = -cur_learning_rate * mom_gradient

            if old_hyper_gradient is not None:
                hyper_gradient += old_hyper_gradient

            self.hyper_gradient_matrix[str(instance_index)] = hyper_gradient

    def __after_epoch_callback(self, trainer, epoch, cur_learning_rates):
        if epoch < 10:
            return
        elif epoch > 10:
            cur_accurary = trainer.validation_accuracy[epoch]
            validation_accuracy = copy.deepcopy(trainer.validation_accuracy)
            validation_accuracy.pop(epoch)
            max_accuracy = max(list(validation_accuracy.values()))
            if cur_accurary < max_accuracy + 0.01:
                return
        get_logger().info("begin do do_delayed_computation")
        self.do_delayed_computation()
        get_logger().info("end do do_delayed_computation")
        self.hyper_gradient_matrix.flush_all()
        self.mom_gradient_matrix.flush_all()
        self.hyper_gradient_matrix.flush_all(True)
        shutil.copytree(
            self.hyper_gradient_matrix.get_storage_dir(),
            self.hyper_gradient_matrix.get_storage_dir() +
            "_epoch_" +
            str(epoch),
        )
        self.mom_gradient_matrix.flush_all(True)
        shutil.copytree(
            self.mom_gradient_matrix.get_storage_dir(),
            self.mom_gradient_matrix.get_storage_dir() +
            "_epoch_" +
            str(epoch),
        )
        epoch_save_dir = os.path.join(self.save_dir, "epoch_" + str(epoch))
        trainer.save(epoch_save_dir)