#!/usr/bin/env python3
import uuid
import os
import copy
import json
import argparse
import logging
import torch

from cyy_naive_lib.log import get_logger

from local_types import MachineLearningPhase
from dataset import (
    sub_dataset,
    replace_dataset_labels,
    DatasetUtil,
    get_dataset,
)
from configuration import (
    get_trainer_from_configuration,
    get_inferencer_from_configuration,
)
from reproducible_env import global_reproducible_env
from trainer import Trainer
from inference import Inferencer


def get_arg_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_name", type=str)
    parser.add_argument("--model_name", type=str)
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch_size", type=int, default=None)
    parser.add_argument("--learning_rate", type=float, default=None)
    parser.add_argument("--momentum", type=float, default=None)
    parser.add_argument("--weight_decay", type=float, default=None)
    parser.add_argument("--stop_accuracy", type=float, default=None)
    parser.add_argument("--model_path", type=str, default=None)
    parser.add_argument("--save_dir", type=str, default=None)
    parser.add_argument("--reproducible_env_load_path", type=str, default=None)
    parser.add_argument(
        "--make_reproducible",
        action="store_true",
        default=False)
    parser.add_argument("--debug", action="store_true", default=False)
    parser.add_argument(
        "--training_dataset_percentage",
        type=float,
        default=None)
    parser.add_argument("--randomized_label_map_path", type=str, default=None)
    parser.add_argument(
        "--training_dataset_indices_path",
        type=str,
        default=None)
    return parser


def get_parsed_args(parser=None):
    if parser is None:
        parser = get_arg_parser()
    args = parser.parse_args()
    if args.save_dir is None:
        args.save_dir = os.path.join(
            "final_models", args.dataset_name, args.model_name, str(
                uuid.uuid4()))

    return args


def affect_global_process_from_args(args):
    if args.debug:
        get_logger().setLevel(logging.DEBUG)
    set_reproducible_env_from_args(args)


def set_reproducible_env_from_args(args):
    # TODO fix location
    global global_reproducible_env
    if args.reproducible_env_load_path is not None:
        assert not global_reproducible_env.initialized
        global_reproducible_env.load(args.reproducible_env_load_path)
        args.make_reproducible = True

    if args.make_reproducible:
        global_reproducible_env.enable()
        global_reproducible_env.save(args.save_dir)


def create_trainer_from_args(args) -> Trainer:
    trainer = get_trainer_from_configuration(
        args.dataset_name, args.model_name)

    trainer.set_training_dataset(get_training_dataset(args))
    if args.model_path is not None:
        trainer.load_model(args.model_path)

    hyper_parameter = copy.deepcopy(trainer.hyper_parameter)
    assert hyper_parameter is not None
    if args.epochs is not None:
        hyper_parameter.set_epochs(args.epochs)
    if args.batch_size is not None:
        hyper_parameter.set_batch_size(args.batch_size)
    if args.learning_rate is not None:
        hyper_parameter.set_learning_rate(args.learning_rate)
    if args.momentum is not None:
        hyper_parameter.set_momentum(args.momentum)
    if args.weight_decay is not None:
        hyper_parameter.set_weight_decay(args.weight_decay)
    trainer.set_hyper_parameter(hyper_parameter)

    if args.stop_accuracy is not None:
        trainer.set_stop_criterion(
            lambda trainer, epoch, __: trainer.validation_accuracy[epoch]
            >= args.stop_accuracy
        )
    return trainer


def create_inferencer_from_args(args) -> Inferencer:
    inferencer = get_inferencer_from_configuration(
        args.dataset_name, args.model_name)
    if args.model_path is not None:
        inferencer.load_model(args.model_path)
    return inferencer


def __get_randomized_label_map(args):
    randomized_label_map: dict = dict()
    with open(args.randomized_label_map_path, "r") as f:
        for k, v in json.load(f).items():
            randomized_label_map[int(k)] = int(v)
    return randomized_label_map


def get_training_dataset(args) -> torch.utils.data.Dataset:

    training_dataset = get_dataset(
        args.dataset_name,
        MachineLearningPhase.Training)
    assert not (
        args.training_dataset_percentage and args.training_dataset_indices_path)
    if args.training_dataset_percentage is not None:
        os.makedirs(args.save_dir, exist_ok=True)
        subset_dict = DatasetUtil(training_dataset).sample_subset(
            args.training_dataset_percentage
        )
        sample_indices: list = sum(subset_dict.values(), [])
        training_dataset = sub_dataset(training_dataset, sample_indices)
        with open(
            os.path.join(args.save_dir, "training_dataset_indices.json"),
            mode="wt",
        ) as f:
            json.dump(sample_indices, f)

    if args.training_dataset_indices_path is not None:
        get_logger().info("use training_dataset_indices_path")
        with open(args.training_dataset_indices_path, "r") as f:
            subset_indices = json.load(f)
            training_dataset = sub_dataset(training_dataset, subset_indices)
    if args.randomized_label_map_path is not None:
        get_logger().info("use randomized_label_map_path")
        training_dataset = replace_dataset_labels(
            training_dataset, __get_randomized_label_map(args)
        )
    return training_dataset