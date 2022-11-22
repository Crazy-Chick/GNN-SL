export PYTHONPATH="$PWD"

DARA_DIR="/nfs1/shuhe/gnn_ner/data/pos/ud1"
FILE_NAME="char.bmes"
SAVE_PATH="/nfs1/shuhe/gnn_ner/results/ud1.4_true_data_pre32"
BERT_PATH="/nfs1/shuhe/gnn_ner/models/ChineseBERT-large"
PARAMS_FILE="/nfs1/shuhe/gnn_ner/results/ud1.4_true_data_pre32/log/version_0/hparams.yaml"
CHECKPOINT_PATH="/nfs1/shuhe/gnn_ner/results/ud1.4_true_data_pre32/checkpoint/epoch=9.ckpt"
DATASTORE_PATH="/nfs1/shuhe/gnn_ner/data/pos/ud1/feature_files_pre32_bert"
link_temperature=0.071
link_ratio=0.71
topk=32

for link_temperature in 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8 0.9 1 1.1 1.2 1.3 1.4 1.5; do
for link_ratio in 0.001 0.002 0.003 0.004 0.005 0.006 0.007 0.008 0.009 0.01 0.011 0.012 0.013 0.014 0.015; do

echo $link_temperature $link_ratio

CUDA_VISIBLE_DEVICES=7 python ./KNN-NER/knn_ner_trainer.py \
--bert_path $BERT_PATH \
--batch_size 1 \
--workers 4 \
--max_length 512 \
--data_dir $DARA_DIR \
--file_name $FILE_NAME \
--path_to_model_hparams_file $PARAMS_FILE \
--checkpoint_path $CHECKPOINT_PATH \
--datastore_path $DATASTORE_PATH \
--save_path $SAVE_PATH \
--link_temperature $link_temperature \
--link_ratio $link_ratio \
--topk $topk \
--task_name "cws" \
--gpus="1"

done
done