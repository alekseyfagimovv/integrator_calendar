from pydantic import BaseModel
from typing import Optional

class EventCreate(BaseModel):
    summary: str
    start_time: str
    end_time: str
    description: Optional[str] = None