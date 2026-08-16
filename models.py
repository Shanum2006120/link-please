from pydantic import BaseModel, Field
from typing import Optional

class RuleCreate(BaseModel):
    keyword: str
    dm_message: str

class RuleResponse(BaseModel):
    rule_id: str
    keyword: str
    dm_message: str

class WebhookEvent(BaseModel):
    event_id: str
    event_type: str
    sent_at: str
    data: dict

class StatsResponse(BaseModel):
    sent: int
    failed: int
    queued: int
    duplicates_blocked: int
