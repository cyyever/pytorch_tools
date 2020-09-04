from inverse_hessian_vector_product import stochastic_inverse_hessian_vector_product
from synced_tensor_dict_util import iterate_over_synced_tensor_dict


def compute_classic_influence_function(
    trainer,
    validator,
    training_sample_gradient_dict,
    batch_size=None,
    dampling_term=0,
    scale=1,
    epsilon=0.0001,
):
    test_gradient = validator.get_gradient()
    training_dataset_size = len(trainer.training_dataset)
    if batch_size is None:
        batch_size = trainer.get_hyper_parameter().batch_size
    product = (
        stochastic_inverse_hessian_vector_product(
            trainer.model,
            trainer.training_dataset,
            trainer.loss_fun,
            test_gradient,
            repeated_num=3,
            max_iteration=None,
            batch_size=batch_size,
            dampling_term=dampling_term,
            scale=scale,
            epsilon=epsilon,
        )
        / training_dataset_size
    )
    contributions = dict()

    for (sample_index, sample_gradient) in iterate_over_synced_tensor_dict(
        training_sample_gradient_dict
    ):
        contributions[sample_index] = (product @ sample_gradient).data.item()
    return contributions
