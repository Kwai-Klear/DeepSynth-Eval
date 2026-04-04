from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Any, Union


class ChecklistItem(BaseModel):
    content: str
    reward: float = 1.0

class ChecklistSubGroup(BaseModel):
    sub_group_name: str
    requirements: List[ChecklistItem]
    # Runtime result field (not in input)
    eval_result: List[str] = Field(default_factory=list)

class ChecklistGroup(BaseModel):
    group_name: str
    sub_groups: List[ChecklistSubGroup]
    strict: bool = False
    detailed_prompt: Optional[str] = None
    clip_factor: float = 1.0
    weight: float = 1.0
    
    model_config = ConfigDict(
        extra="allow"
    )

class TaskInfo(BaseModel):
    task_id: Union[str, int] = ""
    survey_id: Optional[str] = None
    general_prompt: str = ""
    survey_result: str = ""
    checklist: List[ChecklistGroup] = Field(default_factory=list)
    
    model_config = ConfigDict(
        extra="allow"
    )