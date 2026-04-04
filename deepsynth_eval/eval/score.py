import json
import argparse
from pathlib import Path
from typing import Dict, List, Any

from loguru import logger
from pydantic import BaseModel


class ScorerConfig(BaseModel):
    eval_result_path: str
    output_path: str


class Scorer:
    def __init__(self, config: ScorerConfig):
        self.config = config
        self.output_path = Path(config.output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        # Load evaluation results from the Evaluate module
        if not Path(config.eval_result_path).exists():
            raise FileNotFoundError(f"Evaluation result file not found: {config.eval_result_path}")

        self._load_eval_result(config.eval_result_path)
        logger.info(f"Loaded {len(self.task_results)} evaluation results.")


    def _load_eval_result(self, eval_result_path: str):
        path = Path(eval_result_path)
        data = []
        
        if path.suffix == ".jsonl":
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        data.append(json.loads(line))
        else:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                
        if not isinstance(data, list):
            if isinstance(data, dict):
                 data = [data]
            raise ValueError("Input data must be a list or a dictionary")
            
        self.task_results = data


    def _calculate_subgroup_stats(self, subgroup: Dict[str, Any]) -> Dict[str, float | int]:
        """
        Calculates the actual score (reward_get) and total possible score (reward_sum) 
        for a single SubGroup.
        """
        requirements = subgroup.get("requirements", [])
        results = subgroup.get("eval_result", [])

        reward_get = 0.0
        reward_sum = 0.0
        mentioned_correct_cnt = 0
        not_mentioned_cnt = 0
        mentioned_incorrect_cnt = 0

        if not results:
            for req in requirements:
                reward_sum += float(req.get("reward", 1.0))
            return 0.0, reward_sum

        for req, res_status in zip(requirements, results):
            reward = float(req.get("reward", 1.0))
            reward_sum += reward
            
            if res_status == "mentioned_correct":
                reward_get += reward
                mentioned_correct_cnt += 1
            elif res_status == "mentioned_incorrect":
                reward_get -= reward
                mentioned_incorrect_cnt += 1
            elif res_status == "not_mentioned":
                reward_get += 0.0
                not_mentioned_cnt += 1
        
        return dict(
            reward_get=reward_get,
            reward_sum=reward_sum,
            mentioned_correct_cnt=mentioned_correct_cnt,
            mentioned_incorrect_cnt=mentioned_incorrect_cnt,
            not_mentioned_cnt=not_mentioned_cnt
        )


    def _calculate_group_stats(self, group: Dict[str, Any]) -> Dict[str, float | int]:
        """
        Calculates pass rate for a single Group.
        """
        total_reward_get = 0.0
        total_reward_sum = 0.0
        total_mentioned_correct_cnt = 0
        total_mentioned_incorrect_cnt = 0
        total_not_mentioned_cnt = 0
        
        for subgroup in group.get("sub_groups", []):
            subgroup_stats = self._calculate_subgroup_stats(subgroup)
            total_reward_get += subgroup_stats.get("reward_get")
            total_reward_sum += subgroup_stats.get("reward_sum")
            total_mentioned_correct_cnt += subgroup_stats.get("mentioned_correct_cnt")
            total_mentioned_incorrect_cnt += subgroup_stats.get("mentioned_incorrect_cnt")
            total_not_mentioned_cnt += subgroup_stats.get("not_mentioned_cnt")

        clip_factor = float(group.get("clip_factor", 0.8 if group.get("strict", False) else 1.0))

        if total_reward_sum == 0:
            group_pass_rate = 0.0
        else:
            group_pass_rate = min(total_reward_get / (clip_factor * total_reward_sum), 1.0)

        return {
            "reward_get": total_reward_get,
            "reward_sum": total_reward_sum,
            "group_pass_rate": group_pass_rate,
            "mentioned_correct_cnt": total_mentioned_correct_cnt,
            "mentioned_incorrect_cnt": total_mentioned_incorrect_cnt,
            "not_mentioned_cnt": total_not_mentioned_cnt
        }


    def process_task(self, task_info: Dict[str, Any]):
        """
        Process a single Task
        """
        checklist = task_info.get("checklist", [])
        
        # Statistical accumulator: stores weighted scores and total weights for [Overall, Strict, Non-Strict]
        stats = {
            "all": {"score": 0.0, "weight": 0.0},
            "general": {"score": 0.0, "weight": 0.0},
            "constraint": {"score": 0.0, "weight": 0.0},
            "res_cnt": {"mentioned_correct": 0, "mentioned_incorrect": 0, "not_mentioned": 0}
        }

        for group in checklist:
            is_strict_group = group.get("strict", False)
            
            # Calculate group score
            score_stats = self._calculate_group_stats(group)
            group["score_stats"] = score_stats
            
            pass_rate = score_stats["group_pass_rate"]
            weight = group.get("weight", 1.0)
            
            # 1. Count towards Overall (all)
            stats["all"]["score"] += pass_rate * weight
            stats["all"]["weight"] += weight

            # 2. Count towards classification (strict or non_strict)
            key = "constraint" if is_strict_group else "general"
            stats[key]["score"] += pass_rate * weight
            stats[key]["weight"] += weight

            # 3. Result Count
            stats["res_cnt"]["mentioned_correct"] += score_stats.get("mentioned_correct_cnt")
            stats["res_cnt"]["mentioned_incorrect"] += score_stats.get("mentioned_incorrect_cnt")
            stats["res_cnt"]["not_mentioned"] += score_stats.get("not_mentioned_cnt")

        # Inner function: calculate pass rate
        def _calc_rate(key):
            s = stats[key]["score"]
            w = stats[key]["weight"]
            return s / w if w > 0 else 0.0

        # Calculate Pass Rate for each category
        pass_rate_all = _calc_rate("all")
        pass_rate_general = _calc_rate("general")
        pass_rate_constraint = _calc_rate("constraint")

        # Update Task Summary
        task_info["evaluation_summary"] = {
            "pass_rate_all": pass_rate_all,
            "pass_rate_general": pass_rate_general,
            "pass_rate_constraint": pass_rate_constraint,
            
            "weight_all": stats["all"]["weight"],
            "weight_general": stats["general"]["weight"],
            "weight_constraint": stats["constraint"]["weight"],

            "mentioned_correct_cnt": stats["res_cnt"]["mentioned_correct"],
            "mentioned_incorrect_cnt": stats["res_cnt"]["mentioned_incorrect"],
            "not_mentioned_cnt": stats["res_cnt"]["not_mentioned"]
        }

        
        # Build log string
        score_log = f"Pass Rate: {pass_rate_all:.1%}"
        details = []
        if stats["general"]["weight"] > 0:
            details.append(f"General: {pass_rate_general:.1%}")
        if stats["constraint"]["weight"] > 0:
            details.append(f"Constraint: {pass_rate_constraint:.1%}")
        details.append(f'√ {stats["res_cnt"]["mentioned_correct"]}, ○ {stats["res_cnt"]["not_mentioned"]}, × {stats["res_cnt"]["mentioned_incorrect"]}')
        if details:
            score_log += f" [{' | '.join(details)}]"

        logger.info(
            f"Task {task_info.get('task_id', 'unknown')}: "
            f"{score_log}"
        )


    def run(self):
        logger.info("Starting scoring process...")
        
        total_tasks = len(self.task_results)
        
        # Global accumulators
        sum_pass_rate_all = 0.0
        sum_pass_rate_general = 0.0
        sum_pass_rate_constraint = 0.0

        # Valid task counters (denominator for average calculation)
        # Not all tasks have Strict groups. If a task has no Strict groups, its Strict Pass Rate is 0/0=0.
        # We should not count such tasks in the "Strict Average" denominator to avoid skewing the result.
        count_valid_general = 0
        count_valid_constraint = 0

        # Result Cnt
        tot_correct_cnt = 0
        tot_incorrect_cnt = 0
        tot_not_mentioned_cnt = 0

        for task in self.task_results:
            self.process_task(task)
            summary = task["evaluation_summary"]
            
            # Accumulate Overall
            sum_pass_rate_all += summary["pass_rate_all"]
            
            # General
            if summary.get("weight_general", 0.0) > 0:
                sum_pass_rate_general += summary["pass_rate_general"]
                count_valid_general += 1
            
            # Contraint
            if summary.get("weight_constraint", 0.0) > 0:
                sum_pass_rate_constraint += summary["pass_rate_constraint"]
                count_valid_constraint += 1

            # Result Cnt
            tot_correct_cnt += summary["mentioned_correct_cnt"]
            tot_incorrect_cnt += summary["mentioned_incorrect_cnt"]
            tot_not_mentioned_cnt += summary["not_mentioned_cnt"]

        self.save_results()
        
        # Calculate final averages
        avg_pass_all = (sum_pass_rate_all / total_tasks) if total_tasks > 0 else 0.0
        
        # General
        avg_pass_general = (sum_pass_rate_general / count_valid_general) if count_valid_general > 0 else 0.0
        avg_pass_constraint = (sum_pass_rate_constraint / count_valid_constraint) if count_valid_constraint > 0 else 0.0

        # Accuracy
        accuracy = tot_correct_cnt / (tot_correct_cnt + tot_incorrect_cnt) if tot_correct_cnt + tot_incorrect_cnt > 0 else 0.0

        logger.info("--- Final Summary ---")
        logger.info(f"Total Tasks: {total_tasks}")
        logger.info(f"1. Overall Avg Pass Rate: {avg_pass_all:.1%}")
        logger.info(f"2. General Groups Avg Pass Rate: {avg_pass_general:.1%} (over {count_valid_general} tasks)")
        logger.info(f"3. Constraint Groups Avg Pass Rate: {avg_pass_constraint:.1%} (over {count_valid_constraint} tasks)")
        logger.info(f"4. Result Count: √ {tot_correct_cnt} | ○ {tot_not_mentioned_cnt} | × {tot_incorrect_cnt}")
        logger.info(f"5. Accuracy: {accuracy:.1%}")
            
        logger.info(f"Detailed results saved to {self.output_path}")


    def save_results(self):
        with open(self.output_path, "w", encoding="utf-8") as f:
            for item in self.task_results:
                f.write(json.dumps(item, ensure_ascii=False) + "\n")


# === CLI ===

def main():
    parser = argparse.ArgumentParser(description="DeepResearch Score Pipeline")
    parser.add_argument("--eval-result-path", type=str, required=True, help="Path to evaluation result JSON")
    parser.add_argument("--output-path", type=str, required=True, help="Path to save the scored JSON")
    
    args = parser.parse_args()

    config = ScorerConfig(
        eval_result_path=args.eval_result_path,
        output_path=args.output_path
    )
    
    scorer = Scorer(config)
    
    try:
        scorer.run()
    except Exception as e:
        logger.exception("Fatal Error during scoring")

if __name__ == "__main__":
    main()