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

_TRACE_FN_NAMES = ("reset_session", "get_tool_trace", "get_tool_trace_length", "get_tool_trace_since")


def _capture_team_session_fns():
    """
    After a team module is exec'd, sys.modules['core.session_context'] is the
    team's own session_context module (loaded from their core/ directory).

    Capture its trace functions so MultiEngine can read from the SAME instance
    that the team agent writes to.  Returns None if the module lacks the
    standard trace API (engine falls back to its own resolution).

    Supports two patterns:
      Pattern 1 — module-level functions (most teams).
      Pattern 2 — class-based singleton via SessionContext (e.g. JULIAN).
    """
    sc = sys.modules.get("core.session_context")
    if sc is None:
        return None

    # Pattern 1: module-level functions
    if all(hasattr(sc, fn) for fn in _TRACE_FN_NAMES):
        return {fn: getattr(sc, fn) for fn in _TRACE_FN_NAMES}

    # Pattern 2: class-based singleton (e.g. JULIAN's SessionContext)
    sc_class = getattr(sc, "SessionContext", None)
    if sc_class is not None and callable(sc_class):
        try:
            instance = sc_class()  # __new__ returns existing singleton
            if all(hasattr(instance, fn) for fn in _TRACE_FN_NAMES):
                import inspect
                fns = {fn: getattr(instance, fn) for fn in _TRACE_FN_NAMES}
                # Wrap reset_session with save=False to skip DynamoDB writes during eval
                original_reset = fns["reset_session"]
                if "save" in inspect.signature(original_reset).parameters:
                    fns["reset_session"] = lambda: original_reset(save=False)
                return fns
        except Exception:
            pass

    return None


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
    _session_fns: Optional[dict] = field(default=None, repr=False)
    _core_mod: Any = field(default=None, repr=False)  # team's core module snapshot
    load_error: Optional[str] = None
    backend_tag: str = "bedrock"
    backend_notes: str = ""
    is_nonstandard_backend: bool = False

    @property
    def ready(self) -> bool:
        return self.contract_passed and self._create_agent_fn is not None and self.load_error is None

    def create_agent(self, streaming: bool = False) -> Any:
        if not self.ready:
            raise RuntimeError(f"Submission '{self.team_name}' is not ready: {self.load_error}")
        # Restore this team's core module so lazy relative imports inside
        # create_agent() (e.g. `from .strands_tools import ...`) resolve
        # against the correct core/ directory and not a later team's.
        if self._core_mod is not None:
            sys.modules["core"] = self._core_mod
        _inject_path_permanently(Path(self.root_dir))
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

        # Step 1: extract (normalize Windows backslashes in member names)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                for member in zf.infolist():
                    # Normalize Windows-style paths (core\agent.py → core/agent.py)
                    member.filename = member.filename.replace("\\", "/")
                    zf.extract(member, extract_dir)
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
        sub.contract_passed = all(c.passed for c in sub.contract_checks[:2])

        if not sub.contract_passed:
            sub.load_error = "Contrato de integración incompleto."
            return sub

        # Step 4: inject our session_context if team doesn't have one
        _ensure_session_context(root_dir)

        # Step 5: PURGAR módulos del equipo anterior antes de inyectar el nuevo path
        # Esto evita que sys.modules devuelva core.session_context del equipo anterior
        _purge_team_modules(root_dir)

        # Step 6: inject team root into sys.path
        _inject_path_permanently(root_dir)

        # Step 7: dynamic import
        try:
            sub._create_agent_fn = _dynamic_import_create_agent(root_dir, team_name)
            # Snapshot the team's core module so create_agent() can restore it
            # later — avoids lazy relative imports resolving against a different
            # team's core/ when multiple teams are loaded before evaluation runs.
            sub._core_mod = sys.modules.get("core")
            # Capture the team's session_context trace functions so MultiEngine
            # reads from the same module instance the agent writes to.
            sub._session_fns = _capture_team_session_fns()
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
    if not target.exists():
        our_session_context = Path(__file__).parent / "core" / "session_context.py"
        if our_session_context.exists():
            shutil.copy(our_session_context, target)

    # Ensure __init__.py exists in core/ and all other Python subdirectories.
    # Regular packages (with __init__.py) take priority over namespace packages in
    # sys.path, so this prevents a stale namespace package from a previous team's
    # `tools/`, `utils/`, etc. shadowing this team's own package of the same name.
    # NOTE: this loop must always run — do NOT guard behind a session_context check,
    # as teams that already have session_context.py still need __init__.py created
    # in their tools/, utils/, etc. subdirectories.
    for sub in [root_dir / "core"] + [d for d in root_dir.iterdir() if d.is_dir()]:
        if any(sub.glob("*.py")) and not (sub / "__init__.py").exists():
            (sub / "__init__.py").touch()


def _purge_team_modules(incoming_root: Path):
    """
    Elimina de sys.modules todos los módulos del namespace 'core.*'
    que NO pertenezcan al equipo entrante.

    Esto es necesario porque Python cachea módulos en sys.modules por nombre.
    Sin esta limpieza, cuando el Equipo B se carga después del Equipo A:
      - 'core.session_context' en sys.modules todavía apunta al código del Equipo A
      - El agente del Equipo B escribe traces en la instancia del Equipo A
      - El evaluador lee un trace vacío o del equipo equivocado

    También se eliminan módulos 'core' sin prefijo que hayan quedado de
    un equipo anterior o de la carga previa del agente propio.
    """
    incoming_core = str(incoming_root / "core")

    modules_to_remove = []
    for mod_name, mod in list(sys.modules.items()):
        # Always purge core.* and core namespace modules from other teams
        is_core_ns = mod_name == "core" or mod_name.startswith("core.")

        # Also purge ANY flat/non-core module (e.g. 'session_context', 'agent',
        # 'challenge') whose __file__ lives inside an omni_eval temp directory
        # that does NOT belong to the incoming team.
        # Some teams use bare imports (`from session_context import ...`) or
        # self-referential package imports (`from challenge.core import ...`).
        # Without this, a cached module from team A is served to team B.
        mod_file = getattr(mod, "__file__", None) or ""
        # Also check __path__ for namespace packages (tools/, utils/, etc.) which
        # have __file__=None but __path__ pointing to a previous team's directory.
        mod_path_strs = [str(p) for p in (getattr(mod, "__path__", None) or [])]
        is_flat_from_other_team = (
            not is_core_ns
            and (
                (mod_file and "omni_eval" in mod_file and str(incoming_root) not in mod_file)
                or any("omni_eval" in p and str(incoming_root) not in p for p in mod_path_strs)
            )
        )

        if not (is_core_ns or is_flat_from_other_team):
            continue

        if is_flat_from_other_team:
            modules_to_remove.append(mod_name)
        elif mod_file and incoming_core not in mod_file:
            modules_to_remove.append(mod_name)
        elif not mod_file:
            modules_to_remove.append(mod_name)

    for mod_name in modules_to_remove:
        del sys.modules[mod_name]

    if modules_to_remove:
        print(f"  [loader] Purgados {len(modules_to_remove)} módulos cacheados: {modules_to_remove[:5]}{'...' if len(modules_to_remove) > 5 else ''}")


def _inject_path_permanently(root_dir: Path):
    """
    Agrega el root del equipo a sys.path[0] y core/ a sys.path[1].

    Se inserta al frente para que los imports de 'core.*' del equipo
    tengan prioridad sobre cualquier 'core.*' de otro equipo que
    haya quedado en un path más profundo.

    También inyecta root_dir/core para soportar equipos que usan imports
    sin prefijo de paquete: `from session_context import ...` en vez de
    `from core.session_context import ...`.
    """
    root_str = str(root_dir)
    core_str = str(root_dir / "core")

    # Purgar paths de core/ de equipos anteriores (dentro de directorios temporales)
    stale_core = [
        p for p in sys.path
        if p.endswith("/core") and "omni_eval" in p and p != core_str
    ]
    for p in stale_core:
        try:
            sys.path.remove(p)
        except ValueError:
            pass

    # Remover si ya estaban en posición incorrecta
    for p in [core_str, root_str]:
        if p in sys.path:
            sys.path.remove(p)

    # Insertar al frente: root primero, luego core/
    sys.path.insert(0, core_str)
    sys.path.insert(0, root_str)


def _dynamic_import_create_agent(root_dir: Path, team_name: str) -> Callable:
    """
    Importa create_agent dinámicamente desde core/agent.py del equipo.
    El root del equipo ya debe estar en sys.path[0].
    """
    import re
    import types

    agent_path = root_dir / "core" / "agent.py"
    module_name = f"team_{team_name}_core_agent"

    # Some teams use self-referential package imports:
    #   `from challenge.core import session_context`
    # where root_dir.name == "challenge". In that case, root_dir.parent
    # must be in sys.path so Python can resolve the top-level package.
    try:
        agent_source = agent_path.read_text(encoding="utf-8", errors="replace")
        pkg = re.escape(root_dir.name)
        if re.search(rf'(?:from|import)\s+{pkg}\b', agent_source):
            parent_str = str(root_dir.parent)
            if parent_str not in sys.path:
                sys.path.insert(1, parent_str)
    except Exception:
        pass

    # Explicitly register the team's core/ as the canonical 'core' package.
    # This must happen after _purge_team_modules has cleared stale core.* entries.
    # Without this, Python's import machinery may follow sys.path and pick up our
    # own core/__init__.py (from the cwd) instead of the team's core/ directory,
    # breaking both absolute (from core.X import ...) and relative (from .X import ...).
    core_mod = types.ModuleType("core")
    core_mod.__path__ = [str(root_dir / "core")]
    core_mod.__package__ = "core"
    core_mod.__file__ = str(root_dir / "core" / "__init__.py")
    sys.modules["core"] = core_mod

    spec = importlib.util.spec_from_file_location(module_name, agent_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"No se pudo crear spec para {agent_path}")

    module = importlib.util.module_from_spec(spec)
    module.__package__ = "core"
    sys.modules[module_name] = module

    # Attempt exec_module with automatic recovery on NameError / ModuleNotFoundError.
    # Some Bedrock/Strands teams declare type annotations without importing them
    # (NameError), or place helper packages outside the core/ root (ModuleNotFoundError).
    _stubs: dict = {}       # name → resolved or stub value
    _path_fixes: set = set()  # paths already added to sys.path for missing packages
    while True:
        try:
            spec.loader.exec_module(module)
            break
        except NameError as _e:
            _missing = _e.name if hasattr(_e, "name") else str(_e).split("'")[1]
            if _missing in _stubs:
                raise  # same name failed twice — genuine error
            # Try to resolve from strands package first
            _resolved = None
            for _pkg in ("strands", "strands.types", "strands.types.streaming",
                         "strands.agent", "strands.models"):
                try:
                    _mod = importlib.import_module(_pkg)
                    if hasattr(_mod, _missing):
                        _resolved = getattr(_mod, _missing)
                        break
                except ImportError:
                    pass
            _stubs[_missing] = _resolved or type(_missing, (), {})
            # Re-create module for a clean re-exec, injecting all accumulated stubs
            module = importlib.util.module_from_spec(spec)
            module.__package__ = "core"
            module.__dict__.update(_stubs)
            sys.modules[module_name] = module
        except ModuleNotFoundError as _e:
            # A team-local package (e.g. tools.tool) couldn't be found.
            # This usually means the package lives outside root_dir — e.g. tools/
            # is a sibling of the core/ folder at root_dir.parent.
            # Walk upward from root_dir to find the top-level package directory.
            _top_pkg = (_e.name or "").split(".")[0]
            _fixed = False
            for _search in [root_dir, root_dir.parent, root_dir.parent.parent]:
                if (_search / _top_pkg).is_dir() or (_search / f"{_top_pkg}.py").exists():
                    _search_str = str(_search)
                    if _search_str not in _path_fixes:
                        _path_fixes.add(_search_str)
                        sys.path.insert(0, _search_str)
                        _fixed = True
                        break
            if not _fixed:
                raise  # genuinely missing, not a path issue
            # Re-create module so the exec starts clean with the new path
            module = importlib.util.module_from_spec(spec)
            module.__package__ = "core"
            module.__dict__.update(_stubs)
            sys.modules[module_name] = module
        if len(_stubs) + len(_path_fixes) > 8:
            raise RuntimeError(f"Demasiados errores de importación al cargar {team_name}")

    if not hasattr(module, "create_agent"):
        raise AttributeError("create_agent no encontrado en el módulo cargado")

    return module.create_agent
