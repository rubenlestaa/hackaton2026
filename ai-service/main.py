"""
Servicio de IA para organización de notas — HackUDC 2026
=========================================================
Endpoints:
  POST /classify        → Clasifica una nota (texto) en grupo + subgrupo + idea
  POST /transcribe      → Transcribe un audio a texto (Whisper)
  POST /classify-audio  → Transcribe un audio y lo clasifica directamente
  POST /process         → Procesa todos los proyectos (botón PROCESAR)
  GET  /health          → Estado del servicio, Ollama y Whisper
  GET  /models          → Modelos disponibles en Ollama
"""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware

from models import (
    AudioClassificationResult,
    NoteRequest,
    ProcessRequest,
    ClassificationResult,
    ProcessResult,
    TranscriptionResult,
    ErrorResponse,
    SummarizeRequest,
    SummarizeResult,
)
from classifier import classify_note
from processor import process_projects, summarize_ideas
from transcriber import is_whisper_available, transcribe_audio
from llm_client import is_ollama_running, get_available_models, MODEL_NAME

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)


# ── App lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    if is_ollama_running():
        models = get_available_models()
        log.info(f"✅  Ollama activo. Modelos disponibles: {models}")
        if not any(MODEL_NAME in m for m in models):
            log.warning(
                f"⚠️  El modelo '{MODEL_NAME}' no está descargado. "
                f"Ejecuta: ollama pull {MODEL_NAME}"
            )
    else:
        log.warning("⚠️  Ollama NO está corriendo. Inicia Ollama antes de usar los endpoints.")
    yield


# ── FastAPI app ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="HackUDC — AI Notes Organizer",
    description="Servicio de IA que clasifica notas en proyectos y genera resúmenes.",
    version="1.0.0",
    lifespan=lifespan,
)

# Permitir peticiones desde cualquier origen (para que el frontend/backend puedan llamarnos)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/health", tags=["Sistema"])
def health():
    """
    Estado del servicio. Comprueba si Ollama está activo y qué modelos hay.

    Respuesta de ejemplo:
    ```json
    {
      "status": "ok",
      "ollama": true,
      "model": "llama3.2",
      "available_models": ["llama3.2:latest"]
    }
    ```
    """
    ollama_ok  = is_ollama_running()
    whisper_ok = is_whisper_available()
    models = get_available_models() if ollama_ok else []
    return {
        "status":           "ok" if ollama_ok else "ollama_unavailable",
        "ollama":           ollama_ok,
        "model":            MODEL_NAME,
        "available_models": models,
        "whisper":          whisper_ok,
    }


@app.get("/models", tags=["Sistema"])
def list_models():
    """Lista los modelos disponibles en Ollama."""
    if not is_ollama_running():
        raise HTTPException(status_code=503, detail="Ollama no está corriendo")
    return {"models": get_available_models()}


@app.post("/summarize", response_model=SummarizeResult, tags=["IA"])
def summarize_group(request: SummarizeRequest) -> SummarizeResult:
    """Genera un resumen de todas las ideas de un grupo/subgrupo."""
    if not is_ollama_running():
        raise HTTPException(status_code=503, detail="Ollama no est\u00e1 corriendo")
    summary = summarize_ideas(request.group, request.subgroup, request.ideas)
    return SummarizeResult(group=request.group, subgroup=request.subgroup, summary=summary)


@app.post(
    "/classify",
    response_model=list[ClassificationResult],
    responses={503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["IA"],
)
def classify(request: NoteRequest):
    """
    Clasifica una nota en texto libre y devuelve en qué grupo y sección guardarla.

    **Body de ejemplo:**
    ```json
    {
      "text": "una de las páginas web que quiero crear sería sobre gatos",
      "existing_groups": [
        {
          "name": "desarrollo pagina web",
          "ideas": ["crear página web", "fondo azul"],
          "subgroups": []
        }
      ]
    }
    ```

    **Respuesta de ejemplo:**
    ```json
    {
      "group": "desarrollo pagina web",
      "subgroup": "pagina sobre gatos",
      "idea": "página temática sobre gatos",
      "is_new_group": false,
      "is_new_subgroup": true,
      "inherit_parent_ideas": true
    }
    ```
    **Cuando `inherit_parent_ideas` es `true`**, el backend debe copiar las ideas del proyecto
    padre al nuevo subproyecto antes de guardar la idea nueva.
    """
    if not is_ollama_running():
        raise HTTPException(
            status_code=503,
            detail="Ollama no está corriendo. Inicia Ollama con 'ollama serve'.",
        )

    try:
        log.info(f"📝  Clasificando nota: '{request.text}'")
        results = classify_note(request.text, request.existing_groups, lang=request.lang or "es")
        for r in results:
            if r.makes_sense:
                idea_info = f", idea='{r.idea}'" if r.idea else " (sin idea)"
                log.info(
                    f"✅  → grupo='{r.group}', subgrupo='{r.subgroup}'"
                    f"{idea_info}"
                )
            else:
                log.info(f"🚫  Nota sin sentido: {r.reason}")
        return results
    except Exception as exc:
        log.error(f"❌  Error al clasificar: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/process",
    response_model=ProcessResult,
    responses={503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["IA"],
)
def process(request: ProcessRequest):
    """
    Procesa todos los proyectos y sus notas (botón PROCESAR).
    Genera resúmenes, puntos clave y un resumen global.

    **Body de ejemplo:**
    ```json
    {
      "projects": [
        {
          "name": "gimnasio",
          "sections": [
            {
              "name": "dia de espalda",
              "notes": ["hacer bíceps", "remo con barra", "jalón al pecho"]
            },
            {
              "name": "dia de pierna",
              "notes": ["sentadillas 4x8", "prensa", "extensiones de cuádriceps"]
            }
          ]
        }
      ]
    }
    ```

    **Respuesta de ejemplo:**
    ```json
    {
      "projects": [
        {
          "project_name": "gimnasio",
          "suggested_title": "Plan de Entrenamiento",
          "summary": "Tienes un plan de gimnasio bien estructurado...",
          "key_points": [
            {"text": "Hacer bíceps en el día de espalda", "category": "acción"},
            {"text": "Completar rutina de piernas con sentadillas", "category": "acción"}
          ]
        }
      ],
      "global_summary": "Estás construyendo una rutina de entrenamiento completa..."
    }
    ```
    """
    if not is_ollama_running():
        raise HTTPException(
            status_code=503,
            detail="Ollama no está corriendo. Inicia Ollama con 'ollama serve'.",
        )

    if not request.groups:
        raise HTTPException(status_code=400, detail="No hay grupos que procesar.")

    try:
        log.info(f"🔄  Procesando {len(request.groups)} grupo(s)...")
        result = process_projects(request.groups)
        log.info(f"✅  Procesado correctamente.")
        return result
    except Exception as exc:
        log.error(f"❌  Error al procesar: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Audio: transcripción y clasificación ─────────────────────────────────────

@app.post(
    "/transcribe",
    response_model=TranscriptionResult,
    responses={422: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["Audio"],
)
async def transcribe(audio: UploadFile = File(...)):
    """
    Transcribe un fichero de audio a texto usando Whisper.

    - Enviar como `multipart/form-data` con el campo `audio`.
    - Formatos soportados: **mp3, wav, m4a, ogg, webm, flac** (requiere ffmpeg).

    **Respuesta de ejemplo:**
    ```json
    { "transcribed_text": "me gustaría crear una página web" }
    ```
    """
    if not is_whisper_available():
        raise HTTPException(
            status_code=503,
            detail="faster-whisper no está instalado. Ejecuta: pip install faster-whisper",
        )
    try:
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(status_code=422, detail="El fichero de audio está vacío.")
        log.info(f"🎙️  Transcribiendo '{audio.filename}' ({len(audio_bytes)} bytes)...")
        text = transcribe_audio(audio_bytes, audio.filename or "audio.wav")
        if not text:
            raise HTTPException(status_code=422, detail="No se detectó habla en el audio.")
        return TranscriptionResult(transcribed_text=text)
    except HTTPException:
        raise
    except Exception as exc:
        log.error(f"❌  Error al transcribir: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@app.post(
    "/classify-audio",
    response_model=AudioClassificationResult,
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    tags=["Audio"],
)
async def classify_audio(
    audio: UploadFile = File(...),
    existing_groups: str = Form(default="[]"),
):
    """
    **Todo en uno**: transcribe el audio y clasifica el texto resultante.

    - Enviar como `multipart/form-data`:
      - `audio` → fichero de audio
      - `existing_groups` → JSON string con la lista de grupos actuales (opcional)

    **Ejemplo de `existing_groups`:**
    ```json
    [{"name": "desarrollo pagina web", "ideas": ["fondo azul"], "subgroups": []}]
    ```

    **Respuesta de ejemplo:**
    ```json
    {
      "transcribed_text": "quiero añadir una galería de fotos a la página de gatos",
      "classification": {
        "makes_sense": true,
        "group": "desarrollo pagina web",
        "subgroup": "pagina sobre gatos",
        "idea": "galería de fotos",
        "is_new_group": false,
        "is_new_subgroup": false,
        "inherit_parent_ideas": false
      }
    }
    ```
    """
    if not is_whisper_available():
        raise HTTPException(
            status_code=503,
            detail="faster-whisper no está instalado. Ejecuta: pip install faster-whisper",
        )
    if not is_ollama_running():
        raise HTTPException(
            status_code=503,
            detail="Ollama no está corriendo. Inicia Ollama con 'ollama serve'.",
        )

    # ── 1. Parsear existing_projects ─────────────────────────────────────────
    import json
    try:
        existing = json.loads(existing_groups)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail="existing_groups no es JSON válido.")

    # ── 2. Transcribir ────────────────────────────────────────────────────────
    try:
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(status_code=422, detail="El fichero de audio está vacío.")
        log.info(f"🎙️  Transcribiendo '{audio.filename}' ({len(audio_bytes)} bytes)...")
        text = transcribe_audio(audio_bytes, audio.filename or "audio.wav")
        if not text:
            raise HTTPException(status_code=422, detail="No se detectó habla en el audio.")
    except HTTPException:
        raise
    except Exception as exc:
        log.error(f"❌  Error al transcribir: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error en transcripción: {exc}")

    # ── 3. Clasificar ─────────────────────────────────────────────────────────
    try:
        log.info(f"📝  Clasificando texto transcrito: '{text}'")
        result = classify_note(text, existing)
        if result.makes_sense:
            log.info(f"✅  → grupo='{result.group}', idea='{result.idea}'")
        else:
            log.info(f"🚫  Audio sin sentido clasificable: {result.reason}")
        return AudioClassificationResult(transcribed_text=text, classification=result)
    except Exception as exc:
        log.error(f"❌  Error al clasificar: {exc}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Error en clasificación: {exc}")


# ── Punto de entrada ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8001, reload=True)
