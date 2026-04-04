#!/usr/bin/env bash

set -o pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)
cd ${SCRIPT_DIR}/.. || exit

cur_time=$(date +"%Y%m%d_%H%M%S")

# ------------------- Task Info -------------------
task_name=${1:-example}
generate_result_path=${2:-data/example_gen_result.jsonl}

if [ -z "$task_name" -o -z "$generate_result_path" ]; then
    echo "Wrong args"
    exit 1
fi

# -------------- Judge Model Config ---------------
judge_model_name=
judge_model_base=
judge_model_kwargs=

# ------------------ Eval Params ------------------
judge_model_rpm=32

# -------------------------------------------------

main() {
    echo "task_name=$task_name"
    echo "generate_result_path=$generate_result_path"
    echo "overall_log_path=$(realpath "$overall_log_path")"

    # =================================================
    # 1. Evaluate
    # =================================================

    mkdir -p eval_results
    export LOGURU_LEVEL=INFO
    eval_output_path=eval_results/${cur_time}_${task_name}.jsonl

    python -u -m deepsynth_eval.eval.evaluate \
        --judge-model $judge_model_name \
        --task-info-path $generate_result_path \
        --output-path $eval_output_path \
        ${judge_model_base:+--judge-model-base $judge_model_base} \
        --judge-model-rpm $judge_model_rpm \
        ${judge_model_kwargs:+--judge-model-kwargs "$judge_model_kwargs"}

    # =================================================
    # 2. Score
    # =================================================

    mkdir -p score_results
    score_output_path=score_results/${cur_time}_${task_name}.jsonl

    python -u -m deepsynth_eval.eval.score \
        --eval-result-path $eval_output_path \
        --output-path $score_output_path
}

mkdir -p log
overall_log_path=log/eval_and_score_${cur_time}_${task_name}.log
main 2>&1 | tee -a $overall_log_path