from fastapi import FastAPI, Depends, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone, timedelta
from typing import List, Optional
import os
import httpx
import smtplib
import logging
from email.mime.text import MIMEText
from apscheduler.schedulers.background import BackgroundScheduler

from app.database import Base, engine, get_db
from app.models import InboxEntry, GroupSummary, Reminder
from app.schemas import EntryCreate, EntryUpdate, EntryOut
from app.classifier import classify
from app.exporter import export_to_markdown
from app.ai_bridge import classify_with_ai, ai_result_to_entry_fields, find_entry_to_delete, delete_entries_matching, request_summary
from pydantic import BaseModel
import re as _re

_CMD_VERBS = _re.compile(
    r'^(a[ñn]ade|agrega|crea|abre|a[ñn]adir|agregar|crear|abrir|pon|poner|mete|meter)\b',
    _re.IGNORECASE | _re.UNICODE,
)

def _normalize(s: str) -> str:
    return _re.sub(r'\s+', ' ', s.lower().strip())

def _similar(a: str, b: str) -> bool:
    a, b = _normalize(a), _normalize(b)
    return a == b or (len(a) > 3 and (a in b or b in a))


# ── Auto-resumen ──────────────────────────────────────────────────────────────

def _get_group_ideas(group: str, subgroup: Optional[str], db: Session) -> list[str]:
    """Devuelve todas las ideas procesadas de un grupo/subgrupo."""
    all_entries = db.query(InboxEntry).filter(InboxEntry.status == "processed").all()
    ideas: list[str] = []
    for e in all_entries:
        parts = [t.strip() for t in (e.tags or "").split(",") if t.strip()]
        g  = parts[0] if parts else ""
        sg = parts[1] if len(parts) > 1 else None
        if g == group and sg == subgroup and e.summary:
            ideas.append(e.summary)
    return ideas


def _maybe_auto_summarize(group: str, subgroup: Optional[str], db: Session) -> None:
    """Si el grupo/subgrupo tiene >10 ideas, genera (o actualiza) su resumen."""
    ideas = _get_group_ideas(group, subgroup, db)
    if len(ideas) <= 10:
        return
    text = request_summary(group, subgroup, ideas)
    if not text:
        return
    existing = db.query(GroupSummary).filter(
        GroupSummary.group_name    == group,
        GroupSummary.subgroup_name == subgroup,
    ).first()
    if existing:
        existing.summary    = text
        existing.updated_at = datetime.now(timezone.utc)
    else:
        db.add(GroupSummary(group_name=group, subgroup_name=subgroup, summary=text))
    db.commit()

# Crear tablas si no existen (sin borrar datos existentes)
Base.metadata.create_all(bind=engine)

# ── Email + scheduler (recordatorios) ────────────────────────────────────────────
SMTP_HOST    = os.getenv("SMTP_HOST",    "mailhog")
SMTP_PORT    = int(os.getenv("SMTP_PORT", "1025"))
NOTIFY_EMAIL = os.getenv("NOTIFY_EMAIL", "usuario@hackudc.local")
_log = logging.getLogger("uvicorn.error")

def _send_email_notification(message: str, fire_at: datetime) -> None:
    when = fire_at.strftime("%A %d/%m/%Y a las %H:%M")
    body = f"¡Hora de actuar!\n\n⏰ {message}\n\nProgramado para: {when}\n\n— Digital Brain 🧠"
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = f"⏰ Recordatorio: {message}"
    msg["From"]    = "brain@hackudc.local"
    msg["To"]      = NOTIFY_EMAIL
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=5) as s:
            s.sendmail("brain@hackudc.local", [NOTIFY_EMAIL], msg.as_string())
        _log.info(f"[reminders] ✉️  Email enviado: {message}")
    except Exception as exc:
        _log.warning(f"[reminders] Email fallido: {exc}")


def _check_reminders() -> None:
    """Job del scheduler: dispara emails de recordatorios vencidos."""
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        now = datetime.now()
        due = db.query(Reminder).filter(
            Reminder.sent   == False,  # noqa: E712
            Reminder.fire_at <= now,
        ).all()
        for r in due:
            _send_email_notification(r.message, r.fire_at)
            r.sent = True
        if due:
            db.commit()
    finally:
        db.close()


_scheduler = BackgroundScheduler()
_scheduler.add_job(_check_reminders, "interval", seconds=30, id="check_reminders")

app = FastAPI(title="Digital Brain API", version="0.1.0")

@app.on_event("startup")
def _startup():
    _scheduler.start()
    _log.info("[reminders] Scheduler arrancado — comprobando cada 30 s")

@app.on_event("shutdown")
def _shutdown():
    _scheduler.shutdown(wait=False)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Schemas extra para el endpoint unificado ─────────────────────────────────

class NoteIn(BaseModel):
    content: str
    origin: Optional[str] = "manual"
    lang: Optional[str] = "es"

class NoteOut(BaseModel):
    action:        str          # "add" | "delete" | "ignored" | "remind"
    entry:         Optional[EntryOut] = None
    group:         Optional[str] = None
    subgroup:      Optional[str] = None
    idea:          Optional[str] = None
    ai_skipped:    bool = False
    deleted_count: int  = 0
    remind_at:     Optional[str] = None  # ISO datetime del recordatorio


# --- NOTA UNIFICADA (IA + BD en un solo paso) --------------------------------

def _process_single_ai(ai: dict, note: "NoteIn", db: Session) -> "NoteOut":
    """Procesa un resultado individual de la IA y lo guarda en BD."""
    if not ai.get("makes_sense", True):
        return NoteOut(action="ignored", ai_skipped=False)

    action = ai.get("action", "add")

    if action == "delete":
        deleted = delete_entries_matching(ai, db)
        first   = deleted[0] if deleted else None
        if first:
            db.refresh(first)
        return NoteOut(
            action="delete", entry=first,
            group=ai.get("group"), subgroup=ai.get("subgroup"), idea=ai.get("idea"),
            deleted_count=len(deleted),
        )

    if action == "remind":
        remind_at_str = ai.get("remind_at")
        try:
            fire_at = datetime.fromisoformat(remind_at_str) if remind_at_str else datetime.now() + timedelta(minutes=5)
        except (ValueError, TypeError):
            fire_at = datetime.now() + timedelta(minutes=5)
        message = ai.get("idea") or note.content
        reminder = Reminder(message=message, fire_at=fire_at)
        db.add(reminder)
        db.commit()
        return NoteOut(
            action="remind",
            group="recordatorios",
            idea=message,
            remind_at=fire_at.isoformat(),
        )

    fields     = ai_result_to_entry_fields(ai, note.content)
    summary    = fields.get("summary", "")
    entry_type = classify(note.content)
    tags       = fields.get("tags", "")

    if summary and _normalize(summary) == _normalize(note.content):
        summary = ""
    if summary and _CMD_VERBS.match(summary.strip()):
        summary = ""

    # Cada idea distinta se guarda con su propio texto para evitar colisiones
    # de UNIQUE(content) cuando la nota produce múltiples resultados.
    content_to_store = summary if summary else note.content

    existing_dup = (
        db.query(InboxEntry)
        .filter(InboxEntry.status == "processed", InboxEntry.tags == tags)
        .all()
    )
    for dup in existing_dup:
        if _similar(dup.summary or "", summary):
            return NoteOut(action="add", entry=dup,
                           group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)

    db_entry = InboxEntry(content=content_to_store, origin=note.origin,
                          type=entry_type, summary=summary, tags=tags)
    db.add(db_entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(InboxEntry).filter(InboxEntry.content == content_to_store).first()
        if existing:
            return NoteOut(action="add", entry=existing,
                           group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)
        raise HTTPException(status_code=409, detail="Entry already exists")
    db.refresh(db_entry)

    try:
        destination = export_to_markdown(db_entry)
        db_entry.destination  = destination
        db_entry.status       = "processed"
        db_entry.processed_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(db_entry)
    except Exception:
        pass

    if ai.get("group"):
        try:
            _maybe_auto_summarize(ai["group"], ai.get("subgroup"), db)
        except Exception:
            pass  # no interrumpir el flujo principal

    return NoteOut(action="add", entry=db_entry,
                   group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)


@app.post("/note", response_model=list[NoteOut], status_code=201)
def add_note(note: NoteIn, db: Session = Depends(get_db)):
    """
    Endpoint principal. Devuelve una LISTA de resultados (normalmente 1,
    varios cuando la nota contiene múltiples ideas distintas).
    """
    ai_list = classify_with_ai(note.content, db, lang=note.lang or "es")

    if ai_list is None:
        entry_type = classify(note.content)
        db_entry = InboxEntry(content=note.content, origin=note.origin, type=entry_type)
        db.add(db_entry)
        db.commit()
        db.refresh(db_entry)
        return [NoteOut(action="add", entry=db_entry, ai_skipped=True)]

    return [_process_single_ai(ai, note, db) for ai in ai_list]


# --- INBOX ---

@app.post("/inbox", response_model=EntryOut, status_code=201)
def create_entry(entry: EntryCreate, db: Session = Depends(get_db)):
    existing = db.query(InboxEntry).filter(InboxEntry.content == entry.content).first()
    if existing:
        raise HTTPException(status_code=409, detail="Entry with same content already exists")

    entry_type = classify(entry.content)
    db_entry = InboxEntry(**entry.dict(), type=entry_type)
    db.add(db_entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Entry with same content already exists")

    db.refresh(db_entry)
    return db_entry


@app.get("/inbox", response_model=List[EntryOut])
def list_inbox(status: str = "pending", db: Session = Depends(get_db)):
    return db.query(InboxEntry).filter(InboxEntry.status == status).all()


@app.get("/inbox/{entry_id}", response_model=EntryOut)
def get_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    return entry


@app.patch("/inbox/{entry_id}", response_model=EntryOut)
def update_entry(entry_id: int, data: EntryUpdate, db: Session = Depends(get_db)):
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    for field, value in data.dict(exclude_none=True).items():
        setattr(entry, field, value)
    db.commit()
    db.refresh(entry)
    return entry


# --- PROCESADO: valida la propuesta de la IA y exporta ---

@app.post("/inbox/{entry_id}/process", response_model=EntryOut)
def process_entry(entry_id: int, db: Session = Depends(get_db)):
    """
    Tus compis llamarán a este endpoint DESPUÉS de que la IA haya
    rellenado 'summary' y 'tags' vía PATCH. Este endpoint exporta
    a Markdown, hace commit y marca como processed.
    """
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    if entry.status == "processed":
        raise HTTPException(status_code=400, detail="Already processed")

    destination = export_to_markdown(entry)
    entry.destination   = destination
    entry.status        = "processed"
    entry.processed_at  = datetime.now(timezone.utc)
    db.commit()
    db.refresh(entry)
    return entry


@app.delete("/inbox/{entry_id}", status_code=204)
def discard_entry(entry_id: int, db: Session = Depends(get_db)):
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")
    entry.status = "discarded"
    db.commit()


@app.post("/inbox/{entry_id}/ai-classify", response_model=EntryOut)
def ai_classify_entry(entry_id: int, db: Session = Depends(get_db)):
    """
    Clasifica con IA una entrada ya existente (pending) y rellena
    summary + tags.  No exporta a Markdown (usa /process para eso).
    """
    entry = db.query(InboxEntry).filter(InboxEntry.id == entry_id).first()
    if not entry:
        raise HTTPException(status_code=404, detail="Entry not found")

    ai_list = classify_with_ai(entry.content, db)
    if ai_list is None:
        raise HTTPException(status_code=503, detail="Servicio de IA no disponible")
    ai = ai_list[0]
    if not ai.get("makes_sense", True):
        raise HTTPException(status_code=422, detail=ai.get("reason", "La nota no tiene sentido"))

    fields = ai_result_to_entry_fields(ai, entry.content)
    entry.summary = fields.get("summary", "")
    entry.tags    = fields.get("tags", "")
    db.commit()
    db.refresh(entry)
    return entry


# --- BÚSQUEDA básica (tus compis amplían con ChromaDB) ---

@app.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    results = db.query(InboxEntry).filter(
        InboxEntry.content.contains(q) |
        InboxEntry.tags.contains(q) |
        InboxEntry.summary.contains(q)
    ).all()
    return results


# --- SUMMARIES ---

@app.get("/summaries")
def get_summaries(db: Session = Depends(get_db)):
    """Devuelve todos los resúmenes automáticos de grupos/subgrupos."""
    return [
        {"group": s.group_name, "subgroup": s.subgroup_name, "summary": s.summary}
        for s in db.query(GroupSummary).all()
    ]


# ── Audio: proxy hacia el servicio de IA ──────────────────────────────────────

_AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8001")

@app.post("/transcribe")
async def transcribe_proxy(audio: UploadFile = File(...)):
    """
    Recibe un fichero de audio del frontend y lo reenvía al servicio de IA
    para transcribirlo con Whisper. Devuelve {"transcribed_text": "..."}.
    """
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=422, detail="El fichero de audio está vacío.")
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{_AI_SERVICE_URL}/transcribe",
                files={"audio": (audio.filename or "recording.webm", audio_bytes, audio.content_type or "audio/webm")},
            )
        if resp.status_code == 503:
            raise HTTPException(status_code=503, detail="Whisper no disponible en el servicio de IA.")
        resp.raise_for_status()
        return resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Servicio de IA no disponible.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


# ── Recordatorios ─────────────────────────────────────────────────────────────

@app.get("/reminders")
def list_reminders(sent: Optional[bool] = None, db: Session = Depends(get_db)):
    """Lista todos los recordatorios. Filtra por ?sent=false para ver los pendientes."""
    q = db.query(Reminder)
    if sent is not None:
        q = q.filter(Reminder.sent == sent)
    return [
        {
            "id":         r.id,
            "message":    r.message,
            "fire_at":    r.fire_at.isoformat(),
            "sent":       r.sent,
            "created_at": r.created_at.isoformat(),
        }
        for r in q.order_by(Reminder.fire_at).all()
    ]
