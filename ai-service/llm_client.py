"""
Cliente para comunicarse con Ollama (LLM local).
Ollama expone una API compatible con OpenAI en http://localhost:11434/v1
"""

import json
import re
import httpx
import os
from typing import Any

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
# Cambia el modelo con: $env:OLLAMA_MODEL = "nombre_modelo"
# Opciones: llama3.2 (rápido, 2 GB), llama3.1:8b (preciso, 5 GB)
MODEL_NAME = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
TIMEOUT_SECONDS = 240


def _call_ollama(prompt: str, system: str = "", temperature: float = 0.1) -> str:
    """
    Llama al endpoint /api/generate de Ollama.
    Devuelve el texto generado o lanza una excepción.
    """
    payload = {
        "model": MODEL_NAME,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {
            "temperature": temperature,
            "num_predict": 512,  # JSON outputs son cortos; 512 tokens es más que suficiente
        },
    }

    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        response = client.post(f"{OLLAMA_BASE_URL}/api/generate", json=payload)
        response.raise_for_status()
        data = response.json()
        return data.get("response", "").strip()


def _sanitize_json_string(text: str) -> str:
    """Reemplaza saltos de línea literales dentro de strings JSON por espacios."""
    result = []
    in_string = False
    escape = False
    for char in text:
        if escape:
            result.append(char)
            escape = False
        elif char == "\\":
            result.append(char)
            escape = True
        elif char == '"':
            result.append(char)
            in_string = not in_string
        elif char in ("\n", "\r") and in_string:
            result.append(" ")
        else:
            result.append(char)
    return "".join(result)


def _close_incomplete_json(text: str) -> str:
    """
    Si el LLM se 'olvida' de cerrar el JSON, añade las llaves/corchetes
    que faltan para que json.loads pueda parsear el resultado.
    """
    open_braces   = text.count("{") - text.count("}")
    open_brackets = text.count("[") - text.count("]")

    # Cerrar strings sin cerrar (el LLM también corta a veces dentro de un string)
    # Contamos comillas no escapadas; si hay un número impar, cerramos el string
    stripped = text
    unescaped_quotes = len(re.findall(r'(?<!\\)"', stripped))
    if unescaped_quotes % 2 != 0:
        stripped += '"'

    stripped += "]" * max(0, open_brackets)
    stripped += "}" * max(0, open_braces)
    return stripped


def extract_json(text: str) -> Any:
    """
    Extrae el primer bloque JSON válido del texto del LLM.
    Maneja: texto extra alrededor, bloques markdown, saltos de línea dentro de
    strings y JSON truncado (sin llave de cierre).
    """
    text = text.strip()

    def try_parse(s: str) -> Any:
        """Intenta parsear el string tal cual, luego sanitizado, luego completado."""
        for candidate in (s, _sanitize_json_string(s), _close_incomplete_json(s),
                          _close_incomplete_json(_sanitize_json_string(s))):
            try:
                return json.loads(candidate)
            except json.JSONDecodeError:
                pass
        return None

    # 1. Texto completo tal cual
    result = try_parse(text)
    if result is not None:
        return result

    # 2. Bloque ```json ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.DOTALL)
    if match:
        result = try_parse(match.group(1))
        if result is not None:
            return result

    # 3. Desde el primer '{' hasta el último '}', o hasta el final si no hay '}'
    start = text.find("{")
    if start != -1:
        end = text.rfind("}")
        chunk = text[start: end + 1] if end > start else text[start:]
        result = try_parse(chunk)
        if result is not None:
            return result

    # 4. Arrays
    start = text.find("[")
    if start != -1:
        end = text.rfind("]")
        chunk = text[start: end + 1] if end > start else text[start:]
        result = try_parse(chunk)
        if result is not None:
            return result

    raise ValueError(f"No se encontró JSON válido en la respuesta del LLM:\n{text}")


def _call_ollama_with_tools(
    messages: list[dict],
    tools: list[dict],
    system: str = "",
) -> list[dict] | None:
    """
    Llama a Ollama /api/chat con definición de herramientas (MCP / tool calling).
    Devuelve una lista de dicts {"name": str, "arguments": dict}
    por cada tool_call que el LLM emitió, o None si no llamó ninguna herramienta.
    """
    msgs: list[dict] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.extend(messages)

    payload = {
        "model": MODEL_NAME,
        "messages": msgs,
        "tools": tools,
        "stream": False,
        "options": {"temperature": 0.1, "num_predict": 512},
    }

    with httpx.Client(timeout=TIMEOUT_SECONDS) as client:
        response = client.post(f"{OLLAMA_BASE_URL}/api/chat", json=payload)
        response.raise_for_status()
        data = response.json()

    msg = data.get("message", {})
    tool_calls = msg.get("tool_calls") or []
    if not tool_calls:
        return None

    results = []
    for tc in tool_calls:
        fn = tc.get("function", {})
        name = fn.get("name", "")
        args = fn.get("arguments", {})
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        results.append({"name": name, "arguments": args})
    return results or None


def is_ollama_running() -> bool:
    """Comprueba si Ollama está activo."""
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{OLLAMA_BASE_URL}/api/tags")
            return r.status_code == 200
    except Exception:
        return False


def get_available_models() -> list[str]:
    """Lista los modelos descargados en Ollama."""
    try:
        with httpx.Client(timeout=5) as client:
            r = client.get(f"{OLLAMA_BASE_URL}/api/tags")
            r.raise_for_status()
            models = r.json().get("models", [])
            return [m["name"] for m in models]
    except Exception:
        return []
