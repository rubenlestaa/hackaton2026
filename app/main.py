from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from typing import List, Optional

from app.database import Base, engine, get_db
from app.models import InboxEntry
from app.schemas import EntryCreate, EntryUpdate, EntryOut
from app.classifier import classify
from app.exporter import export_to_markdown
from app.ai_bridge import classify_with_ai, ai_result_to_entry_fields, find_entry_to_delete
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

# Resetear la BD en cada arranque (útil para desarrollo/demos)
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="Digital Brain API", version="0.1.0")

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

class NoteOut(BaseModel):
    action:     str          # "add" | "delete" | "ignored"
    entry:      Optional[EntryOut] = None
    group:      Optional[str] = None
    subgroup:   Optional[str] = None
    idea:       Optional[str] = None
    ai_skipped: bool = False  # True si el servicio de IA no estaba disponible


# --- NOTA UNIFICADA (IA + BD en un solo paso) --------------------------------

def _process_single_ai(ai: dict, note: "NoteIn", db: Session) -> "NoteOut":
    """Procesa un resultado individual de la IA y lo guarda en BD."""
    if not ai.get("makes_sense", True):
        return NoteOut(action="ignored", ai_skipped=False)

    action = ai.get("action", "add")

    if action == "delete":
        target = find_entry_to_delete(ai, db)
        if target:
            target.status = "discarded"
            db.commit()
            db.refresh(target)
        return NoteOut(
            action="delete", entry=target,
            group=ai.get("group"), subgroup=ai.get("subgroup"), idea=ai.get("idea"),
        )

    fields     = ai_result_to_entry_fields(ai, note.content)
    summary    = fields.get("summary", "")
    entry_type = classify(note.content)
    tags       = fields.get("tags", "")

    if summary and _normalize(summary) == _normalize(note.content):
        summary = ""
    if summary and _CMD_VERBS.match(summary.strip()):
        summary = ""

    existing_dup = (
        db.query(InboxEntry)
        .filter(InboxEntry.status == "processed", InboxEntry.tags == tags)
        .all()
    )
    for dup in existing_dup:
        if _similar(dup.summary or "", summary):
            return NoteOut(action="add", entry=dup,
                           group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)

    db_entry = InboxEntry(content=note.content, origin=note.origin,
                          type=entry_type, summary=summary, tags=tags)
    db.add(db_entry)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = db.query(InboxEntry).filter(InboxEntry.content == note.content).first()
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

    return NoteOut(action="add", entry=db_entry,
                   group=ai.get("group"), subgroup=ai.get("subgroup"), idea=summary or None)


@app.post("/note", response_model=list[NoteOut], status_code=201)
def add_note(note: NoteIn, db: Session = Depends(get_db)):
    """
    Endpoint principal. Devuelve una LISTA de resultados (normalmente 1,
    varios cuando la nota contiene múltiples ideas distintas).
    """
    ai_list = classify_with_ai(note.content, db)

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
