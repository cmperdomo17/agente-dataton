import sys
import io
import time
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── Agente & servicios ──
from core.agent import create_agent
from core.dynamo_service import ensure_caches
from core.policy_service import ensure_policies

logger = logging.getLogger(__name__)

# Inicialización del agente al arranque
logger.info("Inicializando agente OmniRetail y catálogos...")
ensure_caches()
ensure_policies()
_agent = create_agent()
logger.info("Agente listo.")

# ── API ──
app = FastAPI(title="OmniRetail Agent API")

# Thread pool para tareas bloqueantes (evaluación)
_executor = ThreadPoolExecutor(max_workers=2)


class ChatRequest(BaseModel):
    message: str


def _invoke_agent(query: str) -> str:
    """Invoca el agente suprimiendo mensajes de streaming en consola."""
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        result = _agent(query)
        return str(result)
    finally:
        sys.stdout = old_stdout


@app.post("/api/chat")
async def chat(request: ChatRequest):
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="El mensaje no puede estar vacío.")

    try:
        logger.info("Consulta recibida: %s", request.message[:50])
        start_time = time.time()
        response = _invoke_agent(request.message)
        elapsed = time.time() - start_time
        return {"response": response, "elapsed": round(elapsed, 1)}
    except Exception as e:
        logger.exception("Error procesando consulta")
        raise HTTPException(status_code=500, detail=str(e))


# ── Evaluador ──
def _run_evaluation() -> dict:
    """Ejecuta la evaluación completa en un thread (bloqueante)."""
    from evaluator.engine import EvaluationEngine

    engine = EvaluationEngine()
    return engine.run_all()


@app.post("/api/evaluate")
async def evaluate():
    """Ejecuta los escenarios del golden dataset y devuelve resultados."""
    try:
        logger.info("Iniciando evaluación de escenarios...")
        start_time = time.time()
        loop = asyncio.get_event_loop()
        payload = await loop.run_in_executor(_executor, _run_evaluation)
        elapsed = time.time() - start_time
        logger.info("Evaluación completada en %.1fs", elapsed)
        return {"results": payload, "elapsed": round(elapsed, 1)}
    except Exception as e:
        logger.exception("Error ejecutando evaluación")
        raise HTTPException(status_code=500, detail=str(e))


# ── Frontend (Estáticos) ──
@app.get("/")
async def get_index():
    return FileResponse("public/index.html")

app.mount("/public", StaticFiles(directory="public"), name="public")

if __name__ == "__main__":
    logger.info("Iniciando servidor en http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
