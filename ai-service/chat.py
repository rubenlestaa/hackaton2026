"""
Consola interactiva para probar el clasificador de notas.
Ejecutar con: python chat.py
"""

import httpx
import json

BASE_URL = "http://localhost:8001"
groups = []  # estado en memoria de esta sesión


def classify(text: str) -> dict:
    r = httpx.post(f"{BASE_URL}/classify", json={"text": text, "existing_groups": groups}, timeout=60)
    r.raise_for_status()
    return r.json()


def apply_result(result: dict):
    """Actualiza el estado local de grupos con el resultado del LLM."""
    pname  = result.get("group")
    spname = result.get("subgroup")
    idea   = result.get("idea")

    # ── DELETE: eliminar la idea del estado local ───────────────────────────────────────
    if result.get("action") == "delete":
        proj = next((p for p in groups if p["name"] == pname), None)
        if proj and idea:
            if spname:
                sub = next((s for s in proj["subgroups"] if s["name"] == spname), None)
                if sub:
                    sub["ideas"] = [i for i in sub["ideas"] if i != idea]
            else:
                if idea in proj["ideas"]:
                    proj["ideas"] = [i for i in proj["ideas"] if i != idea]
                else:
                    for sub in proj.get("subgroups", []):
                        if idea in sub["ideas"]:
                            sub["ideas"] = [i for i in sub["ideas"] if i != idea]
                            break
        return

    # Buscar o crear el grupo
    proj = next((p for p in groups if p["name"] == pname), None)
    if not proj:
        proj = {"name": pname, "ideas": [], "subgroups": []}
        groups.append(proj)

    if spname:
        # Buscar o crear el subgrupo
        sub = next((s for s in proj["subgroups"] if s["name"] == spname), None)
        if not sub:
            # Heredar ideas del padre si corresponde
            inherited = proj["ideas"].copy() if result.get("inherit_parent_ideas") else []
            sub = {"name": spname, "ideas": inherited}
            proj["subgroups"].append(sub)
        if idea:
            sub["ideas"].append(idea)
    else:
        if idea:
            proj["ideas"].append(idea)


def print_projects():
    if not groups:
        print("  (sin grupos aún)")
        return
    for p in groups:
        print(f"  \U0001f4c1 {p['name']}")
        for idea in p["ideas"]:
            print(f"     • {idea}")
        for sub in p.get("subgroups", []):
            print(f"     📂 {sub['name']}")
            for idea in sub["ideas"]:
                print(f"        • {idea}")


print("=" * 55)
print("  Clasificador de notas — escribe tus ideas")
print("  Comandos: 'ver' para ver grupos, 'salir' para salir")
print("=" * 55)

while True:
    try:
        text = input("\n📝  Nota: ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nHasta luego!")
        break

    if not text:
        continue
    if text.lower() == "salir":
        print("Hasta luego!")
        break
    if text.lower() == "ver":
        print("\nGrupos actuales:")
        print_projects()
        continue

    print("   Clasificando...", end="", flush=True)
    try:
        result = classify(text)
        apply_result(result)

        sub = f" → {result['subgroup']}" if result.get("subgroup") else ""

        if result.get("action") == "delete":
            print(f"\r   \U0001f5d1\ufe0f  ELIMINADO de {result.get('group', '?')}{sub}: \"{result.get('idea', '?')}\"")
        else:
            nuevo_p = " [NUEVO GRUPO]" if result.get("is_new_group") else ""
            nuevo_s = " [NUEVO SUBGRUPO]" if result.get("is_new_subgroup") else ""
            hereda  = " (hereda ideas del padre)" if result.get("inherit_parent_ideas") else ""

            print(f"\r   ✅  {result['group']}{sub}{nuevo_p}{nuevo_s}")
        print(f"       💡 idea guardada: \"{result['idea']}\"{hereda}")
    except Exception as e:
        print(f"\r   ❌  Error: {e}")
