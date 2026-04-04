from loguru import logger
from typing import Optional, Dict, List, Union, Any
from pydantic import BaseModel, Field
from pathlib import Path
import json
import asyncio
import litellm
from tqdm.asyncio import tqdm_asyncio
from aiolimiter import AsyncLimiter
from tenacity import (
    retry,
    stop_after_attempt,
    wait_random_exponential,
    retry_if_exception_type,
    before_sleep_log,
    RetryCallState
)
import argparse

from deepsynth_eval.eval.schemas import ChecklistItem, ChecklistSubGroup, ChecklistGroup, TaskInfo
from deepsynth_eval.eval.utils import extract_json
from deepsynth_eval.eval.prompts import PROMPTS


class EvaluatorConfig(BaseModel):
    judge_model: str
    task_info_path: str
    output_path: str
    judge_model_base: Optional[str] = None
    judge_model_rpm: int = 60
    judge_model_kwargs: Dict[str, Any] = Field(default_factory=dict)


class Evaluator:
    def __init__(self, config: EvaluatorConfig, limiter: Optional[AsyncLimiter] = None):
        logger.info(f"Evaluator Config:\n{config.model_dump_json(indent=4)}")

        self.config = config
        self.limiter = limiter or AsyncLimiter(max_rate=config.judge_model_rpm, time_period=60)

        self.output_path = Path(config.output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        # Load and validate data
        if not Path(config.task_info_path).exists():
            raise FileNotFoundError(f"Info file not found: {config.task_info_path}")
        raw_data = self._load_task_info(config.task_info_path)

        # Convert to Pydantic models for initial validation
        self.task_infos = [
            TaskInfo( **item )
            for item in raw_data
        ]
        
        logger.info(f"Loaded and validated {len(self.task_infos)} surveys.")


    def _load_task_info(self, task_info_path: str) -> List[Dict]:
        path = Path(task_info_path)
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
                return [data]
            raise ValueError("Input data must be a list or a dictionary")
            
        return data

    
    async def _acall_llm(self, prompt: str) -> str:
        async with self.limiter:
            # Base parameters
            kwargs = dict(
                model=self.config.judge_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,  # Low temperature for more stable evaluation results
                num_retries=2
            )
            if self.config.judge_model_base:
                kwargs["base_url"] = self.config.judge_model_base

            # Merge extra parameters from user
            if self.config.judge_model_kwargs:
                kwargs.update(self.config.judge_model_kwargs)

            response = await litellm.acompletion( **kwargs )
        return response["choices"][0]["message"]["content"]


    async def _acall_llm_with_json_extraction(self, prompt: str) -> Union[List, Dict]:
        """Call LLM and force JSON extraction."""
        content = await self._acall_llm(prompt)
        return extract_json(content)


    def _build_checklist_prompt(self, survey_content: str, subgroup: ChecklistSubGroup) -> str:
        criteria_text = "\n".join([
            f"{i+1}. {req.content}"
            for i, req in enumerate(subgroup.requirements)
        ])

        return PROMPTS["checklist_judge"].format(
            survey_content=survey_content,
            criteria_text=criteria_text,
            req_cnt=len(subgroup.requirements)
        )

    @staticmethod
    def _evaluate_subgroup_callback(retry_state: RetryCallState):
        """
        Callback triggered when LLM calls fail after max retries.
        Sets the result to 'not_mentioned' to avoid blocking the pipeline.
        """
        last_exception = retry_state.outcome.exception()
        # args[0] is self (if method), args[1] is content, args[2] is subgroup
        subgroup = retry_state.args[2]
        
        logger.error(f"Checklist evaluation failed for subgroup '{subgroup.sub_group_name}': {type(last_exception).__name__}: {last_exception}")
        
        # Soft fallback: Assign default failing status
        subgroup.eval_result = ["not_mentioned"] * len(subgroup.requirements)

    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_random_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(logger, log_level="WARNING"),
        retry_error_callback=lambda rs: Evaluator._evaluate_subgroup_callback(rs)
    )
    async def _evaluate_subgroup(self, survey_content: str, subgroup: ChecklistSubGroup):
        # Skip if already filled (e.g., by previous logic)
        if subgroup.eval_result:
            return

        if not subgroup.requirements:
            return

        prompt = self._build_checklist_prompt(survey_content, subgroup)
        results = await self._acall_llm_with_json_extraction(prompt)
        
        # Validate results structure
        if not isinstance(results, list):
            raise ValueError("LLM returned non-list result")
        if len(results) != len(subgroup.requirements):
            raise ValueError(f"Result length mismatch: expected {len(subgroup.requirements)}, got {len(results)}")
        
        subgroup.eval_result = [str(r) for r in results]


    async def _evaluate_checklist(self, survey: TaskInfo):
        tasks = [
            self._evaluate_subgroup(survey.survey_result, sg)
            for group in survey.checklist
                for sg in group.sub_groups
        ]
        await asyncio.gather(*tasks)


    def _fill_defaults_for_task(self, task: TaskInfo):
        """
        Recursively fills missing results in a survey with a fallback value.
        Used when a catastrophic error occurs processing a single survey.
        """
        fill_count = 0
        for group in task.checklist:
            for sg in group.sub_groups:
                if not sg.eval_result:
                    sg.eval_result = ["not_mentioned"] * len(sg.requirements)
                    fill_count += 1
        return fill_count

    
    async def evaluate_single_task(self, task: TaskInfo):
        """
        Process a single survey with a broad try/except block to protect the pipeline.
        """
        try:
            await self._evaluate_checklist(task)
        except Exception as e:
            # Log the full stack trace for debugging
            logger.exception(f"CRITICAL: Failed to evaluate survey {task.survey_id} (Task {task.task_id})")
            
            # Apply Soft Fallback
            logger.warning(f"Applying soft fallback (setting all to 'not_mentioned') for survey {task.survey_id}...")
            filled_count = self._fill_defaults_for_task(task)
            logger.info(f"Fallback complete. Filled {filled_count} subgroups with default values.")


    def save_results(self):
        # Save as JSONL
        with open(self.output_path, "w", encoding="utf-8") as f:
            for item in self.task_infos:
                f.write(item.model_dump_json(exclude_none=True) + "\n")

        logger.info(f"Results saved to {self.output_path}")


    async def run(self) -> List[Dict]:
        tasks = [self.evaluate_single_task(task) for task in self.task_infos]
        await tqdm_asyncio.gather(*tasks, desc="Evaluating Surveys")
        self.save_results()

        return self.task_infos


# === CLI ===
def main():
    parser = argparse.ArgumentParser(description="DeepResearch Evaluation Pipeline")
    parser.add_argument("--judge-model", type=str, required=True, help="Judge model name for LiteLLM")
    parser.add_argument("--task-info-path", type=str, required=True, help="Path to input JSON")
    parser.add_argument("--output-path", type=str, required=True, help="Path to output JSON")
    parser.add_argument("--judge-model-base", type=str, default=None, help="Base URL for judge model")
    parser.add_argument("--judge-model-rpm", type=int, default=60, help="Request per minute for judge model")
    parser.add_argument("--judge-model-kwargs", type=str, default="{}", help="Extra model arguments in JSON")

    args = parser.parse_args()

    try:
        judge_model_kwargs = json.loads(args.judge_model_kwargs)
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON string provided for --judge-model-kwargs: {args.judge_model_kwargs}")
        return
    
    config = EvaluatorConfig(
        judge_model=args.judge_model,
        task_info_path=args.task_info_path,
        output_path=args.output_path,
        judge_model_base=args.judge_model_base,
        judge_model_rpm=args.judge_model_rpm,
        judge_model_kwargs=judge_model_kwargs
    )

    evaluator = Evaluator(config)

    try:
        asyncio.run(evaluator.run())
    except KeyboardInterrupt:
        logger.warning("Process interrupted by user. Saving partial results...")
        evaluator.save_results()  # Try to save what we have
    except Exception as e:
        logger.exception("Unhandled exception in main loop")


if __name__ == "__main__":
    main()