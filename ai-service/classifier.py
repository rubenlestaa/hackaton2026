"""
Lógica de clasificación de notas usando el LLM.
Dado el texto libre de una nota, el LLM decide en qué grupo y sección va.
"""

import json
import re
from datetime import datetime, timedelta
from models import ClassificationResult
from llm_client import _call_ollama, _call_ollama_with_tools, extract_json

# ── Reminder pre-detector (Python-only, before LLM) ──────────────────────────

_REMIND_KEYWORDS = re.compile(
    r'\b(av[ií]same|avisa(me)?|recu[eé]rdame|acu[eé]rdame|noti[fh]í?came|'
    r'ponme\s+un\s+aviso|ponme\s+una\s+alarma|ponme\s+un\s+recordatorio)\b',
    re.IGNORECASE
)

_TIME_RE = re.compile(
    r'\b(?:a\s+las?\s+)?(\d{1,2})(?:[:\.](\d{2}))?\s*(?:h(?:oras?)?)?\b',
    re.IGNORECASE
)

_WEEKDAYS_ES = {
    'lunes': 0, 'martes': 1, 'miercoles': 2, 'miércoles': 2,
    'jueves': 3, 'viernes': 4, 'sabado': 5, 'sábado': 5, 'domingo': 6
}

def _extract_remind_datetime(text: str, now: datetime) -> datetime:
    """Best-effort extract of a target datetime from a Spanish reminder text."""
    text_l = text.lower()

    # Detect day offset
    day_offset = None
    if 'mañana' in text_l or 'manana' in text_l:
        day_offset = 1
    elif 'pasado mañana' in text_l or 'pasado manana' in text_l:
        day_offset = 2
    else:
        for wday_name, wday_num in _WEEKDAYS_ES.items():
            if wday_name in text_l:
                diff = (wday_num - now.weekday()) % 7
                day_offset = diff if diff > 0 else 7
                break

    # Detect time
    hour, minute = None, 0
    m = _TIME_RE.search(text_l)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2)) if m.group(2) else 0

    if hour is None:
        # No time found — default to 5 min from now
        return now + timedelta(minutes=5)

    # Build target datetime
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if day_offset is not None:
        target += timedelta(days=day_offset)
    elif target <= now:
        # Same-day time already passed → push to tomorrow
        target += timedelta(days=1)

    return target


def _extract_remind_message(text: str) -> str:
    """Strip the reminder verb + time/date references to get the core message."""
    msg = re.sub(
        r'\b(av[ií]same|avisa(me)?|recu[eé]rdame|acu[eé]rdame|noti[fh]í?came|'
        r'ponme\s+un\s+aviso|ponme\s+una\s+alarma|ponme\s+un\s+recordatorio)\b',
        '', text, flags=re.IGNORECASE
    )
    # Remove time expressions
    msg = re.sub(r'\b(a\s+las?\s+\d{1,2}(:\d{2})?(\s*h(oras?)?)?)', '', msg, flags=re.IGNORECASE)
    # Remove day/date words
    msg = re.sub(
        r'\b(mañana|manana|pasado\s+mañana|pasado\s+manana|'
        r'el\s+(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)|'
        r'lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo|'
        r'hoy|esta\s+noche|esta\s+tarde)\b',
        '', msg, flags=re.IGNORECASE
    )
    # Remove connectors (only standalone, not inside words)
    msg = re.sub(r'(?<!\w)(para|que|tengo|hay)(?!\w)', '', msg, flags=re.IGNORECASE)
    msg = re.sub(r'\s+', ' ', msg).strip(' ,.-')
    return msg or text.strip()


def _try_remind_precheck(note_text: str) -> list[ClassificationResult] | None:
    """If the note is clearly a reminder request, return result without LLM call."""
    if not _REMIND_KEYWORDS.search(note_text):
        return None
    now = datetime.now()
    fire_at = _extract_remind_datetime(note_text, now)
    message = _extract_remind_message(note_text)
    return [ClassificationResult(
        action="remind",
        makes_sense=True,
        reason=None,
        group="recordatorios",
        subgroup=None,
        idea=message,
        remind_at=fire_at.isoformat(),
        is_new_group=False,
        is_new_subgroup=False,
        inherit_parent_ideas=False,
        rename_group=None,
    )]

# ── Prompt del sistema ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un asistente de organización de ideas. Clasifica cada nota en grupos y devuelve SOLO JSON.

━━ PASO 0 — ¿Quiere ELIMINAR algo? ━━
Palabras clave: elimina, borra, quita, ya no quiero, cancela, descarta, olvida, táchalo...
  • Idea específica     → idea="texto aproximado", subgroup si procede
  • Todo un subgrupo    → idea=null, subgroup="nombre del subgrupo"
  • Todo un grupo       → idea=null, subgroup=null
→ {"action":"delete","makes_sense":true,"group":"...","subgroup":null,"idea":null}
Busca el grupo/subgrupo en los existentes. Si no encuentras nada → makes_sense=false.

━━ PASO 0b — ¿Quiere fijar un RECORDATORIO? ━━  ← PRIORIDAD MÁXIMA
⚠️  Si la nota contiene CUALQUIERA de estas palabras → acción ES SIEMPRE "remind", NUNCA "add".
Palabras clave (con o sin tilde): avísame, avisame, avisa, recuérdame, recuerdame, notifícame, notificame,
  ponme un aviso, ponme una alarma, recuérdame, acuérdame, acuerdame, a las X tengo que...
→ {"action":"remind","makes_sense":true,"group":"recordatorios","subgroup":null,"idea":"texto del aviso","remind_at":"ISO_DATETIME"}
remind_at: calcula el datetime ISO absoluto usando la hora actual indicada al inicio del mensaje.
Si no se da fecha, usa el mismo día a esa hora; si esa hora ya pasó hoy, usa mañana.

⛔ NUNCA pongas action="add" si la nota empieza o contiene avísame/avisame/recuérdame/recuerdame/notifícame.

━━ PASO 1 — ¿Tiene sentido? ━━
Si es texto aleatorio (asdfgh), saludo (hola qué tal) o sin idea real → {"makes_sense":false,"reason":"..."}

━━ PASO 2 — Categorías OBLIGATORIAS (úsalas si encajan, aunque no estén en los grupos existentes) ━━
  "rutina diaria"  → hábitos, horarios, sueño, deporte, comidas.  SUBGRUPO = ámbito (dormir/deporte/desayuno…)
  "compras"        → todo lo que hay que comprar.  SUBGRUPO = tienda/lugar si se menciona.
  "trabajo/clase"  → tareas, reuniones, entregas, exámenes.
  "finanzas"       → pagos, facturas, ahorros, gastos.
  "viajes"         → viajes, escapadas, vuelos, hoteles.
  "vida social"    → planes con amigos/familia, eventos, fiestas.
  "citas"          → citas médicas o profesionales.  SUBGRUPO = especialista (dentista, médico…)

━━ PASO 3 — Extrae el MÍNIMO esquemático ━━
  • La idea = sustantivo/concepto esencial, 1-4 palabras, SIN el verbo del usuario.
  • El verbo está implícito en el nombre del grupo.
  • NUNCA copies la frase del usuario.
  ❌ idea="quiero comprar pan"   ✅ idea="pan"
  ❌ idea="ver Terminator 1984"  ✅ idea="Terminator (1984)"
  • idea=null cuando:
    - Es una iniciativa propia ("quiero montar una banda") → grupo="banda de música", idea=null
    - Es solo un comando de crear grupo/subgrupo sin contenido extra.

━━ PASO 4 — LISTA DE ELEMENTOS → ARRAY OBLIGATORIO ━━
Si la nota menciona 2 o más elementos concretos (unidos por "y", ",", "también", "además"…):
→ devuelve un ARRAY con UN objeto por elemento. NUNCA mezcles varios en una sola idea.

❌ {"idea":"Italia, Francia y España"}
✅ [{"idea":"Italia"},{"idea":"Francia"},{"idea":"España"}]

❌ {"idea":"pan y queso"}
✅ [{"idea":"pan"},{"idea":"queso"}]

is_new_group/is_new_subgroup solo true en el PRIMER objeto del array.

━━ PASO 5 — Nombre del grupo (máx 3 palabras) ━━
Usa la categoría/tema. Compara con grupos existentes:
  • Misma temática → reutiliza el grupo (is_new_group=false).
  • Diferente acción sobre mismo objeto → crea grupo nuevo + rename_group si hay colisión de nombre.
  • Sin relación → nuevo grupo (is_new_group=true).

━━ SALIDA — SOLO JSON, sin texto extra ━━
Idea normal: {"action":"add","makes_sense":true,"reason":null,"group":"...","subgroup":null,"idea":"...",
             "is_new_group":true,"is_new_subgroup":false,"inherit_parent_ideas":false,"rename_group":null}
Recordatorio: {"action":"remind","makes_sense":true,"group":"recordatorios","subgroup":null,"idea":"ir al gimnasio","remind_at":"2026-02-28T16:30:00","is_new_group":false,"is_new_subgroup":false,"inherit_parent_ideas":false,"rename_group":null}
Varias ideas: [{ primer objeto },{ "is_new_group":false, "is_new_subgroup":false, ... }, ...]
"""


# ── Few-shot examples ─────────────────────────────────────────────────────────

FEW_SHOT_EXAMPLES = [
    # ── ELIMINAR ──────────────────────────────────────────────────────────────
    {
        "note": "borra comprar leche de la lista",
        "existing": [{"name": "compras", "ideas": ["leche", "zapatos"], "subgroups": []}],
        "result": {"action": "delete", "makes_sense": True, "reason": None,
                   "group": "compras", "subgroup": None, "idea": "leche",
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },
    {
        "note": "ya no voy a nadar",
        "existing": [{"name": "rutina diaria", "ideas": [], "subgroups": [
            {"name": "deporte", "ideas": ["nadar a las 8 martes"]}
        ]}],
        "result": {"action": "delete", "makes_sense": True, "reason": None,
                   "group": "rutina diaria", "subgroup": "deporte", "idea": "nadar a las 8 martes",
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },
    {
        "note": "elimina todo el grupo de compras",
        "existing": [{"name": "compras", "ideas": ["leche", "pan", "zapatos"], "subgroups": []}],
        "result": {"action": "delete", "makes_sense": True, "reason": None,
                   "group": "compras", "subgroup": None, "idea": None,
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },
    {
        "note": "borra el subgrupo deporte",
        "existing": [{"name": "rutina diaria", "ideas": [], "subgroups": [
            {"name": "deporte", "ideas": ["correr", "nadar"]}
        ]}],
        "result": {"action": "delete", "makes_sense": True, "reason": None,
                   "group": "rutina diaria", "subgroup": "deporte", "idea": None,
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },

    # ── RECORDATORIO ──────────────────────────────────────────────────────────
    {
        "note": "avisame para ir a la compra a las 8:30",
        "existing": [],
        "result": {"action": "remind", "makes_sense": True, "reason": None,
                   "group": "recordatorios", "subgroup": None, "idea": "ir a la compra",
                   "remind_at": "2026-03-01T08:30:00",
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },
    {
        "note": "avisame a las 10 de sacar al perro",
        "existing": [],
        "result": {"action": "remind", "makes_sense": True, "reason": None,
                   "group": "recordatorios", "subgroup": None, "idea": "sacar al perro",
                   "remind_at": "2026-02-28T10:00:00",
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },
    {
        "note": "recuerdame que tengo que llamar al médico mañana a las 9",
        "existing": [],
        "result": {"action": "remind", "makes_sense": True, "reason": None,
                   "group": "recordatorios", "subgroup": None, "idea": "llamar al médico",
                   "remind_at": "2026-03-01T09:00:00",
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },
    {
        "note": "avísame el martes a las 4:30 que tengo que ir al gimnasio",
        "existing": [],
        "result": {"action": "remind", "makes_sense": True, "reason": None,
                   "group": "recordatorios", "subgroup": None, "idea": "ir al gimnasio",
                   "remind_at": "2026-03-03T04:30:00",
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },
    {
        "note": "recuérdame mañana a las 9 que tengo reunión",
        "existing": [],
        "result": {"action": "remind", "makes_sense": True, "reason": None,
                   "group": "recordatorios", "subgroup": None, "idea": "reunión",
                   "remind_at": "2026-03-01T09:00:00",
                   "is_new_group": False, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },

    # ── SIN SENTIDO ───────────────────────────────────────────────────────────
    {
        "note": "asdfghjkl",
        "existing": [],
        "result": {"makes_sense": False, "reason": "Texto aleatorio sin idea."}
    },

    # ── RUTINA DIARIA ─────────────────────────────────────────────────────────
    {
        "note": "quiero empezar a nadar a las 8 los martes",
        "existing": [],
        "result": {"action": "add", "makes_sense": True, "reason": None,
                   "group": "rutina diaria", "subgroup": "deporte", "idea": "nadar a las 8 martes",
                   "is_new_group": True, "is_new_subgroup": True,
                   "inherit_parent_ideas": False, "rename_group": None}
    },

    # ── COMPRAS con tienda ────────────────────────────────────────────────────
    {
        "note": "ir a comprar pan al super",
        "existing": [],
        "result": {"action": "add", "makes_sense": True, "reason": None,
                   "group": "compras", "subgroup": "super", "idea": "pan",
                   "is_new_group": True, "is_new_subgroup": True,
                   "inherit_parent_ideas": False, "rename_group": None}
    },

    # ── INICIATIVA PROPIA → idea=null ─────────────────────────────────────────
    {
        "note": "quiero montar una banda de música",
        "existing": [],
        "result": {"action": "add", "makes_sense": True, "reason": None,
                   "group": "banda de música", "subgroup": None, "idea": None,
                   "is_new_group": True, "is_new_subgroup": False,
                   "inherit_parent_ideas": False, "rename_group": None}
    },

    # ── MÚLTIPLES IDEAS → ARRAY OBLIGATORIO ───────────────────────────────────
    {
        "note": "quiero comprar pan y queso",
        "existing": [],
        "result": [
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "compras", "subgroup": None, "idea": "pan",
             "is_new_group": True,  "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "compras", "subgroup": None, "idea": "queso",
             "is_new_group": False, "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
        ]
    },
    {
        "note": "comprar café, azúcar y leche del super",
        "existing": [{"name": "compras", "ideas": [], "subgroups": []}],
        "result": [
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "compras", "subgroup": "super", "idea": "café",
             "is_new_group": False, "is_new_subgroup": True,
             "inherit_parent_ideas": False, "rename_group": None},
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "compras", "subgroup": "super", "idea": "azúcar",
             "is_new_group": False, "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "compras", "subgroup": "super", "idea": "leche",
             "is_new_group": False, "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
        ]
    },
    {
        "note": "quiero viajar a Italia, Francia y España",
        "existing": [],
        "result": [
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "viajes", "subgroup": None, "idea": "Italia",
             "is_new_group": True,  "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "viajes", "subgroup": None, "idea": "Francia",
             "is_new_group": False, "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "viajes", "subgroup": None, "idea": "España",
             "is_new_group": False, "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
        ]
    },
    {
        "note": "quiero ver Alien y Predator",
        "existing": [{"name": "películas", "ideas": ["Terminator (1984)"], "subgroups": []}],
        "result": [
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "películas", "subgroup": None, "idea": "Alien",
             "is_new_group": False, "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
            {"action": "add", "makes_sense": True, "reason": None,
             "group": "películas", "subgroup": None, "idea": "Predator",
             "is_new_group": False, "is_new_subgroup": False,
             "inherit_parent_ideas": False, "rename_group": None},
        ]
    },
]


# Categorías predefinidas — inyectadas en cada prompt para que el LLM las tenga siempre presentes
PREDEFINED_CATEGORIES = [
    "rutina diaria", "compras", "trabajo/clase", "finanzas",
    "viajes", "vida social", "citas",
]

# ── Prompt simplificado para tool calling (MCP) ───────────────────────────────
# Igual que SYSTEM_PROMPT pero la sección SALIDA se reemplaza por instrucciones de herramienta.
SYSTEM_PROMPT_TOOLS = SYSTEM_PROMPT.split("━━ SALIDA —")[0].rstrip() + """

━━ ACCIÓN — Llama SIEMPRE a una de estas herramientas (NUNCA respondas con texto libre):
  • save_ideas  → cuando la nota expresa una o varias ideas/planes/tareas.
                  Un elemento por cada idea distinta. is_new_group/is_new_subgroup solo true en el PRIMERO.
  • delete_idea → cuando el usuario quiere eliminar algo (PASO 0).
  • ignore_note → cuando la nota no tiene sentido (PASO 1 falla).
"""

# ── Definición de herramientas MCP ────────────────────────────────────────────
_MCP_TOOLS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "save_ideas",
            "description": (
                "Guarda una o varias ideas destiladas. Usa un elemento por cada idea distinta. "
                "Para una sola idea, el array tiene 1 elemento."
            ),
            "parameters": {
                "type": "object",
                "required": ["ideas"],
                "properties": {
                    "ideas": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["group", "is_new_group", "is_new_subgroup", "inherit_parent_ideas"],
                            "properties": {
                                "group":               {"type": "string",  "description": "Nombre del grupo (máx 3 palabras). Prioriza categorías obligatorias."},
                                "subgroup":            {"type": "string",  "description": "Subgrupo cuando hay lugar/contexto concreto, o null."},
                                "idea":                {"type": "string",  "description": "Concepto esencial, 1-4 palabras, sin verbos del usuario. null para iniciativas/comandos puros."},
                                "is_new_group":        {"type": "boolean"},
                                "is_new_subgroup":     {"type": "boolean"},
                                "inherit_parent_ideas":{"type": "boolean"},
                                "rename_group": {
                                    "type": "object",
                                    "description": "Solo si hay colisión de nombre. null en caso contrario.",
                                    "properties": {
                                        "old_name": {"type": "string"},
                                        "new_name":  {"type": "string"},
                                    },
                                },
                            },
                        },
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "delete_idea",
            "description": "Elimina una idea existente cuando el usuario expresa intención de borrarla.",
            "parameters": {
                "type": "object",
                "required": ["group", "idea"],
                "properties": {
                    "group":    {"type": "string", "description": "Grupo donde está la idea."},
                    "subgroup": {"type": "string", "description": "Subgrupo, o null."},
                    "idea":     {"type": "string", "description": "Texto exacto de la idea a eliminar."},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "ignore_note",
            "description": "La nota no tiene sentido o no expresa ninguna idea clasificable.",
            "parameters": {
                "type": "object",
                "required": ["reason"],
                "properties": {
                    "reason": {"type": "string", "description": "Breve explicación."},
                },
            },
        },
    },
]


def _build_classification_prompt(note_text: str, existing_groups: list[dict]) -> str:
    """Construye el prompt con few-shot examples para clasificar una nota."""

    examples_str = ""
    for ex in FEW_SHOT_EXAMPLES:
        examples_str += f"""
EJEMPLO:
Nota: "{ex['note']}"
grupos existentes: {json.dumps(ex['existing'], ensure_ascii=False)}
Respuesta: {json.dumps(ex['result'], ensure_ascii=False)}
"""

    existing_str = json.dumps(existing_groups, ensure_ascii=False) if existing_groups else "[]"

    # Categorías predefinidas: siempre visibles en el hint
    predefined_str = ", ".join(f'"{c}"' for c in PREDEFINED_CATEGORIES)
    predefined_hint = (
        f'\nCATEGORÍAS OBLIGATORIAS (siempre existen): {predefined_str}'
        f'\nSi la nota encaja en una categoría obligatoria, DEBES usarla aunque no aparezca en los grupos existentes.'
    )

    # Si hay grupos, listar sus nombres explícitamente para que el LLM compare
    if existing_groups:
        names = [p["name"] for p in existing_groups]
        names_str = ", ".join(f'"{n}"' for n in names)
        relation_hint = (
            f'\ngrupos existentes: {names_str}'
            f'\nPregúntate: ¿la nota habla del mismo tema que alguno de esos grupos o de una categoría obligatoria?'
            f'\nSi NO → is_new_group=true y elige un nombre descriptivo nuevo.'
            f'\nSi SÍ → usa ese grupo con is_new_group=false.'
        )
    else:
        relation_hint = f'\n(No hay grupos existentes — usa una categoría obligatoria si aplica, si no crea un grupo nuevo)'

    return f"""{examples_str}
AHORA CLASIFICA:
Nota: "{note_text}"
Estado: {existing_str}
{predefined_hint}
{relation_hint}
Respuesta (solo JSON):"""


def _find_mentioned_group(note_text: str, existing_groups: list[dict]) -> str | None:
    """
    Si el texto de la nota contiene literalmente el nombre de un grupo existente,
    devuelve ese nombre. Así evitamos que el LLM cree un grupo nuevo cuando el
    usuario menciona explícitamente uno que ya existe.
    """
    note_lower = note_text.lower()
    for proj in existing_groups:
        if proj["name"].lower() in note_lower:
            return proj["name"]
    return None


# Palabras clave que casi siempre indican una categoría predefinida
_CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "rutina diaria":  ["dormir", "despertar", "levantarme", "levantarse", "acostarme",
                       "acostarse", "desayunar", "desayuno", "almorzar", "almuerzo",
                       "comer a las", "merendar", "merienda", "cenar", "cena",
                       "ducharme", "ducharse", "meditar", "rutina", "hábito", "horario de",
                       "hacer deporte", "deporte", "nadar", "natación", "natacion",
                       "correr", "running", "yoga", "ciclismo", "bici ", "bicicleta",
                       "entrenar", "entrenamiento", "pilates", "boxeo", "gimnasio", "gym"],
    "compras":        ["comprar ", "necesito comprar", "tengo que comprar"],
    "trabajo/clase":  ["examen", "entrega", "trabajo de clase", "reunión de trabajo",
                       "presentación del trabajo"],
    "finanzas":       ["pagar el recibo", "pagar la factura", "pagar impuesto",
                       "recibo de", "factura de", "mi sueldo", "mis ahorros"],
    "viajes":         ["viaje a ", "viajar a ", "vuelo a ", "reservar hotel",
                       "billete de avión", "de vacaciones"],
    "vida social":    ["quedar con ", "quedada con ", "cena con ", "comida con ",
                       "fiesta de ", "cumpleaños de "],
    "citas":          ["cita con el ", "cita médica", "cita con mi ", "ir al dentista",
                       "ir al médico", "cita con el dentista", "cita con el médico"],
}

# Actividad de rutina → nombre normalizado para el SUBGRUPO
_RUTINA_ACTIVITY_MAP: dict[str, str] = {
    "dormir":        "dormir",
    "acostarme":     "dormir",
    "acostarse":     "dormir",
    "levantarme":    "levantarse",
    "levantarse":    "levantarse",
    "despertar":     "levantarse",
    "despertarme":   "levantarse",
    "desayunar":     "desayuno",
    "desayuno":      "desayuno",
    "almorzar":      "almuerzo",
    "almuerzo":      "almuerzo",
    "comer":         "comer",
    "merendar":      "merienda",
    "merienda":      "merienda",
    "cenar":         "cena",
    "cena":          "cena",
    "ducharme":      "ducha",
    "ducharse":      "ducha",
    "ducha":         "ducha",
    "meditar":       "meditación",
    "meditación":    "meditación",
    # actividades físicas → todas van bajo "deporte"
    "deporte":       "deporte",
    "hacer deporte": "deporte",
    "nadar":         "deporte",
    "natación":      "deporte",
    "natacion":      "deporte",
    "correr":        "deporte",
    "running":       "deporte",
    "yoga":          "deporte",
    "ciclismo":      "deporte",
    "bicicleta":     "deporte",
    "pilates":       "deporte",
    "boxeo":         "deporte",
    "ejercicio":     "deporte",
    "entrenar":      "deporte",
    "entrenamiento": "deporte",
    "gimnasio":      "deporte",
    "gym":           "deporte",
    "estudiar":      "estudio",
}


def _extract_rutina_subproject(note_text: str) -> str | None:
    """Devuelve el nombre del SUBGRUPO de rutina si se detecta la actividad."""
    note_lower = note_text.lower()
    for keyword, subproj in _RUTINA_ACTIVITY_MAP.items():
        if keyword in note_lower:
            return subproj
    return None


def _guess_predefined_category(note_text: str) -> str | None:
    """Comprueba si la nota contiene palabras clave de una categoría predefinida."""
    note_lower = note_text.lower()
    for category, keywords in _CATEGORY_KEYWORDS.items():
        if any(kw in note_lower for kw in keywords):
            return category
    return None


# Palabras que indican intención de eliminar
_DELETE_KEYWORDS = [
    "elimina ", "eliminar ", "elimina la", "eliminar la",
    "borra ", "borrar ", "borra la", "borrar la",
    "quita ", "quitar ", "quita la", "quitar la",
    "ya no quiero", "descarta ", "descartar ",
    "bórralo", "bórrala", "elimínalo", "elimínala",
    "ya no necesito", "tacha ", "tachar ",
]


def _is_delete_intent(note_text: str) -> bool:
    """Detecta si la nota expresa intención de eliminar algo."""
    note_lower = note_text.lower()
    return any(kw in note_lower for kw in _DELETE_KEYWORDS)


def _build_single_result(data: dict, note_text: str, existing_groups: list[dict]) -> ClassificationResult:
    """Convierte un dict de resultado LLM a ClassificationResult aplicando safety-nets."""
    if not data.get("makes_sense", True):
        return ClassificationResult(
            makes_sense=False,
            reason=data.get("reason", "La nota no expresa una idea clasificable."),
        )

    action = data.get("action", "add").lower()
    if action != "delete" and _is_delete_intent(note_text):
        action = "delete"

    if action == "delete":
        return ClassificationResult(
            action="delete",
            makes_sense=True,
            group=data.get("group") or None,
            subgroup=data.get("subgroup") or None,
            idea=data.get("idea") or None,
            is_new_group=False,
            is_new_subgroup=False,
            inherit_parent_ideas=False,
            rename_group=None,
        )

    group        = data.get("group", "")
    is_new_group = data.get("is_new_group", False)

    if is_new_group and existing_groups:
        mentioned = _find_mentioned_group(note_text, existing_groups)
        if mentioned:
            group        = mentioned
            is_new_group = False

    if group.lower() not in PREDEFINED_CATEGORIES:
        guessed = _guess_predefined_category(note_text)
        if guessed:
            existing_match = next(
                (p for p in existing_groups if p["name"].lower() == guessed), None
            )
            group        = guessed
            is_new_group = existing_match is None
            if guessed == "rutina diaria" and not data.get("subgroup"):
                extracted_sub = _extract_rutina_subproject(note_text)
                if extracted_sub:
                    data["subgroup"]        = extracted_sub
                    data["is_new_subgroup"] = True

    rename = data.get("rename_group") or None
    if rename and not is_new_group:
        rename = None

    return ClassificationResult(
        action="add",
        makes_sense=True,
        group=group,
        subgroup=data.get("subgroup") or None,
        idea=data.get("idea"),
        is_new_group=is_new_group,
        is_new_subgroup=data.get("is_new_subgroup", False),
        inherit_parent_ideas=data.get("inherit_parent_ideas", False),
        rename_group=rename,
    )


def _results_from_tool_calls(
) -> list[ClassificationResult] | None:
    """
    Convierte una lista de tool calls de Ollama en ClassificationResults.
    Devuelve None si no se pudo interpretar ninguna llamada.
    """
    results: list[ClassificationResult] = []
    for tc in tool_calls:
        name = tc.get("name", "")
        args = tc.get("arguments", {})

        if name == "ignore_note":
            results.append(ClassificationResult(
                makes_sense=False,
                reason=args.get("reason", "Nota no clasificable."),
            ))

        elif name == "delete_idea":
            results.append(ClassificationResult(
                action="delete",
                makes_sense=True,
                group=args.get("group") or None,
                subgroup=args.get("subgroup") or None,
                idea=args.get("idea") or None,
                is_new_group=False,
                is_new_subgroup=False,
                inherit_parent_ideas=False,
                rename_group=None,
            ))

        elif name == "save_ideas":
            ideas_list = args.get("ideas", [])
            if not isinstance(ideas_list, list):
                ideas_list = [ideas_list]  # por si el LLM manda un objeto suelto
            for i, item in enumerate(ideas_list):
                if not isinstance(item, dict):
                    continue
                r = _build_single_result(
                    {**item, "action": "add", "makes_sense": True},
                    note_text, existing_groups,
                )
                if i > 0:
                    r.is_new_group        = False
                    r.is_new_subgroup     = False
                    r.inherit_parent_ideas = False
                    r.rename_group        = None
                results.append(r)

    return results if results else None


# ── Enumeración post-procesada determinista ───────────────────────────────────

_Y_NORM = re.compile(r'\s+y\s+', re.IGNORECASE | re.UNICODE)


def _split_list_text(text: str, max_words: int = 4) -> list | None:
    """Divide 'A, B, C y D' en ['A','B','C','D'] si cada parte tiene ≤ max_words palabras."""
    normalized = _Y_NORM.sub(', ', text.strip().rstrip('.!?'))
    parts = [p.strip() for p in normalized.split(',') if p.strip()]
    if len(parts) < 2:
        return None
    if all(1 <= len(p.split()) <= max_words for p in parts):
        return parts
    return None


def _find_list_in_note(note_text: str) -> list | None:
    """
    Detecta una enumeración al final de la nota.
    Requiere ≥ 3 ítems para evitar falsos positivos ("ir al super y comprar pan").
    """
    normalized = _Y_NORM.sub(', ', note_text.strip().rstrip('.!?'))
    parts = [p.strip() for p in normalized.split(',') if p.strip()]
    if len(parts) < 3:
        return None
    items: list[str] = []
    for part in reversed(parts):
        words = part.split()
        if 1 <= len(words) <= 3:
            items.insert(0, part)
        elif items:
            # Último trozo largo: tomamos su última palabra como ítem inicial
            if words:
                items.insert(0, words[-1])
            break
        else:
            break
    return items if len(items) >= 3 else None


def _expand_result(r: ClassificationResult, items: list) -> list[ClassificationResult]:
    """Expande un único ClassificationResult en N, uno por ítem."""
    expanded = []
    for i, item in enumerate(items):
        cr = ClassificationResult(
            action=r.action,
            makes_sense=r.makes_sense,
            reason=None,
            group=r.group,
            subgroup=r.subgroup,
            idea=item,
            is_new_group=r.is_new_group if i == 0 else False,
            is_new_subgroup=r.is_new_subgroup if i == 0 else False,
            inherit_parent_ideas=False,
            rename_group=r.rename_group if i == 0 else None,
        )
        expanded.append(cr)
    return expanded


def _maybe_expand_enumeration(results: list[ClassificationResult], note_text: str) -> list[ClassificationResult]:
    """
    Post-procesador: si el LLM devolvió UN solo resultado con una idea que es
    en realidad una lista ('Italia, Francia y España'), la divide en N resultados.
    También activa si la nota original contiene ≥ 3 ítems cortos y el LLM sólo
    capturó el primero.
    """
    if len(results) != 1:
        return results
    r = results[0]
    if not r.makes_sense or r.action == 'delete':
        return results

    # Caso A: el campo idea ya contiene la lista
    if r.idea:
        items = _split_list_text(r.idea, max_words=4)
        if items and len(items) >= 2:
            return _expand_result(r, items)

    # Caso B: la nota tiene lista pero el LLM sólo puso la primera idea
    note_items = _find_list_in_note(note_text)
    if note_items and len(note_items) >= 3:
        return _expand_result(r, note_items)

    return results


# ── Clasificación principal ───────────────────────────────────────────────────

def classify_note(note_text: str, existing_groups: list[dict], lang: str = "es") -> list[ClassificationResult]:
    """
    Clasifica una nota usando el LLM.
    Intenta primero tool calling (MCP); si falla, cae al método JSON clásico.
    Devuelve una LISTA de ClassificationResult (normalmente 1 elemento,
    varios cuando la nota contiene múltiples ideas distintas).
    """
    # ── Pre-check: detect reminder keywords before calling the LLM ──────────
    precheck = _try_remind_precheck(note_text)
    if precheck is not None:
        return precheck

    existing_str = json.dumps(existing_groups, ensure_ascii=False) if existing_groups else "[]"
    now_str  = datetime.now().strftime("%A %Y-%m-%d %H:%M")
    user_msg = f'Ahora: {now_str}\nNota: "{note_text}"\nGrupos existentes: {existing_str}'
    if lang and lang.lower() == "en":
        user_msg += "\n[IMPORTANT: Output ALL group names, subgroup names, and idea text in ENGLISH only.]"

    # ── 1. Intentar tool calling (MCP) ────────────────────────────────────────
    try:
        tool_calls = _call_ollama_with_tools(
            messages=[{"role": "user", "content": user_msg}],
            tools=_MCP_TOOLS,
            system=SYSTEM_PROMPT_TOOLS,
        )
        if tool_calls:
            results = _results_from_tool_calls(tool_calls, note_text, existing_groups)
            if results:
                return _maybe_expand_enumeration(results, note_text)
    except Exception:
        pass  # fallback al método JSON

    # ── 2. Fallback: prompt JSON clásico ──────────────────────────────────────
    prompt = _build_classification_prompt(note_text, existing_groups)
    raw_response = _call_ollama(prompt=prompt, system=SYSTEM_PROMPT, temperature=0.1)
    data = extract_json(raw_response)

    if isinstance(data, list):
        results = [_build_single_result(item, note_text, existing_groups) for item in data if isinstance(item, dict)]
        for r in results[1:]:
            r.is_new_group        = False
            r.is_new_subgroup     = False
            r.inherit_parent_ideas = False
            r.rename_group        = None
        final = results or [ClassificationResult(makes_sense=False, reason="Respuesta vacía del LLM.")]
        return _maybe_expand_enumeration(final, note_text)

    return _maybe_expand_enumeration([_build_single_result(data, note_text, existing_groups)], note_text)
