"""
Script de verificación rápida del servicio.
Ejecutar con: python check.py
"""

import sys
import httpx

BASE_URL = "http://localhost:8001"


def check():
    print("=" * 50)
    print("  HackUDC — AI Service Check")
    print("=" * 50)

    # 1. Comprobar servicio
    try:
        r = httpx.get(f"{BASE_URL}/health", timeout=5)
        data = r.json()
        print(f"\n✅  Servicio levantado en {BASE_URL}")
        print(f"   Ollama activo: {data['ollama']}")
        print(f"   Modelo:        {data['model']}")
        print(f"   Modelos dis.:  {data['available_models']}")
    except Exception as e:
        print(f"\n❌  El servicio NO está corriendo: {e}")
        print("    Arranca con: python main.py")
        sys.exit(1)

    # 2. Prueba de clasificación
    print("\n🧪  Prueba de clasificación...")
    payload = {
        "text": "quiero hacer biceps el dia de espalda en el gym",
        "existing_projects": [
            {"name": "gimnasio", "sections": ["dia de pierna"]}
        ]
    }
    try:
        r = httpx.post(f"{BASE_URL}/classify", json=payload, timeout=60)
        result = r.json()
        print(f"   Proyecto:  {result.get('project')}")
        print(f"   Sección:   {result.get('section')}")
        print(f"   Nota:      {result.get('note_content')}")
        print(f"   Nueva sec: {result.get('is_new_section')}")
        print(f"   Confianza: {result.get('confidence')}")
        print("\n✅  Clasificación OK")
    except Exception as e:
        print(f"\n❌  Error en clasificación: {e}")

    # 3. Prueba de PROCESAR
    print("\n🧪  Prueba de PROCESAR...")
    payload = {
        "projects": [
            {
                "name": "gimnasio",
                "sections": [
                    {"name": "dia de espalda", "notes": ["hacer bíceps", "remo con barra"]},
                ]
            }
        ]
    }
    try:
        r = httpx.post(f"{BASE_URL}/process", json=payload, timeout=120)
        result = r.json()
        proj = result["projects"][0]
        print(f"   Título:     {proj.get('suggested_title')}")
        print(f"   Resumen:    {proj.get('summary')[:80]}...")
        print(f"   Puntos clave: {len(proj.get('key_points', []))}")
        print("\n✅  PROCESAR OK")
    except Exception as e:
        print(f"\n❌  Error en PROCESAR: {e}")

    print("\n" + "=" * 50)
    print("  Todo listo 🚀")
    print("=" * 50)


if __name__ == "__main__":
    check()
