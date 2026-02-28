"""
Lógica de clasificación de notas usando el LLM.
Dado el texto libre de una nota, el LLM decide en qué grupo y sección va.
"""

import json
from models import ClassificationResult
from llm_client import _call_ollama, extract_json

# ── Prompt del sistema ────────────────────────────────────────────────────────

SYSTEM_PROMPT = """Eres un asistente de organización de ideas. Tu trabajo es destilar la nota al mínimo esquemático posible.
Dado una nota y una lista de grupos existentes, debes:

PASO 0 — ¿El usuario quiere ELIMINAR algo?
  Señales de intención de eliminar: "elimina", "eliminar", "borra", "borrar",
  "quita", "quitar", "quítame", "quítalo", "ya no quiero", "ya no voy a",
  "no quiero ya", "cancela", "cancelar", "descarta", "descartar",
  "me arrepentí", "no me interesa", "me olvidé de", "olvida",
  "no lo voy a hacer", "no voy a hacer", "descarto la idea de",
  "no hace falta", "ya está hecho", "ya lo hice", "táchalo", "tachalo"...
  → action="delete".
  Identifica en los grupos existentes qué idea (y en qué grupo/SUBGRUPO) hay que borrar.
  El usuario puede mencionar la idea de forma aproximada; busca la más parecida.
  TAMBIÉN detecta intención implícita: "ya no voy a nadar" → delete idea de nadar.
  Si no puedes identificar qué borrar → makes_sense=false, reason="No encontré qué eliminar".
  Devuelve solo: {"action": "delete", "makes_sense": true, "group": "...", "subgroup": null o "...", "idea": "idea exacta a borrar"}
  (Los campos is_new_group, is_new_subgroup, inherit_parent_ideas, rename_group no aplican y van a false/null.)

  Si NO hay intención de eliminar, sigue al PASO 1.

PASO 1 — ¿La nota tiene sentido como idea o tarea?
No tiene sentido si es: texto aleatorio, teclas pulsadas por error ("asdfgh"),
frases sin significado ("el el el"), preguntas dirigidas a la IA ("hola, ¿cómo estás?"),
o cualquier texto que no exprese una idea, plan, tarea o intención del usuario.
Si NO tiene sentido → devuelve: {"makes_sense": false, "reason": "explicación breve"}

PASO 2 — OBLIGATORIO: comprueba primero las CATEGORÍAS PRINCIPALES del sistema.
  ESTAS CATEGORÍAS SIEMPRE EXISTEN en el sistema aunque no aparezcan en la lista
  de grupos existentes. Si la nota encaja, DEBES usarlas. No las ignores.

  CATEGORÍAS OBLIGATORIAS:
  • "rutina diaria"  — hábitos, horarios, recordatorios cotidianos, cosas del día a día.
                       SUBGRUPO = el ámbito concreto (dormir, desayuno, deporte…)
                       ✔ "dormir a las 3" → SUBGRUPO="dormir", idea="a las 3"
                       ✔ "desayunar antes de las 9" → SUBGRUPO="desayuno", idea="antes de las 9"
                       ✔ "levantarme a las 7" → SUBGRUPO="levantarse", idea="a las 7"
                       ✔ "empezar a nadar a las 8 los martes" → SUBGRUPO="deporte", idea="nadar a las 8 martes"
                       ✔ "quiero correr 3 veces por semana" → SUBGRUPO="deporte", idea="correr 3 veces/semana"
                       REGLA DE DEPORTE: nadar, correr, gym, yoga, ciclismo, pilates, boxeo
                       y cualquier actividad física van bajo SUBGRUPO="deporte".
                       La actividad concreta (con horario si lo hay) es la IDEA.
  • "compras"        — cualquier cosa que necesites comprar, en cualquier tienda/lugar.
  • "trabajo/clase"  — tareas laborales o de estudio, reuniones, entregas, exámenes.
  • "finanzas"       — gastos, ahorros, facturas, inversiones, ingresos, pagos.
  • "viajes"         — viajes, escapadas, reservas, vuelos, hoteles, excursiones.
  • "vida social"    — planes con amigos o familia, eventos, celebraciones, quedadas.
  • "citas"          — citas médicas o con profesionales (dentista, gestor, abogado…).
                       ✔ "cita con el dentista" → grupo="citas", SUBGRUPO="dentista"

  REGLA: Si la nota encaja en una categoría principal, ÚSA ESA CATEGORÍA.
         Solo si claramente no encaja en ninguna, pasa al PASO 3.

PASO 3 — Principio general: SER MÁXIMO ESQUEMÁTICO
  Elimina todo lo que se puede inferir del contexto:
  - El VERBO de la acción ya lo implica el Nombre del grupo o SUBGRUPO.
    ✔ grupo="compras", SUBGRUPO="super", idea="pan"  (no "comprar pan en el super")
    ✔ grupo="películas", idea="Terminator (1984)"        (no "ver Terminator de 1984")
    ✔ grupo="gimnasio", idea="bíceps en día de espalda" (no "quiero hacer bíceps")
  - La idea mínima es el SUSTANTIVO o concepto clave, sin verbo cuando el verbo
    ya está implícito en el grupo.

  ⛔ REGLA ABSOLUTA — PROHIBIDO copiar frases:
  La idea NUNCA puede ser una copia, paráfrasis ni reproducción de lo que dijo el
  usuario. Si tu idea resultante contiene el verbo original del usuario o más de
  4 palabras tomadas del input, estás siendo demasiado literal. REESCRIBE.
  La idea es SÓLO el objeto/concepto esencial, entre 1 y 4 palabras.

  ❌ INCORRECTO (literal):                ✅ CORRECTO (destilado):
  • "me gustaría ver película de terror"  → "película de terror"
  • "quiero comprar zapatos en el centro" → idea="zapatos", subgrupo="tienda centro"
  • "tengo que llamar a mi médico"        → grupo="citas", idea="médico"
  • "me gustaría crear una página web"    → grupo="página web", idea=null
  • "quiero hacer bíceps en el gimnasio" → grupo="gimnasio", idea="bíceps"
  • "tengo que pagar el recibo de la luz" → grupo="finanzas", idea="recibo luz"
  • "quiero aprender a tocar la guitarra" → grupo="guitarra", idea=null
  • "me apetece ir a la playa este verano"→ grupo="viajes", idea="playa verano"

PASO 4 — Cuándo usar SUBGRUPO (contexto o lugar):
  Usa SUBGRUPO cuando la acción del grupo se repite en distintos
  LUGARES, TIENDAS, PLATAFORMAS o CONTEXTOS, y la nota especifica uno concreto:
    ✔ "comprar pan en el super" → grupo="compras", SUBGRUPO="super", idea="pan"
    ✔ "ver Interstellar en Netflix" → grupo="películas", SUBGRUPO="netflix", idea="Interstellar"
    ✔ "reservar hotel en Booking" → grupo="viajes", SUBGRUPO="booking", idea="reservar hotel"
  Si la nota NO especifica lugar/contexto concreto, no uses SUBGRUPO:
    ✔ "comprar leche" → grupo="compras", SUBGRUPO=null, idea="leche"
    ✔ "ver Alien" → grupo="películas", SUBGRUPO=null, idea="Alien"

PASO 5 — Cuándo usar idea=null:
  a) Cuando la nota describe una INICIATIVA PROPIA (crear, abrir, montar, fabricar
  algo tuyo). El Nombre del grupo ya lo recuerda todo.
    ✔ "abrir una tienda de peluches" → grupo="tienda de peluches", idea=null
    ✔ "quiero montar una banda de música" → grupo="banda de música", idea=null
  b) Cuando la nota es EXCLUSIVAMENTE un comando de creación de grupo o subgrupo sin
  contenido propio, es decir, el usuario pide CREAR/AÑADIR/AGREGAR el grupo/subgrupo
  pero no aporta ninguna idea adicional.
    ✔ "añade el grupo comida" → grupo="comida", idea=null
    ✔ "crea el grupo trabajo" → grupo="trabajo", idea=null
    ✔ "agrega una categoría de películas" → grupo="películas", idea=null
    ✔ "quiero un grupo llamado viajes" → grupo="viajes", idea=null
    ✔ "añade el subgrupo desayuno en rutina diaria" → grupo="rutina diaria", subgrupo="desayuno", idea=null
    ✔ "crea el subgrupo cena" → subgrupo="cena", idea=null
    ✔ "agrega un subgrupo llamado deporte" → subgrupo="deporte", idea=null
    ✔ "abre el subgrupo desayuno en rutina diaria" → grupo="rutina diaria", subgrupo="desayuno", idea=null
  d) NOTA CON MÚLTIPLES IDEAS DISTINTAS — cuando la nota lista varias cosas
  concretas para el mismo grupo/subgrupo (unidas por 'y', ',', 'también',
  'además'...), devuelve un ARRAY JSON con un objeto por cada idea.
  Cada objeto tiene exactamente la misma estructura que el caso de una sola idea;
  solo cambia el campo "idea". Los flags is_new_group / is_new_subgroup solo
  deben ser true en el PRIMER objeto (los siguientes usan el grupo ya creado).
    ✔ "comprar leche y huevos" (grupo=compras):
       → [{"idea":"leche", "is_new_group":true,...}, {"idea":"huevos", "is_new_group":false,...}]
    ✔ "quiero ver Alien y Terminator":
       → [{"idea":"Alien", "is_new_group":true,...}, {"idea":"Terminator (1984)", "is_new_group":false,...}]
    Si todas las ideas son iguales o la nota solo tiene una idea → devuelve objeto único (no array).

  c) NOTA COMPUESTA — creación de grupo/subgrupo COMBINADA con contenido real:
  Si el usuario en la misma frase pide crear un grupo o subgrupo Y además expresa
  un deseo, plan o idea concreta, DEBES capturar esa idea mínima.
  El truco: ignora completamente la parte "crea/añade el grupo/subgrupo X" y
  analiza SOLO la parte de contenido real que queda.
    ✔ "crea un grupo viajes con subgrupo cancún, quiero viajar con mis amigos"
       → grupo="viajes", subgrupo="cancún", idea="viaje con amigos"
    ✔ "añade una categoría de películas de ciencia ficción para ver Dune"
       → grupo="películas", subgrupo="ciencia ficción", idea="Dune"
    ✔ "crea grupo trabajo con subgrupo reuniones, tengo reunión el lunes"
       → grupo="trabajo", subgrupo="reuniones", idea="reunión lunes"
  La idea resultante NO debe incluir verbos de creación ni nombres de grupo/subgrupo.

  REGLA CLAVE: si el único contenido semántico de la nota es el NOMBRE del grupo/subgrupo,
  entonces idea=null. No pongas el propio nombre del grupo ni del subgrupo como idea,
  ni pongas el texto del comando ("añade el subgrupo X") como idea.
  En categorías generales (compras, películas, gimnasio...) SIEMPRE hay idea cuando
  hay contenido real más allá del nombre del grupo.

PASO 6 — Nombre del grupo (máximo 3 palabras):
  - Usa la CATEGORÍA o TEMA.
    ✅ "películas", "gimnasio", "compras", "pagina web", "viajes"
    ❌ "películas que ver", "lista de la compra"
  - Si ya existe uno con mismo OBJETO pero distinta acción, añade el verbo:
    "filmar película" frente al existente "ver películas".
  - Si no hay relación con ningún grupo existente, SIEMPRE crea uno nuevo.

PASO 7 — ¿Hay que renombrar un grupo existente?
  Solo cuando creas un grupo nuevo que colisiona con el nombre de uno existente:
  - Existe "tienda de peluches" (iniciativa): creas SUBGRUPO del mismo nombre
    bajo "compras" → renombra el antiguo a "abrir tienda de peluches".
  - Existe "películas": creas "filmar película" → renombra "películas" a "ver películas".
  - Sin colisión: rename_group=null.

PASO 8 — Devuelve SOLO JSON (sin texto antes ni después).
  - Si hay UNA idea → un objeto:
    {"action":"add", "makes_sense":true, "reason":null,
     "group":"...", "subgroup":null, "idea":"...",
     "is_new_group":true/false, "is_new_subgroup":true/false,
     "inherit_parent_ideas":false,
     "rename_group":null}
  - Si hay MÚLTIPLES ideas distintas (PASO 5d) → un ARRAY de objetos:
    [{...primer objeto con is_new_group/is_new_subgroup correctos...},
     {...resto con is_new_group:false, is_new_subgroup:false...}]
  Campos:
  "idea": null o "sustantivo/concepto esencial, MÁXIMO 4 palabras, NUNCA copia del input"
"""


# ── Few-shot examples ─────────────────────────────────────────────────────────

FEW_SHOT_EXAMPLES = [
    # ── ELIMINAR idea (PASO 0) ────────────────────────────────────────────────
    {
        "note": "elimina la idea de nadar",
        "existing": [
            {"name": "rutina diaria", "ideas": [], "subgroups": [
                {"name": "deporte", "ideas": ["nadar a las 8 martes", "correr 3 veces/semana"]}
            ]}
        ],
        "result": {
            "action": "delete",
            "makes_sense": True,
            "reason": None,
            "group": "rutina diaria",
            "subgroup": "deporte",
            "idea": "nadar a las 8 martes",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "borra comprar leche de la lista",
        "existing": [
            {"name": "compras", "ideas": ["leche", "zapatos"], "subgroups": []}
        ],
        "result": {
            "action": "delete",
            "makes_sense": True,
            "reason": None,
            "group": "compras",
            "subgroup": None,
            "idea": "leche",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "ya no quiero ver Alien",
        "existing": [
            {"name": "películas", "ideas": ["Terminator (1984)", "Alien"], "subgroups": []}
        ],
        "result": {
            "action": "delete",
            "makes_sense": True,
            "reason": None,
            "group": "películas",
            "subgroup": None,
            "idea": "Alien",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "ya no voy a nadar",
        "existing": [
            {"name": "rutina diaria", "ideas": [], "subgroups": [
                {"name": "deporte", "ideas": ["nadar a las 8 martes", "correr 3 veces/semana"]}
            ]}
        ],
        "result": {
            "action": "delete",
            "makes_sense": True,
            "reason": None,
            "group": "rutina diaria",
            "subgroup": "deporte",
            "idea": "nadar a las 8 martes",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "quita la leche de compras",
        "existing": [
            {"name": "compras", "ideas": ["leche", "pan"], "subgroups": []}
        ],
        "result": {
            "action": "delete",
            "makes_sense": True,
            "reason": None,
            "group": "compras",
            "subgroup": None,
            "idea": "leche",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "cancela lo de Terminator",
        "existing": [
            {"name": "películas", "ideas": ["Terminator (1984)", "Alien"], "subgroups": []}
        ],
        "result": {
            "action": "delete",
            "makes_sense": True,
            "reason": None,
            "group": "películas",
            "subgroup": None,
            "idea": "Terminator (1984)",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "no me interesa correr",
        "existing": [
            {"name": "rutina diaria", "ideas": [], "subgroups": [
                {"name": "deporte", "ideas": ["correr 3 veces/semana", "nadar"]}
            ]}
        ],
        "result": {
            "action": "delete",
            "makes_sense": True,
            "reason": None,
            "group": "rutina diaria",
            "subgroup": "deporte",
            "idea": "correr 3 veces/semana",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },

    # ── Nota sin sentido ──────────────────────────────────────────────────────
    {
        "note": "asdfghjkl",
        "existing": [],
        "result": {
            "makes_sense": False,
            "reason": "El texto parece ser teclas aleatorias, no expresa ninguna idea.",
            "rename_group": None,
        }
    },
    {
        "note": "hola como estas",
        "existing": [],
        "result": {
            "makes_sense": False,
            "reason": "Es un saludo, no una idea o tarea para organizar.",
            "rename_group": None,
        }
    },

    # ── Categorías principales: rutina diaria, citas ─────────────────────────
    {
        "note": "quiero dormir a las 3 de la madrugada",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "rutina diaria",
            "subgroup": "dormir",
            "idea": "a las 3",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },    {
        "note": "quiero hacer deporte, voy a empezar a nadar a las 8 los martes",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "rutina diaria",
            "subgroup": "deporte",
            "idea": "nadar a las 8 martes",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "quiero salir a correr tres veces por semana",
        "existing": [
            {"name": "rutina diaria", "ideas": [], "subgroups": [{"name": "deporte", "ideas": ["nadar a las 8 martes"]}]}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "rutina diaria",
            "subgroup": "deporte",
            "idea": "correr 3 veces/semana",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },    {
        "note": "tengo cita con el dentista el martes",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "citas",
            "subgroup": "dentista",
            "idea": "martes",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "tengo que pagar el recibo de la luz",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "finanzas",
            "subgroup": None,
            "idea": "recibo luz",
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "quedar con Ana el viernes para cenar",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "vida social",
            "subgroup": None,
            "idea": "cena con Ana viernes",
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },

    # ── Compras: SUBGRUPO = lugar/tienda cuando se especifica ─────────────
    {
        "note": "ir a comprar pan al super",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "compras",
            "subgroup": "super",
            "idea": "pan",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "comprar leche",
        "existing": [
            {"name": "compras", "ideas": [], "subgroups": [{"name": "super", "ideas": ["pan"]}]}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "compras",
            "subgroup": None,
            "idea": "leche",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "necesito comprar zapatos en Zara",
        "existing": [
            {"name": "compras", "ideas": ["leche"], "subgroups": [{"name": "super", "ideas": ["pan"]}]}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "compras",
            "subgroup": "zara",
            "idea": "zapatos",
            "is_new_group": False,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },

    # ── INICIATIVA PROPIA → idea=null (el Nombre del grupo ya lo recuerda) ─
    {
        "note": "abrir una tienda de vender peluches",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "tienda de peluches",
            "subgroup": None,
            "idea": None,
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "quiero montar una banda de música",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "banda de música",
            "subgroup": None,
            "idea": None,
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    # ── CREACIÓN DE SUBGRUPO explícita → idea=null ────────────────────────────
    {
        "note": "crea un subgrupo de casa en compras",
        "existing": [
            {"name": "compras", "ideas": ["leche"], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "compras",
            "subgroup": "casa",
            "idea": None,
            "is_new_group": False,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "añade el subgrupo desayuno a rutina diaria",
        "existing": [
            {"name": "rutina diaria", "ideas": [], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "rutina diaria",
            "subgroup": "desayuno",
            "idea": None,
            "is_new_group": False,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    # ── NOTA COMPUESTA: creación + idea real ───────────────────────────────────
    {
        "note": "crea un grupo de viajes con un subgrupo de cancun, que quiero viajar con mis amigos",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "viajes",
            "subgroup": "cancún",
            "idea": "viaje con amigos",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "añade una categoría de películas de ciencia ficción para ver Dune",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "películas",
            "subgroup": "ciencia ficción",
            "idea": "Dune",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "crea grupo trabajo con subgrupo reuniones, tengo una reunión el lunes",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "trabajo",
            "subgroup": "reuniones",
            "idea": "reunión lunes",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    # ── Existe "tienda de peluches" (iniciativa): compra en tienda del centro ──
    {
        "note": "quiero ir a ver si tienen el peluche de pinguino a esa tienda del centro",
        "existing": [
            {"name": "tienda de peluches", "ideas": [], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "compras",
            "subgroup": "tienda del centro",
            "idea": "peluche pingüino",
            "is_new_group": True,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },

    # ── CATEGORÍA GENERAL → idea necesaria ───────────────────────────────────
    {
        "note": "me gustaría ver la película de terminator de 1984",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "películas",
            "subgroup": None,
            "idea": "Terminator (1984)",
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "ver Interstellar en Netflix",
        "existing": [
            {"name": "películas", "ideas": ["Terminator (1984)"], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "películas",
            "subgroup": "netflix",
            "idea": "Interstellar",
            "is_new_group": False,
            "is_new_subgroup": True,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "quiero ver Alien",
        "existing": [
            {"name": "películas", "ideas": ["Terminator (1984)"], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "películas",
            "subgroup": None,
            "idea": "Alien",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    # ── Distinta acción en misma categoría → nuevo grupo + renombrar viejo ─
    {
        "note": "me gustaría crear mi propia película",
        "existing": [
            {"name": "películas", "ideas": ["Terminator (1984)", "Alien"], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "filmar película",
            "subgroup": None,
            "idea": None,
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": {"old_name": "películas", "new_name": "ver películas"},
        }
    },

    # ── grupo web ──────────────────────────────────────────────────────────
    {
        "note": "me gustaría crear una página web",
        "existing": [],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "pagina web",
            "subgroup": None,
            "idea": None,
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "me gustaría que la pagina web fuera con un fondo azul",
        "existing": [
            {"name": "pagina web", "ideas": [], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "pagina web",
            "subgroup": None,
            "idea": "fondo azul",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "una de las páginas web que quiero crear sería sobre gatos",
        "existing": [
            {"name": "pagina web", "ideas": ["fondo azul"], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "pagina web",
            "subgroup": "pagina sobre gatos",
            "idea": None,
            "is_new_group": False,
            "is_new_subgroup": True,
            "inherit_parent_ideas": True,
            "rename_group": None,
        }
    },

    # ── Gimnasio (categoría → idea necesaria) ────────────────────────────────
    {
        "note": "quiero hacer biceps el dia de espalda en el gym",
        "existing": [
            {"name": "gimnasio", "ideas": [], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "gimnasio",
            "subgroup": None,
            "idea": "bíceps en día de espalda",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },

    # ── Sin relación con existentes → grupo nuevo, sin rename ─────────────
    {
        "note": "me gustaría ir a nadar",
        "existing": [
            {"name": "hackudc", "ideas": [], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "natacion",
            "subgroup": None,
            "idea": "nadar",
            "is_new_group": True,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },
    {
        "note": "para el hackudc quiero usar una base de datos",
        "existing": [
            {"name": "hackudc", "ideas": [], "subgroups": []},
            {"name": "natacion", "ideas": ["nadar"], "subgroups": []}
        ],
        "result": {
            "makes_sense": True,
            "reason": None,
            "group": "hackudc",
            "subgroup": None,
            "idea": "base de datos",
            "is_new_group": False,
            "is_new_subgroup": False,
            "inherit_parent_ideas": False,
            "rename_group": None,
        }
    },

    # ── Añade aquí más ejemplos de tu dominio ────────────────────────────────
]


# Categorías predefinidas — inyectadas en cada prompt para que el LLM las tenga siempre presentes
PREDEFINED_CATEGORIES = [
    "rutina diaria", "compras", "trabajo/clase", "finanzas",
    "viajes", "vida social", "citas",
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


def classify_note(note_text: str, existing_groups: list[dict]) -> list[ClassificationResult]:
    """
    Clasifica una nota usando el LLM.
    Devuelve una LISTA de ClassificationResult (normalmente 1 elemento,
    varios cuando la nota contiene múltiples ideas distintas).
    """
    prompt = _build_classification_prompt(note_text, existing_groups)
    raw_response = _call_ollama(prompt=prompt, system=SYSTEM_PROMPT, temperature=0.1)
    data = extract_json(raw_response)

    # ── El LLM devolvió un array (múltiples ideas) ─────────────────────────────
    if isinstance(data, list):
        results = [_build_single_result(item, note_text, existing_groups) for item in data if isinstance(item, dict)]
        # Asegurar que solo el primer resultado puede tener is_new_group/is_new_subgroup=True
        for r in results[1:]:
            r.is_new_group    = False
            r.is_new_subgroup = False
            r.inherit_parent_ideas = False
            r.rename_group    = None
        return results or [ClassificationResult(makes_sense=False, reason="Respuesta vacía del LLM.")]

    # ── Respuesta de objeto único ──────────────────────────────────────────────
    return [_build_single_result(data, note_text, existing_groups)]
