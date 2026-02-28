"""
Módulo de transcripción de audio usando Whisper (faster-whisper).
Carga el modelo en memoria la primera vez y lo reutiliza.

Modelos disponibles (nombre → tamaño en disco):
  tiny    →  ~39 MB  (más rápido, menos preciso)
  base    →  ~74 MB  ← por defecto
  small   → ~244 MB
  medium  → ~769 MB
  large-v3→ ~1.5 GB

Requiere ffmpeg en el PATH del sistema.
  Windows: https://ffmpeg.org/download.html  (o: winget install ffmpeg)
"""

import os
import tempfile
import logging

log = logging.getLogger(__name__)

# ── Configuración ─────────────────────────────────────────────────────────────

WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL", "base")   # cambia aquí o con variable de entorno
WHISPER_DEVICE     = "cpu"          # "cuda" si tienes GPU NVIDIA con CUDA
WHISPER_COMPUTE    = "int8"         # "float16" para GPU, "int8" para CPU

# ── Estado global (singleton del modelo) ─────────────────────────────────────

_model = None


def _get_model():
    """Carga el modelo Whisper la primera vez (lazy loading)."""
    global _model
    if _model is None:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise RuntimeError(
                "faster-whisper no está instalado. "
                "Ejecuta: pip install faster-whisper"
            )
        log.info(f"⏳  Cargando modelo Whisper '{WHISPER_MODEL_SIZE}' en {WHISPER_DEVICE}...")
        _model = WhisperModel(
            WHISPER_MODEL_SIZE,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE,
        )
        log.info("✅  Modelo Whisper cargado.")
    return _model


# ── Función principal ─────────────────────────────────────────────────────────

def transcribe_audio(audio_bytes: bytes, filename: str = "audio.wav") -> str:
    """
    Transcribe un audio a texto.

    Parámetros:
        audio_bytes: contenido binario del fichero de audio
        filename:    nombre original (se usa la extensión para el fichero temporal)

    Devuelve:
        Texto transcrito como string. Vacío si no se detectó habla.

    Formatos soportados (con ffmpeg instalado):
        mp3, mp4, m4a, ogg, wav, webm, flac, ...
    """
    model = _get_model()

    # Guardar en fichero temporal con la extensión correcta
    ext = os.path.splitext(filename)[1] or ".wav"
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
        tmp.write(audio_bytes)
        tmp_path = tmp.name

    try:
        segments, info = model.transcribe(
            tmp_path,
            beam_size=5,
            language=None,          # detección automática de idioma
            vad_filter=True,        # filtra silencios
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        detected_lang = info.language
        log.info(f"🎙️  Transcripción completa. Idioma detectado: {detected_lang}. Texto: '{text}'")
        return text
    finally:
        os.unlink(tmp_path)


def is_whisper_available() -> bool:
    """Comprueba si faster-whisper está instalado."""
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False
