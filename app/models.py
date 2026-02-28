from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, UniqueConstraint
from datetime import datetime, timezone
from app.database import Base
import enum

class EntryStatus(str, enum.Enum):
    pending   = "pending"
    processed = "processed"
    discarded = "discarded"

class EntryType(str, enum.Enum):
    url      = "url"
    note     = "note"
    task     = "task"
    code     = "code"
    audio    = "audio"
    document = "document"
    unknown  = "unknown"

class InboxEntry(Base):
    __tablename__ = "inbox_entries"

    id         = Column(Integer, primary_key=True, index=True)
    content    = Column(Text, nullable=False)
    type       = Column(String, default=EntryType.unknown)
    origin     = Column(String, default="manual")   # manual, api, cli...
    status     = Column(String, default=EntryStatus.pending)
    tags       = Column(String, default="")          # CSV: "python,IA,notes"
    summary    = Column(Text, default="")            # Rellenado por la IA
    destination= Column(String, default="")          # Ruta del .md generado
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("content", name="uq_inbox_content"),
    )