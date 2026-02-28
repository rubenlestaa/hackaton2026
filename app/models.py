from sqlalchemy import Column, Integer, String, DateTime, Text, Enum, UniqueConstraint, ForeignKey, Boolean
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


class GroupSummary(Base):
    """Resumen automático generado cuando un grupo/subgrupo supera 10 ideas."""
    __tablename__ = "group_summaries"

    id            = Column(Integer, primary_key=True, index=True)
    group_name    = Column(String, nullable=False)
    subgroup_name = Column(String, nullable=True)   # None = resumen del grupo raíz
    summary       = Column(Text, default="")
    updated_at    = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    __table_args__ = (
        UniqueConstraint("group_name", "subgroup_name", name="uq_group_subgroup_summary"),
    )


class Reminder(Base):
    """Recordatorio programado: se envía por email cuando llega la hora."""
    __tablename__ = "reminders"

    id         = Column(Integer, primary_key=True, index=True)
    message    = Column(Text, nullable=False)
    fire_at    = Column(DateTime, nullable=False)
    sent       = Column(Boolean, default=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))