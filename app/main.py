from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from datetime import datetime, timezone
from typing import List

from app.database import Base, engine, get_db
from app.models import InboxEntry
from app.schemas import EntryCreate, EntryUpdate, EntryOut
from app.classifier import classify
from app.exporter import export_to_markdown

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Digital Brain API", version="0.1.0")


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


# --- BÚSQUEDA básica (tus compis amplían con ChromaDB) ---

@app.get("/search")
def search(q: str, db: Session = Depends(get_db)):
    results = db.query(InboxEntry).filter(
        InboxEntry.content.contains(q) |
        InboxEntry.tags.contains(q) |
        InboxEntry.summary.contains(q)
    ).all()
    return results
