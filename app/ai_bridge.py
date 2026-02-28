"""
ai_bridge.py — Puente entre el backend (SQLite) y el servicio de IA (puerto 8001).

Funciones principales:
  classify_with_ai(content, db) → dict con group, subgroup, idea, action, ...
  build_existing_groups(db)   → lista de grupos existentes sacada de la BD
"""

import logging
import os
import re
from typing import Optional

import httpx
from sqlalchemy.orm import Session

from app.models import InboxEntry

# ── Condensación de idea verbatim ────────────────────────────────────────────

# Verbos/frases de relleno que el usuario pone al inicio y que NO son parte de la idea
_FILLER_RE = re.compile(
    r"^(me gustar[ií]a\s+(que\s+)?|quisiera\s+|quiero\s+que\s+|quiero\s+|tengo\s+que\s+|"
    r"tengo\s+ganas\s+de\s+|voy\s+a\s+|me\s+apetece\s+|me\s+gustar[ií]a\s+|"
    r"tendr[ií]a\s+que\s+|deber[ií]a\s+|me\s+conviene\s+|necesito\s+|necesitar[ií]a\s+|"
    r"pienso\s+en\s+|estoy\s+pensando\s+en\s+|pienso\s+|me\s+interesar[ií]a\s+|"
    r"me\s+mola\s+|me\s+apetecer[ií]a\s+|planifico\s+|planeo\s+|plan\s+de\s+)",
    re.IGNORECASE,
)

_STOP_ES = {
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "al", "que", "en", "y", "a", "o", "con",
    "por", "para", "me", "te", "se", "le", "lo", "su",
    "si", "ya", "no", "como", "pero", "este", "esta",
    "ese", "esa", "aquel", "mi", "tu", "nos", "les",
}


def _trim_idea(idea: str, content: str = "") -> str:
    """Reduce una idea verbatim a su núcleo esencial (≤4 palabras)."""
    if not idea:
        return idea

    # 1. Quitar verbos/frases de relleno al inicio
    trimmed = _FILLER_RE.sub("", idea).strip()

    # 2. Si sigue siendo larga Y se parece mucho al input original, extraer solo sustantivos
    words = trimmed.split()
    if len(words) > 4 and content:
        content_tokens = set(re.findall(r"\w+", content.lower())) - _STOP_ES
        idea_tokens = [w for w in re.findall(r"\w+", trimmed.lower()) if w not in _STOP_ES]
        if content_tokens and len(set(idea_tokens) & content_tokens) / max(len(idea_tokens), 1) >= 0.65:
            # Demasiado literal: quedarse con las primeras 4 palabras significativas
            meaningful = [t for t in idea_tokens if t not in _STOP_ES][:4]
            # Reconstruir capitalizándolo igual que en la idea original
            orig_lower = trimmed.lower().split()
            result_words = []
            for mw in meaningful:
                for ow, orig_word in zip(orig_lower, trimmed.split()):
                    if ow == mw:
                        result_words.append(orig_word)
                        break
                else:
                    result_words.append(mw)
            return " ".join(result_words) if result_words else trimmed

    # 3. Limitar a 5 palabras máximo de todas formas
    return " ".join(trimmed.split()[:5]) if len(trimmed.split()) > 5 else trimmed

AI_SERVICE_URL = os.getenv("AI_SERVICE_URL", "http://localhost:8001")
CLASSIFY_TIMEOUT = 240  # segundos (LLM puede tardar; debe superar el timeout de Ollama)

logger = logging.getLogger(__name__)


# ── Construir existing_groups desde la BD ─────────────────────────────────────────────

def build_existing_groups(db: Session) -> list[dict]:
    """
    Reconstruye la lista de grupos existentes a partir de las entradas
    procesadas en la BD.  Cada entrada procesada tiene:
      tags    → "group[,subgroup]"   (CSV)
      summary → idea clasificada
    """
    processed = (
        db.query(InboxEntry)
        .filter(InboxEntry.status == "processed")
        .all()
    )

    # Agrupar: groups[name] = {"name": ..., "ideas": [...], "subgroups": {...}}
    groups: dict[str, dict] = {}

    for entry in processed:
        parts = [t.strip() for t in (entry.tags or "").split(",") if t.strip()]
        if not parts:
            continue
        pname = parts[0]
        spname = parts[1] if len(parts) > 1 else None
        idea = entry.summary or ""

        if pname not in groups:
            groups[pname] = {"name": pname, "ideas": [], "subgroups": {}}

        if spname:
            if spname not in groups[pname]["subgroups"]:
                groups[pname]["subgroups"][spname] = {"name": spname, "ideas": []}
            if idea:
                groups[pname]["subgroups"][spname]["ideas"].append(idea)
        else:
            if idea:
                groups[pname]["ideas"].append(idea)

    # Convertir subgroups dict → list para el formato que espera la IA
    result = []
    for proj in groups.values():
        result.append({
            "name":     proj["name"],
            "ideas":    proj["ideas"],
            "subgroups": list(proj["subgroups"].values()),
        })
    return result


# ── Llamada al servicio de IA ─────────────────────────────────────────────────

def classify_with_ai(content: str, db: Session, lang: str = "es") -> Optional[list[dict]]:
    """
    Llama a POST /classify del servicio de IA y devuelve la lista de resultados.
    Devuelve None si el servicio no está disponible.
    Devuelve siempre una lista (normalmente 1 elemento).
    """
    existing = build_existing_groups(db)
    payload = {"text": content, "existing_groups": existing, "lang": lang}

    try:
        with httpx.Client(timeout=CLASSIFY_TIMEOUT) as client:
            resp = client.post(f"{AI_SERVICE_URL}/classify", json=payload)
            resp.raise_for_status()
            data = resp.json()
            # El servicio devuelve siempre una lista ahora
            if isinstance(data, list):
                return data
            return [data]  # compatibilidad con respuesta antigua
    except httpx.ConnectError:
        logger.warning("El servicio de IA no está disponible en %s", AI_SERVICE_URL)
        return None
    except Exception as exc:
        logger.error("Error llamando al servicio de IA: %s", exc)
        return None


# ── Convertir resultado de IA a campos de InboxEntry ─────────────────────────

def ai_result_to_entry_fields(ai: dict, content: str = "") -> dict:
    """
    Transforma el JSON del clasificador en los campos que se guardan en BD:
      tags    → "project[,subproject]"
      summary → idea (o razón si `makes_sense=False`)
    content: texto original de la nota (para detectar y recortar frases verbatim).
    """
    if not ai.get("makes_sense", True):
        return {"summary": ai.get("reason", ""), "tags": ""}

    parts = [ai["group"]] if ai.get("group") else []
    if ai.get("subgroup"):
        parts.append(ai["subgroup"])

    idea = ai.get("idea") or ""

    # Recortar idea si es demasiado literal o larga
    idea = _trim_idea(idea, content)

    # Safety-net: descarta la idea si es un comando de creación/gestión.
    # Comprobación 1 — lista de palabras clave
    _CREATION_KEYWORDS = (
        "añade", "añadir", "agrega", "agregar", "crea", "crear",
        "abre", "abrir", "nuevo grupo", "nueva categoria",
        "nueva categoría", "el grupo", "un grupo",
        "el subgrupo", "un subgrupo", "nuevo subgrupo", "nueva sección",
        "nueva seccion", "subgrupo de",
    )
    idea_lower = idea.lower()
    if any(kw in idea_lower for kw in _CREATION_KEYWORDS):
        idea = ""
    # Comprobación 2 — la idea contiene la palabra "subgrupo" o "grupo" entre los primeros tokens
    if idea and any(w in idea_lower.split()[:4] for w in ("subgrupo", "grupo", "categoría", "categoria", "seccion", "sección")):
        idea = ""

    return {
        "tags":    ",".join(parts),
        "summary": idea,
    }


# ── Buscar entrada a eliminar ─────────────────────────────────────────────────

def request_summary(group: str, subgroup: Optional[str], ideas: list[str]) -> Optional[str]:
    """
    Pide al servicio de IA que genere un resumen para un grupo/subgrupo
    basándose en la lista de ideas almacenadas.
    Devuelve el texto del resumen o None si falla.
    """
    payload = {"group": group, "subgroup": subgroup, "ideas": ideas}
    try:
        with httpx.Client(timeout=CLASSIFY_TIMEOUT) as client:
            resp = client.post(f"{AI_SERVICE_URL}/summarize", json=payload)
            resp.raise_for_status()
            return resp.json().get("summary")
    except Exception as exc:
        logger.warning("Error generando resumen de grupo: %s", exc)
        return None


# ── Buscar entrada a eliminar ─────────────────────────────────────────────────

def delete_entries_matching(ai: dict, db: Session) -> list[InboxEntry]:
    """
    Marca como 'discarded' todas las InboxEntry que coincidan con el
    group/subgroup/idea del resultado de la IA.

    Semántica de borrado:
      - idea provista          → borra la entrada específica (1 elemento)
      - idea=None, subgroup    → borra TODO el subgrupo
      - idea=None, subgroup=None → borra TODO el grupo
    """
    group    = (ai.get("group") or "").lower().strip()
    subgroup = (ai.get("subgroup") or "").lower().strip()
    idea     = (ai.get("idea") or "").lower().strip()

    if not group:
        return []

    candidates = (
        db.query(InboxEntry)
        .filter(InboxEntry.status == "processed")
        .all()
    )

    deleted = []
    for entry in candidates:
        parts       = [t.strip().lower() for t in (entry.tags or "").split(",") if t.strip()]
        entry_group = parts[0] if parts else ""
        entry_sub   = parts[1] if len(parts) > 1 else ""
        entry_idea  = (entry.summary or "").lower()

        if entry_group != group:
            continue

        if idea:
            # Borrado de idea específica — requiere subgrupo y texto coincidentes
            if subgroup and entry_sub != subgroup:
                continue
            if idea not in entry_idea and entry_idea not in idea:
                continue
        elif subgroup:
            # Borrado de subgrupo completo
            if entry_sub != subgroup:
                continue
        # else: borrado de grupo completo — cualquier entrada del grupo

        entry.status = "discarded"
        deleted.append(entry)

    if deleted:
        db.commit()

    return deleted


# Keep old name as alias for backward compatibility
def find_entry_to_delete(ai: dict, db: Session) -> Optional[InboxEntry]:
    results = delete_entries_matching(ai, db)
    return results[0] if results else None
