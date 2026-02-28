from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class EntryCreate(BaseModel):
    content: str
    origin: Optional[str] = "manual"

class EntryUpdate(BaseModel):
    status:      Optional[str] = None
    tags:        Optional[str] = None
    summary:     Optional[str] = None
    destination: Optional[str] = None

class EntryOut(BaseModel):
    id:           int
    content:      str
    type:         str
    origin:       str
    status:       str
    tags:         str
    summary:      str
    destination:  str
    created_at:   datetime
    processed_at: Optional[datetime]

    class Config:
        from_attributes = True
