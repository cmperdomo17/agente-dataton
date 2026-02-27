import sys
import io
import json
import logging
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
for _noisy in ("botocore", "boto3", "urllib3", "strands"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

# ── Agente ──
from core.agent import create_agent
from core.dynamo_service import ensure_caches
from core.policy_service import ensure_policies

# Inicialización del agente al arranque
logger.info("Inicializando agente OmniRetail y catálogos...")
ensure_caches()
ensure_policies()
_agent = create_agent()
logger.info("Agente listo.")

# ── API ──
app = FastAPI(title="OmniRetail Agent API")

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

import time

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

# ── Frontend (Estáticos) ──
# Servir index.html en la raíz
@app.get("/")
async def get_index():
    return FileResponse("public/index.html")

# Servir el resto de archivos en /public
app.mount("/public", StaticFiles(directory="public"), name="public")

if __name__ == "__main__":
    logger.info("Iniciando servidor en http://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000)
