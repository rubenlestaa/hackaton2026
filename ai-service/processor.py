"""
Lógica del botón PROCESAR.
Dado el árbol completo de grupos con sus notas, genera:
  - Un resumen de cada grupo
  - Puntos clave accionables
  - Un resumen global
"""

import json
from models import ProcessResult, ProjectSummary, KeyPoint
from llm_client import _call_ollama, extract_json

# ── Prompt del sistema ────────────────────────────────────────────────────────

SYSTEM_PROMPT_PROCESS = """Eres un asistente experto en organización y productividad.
Tu tarea es analizar un conjunto de notas organizadas por grupo y sección,
y generar un resumen estructurado con puntos clave accionables.

REGLAS:
- Responde SIEMPRE con un JSON válido, sin texto adicional.
- El resumen de cada grupo debe ser un párrafo claro y motivador (2-4 frases).
- Los puntos clave deben ser concretos, accionables y en infinitivo ("Hacer X", "Comprar Y").
- La categoría de cada punto puede ser: "acción", "meta", "recordatorio" o "recurso".
- "suggested_title" es un título corto y descriptivo para el grupo (máximo 4 palabras).
- El "global_summary" resume todos los grupos en 2-3 frases.

FORMATO DE RESPUESTA:
{
  "groups": [
    {
      "group_name": "nombre del grupo",
      "suggested_title": "título sugerido",
      "summary": "resumen del grupo...",
      "key_points": [
        {"text": "Hacer bíceps en el día de espalda", "category": "acción"},
        {"text": "Mantener consistencia en el entrenamiento", "category": "meta"}
      ]
    }
  ],
  "global_summary": "resumen global de todos los grupos..."
}"""


def _build_process_prompt(groups: list[dict]) -> str:
    """Construye el prompt para el procesado completo."""

    projects_str = json.dumps(groups, ensure_ascii=False, indent=2)

    return f"""Analiza los siguientes grupos y sus notas, y genera el resumen estructurado:

GRUPOS:
{projects_str}

Respuesta (solo JSON):"""


def _build_single_project_prompt(group: dict) -> str:
    """Construye el prompt para procesar un solo grupo."""

    project_str = json.dumps(group, ensure_ascii=False, indent=2)

    return f"""Analiza el siguiente grupo y sus notas:

GRUPO:
{project_str}

Genera un resumen y puntos clave. Responde con este JSON exacto:
{{
  "group_name": "{group.get('name', '')}",
  "suggested_title": "título sugerido (máximo 4 palabras)",
  "summary": "resumen del grupo en 2-4 frases",
  "key_points": [
    {{"text": "punto clave accionable", "category": "acción|meta|recordatorio|recurso"}}
  ]
}}

Solo JSON:"""


def process_projects(groups: list[dict]) -> ProcessResult:
    """
    Procesa todos los grupos con sus notas.
    Si hay muchos grupos, los procesa uno por uno para evitar contextos muy largos.
    Devuelve ProcessResult con resúmenes y puntos clave.
    """

    # Si hay pocos grupos, procesar todos juntos
    if len(groups) <= 3:
        return _process_all_together(groups)
    else:
        # Muchos GRUPOS: procesar uno a uno y luego generar resumen global
        return _process_one_by_one(groups)


def _process_all_together(groups: list[dict]) -> ProcessResult:
    """Procesa todos los grupos en una sola llamada."""
    prompt = _build_process_prompt(groups)
    raw = _call_ollama(prompt=prompt, system=SYSTEM_PROMPT_PROCESS, temperature=0.3)
    data = extract_json(raw)

    group_summaries = [
        ProjectSummary(
            project_name=p["project_name"],
            suggested_title=p.get("suggested_title", p["project_name"]),
            summary=p["summary"],
            key_points=[
                KeyPoint(text=kp["text"], category=kp.get("category", "acción"))
                for kp in p.get("key_points", [])
            ],
        )
        for p in data.get("groups", [])
    ]

    return ProcessResult(groups=group_summaries,
        global_summary=data.get("global_summary", ""),
    )


def _process_one_by_one(groups: list[dict]) -> ProcessResult:
    """Procesa cada grupo por separado y luego genera un resumen global."""
    group_summaries: list[ProjectSummary] = []

    for group in groups:
        prompt = _build_single_project_prompt(group)
        raw = _call_ollama(prompt=prompt, system=SYSTEM_PROMPT_PROCESS, temperature=0.3)
        data = extract_json(raw)

        group_summaries.append(
            ProjectSummary(
                project_name=data.get("project_name", group.get("name", "")),
                suggested_title=data.get("suggested_title", group.get("name", "")),
                summary=data.get("summary", ""),
                key_points=[
                    KeyPoint(text=kp["text"], category=kp.get("category", "acción"))
                    for kp in data.get("key_points", [])
                ],
            )
        )

    # Generar resumen global
    summaries_for_global = [
        {"group": ps.project_name, "summary": ps.summary}
        for ps in group_summaries
    ]
    global_prompt = f"""Dado el siguiente resumen de grupos, escribe un párrafo global (2-3 frases) 
que describa el panorama general de todas las ideas del usuario.

{json.dumps(summaries_for_global, ensure_ascii=False, indent=2)}

Responde solo con el texto del párrafo, sin JSON ni formato adicional:"""

    global_summary = _call_ollama(
        prompt=global_prompt,
        system="Eres un asistente conciso y motivador.",
        temperature=0.4,
    )

    return ProcessResult(groups=group_summaries, global_summary=global_summary)
