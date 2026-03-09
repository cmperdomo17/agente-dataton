"""
submission_loader.py
────────────────────
Carga, valida e instancia agentes desde ZIPs de equipos participantes.

Cada ZIP debe tener la siguiente estructura mínima:
    <cualquier_carpeta_raíz>/
        core/
            agent.py        ← contiene create_agent(streaming: bool) -> Agent

El loader:
  1. Extrae el ZIP en un directorio temporal aislado.
  2. Valida que el contrato de integración esté presente.
  3. Agrega el root del equipo a sys.path de forma PERMANENTE para que
     los imports relativos (core.config, core.session_context, etc.)
     funcionen en tiempo de ejecución, no solo durante el import.
  4. Importa create_agent dinámicamente sin contaminar otros equipos.
  5. Devuelve un SubmissionInfo con estado, agente instanciado y errores.
"""

import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
import traceback
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, List, Optional


# ─────────────────────────────────────────────────────────────────────────────
# Data classes
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ContractCheck:
    name: str
    passed: bool
    detail: str = ""


@dataclass
class SubmissionInfo:
    team_name: str
    zip_path: str
    extract_dir: str
    root_dir: str

    contract_checks: List[ContractCheck] = field(default_factory=list)
    contract_passed: bool = False

    _create_agent_fn: Optional[Callable] = field(default=None, repr=False)
    load_error: Optional[str] = None

    @property
    def ready(self) -> bool:
        return self.contract_passed and self._create_agent_fn is not None and self.load_error is None

    def create_agent(self, streaming: bool = False) -> Any:
        if not self.ready:
            raise RuntimeError(f"Submission '{self.team_name}' is not ready: {self.load_error}")
        return self._create_agent_fn(streaming=streaming)

    def summary(self) -> str:
        checks = ", ".join(
            f"{'✓' if c.passed else '✗'} {c.name}"
            for c in self.contract_checks
        )
        status = "LISTO" if self.ready else f"ERROR: {self.load_error or 'contrato incompleto'}"
        return f"[{self.team_name}] {status} | {checks}"


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────

class SubmissionLoader:
    def __init__(self, submissions_dir: str, work_dir: Optional[str] = None):
        self.submissions_dir = Path(submissions_dir)
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.mkdtemp(prefix="omni_eval_"))
        self.work_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> List[SubmissionInfo]:
        zips = sorted(self.submissions_dir.glob("*.zip"))
        if not zips:
            return []
        return [self.load_one(zip_path) for zip_path in zips]

    def load_one(self, zip_path: Path) -> SubmissionInfo:
        team_name = _team_name_from_zip(zip_path)
        extract_dir = self.work_dir / team_name

        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        extract_dir.mkdir(parents=True)

        sub = SubmissionInfo(
            team_name=team_name,
            zip_path=str(zip_path),
            extract_dir=str(extract_dir),
            root_dir="",
        )

        # Step 1: extract
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except Exception as e:
            sub.load_error = f"No se pudo extraer el ZIP: {e}"
            return sub

        # Step 2: find project root
        root_dir = _find_project_root(extract_dir)
        if root_dir is None:
            sub.load_error = "No se encontró core/agent.py en el ZIP."
            sub.contract_checks.append(ContractCheck("core/agent.py presente", False, "archivo no encontrado"))
            return sub
        sub.root_dir = str(root_dir)

        # Step 3: validate contract
        sub.contract_checks = _validate_contract(root_dir)
        sub.contract_passed = all(c.passed for c in sub.contract_checks[:3])

        if not sub.contract_passed:
            sub.load_error = "Contrato de integración incompleto."
            return sub

        # Step 4: inject our session_context if team doesn't have one
        _ensure_session_context(root_dir)

        # Step 5: inject team root into sys.path PERMANENTLY
        # This must persist beyond the import so that runtime imports like
        # `from core.config import ...` keep working when the agent is called.
        _inject_path_permanently(root_dir)

        # Step 6: dynamic import
        try:
            sub._create_agent_fn = _dynamic_import_create_agent(root_dir, team_name)
        except Exception as e:
            sub.load_error = f"Error importando create_agent: {e}\n{traceback.format_exc()}"

        return sub

    def cleanup(self):
        if self.work_dir.exists():
            shutil.rmtree(self.work_dir)


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _team_name_from_zip(zip_path: Path) -> str:
    name = zip_path.stem
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _find_project_root(extract_dir: Path) -> Optional[Path]:
    """Find the directory containing core/agent.py (supports nested ZIPs)."""
    if (extract_dir / "core" / "agent.py").exists():
        return extract_dir
    for child in extract_dir.iterdir():
        if child.is_dir() and (child / "core" / "agent.py").exists():
            return child
    for child in extract_dir.iterdir():
        if child.is_dir():
            for grandchild in child.iterdir():
                if grandchild.is_dir() and (grandchild / "core" / "agent.py").exists():
                    return grandchild
    return None


def _validate_contract(root_dir: Path) -> List[ContractCheck]:
    checks = []

    agent_path = root_dir / "core" / "agent.py"
    checks.append(ContractCheck(
        "core/agent.py presente",
        agent_path.exists(),
        "" if agent_path.exists() else "archivo no encontrado",
    ))

    if not agent_path.exists():
        return checks

    agent_source = agent_path.read_text(encoding="utf-8", errors="replace")

    has_create_agent = "def create_agent" in agent_source
    checks.append(ContractCheck(
        "create_agent() definido",
        has_create_agent,
        "" if has_create_agent else "función create_agent no encontrada en core/agent.py",
    ))

    has_streaming = "streaming" in agent_source
    checks.append(ContractCheck(
        "parámetro streaming presente",
        has_streaming,
        "" if has_streaming else "create_agent no acepta parámetro 'streaming'",
    ))

    has_content = ".content" in agent_source or "str(" in agent_source
    checks.append(ContractCheck(
        "respuesta con .content o str()",
        has_content,
        "" if has_content else "no se detectó uso de .content ni str() (advertencia)",
    ))

    has_session = (
        "session_context" in agent_source
        or "add_tool_trace" in agent_source
        or "set_session_customer" in agent_source
    )
    checks.append(ContractCheck(
        "usa session_context (telemetría)",
        has_session,
        "" if has_session else "no se detectó integración con session_context",
    ))

    return checks


def _ensure_session_context(root_dir: Path):
    """Copy our session_context.py into the team's core/ if they don't have one."""
    target = root_dir / "core" / "session_context.py"
    if target.exists():
        return
    our_session_context = Path(__file__).parent / "core" / "session_context.py"
    if our_session_context.exists():
        shutil.copy(our_session_context, target)


def _inject_path_permanently(root_dir: Path):
    """
    Add the team's root directory to sys.path permanently.

    Unlike a temporary injection (with finally: sys.path.remove(...)),
    this keeps the path available for ALL runtime imports — including
    `from core.config import ...` that happen when the agent is called,
    not just when its module is first loaded.

    Each team gets its own entry. If two teams have conflicting module names,
    the last-loaded team wins — acceptable for sequential evaluation.
    """
    root_str = str(root_dir)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)


def _dynamic_import_create_agent(root_dir: Path, team_name: str) -> Callable:
    """
    Dynamically import create_agent from a team's core/agent.py.
    The team's root must already be in sys.path (done by _inject_path_permanently).
    """
    agent_path = root_dir / "core" / "agent.py"
    module_name = f"team_{team_name}_core_agent"

    spec = importlib.util.spec_from_file_location(module_name, agent_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo crear spec para {agent_path}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    if not hasattr(module, "create_agent"):
        raise AttributeError("create_agent no encontrado en el módulo cargado")

    return module.create_agent
