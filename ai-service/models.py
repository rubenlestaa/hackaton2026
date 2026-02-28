from pydantic import BaseModel
from typing import Optional


# ── Peticiones entrantes ──────────────────────────────────────────────────────

class NoteRequest(BaseModel):
    """Una nota en texto libre que el usuario acaba de escribir."""
    text: str                          # "quiero hacer biceps el dia de espalda en el gym"
    existing_groups: list[dict] = [] # Ver formato abajo
    # Formato de existing_groups:
    # [
    #   {
    #     "name": "desarrollo pagina web",
    #     "ideas": ["fondo azul", "crear pagina web"],
    #     "subgroups": [
    #       {"name": "pagina sobre gatos", "ideas": ["fondo azul"]}
    #     ]
    #   }
    # ]


class ProcessRequest(BaseModel):
    """Todos los proyectos y sus notas para generar el resumen final."""
    groups: list[dict]
    # Formato esperado:
    # [
    #   {
    #     "name": "gimnasio",
    #     "sections": [
    #       {"name": "dia de espalda", "notes": ["hacer biceps", "remo con barra"]}
    #     ]
    #   }
    # ]


# ── Respuestas ────────────────────────────────────────────────────────────────

class ClassificationResult(BaseModel):
    """Dónde debe ir esta nota según la IA."""
    action: str = "add"                 # "add" (default) o "delete"
    makes_sense: bool = True            # False si la nota no tiene sentido o no es clasificable
    reason: Optional[str] = None        # Explicación cuando makes_sense=False
    group: Optional[str] = None         # "películas"
    subgroup: Optional[str] = None      # "pagina sobre gatos" (None si va al grupo raíz)
    idea: Optional[str] = None          # "ver Terminator (1999)" (la idea limpia)
    is_new_group: bool = False          # True si hay que crear el grupo
    is_new_subgroup: bool = False       # True si hay que crear el subgrupo
    inherit_parent_ideas: bool = False  # True si el subgrupo nuevo debe heredar las ideas del padre
    rename_group: Optional[dict] = None
    # rename_group formato: {"old_name": "películas", "new_name": "ver películas"}
    # No nulo solo cuando hay que renombrar un grupo existente para desambiguar


class KeyPoint(BaseModel):
    text: str
    category: str = ""  # "acción", "meta", "recordatorio", etc.


class ProjectSummary(BaseModel):
    group_name: str
    summary: str             # Párrafo resumen
    key_points: list[KeyPoint]
    suggested_title: str     # Título sugerido para el grupo


class ProcessResult(BaseModel):
    """Resultado del botón PROCESAR."""
    groups: list[ProjectSummary]
    global_summary: str      # Resumen global de todos los grupos


# ── Audio ────────────────────────────────────────────────────────────────────

class TranscriptionResult(BaseModel):
    """Resultado de transcribir un audio."""
    transcribed_text: str


class AudioClassificationResult(BaseModel):
    """Resultado de transcribir + clasificar un audio."""
    transcribed_text: str           # Lo que dijo el usuario
    classification: ClassificationResult


# ── Respuesta genérica de error ───────────────────────────────────────────────

class ErrorResponse(BaseModel):
    error: str
    detail: str = ""
