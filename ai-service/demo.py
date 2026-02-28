"""
Simulador interactivo de la IA.
Escribe notas en lenguaje natural y verás:
  1. Lo que clasificó el LLM
  2. La llamada exacta que se haría al backend

Ejecutar con: python demo.py
"""

import httpx
import json

AI_SERVICE = "http://localhost:8001"

# ── Categorías principales predefinidas ──────────────────────────────────────
PREDEFINED_CATEGORIES = {
    "rutina diaria",
    "compras",
    "trabajo/clase",
    "finanzas",
    "viajes",
    "vida social",
    "citas",
}

# ── Estado de la sesión (simula la base de datos del backend) ─────────────────
groups: list[dict] = []


# ── Helpers ───────────────────────────────────────────────────────────────────

def classify(text: str) -> dict:
    r = httpx.post(
        f"{AI_SERVICE}/classify",
        json={"text": text, "existing_groups": groups},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


def build_backend_calls(result: dict) -> list[dict]:
    """
    Traduce el resultado del LLM a las llamadas REST que el backend debería recibir.
    Devuelve una lista de operaciones en orden.
    """
    calls = []
    pname  = result["group"]
    spname = result.get("subgroup")
    idea   = result.get("idea")
    rename = result.get("rename_group")  # {"old_name": "...", "new_name": "..."} o None

    # ── DELETE: devolver una sola llamada REST y salir ─────────────────────────
    if result.get("action") == "delete":
        if idea:
            # Si el LLM no devolvió subproyecto, buscar dónde vive la idea realmente
            resolved_sp = spname
            if not resolved_sp:
                proj_state = next((p for p in projects if p["name"] == pname), None)
                if proj_state:
                    if idea not in proj_state["ideas"]:
                        for sub in proj_state.get("subprojects", []):
                            if idea in sub["ideas"]:
                                resolved_sp = sub["name"]
                                break
            if resolved_sp:
                calls.append({
                    "acción": "ELIMINAR IDEA",
                    "método": "DELETE",
                    "ruta":   f"/projects/{pname}/subprojects/{resolved_sp}/ideas/{idea}",
                    "body":   {},
                })
            else:
                calls.append({
                    "acción": "ELIMINAR IDEA",
                    "método": "DELETE",
                    "ruta":   f"/projects/{pname}/ideas/{idea}",
                    "body":   {},
                })
        return calls

    # Si hay que renombrar un proyecto existente, va PRIMERO
    if rename:
        calls.append({
            "acción":      "RENOMBRAR GRUPO",
            "método":      "PATCH",
            "ruta":        f"/groups/{rename['old_name']}",
            "body":        {"name": rename["new_name"]},
        })

    if result["is_new_group"]:
        calls.append({
            "acción":      "CREAR GRUPO",
            "método":      "POST",
            "ruta":        "/groups",
            "body":        {"name": pname, "ideas": [], "subgroups": []},
        })

    if spname and result.get("is_new_subgroup"):
        inherited = [i for i in get_group_ideas(pname)] if result.get("inherit_parent_ideas") else []
        calls.append({
            "acción":      "CREAR SUBGRUPO",
            "método":      "POST",
            "ruta":        f"/groups/{pname}/subgroups",
            "body":        {"name": spname, "ideas": inherited},
            "nota":        "hereda ideas del padre" if inherited else None,
        })

    if spname:
        if idea:
            calls.append({
                "acción":      "AÑADIR IDEA A SUBGRUPO",
                "método":      "POST",
                "ruta":        f"/groups/{pname}/subgroups/{spname}/ideas",
                "body":        {"idea": idea},
            })
    else:
        if idea:
            calls.append({
                "acción":      "AÑADIR IDEA AL GRUPO",
                "método":      "POST",
                "ruta":        f"/groups/{pname}/ideas",
                "body":        {"idea": idea},
            })

    return calls


def get_group_ideas(pname: str) -> list[str]:
    proj = next((p for p in groups if p["name"] == pname), None)
    return proj["ideas"] if proj else []


def apply_result(result: dict):
    """Actualiza el estado local simulando lo que haría el backend."""
    # ── DELETE: eliminar la idea del estado local ──────────────────────────────
    if result.get("action") == "delete":
        pname  = result.get("group")
        spname = result.get("subgroup")
        idea   = result.get("idea")
        proj   = next((p for p in groups if p["name"] == pname), None)
        if proj and idea:
            if spname:
                sub = next((s for s in proj["subgroups"] if s["name"] == spname), None)
                if sub:
                    sub["ideas"] = [i for i in sub["ideas"] if i != idea]
            else:
                # Intentar nivel superior primero; si no está, buscar en subgrupos
                if idea in proj["ideas"]:
                    proj["ideas"] = [i for i in proj["ideas"] if i != idea]
                else:
                    for sub in proj.get("subgroups", []):
                        if idea in sub["ideas"]:
                            sub["ideas"] = [i for i in sub["ideas"] if i != idea]
                            break
        return

    # Si hay rename, aplicarlo primero
    rename = result.get("rename_group")
    if rename:
        for p in groups:
            if p["name"] == rename["old_name"]:
                p["name"] = rename["new_name"]
                break

    pname  = result["group"]
    spname = result.get("subgroup")
    idea   = result.get("idea")  # puede ser None

    proj = next((p for p in groups if p["name"] == pname), None)
    if not proj:
        proj = {"name": pname, "ideas": [], "subgroups": []}
        groups.append(proj)

    if spname:
        sub = next((s for s in proj["subgroups"] if s["name"] == spname), None)
        if not sub:
            inherited = proj["ideas"].copy() if result.get("inherit_parent_ideas") else []
            sub = {"name": spname, "ideas": inherited}
            proj["subgroups"].append(sub)
        if idea and idea not in sub["ideas"]:
            sub["ideas"].append(idea)
    else:
        if idea and idea not in proj["ideas"]:
            proj["ideas"].append(idea)


def print_state():
    print("\n  Estado actual de grupos:")
    if not groups:
        print("  (vacío)")
        return
    for p in groups:
        ideas_str = ", ".join(f'"{i}"' for i in p["ideas"]) or "—"
        print(f"\n  \U0001f4c1  {p['name']}")
        print(f"       ideas: {ideas_str}")
        for sub in p.get("subgroups", []):
            sub_ideas = ", ".join(f'"{i}"' for i in sub["ideas"]) or "—"
            print(f"       📂  {sub['name']}")
            print(f"            ideas: {sub_ideas}")


def print_calls(calls: list[dict]):
    for i, call in enumerate(calls, 1):
        accion = call["acción"]
        metodo = call["método"]
        ruta   = call["ruta"]
        body   = json.dumps(call["body"], ensure_ascii=False)
        nota   = f"  ← {call['nota']}" if call.get("nota") else ""
        print(f"\n  [{i}] {accion}")
        print(f"       {metodo} {ruta}")
        print(f"       {body}{nota}")


# ── Bucle principal ───────────────────────────────────────────────────────────

PREDEFINED_LABEL = "  📌  Categorías principales:  " + "  ·  ".join(PREDEFINED_CATEGORIES)

HELP = """
Comandos:
  ver      → muestra el estado actual de proyectos
  limpiar  → resetea todos los proyectos
  salir    → cierra el demo

Ejemplos de notas:
  "dormir a las 3"                  → rutina diaria / dormir
  "cita con el dentista el martes"   → citas / dentista
  "comprar leche"                    → compras
  "comprar zapatos en Zara"          → compras / zara
  "pagar el recibo de la luz"        → finanzas
  "quedar con Ana el viernes"        → vida social
  "ver Interstellar en Netflix"      → películas / netflix
  "abrir una tienda de peluches"     → grupo nuevo (iniciativa)
"""

print("=" * 60)
print("  Demo IA — Organizador de ideas")
print("  Escribe una nota y verás qué haría la IA + backend")
print()
print(PREDEFINED_LABEL)
print(HELP)
print("=" * 60)

while True:
    try:
        text = input("\n📝  Nota: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nHasta luego!")
        break

    if not text:
        continue

    cmd = text.lower()
    if cmd == "salir":
        print("Hasta luego!")
        break
    if cmd == "ver":
        print_state()
        continue
    if cmd == "limpiar":
        groups.clear()
        print("  ✅  Grupos reseteados.")
        continue
    if cmd in ("ayuda", "help", "?"):
        print(HELP)
        continue

    # ── Llamar al LLM ─────────────────────────────────────────────────────────
    print("  ⏳  Clasificando...", end="", flush=True)
    try:
        result = classify(text)
    except Exception as e:
        print(f"\r  ❌  Error al llamar al servicio de IA: {e}")
        continue

    # ── Nota sin sentido: avisar y no hacer nada ─────────────────────────────
    if not result.get("makes_sense", True):
        reason = result.get("reason", "No se encontró sentido a la nota.")
        print(f"\r  ⚠️   La IA no entiende esta nota y no hará nada.")
        print(f"       Razón: {reason}")
        print(f"       (El frontend avisaría al usuario con este mensaje.)")
        continue

    # ── Mostrar resultado del LLM ─────────────────────────────────────────────
    sub_str  = f" → \033[96m{result['subproject']}\033[0m" if result.get("subproject") else ""
    idea_str = f"\"{ result.get('idea', '') }\"" if result.get("idea") else "\033[90m(sin idea)\033[0m"
    is_predefined = result.get("project", "").lower() in PREDEFINED_CATEGORIES
    category_badge = " \033[32m[categoría principal]\033[0m" if is_predefined else " \033[90m[proyecto personalizado]\033[0m"

    if result.get("action") == "delete":
        print(f"\r  🗑️   LLM detectó ELIMINACIÓN:")
        print(f"       proyecto:    \033[93m{result['project']}\033[0m{sub_str}{category_badge}")
        print(f"       idea:        {idea_str}")
    else:
        print(f"\r  ✅  LLM clasificó:")
        print(f"       proyecto:    \033[93m{result['project']}\033[0m{sub_str}{category_badge}")
        print(f"       idea:        {idea_str}")
        flags = []
        if result.get("is_new_project"):        flags.append("nuevo proyecto")
        if result.get("is_new_subproject"): flags.append("nuevo subproyecto")
        if result.get("inherit_parent_ideas"): flags.append("hereda ideas del padre")
        if result.get("rename_project"):
            r = result["rename_project"]
            flags.append(f"renombrar '{r['old_name']}' → '{r['new_name']}'")
        if flags:
            print(f"       flags:       {', '.join(flags)}")

    # ── Mostrar llamadas al backend ──────────────────────────────────────────
    calls = build_backend_calls(result)
    print(f"\n  📡  Llamadas al backend ({len(calls)}):")
    print_calls(calls)

    # ── Actualizar estado local ───────────────────────────────────────────────
    apply_result(result)
