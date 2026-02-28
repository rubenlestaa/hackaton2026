"""
run_all.py — Arranca los dos servicios en paralelo.

Uso:
    python run_all.py

  • Backend (BD + IA unida):  http://localhost:8000
  • Servicio IA puro:         http://localhost:8001
  • Docs backend:             http://localhost:8000/docs
  • Docs IA:                  http://localhost:8001/docs
"""

import os
import sys
import subprocess
import threading
import time
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR   = ROOT / "data" / "vault" / "notes"
DB_DIR     = ROOT / "data"

# Asegurarse de que existen los directorios necesarios
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_DIR.mkdir(parents=True, exist_ok=True)

PYTHON = sys.executable


def stream_output(proc: subprocess.Popen, prefix: str):
    """Reimprime la salida de un subproceso con un prefijo de color."""
    colors = {"[BACKEND]": "\033[36m", "[AI-SVC] ": "\033[33m"}
    color  = colors.get(prefix, "")
    reset  = "\033[0m"
    for line in iter(proc.stdout.readline, b""):
        print(f"{color}{prefix}{reset} {line.decode('utf-8', errors='replace').rstrip()}", flush=True)


def _find_node():
    """Busca el ejecutable node en el PATH."""
    import shutil
    return shutil.which("node") or "node"


def run():
    print("=" * 60)
    print("  Digital Brain -- arrancando todos los servicios")
    print("=" * 60)

    # ── Servicio de IA (puerto 8001) ──────────────────────────────────────
    ai_env = os.environ.copy()
    ai_env["PYTHONPATH"]    = str(ROOT)
    ai_env["OLLAMA_BASE_URL"] = ai_env.get("OLLAMA_BASE_URL", "http://localhost:11434")
    ai_proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "main:app",
         "--host", "0.0.0.0", "--port", "8001"],
        cwd=str(ROOT / "ai-service"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=ai_env,
    )

    # ── Backend principal (puerto 8000) ───────────────────────────────────
    be_env = os.environ.copy()
    be_env["PYTHONPATH"]    = str(ROOT)
    be_env["AI_SERVICE_URL"] = "http://localhost:8001"
    be_proc = subprocess.Popen(
        [PYTHON, "-m", "uvicorn", "app.main:app",
         "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=be_env,
    )

    # ── Frontend (puerto 5002) ────────────────────────────────────────────
    fe_env = os.environ.copy()
    fe_env["BACKEND_URL"] = "http://localhost:8000"
    fe_proc = subprocess.Popen(
        [_find_node(), "app"],
        cwd=str(ROOT / "frontend"),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=fe_env,
    )

    # Hilos que redirigen la salida al terminal
    t_ai = threading.Thread(target=stream_output, args=(ai_proc, "[AI-SVC] "), daemon=True)
    t_be = threading.Thread(target=stream_output, args=(be_proc, "[BACKEND]"), daemon=True)
    t_fe = threading.Thread(target=stream_output, args=(fe_proc, "[FRONT]  "), daemon=True)
    t_ai.start()
    t_be.start()
    t_fe.start()

    print("\n  Frontend -> http://localhost:5002")
    print("  Backend  -> http://localhost:8000   (docs: /docs)")
    print("  IA       -> http://localhost:8001   (docs: /docs)")
    print("\n  Pulsa Ctrl+C para detener todos los servicios.\n")

    try:
        while True:
            if ai_proc.poll() is not None:
                print("\n[AI-SVC]  El proceso de IA termino inesperadamente.")
                be_proc.terminate(); fe_proc.terminate()
                break
            if be_proc.poll() is not None:
                print("\n[BACKEND] El proceso backend termino inesperadamente.")
                ai_proc.terminate(); fe_proc.terminate()
                break
            if fe_proc.poll() is not None:
                print("\n[FRONT]   El proceso frontend termino inesperadamente.")
                ai_proc.terminate(); be_proc.terminate()
                break
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n  Deteniendo servicios...")
        ai_proc.terminate()
        be_proc.terminate()
        fe_proc.terminate()

    ai_proc.wait()
    be_proc.wait()
    fe_proc.wait()
    print("  Servicios detenidos.")


if __name__ == "__main__":
    run()
