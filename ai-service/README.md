# HackUDC — AI Notes Organizer · Servicio de IA

Servicio FastAPI que usa **Ollama + Llama** para clasificar notas en texto libre  
y organizarlas automáticamente en proyectos y secciones.

---

## Arquitectura

```
Frontend  →  Backend de mis compañeros  →  Este servicio (puerto 8001)  →  Ollama (puerto 11434)
```

### Endpoints

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET`  | `/health` | Estado del servicio y Ollama |
| `GET`  | `/models` | Modelos disponibles en Ollama |
| `POST` | `/classify` | Clasifica una nota en proyecto + sección |
| `POST` | `/process` | Genera resúmenes (botón PROCESAR) |

Documentación interactiva en: **http://localhost:8001/docs**

---

## Instalación paso a paso

### 1. Instalar Ollama

Descarga e instala Ollama desde: **https://ollama.com/download**  
(elige Windows)

### 2. Descargar el modelo Llama

Abre una terminal y ejecuta:

```powershell
# Opción A: Llama 3.2 (3B, rápido, ~2GB) ← recomendado para hackathon
ollama pull llama3.2

# Opción B: Llama 3.2 (1B, muy rápido, ~1GB)
ollama pull llama3.2:1b

# Opción C: Llama 4 (cuando esté disponible en Ollama)
ollama pull llama4
```

### 3. Instalar dependencias Python

```powershell
cd ai-service
pip install -r requirements.txt
```

### 4. (Opcional) Cambiar el modelo en `llm_client.py`

```python
MODEL_NAME = "llama3.2"   # ← cambia esto si usas otro modelo
```

---

## Arrancar el servicio

```powershell
# Terminal 1: Iniciar Ollama
ollama serve

# Terminal 2: Iniciar el servicio de IA
cd ai-service
python main.py
```

El servicio arranca en **http://localhost:8001**

---

## Ejemplos de uso (para el equipo de backend)

### POST `/classify` — Clasificar una nota

```bash
curl -X POST http://localhost:8001/classify \
  -H "Content-Type: application/json" \
  -d '{
    "text": "quiero hacer biceps el dia de espalda en el gym",
    "existing_projects": [
      {"name": "gimnasio", "sections": ["dia de pierna", "calentamiento"]}
    ]
  }'
```

**Respuesta:**
```json
{
  "project": "gimnasio",
  "section": "dia de espalda",
  "note_content": "hacer bíceps",
  "is_new_project": false,
  "is_new_section": true,
  "confidence": 0.97,
  "explanation": "La nota habla de un ejercicio en el día de espalda del gimnasio"
}
```

---

### POST `/process` — Botón PROCESAR

```bash
curl -X POST http://localhost:8001/process \
  -H "Content-Type: application/json" \
  -d '{
    "projects": [
      {
        "name": "gimnasio",
        "sections": [
          {"name": "dia de espalda", "notes": ["hacer bíceps", "remo con barra"]},
          {"name": "dia de pierna", "notes": ["sentadillas 4x8", "prensa"]}
        ]
      }
    ]
  }'
```

**Respuesta:**
```json
{
  "projects": [
    {
      "project_name": "gimnasio",
      "suggested_title": "Plan de Entrenamiento",
      "summary": "Tienes un plan de entrenamiento bien estructurado con días específicos...",
      "key_points": [
        {"text": "Hacer bíceps en el día de espalda", "category": "acción"},
        {"text": "Completar rutina de piernas con sentadillas y prensa", "category": "acción"},
        {"text": "Mantener consistencia en los entrenamientos", "category": "meta"}
      ]
    }
  ],
  "global_summary": "Estás construyendo una rutina de gimnasio completa y equilibrada..."
}
```

---

## Flujo completo de integración

```
1. Usuario escribe nota
      ↓
2. Frontend envía la nota al backend
      ↓
3. Backend llama a POST /classify con la nota + proyectos existentes
      ↓
4. Este servicio devuelve { project, section, note_content, is_new_project, is_new_section }
      ↓
5. Backend crea el proyecto/sección si is_new_project/is_new_section = true
6. Backend guarda la nota limpia en la sección correcta
      ↓
7. [Cuando el usuario pulsa PROCESAR]
8. Backend recopila todos los proyectos y llama a POST /process
      ↓
9. Este servicio devuelve resúmenes + puntos clave
     ↓
10. Frontend muestra el resultado
```

---

## Cambiar a Llama 4 cuando esté disponible

Solo cambia una línea en `llm_client.py`:

```python
MODEL_NAME = "llama4"   # o "llama4:latest", etc.
```

Y descarga el modelo:
```powershell
ollama pull llama4
```

---

## Estructura del proyecto

```
ai-service/
├── main.py          # FastAPI app con todos los endpoints
├── classifier.py    # Lógica de clasificación de notas con el LLM
├── processor.py     # Lógica del botón PROCESAR (resúmenes + puntos clave)
├── llm_client.py    # Cliente HTTP para Ollama
├── models.py        # Modelos Pydantic (request/response)
├── requirements.txt
└── README.md
```
