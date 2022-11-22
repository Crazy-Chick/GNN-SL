#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
@file  : build_datastore.py
@author: shuhe wang
@contact : shuhe_wang@shannonai.com
@date  : 2021/07/10 15:54
@version: 1.0
@desc  :
"""

import os
import json
import argparse
from functools import partial

from utils import collate_to_max_length
from dataset import NERDataset
from ner_trainer import NERTask
from utils import set_random_seed

# enable reproducibility
# https://pytorch-lightning.readthedocs.io/en/latest/trainer.html
set_random_seed(2333)

import numpy as np
import torch
from torch.utils.data.dataloader import DataLoader, SequentialSampler
from transformers import BertConfig, RobertaConfig

import pytorch_lightning as pl
from pytorch_lightning import Trainer


class Datastore(pl.LightningModule):
    def __init__(self, args: argparse.Namespace):
        """Initialize a model, tokenizer and config"""
        super().__init__()
        self.args = args

        self.en_roberta = args.en_roberta
        self.entity_labels = NERDataset.get_labels(os.path.join(args.data_dir, "ner_labels.txt"))
        self.bert_dir = args.bert_path
        self.num_labels = len(self.entity_labels)
        if not self.en_roberta:
            self.bert_config = BertConfig.from_pretrained(self.bert_dir, output_hidden_states=True, 
                                                          return_dict=True, num_labels=self.num_labels)
        else:
            self.bert_config = RobertaConfig.from_pretrained(self.bert_dir, output_hidden_states=True, 
                                                             return_dict=True, num_labels=self.num_labels)
        self.model = None

    def forward(self, input_ids):
        attention_mask = (input_ids != 0).long()
        return self.model(input_ids, attention_mask=attention_mask)

    def test_dataloader(self, ) -> DataLoader:
        vocab_file = self.args.bert_path
        if not self.en_roberta:
            vocab_file = os.path.join(self.args.bert_path, "vocab.txt")
        dataset = NERDataset(directory=self.args.data_dir, prefix=self.args.datastore_sub_set,
                             vocab_file=vocab_file,
                             max_length=self.args.max_length,
                             config_path=os.path.join(self.args.bert_path, "config"),
                             file_name=self.args.file_name, en_roberta=self.en_roberta)

        batch_size = self.args.batch_size
        data_sampler = SequentialSampler(dataset)

        dataloader = DataLoader(dataset=dataset, sampler=data_sampler, batch_size=batch_size,
                                num_workers=self.args.workers, collate_fn=partial(collate_to_max_length, fill_values=[0, 0, 0]),
                                drop_last=False)

        return dataloader

    def test_step(self, batch, batch_idx):
        input_ids, gold_labels, sub_token_starts = batch
        sequence_mask = (input_ids != 0).long()
        batch_size, seq_len = input_ids.shape

        sub_token_starts_mask = None
        if not self.args.build_datastore:
            sub_token_starts_mask = torch.zeros_like(sequence_mask).bool()
            for batch_idx in range(batch_size):
                sub_token_starts_mask[batch_idx][0] = True
                for seq_len_idx in range(1, seq_len):
                    if sub_token_starts[batch_idx][seq_len_idx] == 0:
                        break
                    if sub_token_starts[batch_idx][seq_len_idx] != sub_token_starts[batch_idx][seq_len_idx-1]:
                        sub_token_starts_mask[batch_idx][seq_len_idx] = True
            sequence_mask = sequence_mask & sub_token_starts_mask

        features = self.forward(input_ids=input_ids).hidden_states[-1]    # [bsz, sent_len, feature_size]
        # [bsz, sent_len, feature_size], [bsz, sent_len], [bsz, sent_len]
        return {"features": features, "labels": gold_labels, "mask": sequence_mask, "input_ids": input_ids}

    def test_epoch_end(self, outputs):
        if self.args.get_datastore_index:
            return self.get_datastore_index(outputs)
        if self.args.build_datastore:
            return self.building_datastore(outputs)
        return self.extract_features(outputs)

    def get_datastore_index(self, outputs):
        datastore_list = []
        for x in outputs:
            sub_batch_size = x['features'].shape[0]
            for sub_batch_size_idx in range(sub_batch_size):
                mask = x['mask'][sub_batch_size_idx].bool()
                input_ids = torch.masked_select(x['input_ids'][sub_batch_size_idx], mask).view(-1).cpu()
                datastore_list.append(input_ids.tolist())
        
        file = open(os.path.join(self.args.datastore_path, "datastore_index.jsonl"), "w")
        json.dump(datastore_list, file, ensure_ascii=False)

        return {"saved dir": self.args.datastore_path,
                "saved sub-set": self.args.datastore_sub_set}

    def building_datastore(self, outputs):
        hidden_size = outputs[0]['features'].shape[2]
        token_sum = sum(int(x['mask'].sum(dim=-1).sum(dim=-1).cpu()) for x in outputs)

        l_size = self.args.datastore_l_context
        r_size = self.args.datastore_r_context
        mid_size = 1 + l_size + r_size
        data_store_key_in_memory = np.zeros((token_sum, mid_size, hidden_size), dtype=np.float32)
        data_store_val_in_memory = np.zeros((token_sum, mid_size), dtype=np.int32)

        now_cnt = 0
        for x in outputs:
            sub_batch_size = x['features'].shape[0]
            for sub_batch_size_idx in range(sub_batch_size):
                features = x['features'][sub_batch_size_idx].reshape(-1, hidden_size)
                mask = x['mask'][sub_batch_size_idx].bool()
                labels = torch.masked_select(x['labels'][sub_batch_size_idx], mask).cpu().numpy()
                mask = mask.view(-1, 1).expand(features.shape[0], features.shape[1])
                features = torch.masked_select(features, mask).view(-1, hidden_size).cpu()
                np_features = features.numpy().astype(np.float32)
                for idx_ in range(np_features.shape[0]):
                    if idx_ != 0:
                        span_ = idx_ - max(0, idx_-l_size)
                        data_store_key_in_memory[now_cnt, l_size-span_:l_size, :] = np_features[max(0, idx_-l_size):idx_, :]
                        data_store_val_in_memory[now_cnt, l_size-span_:l_size] = labels[max(0, idx_-l_size):idx_]
                    span_ = min(np_features.shape[0], idx_+r_size+1) - idx_
                    data_store_key_in_memory[now_cnt, l_size:l_size+span_, :] = np_features[idx_:min(np_features.shape[0], idx_+r_size+1), :]
                    data_store_val_in_memory[now_cnt, l_size:l_size+span_] = labels[idx_:min(np_features.shape[0], idx_+r_size+1)]
                    now_cnt += 1

        datastore_info = {
            "token_sum": token_sum,
            "l_size": l_size,
            "r_size": r_size,
            "hidden_size": hidden_size
        }
        json.dump(datastore_info, open(os.path.join(self.args.datastore_path, "datastore_info.json"), "w"),
                    sort_keys=True, indent=4, ensure_ascii=False)

        key_file = os.path.join(self.args.datastore_path, "datastore_features.npy")
        keys = np.memmap(key_file, 
                     dtype=np.float32,
                     mode="w+",
                     shape=(token_sum, mid_size, hidden_size))

        val_file = os.path.join(self.args.datastore_path, "datastore_vals.npy")
        vals = np.memmap(val_file, 
                     dtype=np.int32,
                     mode="w+",
                     shape=(token_sum, mid_size))
        
        keys[:] = data_store_key_in_memory[:]
        vals[:] = data_store_val_in_memory[:]

        return {"saved dir": self.args.datastore_path,
                "saved sub-set": self.args.datastore_sub_set}
    
    def extract_features(self, outputs):
        hidden_size = outputs[0]['features'].shape[2]
        max_seq_len = max(int(torch.max(x['mask'].sum(dim=-1)).cpu()) for x in outputs)
        seq_num = sum(int(x['features'].shape[0]) for x in outputs)

        feature_file_in_memory = np.zeros((seq_num, max_seq_len, hidden_size), dtype=np.float32)
        label_file_in_memory = np.full((seq_num, max_seq_len), -1, dtype=np.int32)

        now_cnt = 0
        for x in outputs:
            sub_batch_size = x['features'].shape[0]
            for sub_batch_size_idx in range(sub_batch_size):
                features = x['features'][sub_batch_size_idx].reshape(-1, hidden_size)
                mask = x['mask'][sub_batch_size_idx].bool()
                labels = torch.masked_select(x['labels'][sub_batch_size_idx], mask).cpu().numpy()
                mask = mask.view(-1, 1).expand(features.shape[0], features.shape[1])
                features = torch.masked_select(features, mask).view(-1, hidden_size).cpu()
                np_features = features.numpy().astype(np.float32)
                feature_file_in_memory[now_cnt, :np_features.shape[0], :] = np_features[:]
                label_file_in_memory[now_cnt, :np_features.shape[0]] = labels[:]
                now_cnt += 1

        feature_file_info = {
            "seq_num": seq_num,
            "max_seq_len": max_seq_len,
            "hidden_size": hidden_size
        }
        json.dump(feature_file_info, open(os.path.join(self.args.datastore_path, f"{self.args.datastore_sub_set}_feature_info.json"), "w"),
                    sort_keys=True, indent=4, ensure_ascii=False)

        feature_file = os.path.join(self.args.datastore_path, f"{self.args.datastore_sub_set}_features.npy")
        really_features = np.memmap(feature_file, 
                     dtype=np.float32,
                     mode="w+",
                     shape=(seq_num, max_seq_len, hidden_size))

        label_file = os.path.join(self.args.datastore_path, f"{self.args.datastore_sub_set}_labels.npy")
        really_lables = np.memmap(label_file, 
                     dtype=np.int32,
                     mode="w+",
                     shape=(seq_num, max_seq_len))
        
        really_features[:] = feature_file_in_memory[:]
        really_lables[:] = label_file_in_memory[:]

        return {"saved dir": self.args.datastore_path,
                "saved sub-set": self.args.datastore_sub_set}

def get_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument("--bert_path", type=str, help="bert config file")
    parser.add_argument("--batch_size", type=int, default=8, help="batch size")
    parser.add_argument("--workers", type=int, default=0, help="num workers for dataloader")
    parser.add_argument("--use_memory", action="store_true", help="load dataset to memory to accelerate.")
    parser.add_argument("--max_length", default=512, type=int, help="max length of dataset")
    parser.add_argument("--data_dir", type=str, help="train data path")
    parser.add_argument("--seed", type=int, default=2333)
    parser.add_argument("--file_name", default="", type=str, help="use for truncated sets.")
    parser.add_argument("--path_to_model_hparams_file", default="", type=str, help="use for evaluation")
    parser.add_argument("--checkpoint_path", default="", type=str, help="use for evaluation.")
    parser.add_argument("--datastore_path", default="", type=str, help="use for saving datastore.")
    parser.add_argument("--datastore_sub_set", default="", type=str, help="sub-set name used for saving datastore.")
    parser.add_argument("--datastore_l_context", default=1, type=int, help="left context size for graph in gnn.")
    parser.add_argument("--datastore_r_context", default=1, type=int, help="right context size for graph in gnn.")
    parser.add_argument("--build_datastore", action="store_true", help="extracting token feature or building datastore.")
    parser.add_argument("--en_roberta", action="store_true", help="whether load roberta for classification or not.")
    parser.add_argument("--get_datastore_index", action="store_true", help="extracting token index in datastore.")

    return parser

def main():
    parser = get_parser()
    parser = Trainer.add_argparse_args(parser)
    args = parser.parse_args()

    ner_model = NERTask.load_from_checkpoint(checkpoint_path=args.checkpoint_path,
                                                            hparams_file=args.path_to_model_hparams_file,
                                                            map_location=None,
                                                            batch_size=args.batch_size)
    model = Datastore(args)
    model.model = ner_model.model
    trainer = Trainer.from_argparse_args(args, deterministic=True)

    trainer.test(model)


if __name__ == "__main__":
    from multiprocessing import freeze_support

    freeze_support()
    main()