export PYTHONPATH="$PWD"

PREMODEL=/data2/wangshuhe/gnn_ner/results/conll_bert_large_gnn_32_label_1e-4_batch16/checkpoint/epoch=2.ckpt
PREFILE=/data2/wangshuhe/gnn_ner/results/conll_bert_large_gnn_32_label_1e-4_batch16/log/version_0/hparams.yaml

link_temperature=0.1
link_ratio=1

for link_temperature in 0.001 0.011 0.021 0.031 0.041 0.051 0.061 0.071 0.081 0.091; do
for link_ratio in 0.01 0.11 0.21 0.31 0.41 0.51 0.61 0.71 0.81 0.91; do

CUDA_VISIBLE_DEVICES=5 python ./gnn_knn_test.py \
--path_to_model_hparams_file $PREFILE \
--checkpoint_path $PREMODEL \
--add_knn \
--link_temperature $link_temperature \
--link_ratio $link_ratio \
--gnn_k 32 \
--gpus="1"

done
done