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

# Module-level sentinel and global used by the LLM shim system (see _install_llm_shims).
_SHIM_MISSING = object()
_active_restore_shims: Optional[Callable] = None

# Lock judge credentials at import time — before ANY team .env or setdefault call
# can contaminate MODEL_ID / AWS_PROFILE / AWS_REGION.
# _inject_aws_env() restores these values on every team load so the judge always
# uses the original shell credentials (which have Bedrock access).
_LOCKED_JUDGE_MODEL   = os.getenv("MODEL_ID",   "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
_LOCKED_JUDGE_REGION  = os.getenv("AWS_REGION", "us-east-2")
_LOCKED_JUDGE_PROFILE = os.getenv("AWS_PROFILE", "")  # e.g. "smartcatalog"

# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB table-name redirect
# Some teams hardcode Retail* table names.  We transparently redirect those to
# our existing omniretail_* tables so they never need to create anything.
# Installed once at module-import time via _install_dynamo_name_redirect().
# ─────────────────────────────────────────────────────────────────────────────

_DYNAMO_TABLE_REDIRECT = {
    # Retail* names (juan_david_vela, load_dynamodb.py convention)
    "retailcustomers":   "omniretail_customers",
    "retailorders":      "omniretail_orders",
    "retailorderitems":  "omniretail_order_items",
    "retailproducts":    "omniretail_products",
    "retailstock":       "omniretail_stock",
    "retailshipments":   "omniretail_shipments",
    # Bare names (daniel_ceron and others who use plain table names)
    "customers":         "omniretail_customers",
    "orders":            "omniretail_orders",
    "order_items":       "omniretail_order_items",
    "orderitems":        "omniretail_order_items",
    "products":          "omniretail_products",
    "stock":             "omniretail_stock",
    "shipments":         "omniretail_shipments",
    "customer_emails":   "omniretail_customer_emails",
    "addresses":         "omniretail_addresses",
    "brands":            "omniretail_brands",
    "cards":             "omniretail_cards",
    "categories":        "omniretail_categories",
    "promotions":        "omniretail_promotions",
    "tracking":          "omniretail_tracking",
}


class _TableProxy:
    """
    Wraps a boto3 DynamoDB Table object returned from a redirected create_table().
    Makes wait_until_exists() a no-op (table already exists) and passes everything
    else through to the real Table object.
    """
    __slots__ = ("_real",)

    def __init__(self, real_table):
        object.__setattr__(self, "_real", real_table)

    def wait_until_exists(self, **kwargs):
        print("  [loader] wait_until_exists skipped — table already active")

    def wait_until_not_exists(self, **kwargs):
        pass  # no-op for symmetry

    def __getattr__(self, item):
        return getattr(object.__getattribute__(self, "_real"), item)

    def __setattr__(self, key, value):
        setattr(object.__getattribute__(self, "_real"), key, value)


class _DynamoResourceProxy:
    """
    Wraps a boto3 DynamoDB ServiceResource.  Any access to a Retail* table name
    is silently redirected to the corresponding omniretail_* table.
    create_table() for a Retail* name is a no-op — returns the existing table.
    """
    __slots__ = ("_real", "_map")

    def __init__(self, real_resource, table_map):
        object.__setattr__(self, "_real", real_resource)
        object.__setattr__(self, "_map",  table_map)

    def _resolve(self, name: str) -> str:
        return object.__getattribute__(self, "_map").get(name.lower(), name)

    def Table(self, name: str):
        resolved = self._resolve(name)
        return object.__getattribute__(self, "_real").Table(resolved)

    def create_table(self, **kwargs):
        name = kwargs.get("TableName", "")
        resolved = self._resolve(name)
        real = object.__getattribute__(self, "_real")
        if resolved != name:
            print(f"  [loader] DynamoDB redirect: create_table({name!r}) → Table({resolved!r}) [no-op]")
            return _TableProxy(real.Table(resolved))
        return real.create_table(**kwargs)

    def __getattr__(self, item):
        return getattr(object.__getattribute__(self, "_real"), item)

    def __setattr__(self, key, value):
        setattr(object.__getattribute__(self, "_real"), key, value)


_DYNAMO_ACCOUNT_PROFILE = "Valen-Agentic"   # account 469511944548 — holds all omniretail_* tables
_DYNAMO_ACCOUNT_REGION  = "us-east-2"


def _install_dynamo_name_redirect():
    """Patch boto3.resource so DynamoDB resources get the Retail→omniretail redirect.

    Also forces all DynamoDB resources to use the Valen-Agentic AWS profile so that
    team code always hits the correct account (469511944548) regardless of what
    AWS_PROFILE is set to in the shell.  This keeps the judge's Bedrock calls on the
    original shell credentials (which have Bedrock access) without a global profile swap.
    """
    try:
        import boto3
        import boto3.session as _boto3_session

        _map     = _DYNAMO_TABLE_REDIRECT
        _profile = _DYNAMO_ACCOUNT_PROFILE
        _region  = _DYNAMO_ACCOUNT_REGION
        _orig_fn  = boto3.resource
        _orig_ses = _boto3_session.Session.resource

        def _make_dynamo_resource(region_name):
            """Create a DynamoDB resource pinned to the dataton account."""
            try:
                session = boto3.Session(profile_name=_profile)
                return _orig_ses(session, "dynamodb", region_name=region_name)
            except Exception:
                # Profile not available (e.g. CI env) — fall back to default creds
                return _orig_fn("dynamodb", region_name=region_name)

        def _wrap(service_name, *args, **kwargs):
            if service_name != "dynamodb":
                return _orig_fn(service_name, *args, **kwargs)
            region_name = kwargs.get("region_name") or os.environ.get("AWS_DEFAULT_REGION", _region)
            real = _make_dynamo_resource(region_name)
            return _DynamoResourceProxy(real, _map)

        def _wrap_session(self, service_name, *args, **kwargs):
            if service_name != "dynamodb":
                return _orig_ses(self, service_name, *args, **kwargs)
            region_name = kwargs.get("region_name") or os.environ.get("AWS_DEFAULT_REGION", _region)
            real = _make_dynamo_resource(region_name)
            return _DynamoResourceProxy(real, _map)

        boto3.resource = _wrap
        _boto3_session.Session.resource = _wrap_session
    except Exception as exc:  # pragma: no cover
        print(f"  [loader] WARNING: DynamoDB redirect patch failed: {exc}")


_install_dynamo_name_redirect()


def _install_bedrock_model_upgrade():
    """
    Patch strands BedrockModel so that any hardcoded deprecated model ID
    (e.g. claude-3-5-haiku-20241022) is silently upgraded to the working
    equivalent.  Students often hardcode model IDs; env-var injection can't
    fix those.  Installed once at module-import time.
    """
    _UPGRADES = {
        # Haiku 3.5 → Haiku 4.5 (3.5 deprecated in this account)
        "claude-3-5-haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
        # Claude 4 / Sonnet 4 series require Anthropic use-case form approval
        "claude-sonnet-4-20250514": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
        "claude-opus-4":            "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    }

    try:
        from strands.models import BedrockModel as _BM
        _orig_init = _BM.__init__

        def _patched_init(self, *args, **kwargs):
            # model_id can arrive as positional arg[0] or keyword
            model_id = kwargs.get("model_id") or (args[0] if args else "")
            for fragment, replacement in _UPGRADES.items():
                if fragment in str(model_id):
                    print(f"  [loader] BedrockModel model upgrade: {model_id!r} → {replacement!r}")
                    if args:
                        args = (replacement,) + args[1:]
                    else:
                        kwargs["model_id"] = replacement
                    break
            _orig_init(self, *args, **kwargs)

        _BM.__init__ = _patched_init
    except Exception as exc:
        print(f"  [loader] WARNING: BedrockModel upgrade patch failed: {exc}")


_install_bedrock_model_upgrade()


def _install_bedrock_client_normalizer():
    """
    Patch boto3.client('bedrock-runtime') to return a wrapper that normalizes
    request/response formats for teams whose code was written against a different
    API shape than what boto3 actually returns.

    Converse response normalizer (fixes JuanFe / any team that expects the flat format):
      boto3 Converse API returns {'toolUse': {'toolUseId':…, 'name':…, 'input':…}}
      without a top-level 'type' key.  Some teams (and their local mock backends)
      assume a flat format with 'type', 'toolUseId', 'name', 'input' at the top level.
      We add the flat keys IN ADDITION to the nested 'toolUse' key — no existing
      code that accesses block['toolUse'] is broken.

    InvokeModel request normalizer (fixes jorge_andres / teams using Converse format
    sent to invoke_model):
      Teams that use Nova via invoke_model sometimes send Converse-style bodies
      (with 'inferenceConfig') but omit 'type' from content blocks.  Nova's
      invoke_model schema requires 'type'.  We add 'type':'text' to any text-only
      content block that is missing the field.
    """
    try:
        import boto3
        import boto3.session as _boto3_session
        import json as _json

        _orig_client_fn  = boto3.client
        _orig_ses_client = _boto3_session.Session.client

        class _BedrockClientWrapper:
            """Thin wrapper around a boto3 bedrock-runtime client."""
            __slots__ = ("_real",)

            def __init__(self, real):
                object.__setattr__(self, "_real", real)

            # ── Converse: upgrade model ID + normalize response ──────────────
            def converse(self, **kwargs):
                real = object.__getattribute__(self, "_real")
                # Upgrade deprecated model IDs before the call
                _CONVERSE_UPGRADES = {
                    "claude-3-5-haiku": "us.anthropic.claude-haiku-4-5-20251001-v1:0",
                    "claude-sonnet-4-20250514": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                    "claude-opus-4": "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                }
                mid = str(kwargs.get("modelId", ""))
                for frag, repl in _CONVERSE_UPGRADES.items():
                    if frag in mid:
                        print(f"  [loader] Converse model upgrade: {mid!r} → {repl!r}")
                        kwargs = dict(kwargs)
                        kwargs["modelId"] = repl
                        break
                # Strip flat keys we may have injected into previous response blocks.
                # Teams store the normalised response in their conversation history and
                # resend it; AWS rejects blocks that have BOTH a "toolUse" dict AND a
                # flat "type" key (tagged-union violation).
                _FLAT_KEYS = ("type", "toolUseId", "name", "input")
                messages = kwargs.get("messages")
                if messages:
                    cleaned = False
                    new_messages = []
                    for msg in messages:
                        content = msg.get("content", [])
                        if isinstance(content, list):
                            new_content = []
                            for block in content:
                                if isinstance(block, dict) and "toolUse" in block:
                                    block = {k: v for k, v in block.items()
                                             if k not in _FLAT_KEYS}
                                    cleaned = True
                                new_content.append(block)
                            msg = dict(msg); msg["content"] = new_content
                        new_messages.append(msg)
                    if cleaned:
                        kwargs = dict(kwargs); kwargs["messages"] = new_messages
                response = real.converse(**kwargs)
                try:
                    content = (
                        response.get("output", {})
                                .get("message", {})
                                .get("content", [])
                    )
                    for block in content:
                        if isinstance(block, dict) and "toolUse" in block and "type" not in block:
                            tu = block["toolUse"]
                            # Add flat keys alongside the nested toolUse dict
                            block["type"]      = "toolUse"
                            block["toolUseId"] = tu.get("toolUseId", "")
                            block["name"]      = tu.get("name", "")
                            block["input"]     = tu.get("input", {})
                except Exception:
                    pass
                return response

            # ── invoke_model: normalize body format + response ───────────────
            def invoke_model(self, **kwargs):
                real = object.__getattribute__(self, "_real")
                is_converse_fmt = False
                is_claude = False
                try:
                    body = kwargs.get("body", "")
                    model_id = str(kwargs.get("modelId", ""))
                    is_claude = "anthropic" in model_id.lower() or "claude" in model_id.lower()
                    if isinstance(body, (str, bytes)):
                        body_dict = _json.loads(body)
                        is_converse_fmt = (
                            "inferenceConfig" in body_dict
                            or ("messages" in body_dict and "anthropic_version" not in body_dict)
                        )
                        if is_converse_fmt:
                            changed = False

                            # Add type field to content blocks missing it
                            for msg in body_dict.get("messages", []):
                                content = msg.get("content", [])
                                if isinstance(content, list):
                                    for blk in content:
                                        if isinstance(blk, dict) and "type" not in blk:
                                            if "text" in blk:
                                                blk["type"] = "text"
                                                changed = True
                                            elif "toolResult" in blk:
                                                blk["type"] = "toolResult"
                                                changed = True

                            if is_claude and "anthropic_version" not in body_dict:
                                # Convert Converse-style body to Claude Messages API format
                                inf = body_dict.pop("inferenceConfig", {})
                                body_dict["anthropic_version"] = "bedrock-2023-05-31"
                                if "maxTokens" in inf and "max_tokens" not in body_dict:
                                    body_dict["max_tokens"] = inf["maxTokens"]
                                elif "max_tokens" not in body_dict:
                                    body_dict["max_tokens"] = 1024
                                if "temperature" in inf and "temperature" not in body_dict:
                                    body_dict["temperature"] = inf["temperature"]
                                # Do NOT inject top_p — Claude rejects requests with both
                                # temperature AND top_p set simultaneously.
                                # system: [{"text": "..."}] → plain string
                                sys_val = body_dict.get("system")
                                if isinstance(sys_val, list):
                                    texts = [b.get("text", "") for b in sys_val if isinstance(b, dict)]
                                    body_dict["system"] = "\n".join(texts)
                                changed = True

                            if changed:
                                kwargs = dict(kwargs)
                                kwargs["body"] = _json.dumps(body_dict)
                except Exception:
                    pass

                response = real.invoke_model(**kwargs)

                # If body was Converse-format and model is Claude, the raw response
                # is Claude Messages API format.  Re-wrap as Converse output.message
                # so callers that expect output.message.content can parse it.
                if is_converse_fmt and is_claude:
                    try:
                        import io as _io
                        raw_body = response.get("body")
                        if raw_body is not None:
                            raw_bytes = raw_body.read() if hasattr(raw_body, "read") else raw_body
                            resp_dict = _json.loads(raw_bytes)
                            if "content" in resp_dict and "output" not in resp_dict:
                                converse_content = [
                                    {"type": "text", "text": blk.get("text", "")}
                                    for blk in resp_dict.get("content", [])
                                    if isinstance(blk, dict) and blk.get("type") == "text"
                                ]
                                stop_map = {"end_turn": "end_turn", "tool_use": "tool_use",
                                            "max_tokens": "max_tokens"}
                                wrapped = {
                                    "output": {"message": {"role": "assistant", "content": converse_content}},
                                    "stopReason": stop_map.get(resp_dict.get("stop_reason", "end_turn"), "end_turn"),
                                }
                                response = dict(response)
                                response["body"] = _io.BytesIO(_json.dumps(wrapped).encode())
                    except Exception:
                        pass
                return response

            def __getattr__(self, item):
                return getattr(object.__getattribute__(self, "_real"), item)

            def __setattr__(self, key, value):
                setattr(object.__getattribute__(self, "_real"), key, value)

        def _wrap_client(service_name, *args, **kwargs):
            client = _orig_client_fn(service_name, *args, **kwargs)
            if service_name == "bedrock-runtime":
                return _BedrockClientWrapper(client)
            return client

        def _wrap_session_client(self_session, service_name, *args, **kwargs):
            client = _orig_ses_client(self_session, service_name, *args, **kwargs)
            if service_name == "bedrock-runtime":
                return _BedrockClientWrapper(client)
            return client

        boto3.client = _wrap_client
        _boto3_session.Session.client = _wrap_session_client

    except Exception as exc:
        print(f"  [loader] WARNING: Bedrock client normalizer patch failed: {exc}")


_install_bedrock_client_normalizer()


_TRACE_FN_NAMES = ("reset_session", "get_tool_trace", "get_tool_trace_length", "get_tool_trace_since")


def _capture_team_session_fns():
    """
    After a team module is exec'd, sys.modules['core.session_context'] is the
    team's own session_context module (loaded from their core/ directory).

    Capture its trace functions so MultiEngine can read from the SAME instance
    that the team agent writes to.  Returns None if the module lacks the
    standard trace API (engine falls back to its own resolution).

    Supports three patterns:
      Pattern 1 — module-level functions (most teams).
      Pattern 2 — class-based singleton via SessionContext (e.g. JULIAN).
      Flat fallback — teams that import `from session_context import ...` instead of
        `from core.session_context import ...` set sys.modules['session_context'],
        not 'core.session_context' (e.g. Alvaro).
    """
    sc = sys.modules.get("core.session_context")
    if sc is None:
        # Fallback: teams that use flat imports (`from session_context import ...`)
        # never set sys.modules['core.session_context'] — they set the bare name instead.
        sc = sys.modules.get("session_context")
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

    # Backend detection — set by _detect_backend() at load time.
    # Possible values: "bedrock" | "bedrock-nonstandard" | "ollama" | "openai" | "anthropic-direct" | "local-csv" | "unknown"
    backend_tag: str = "unknown"
    # Human-readable note explaining the detection (e.g. "OllamaModel on localhost:11434")
    backend_notes: str = ""

    # README content extracted from submission (empty if none found)
    readme_content: str = ""
    # pip install log (from cache or fresh install); empty if no requirements.txt
    install_log: str = ""
    # Callable that undoes LLM shims installed for this team.  None = no shims.
    # Call before loading the next team to restore the original SDK classes.
    restore_shims: Optional[Callable] = field(default=None, repr=False)

    @property
    def ready(self) -> bool:
        return self.contract_passed and self._create_agent_fn is not None and self.load_error is None

    @property
    def is_nonstandard_backend(self) -> bool:
        """True when the team uses a backend not available in the eval environment."""
        return self.backend_tag not in ("bedrock", "unknown")

    @property
    def uses_athena(self) -> bool:
        return "athena" in self.backend_tag

    def create_agent(self, streaming: bool = False) -> Any:
        if not self.ready:
            raise RuntimeError(f"Submission '{self.team_name}' is not ready: {self.load_error}")
        # Restore this team's core module so lazy relative imports inside
        # create_agent() (e.g. `from .strands_tools import ...`) resolve
        # against the correct core/ directory and not a later team's.
        if self._core_mod is not None:
            sys.modules["core"] = self._core_mod
        _inject_path_permanently(Path(self.root_dir))
        try:
            return self._create_agent_fn(streaming=streaming)
        except TypeError:
            # Student's create_agent() doesn't accept streaming — call without it
            return self._create_agent_fn()

    def summary(self) -> str:
        checks = ", ".join(
            f"{'✓' if c.passed else '✗'} {c.name}"
            for c in self.contract_checks
        )
        status = "LISTO" if self.ready else f"ERROR: {self.load_error or 'contrato incompleto'}"
        backend = f"[{self.backend_tag}]" if self.backend_tag != "unknown" else ""
        suffix = f" ⚠ backend no estándar: {self.backend_notes}" if self.is_nonstandard_backend else ""
        return f"[{self.team_name}]{backend} {status} | {checks}{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────

class SubmissionLoader:
    def __init__(self, submissions_dir: str, work_dir: Optional[str] = None):
        self.submissions_dir = Path(submissions_dir)
        # Use a fixed (stable) temp dir so repeated Streamlit reloads reuse the same
        # directory instead of accumulating ~2GB dirs that fill the disk.
        self.work_dir = Path(work_dir) if work_dir else Path(tempfile.gettempdir()) / "omni_eval_stable"
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir = self.submissions_dir / ".eval_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> List[SubmissionInfo]:
        zips = sorted(self.submissions_dir.glob("*.zip"))
        if not zips:
            return []
        return [self.load_one(zip_path) for zip_path in zips]

    def load_one(self, zip_path: Path) -> SubmissionInfo:
        team_name = _team_name_from_zip(zip_path)
        extract_dir = self.work_dir / team_name

        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
            # If rmtree failed silently (e.g. cpython-3.14 .pyc files with odd
            # permissions), force-remove any surviving files before re-creating.
            if extract_dir.exists():
                import subprocess
                subprocess.run(["rm", "-rf", str(extract_dir)], check=False)
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
        sub.backend_tag, sub.backend_notes = _detect_backend(root_dir)

        # README + requirements (cached per ZIP mtime)
        sub.readme_content = _read_readme(root_dir, team_name, zip_path, self.cache_dir)
        sub.install_log = _install_requirements(root_dir, team_name, zip_path, self.cache_dir)

        # Inject CSV data files for teams that load pandas DataFrames from disk
        csv_dir = _export_dynamo_to_csv(self.cache_dir)
        _setup_athena_if_needed(csv_dir)  # idempotent — creates Glue DB once
        _inject_data_files(root_dir, csv_dir)

        # Step 3: validate contract
        # Required: file present + create_agent defined. streaming is now optional.
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
            sub._create_agent_fn, sub.restore_shims = _dynamic_import_create_agent(
                root_dir, team_name, sub.backend_tag
            )
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


def _detect_backend(root_dir: Path) -> tuple:
    """
    Scan core/agent.py (and requirements.txt if present) to guess the LLM/data backend.

    Returns (tag, notes) where tag is one of:
      "bedrock"          — AWS Bedrock (standard, expected)
      "ollama"           — local Ollama server
      "openai"           — OpenAI API
      "anthropic-direct" — Anthropic API directly (not via Bedrock)
      "local-csv"        — local CSV/pandas data, no cloud DB
      "unknown"          — cannot determine

    Teams using non-standard backends will fail in the eval environment because
    their model endpoint (Ollama, OpenAI key, etc.) is not available here.
    This tag is purely informational — it does NOT change scoring.
    """
    import re

    sources = []
    agent_path = root_dir / "core" / "agent.py"
    if agent_path.exists():
        sources.append(agent_path.read_text(encoding="utf-8", errors="replace"))

    # Also scan requirements.txt if present (anywhere in the root)
    for req_path in (root_dir / "requirements.txt", root_dir.parent / "requirements.txt"):
        if req_path.exists():
            sources.append(req_path.read_text(encoding="utf-8", errors="replace"))

    # Scan provider/backend subdirectories for teams using a factory pattern
    # (e.g. Eider has llm/groq_provider.py but core/agent.py only imports from llm.factory)
    _EXTRA_SCAN_DIRS = ["llm", "providers", "backends", "backend", "models"]
    for subdir in _EXTRA_SCAN_DIRS:
        scan_dir = root_dir / subdir
        if scan_dir.is_dir():
            for py_file in scan_dir.glob("*.py"):
                try:
                    sources.append(py_file.read_text(encoding="utf-8", errors="replace"))
                except Exception:
                    pass

    combined = "\n".join(sources).lower()

    # Ollama — local server, always fails in eval env
    if "ollama" in combined:
        host_match = re.search(r'host\s*[=:]\s*["\']([^"\']+)["\']', combined)
        host = host_match.group(1) if host_match else "localhost:11434"
        return ("ollama", f"OllamaModel en {host}")

    # Groq — check before OpenAI: groq_provider.py imports openai-compat patterns
    # but the intent is clearly Groq if groq SDK or GROQ_API_KEY is referenced.
    if "groq" in combined and (
        "groq_api_key" in combined
        or "groq_provider" in combined
        or re.search(r'(?:from|import)\s+groq\b', combined)
        or "llmprovider=groq" in combined.replace(" ", "").replace("\n", "").lower()
        or "llm_provider.*=.*groq" in re.sub(r'\s+', '', combined).lower()
    ):
        return ("groq", "Groq SDK detectado")

    # OpenAI
    if "openai" in combined:
        return ("openai", "openai SDK detectado")

    # Groq fallback (mentioned but not via explicit import)
    if "groq" in combined:
        return ("groq", "Groq SDK detectado")

    # Anthropic direct (but NOT via Bedrock wrapper)
    if re.search(r'\banthropicbedrock\b|\bbedrockruntime\b|bedrock[-_]runtime|anthropic\.bedrock', combined):
        pass  # falls through to bedrock check below
    elif re.search(r'\bfrom anthropic\b|\bimport anthropic\b', combined):
        return ("anthropic-direct", "Anthropic SDK directo (no Bedrock)")

    # Bedrock — standard (Claude) vs non-standard (Llama, Mistral, Titan, etc.)
    if "bedrock" in combined or "bedrockruntime" in combined or "bedrock-runtime" in combined:
        # Detect non-Claude model identifiers anywhere in source/requirements
        _non_claude = ["meta.llama", "llama3", "llama-3", "mistral", "amazon.titan",
                       "cohere", "ai21", "jamba", "amazon.nova"]
        for marker in _non_claude:
            if marker in combined:
                return ("bedrock-nonstandard", f"Bedrock con modelo no-Claude: {marker}")
        # Bedrock + Athena: LLM is standard but data backend requires our Athena setup
        if "athena" in combined and ("start_query_execution" in combined or "athena_db" in combined or "athena_output" in combined):
            return ("bedrock+athena", "Bedrock Claude + Athena como base de datos")
        return ("bedrock", "")

    # Local CSV / pandas without any cloud DB
    uses_local_data = "pandas" in combined or "read_csv" in combined or ".csv" in combined
    uses_cloud_db = any(kw in combined for kw in ("dynamodb", "athena", "boto3", "dynamo"))
    if uses_local_data and not uses_cloud_db:
        return ("local-csv", "datos locales (CSV/pandas), sin DynamoDB/Athena")

    return ("unknown", "")


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
    # NOTE: this loop must always run — do NOT guard it behind a session_context check,
    # as teams that already have session_context.py (e.g. Samuel) still need
    # __init__.py created in their tools/, utils/, etc. subdirectories.
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
        # For namespace packages (e.g. tools/, __file__ is None but __path__ exists)
        mod_path_strs = [str(p) for p in (getattr(mod, "__path__", None) or [])]
        is_flat_from_other_team = (
            not is_core_ns
            and (
                (mod_file and "omni_eval" in mod_file and str(incoming_root) not in mod_file)
                or any(
                    "omni_eval" in p and str(incoming_root) not in p
                    for p in mod_path_strs
                )
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


def _get_zip_mtime(zip_path: Path) -> str:
    """Return ZIP file modification time as a string (cache key)."""
    try:
        return str(zip_path.stat().st_mtime)
    except Exception:
        return "0"


def _read_readme(root_dir: Path, team_name: str, zip_path: Path, cache_dir: Path) -> str:
    """
    Find and return README content from the team's submission.
    Results are cached in cache_dir/{team_name}/readme.md, keyed by ZIP mtime.
    Returns "" if no README found.
    """
    team_cache = cache_dir / team_name
    team_cache.mkdir(parents=True, exist_ok=True)
    mtime_file = team_cache / "zip_mtime"
    readme_cache = team_cache / "readme.md"

    current_mtime = _get_zip_mtime(zip_path)

    # Return from cache if ZIP hasn't changed
    if mtime_file.exists() and readme_cache.exists():
        if mtime_file.read_text().strip() == current_mtime:
            return readme_cache.read_text(encoding="utf-8", errors="replace")

    # Search for README-like files (breadth-first, stop at first match)
    readme_names = {"readme.md", "readme.txt", "instrucciones.md", "setup.md", "readme.rst"}
    found_content = ""
    for candidate in sorted(root_dir.rglob("*")):
        if candidate.is_file() and candidate.name.lower() in readme_names:
            raw = candidate.read_text(encoding="utf-8", errors="replace")
            found_content = raw[:3000]
            break

    # Write cache (only mtime file if no README, to avoid re-scanning next run)
    mtime_file.write_text(current_mtime)
    readme_cache.write_text(found_content, encoding="utf-8")
    return found_content


def _install_requirements(root_dir: Path, team_name: str, zip_path: Path, cache_dir: Path) -> str:
    """
    Find requirements.txt in the team's submission and pip-install it.
    Skips install if the ZIP mtime matches the cached mtime (already installed).
    Returns pip output log string, or "" if no requirements.txt.
    """
    import subprocess

    team_cache = cache_dir / team_name
    team_cache.mkdir(parents=True, exist_ok=True)
    mtime_file = team_cache / "zip_mtime"
    install_log_file = team_cache / "install.log"

    current_mtime = _get_zip_mtime(zip_path)

    # Return from cache if ZIP hasn't changed and install already ran
    if mtime_file.exists() and install_log_file.exists():
        if mtime_file.read_text().strip() == current_mtime:
            return install_log_file.read_text(encoding="utf-8", errors="replace")

    # Find requirements.txt
    req_path = None
    for candidate in sorted(root_dir.rglob("requirements*.txt")):
        req_path = candidate
        break
    if req_path is None:
        # No requirements.txt — write cache so we don't re-scan
        mtime_file.write_text(current_mtime)
        install_log_file.write_text("")
        return ""

    print(f"  [loader] Installing requirements from {req_path.relative_to(root_dir)} …")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q",
             "--no-warn-script-location", "-r", str(req_path)],
            capture_output=True, text=True, timeout=120,
        )
        log = (result.stdout + result.stderr).strip()
        status = "OK" if result.returncode == 0 else f"exit={result.returncode}"
        full_log = f"[{status}] {req_path.name}\n{log}"
    except subprocess.TimeoutExpired:
        full_log = "[TIMEOUT] pip install exceeded 120s"
    except Exception as e:
        full_log = f"[ERROR] pip install failed: {e}"

    # Write cache
    mtime_file.write_text(current_mtime)
    install_log_file.write_text(full_log, encoding="utf-8")
    print(f"  [loader] Install done: {full_log.splitlines()[0]}")
    return full_log


def _inject_aws_env(backend_tag: str = "unknown") -> dict:
    """
    Pre-inject our AWS credentials and infrastructure config into os.environ
    BEFORE loading the team's module.  python-dotenv's load_dotenv() (without
    override=True) respects existing env vars, so our values will be preserved
    even when a team's .env has blank or wrong AWS keys.

    Also injects standard DynamoDB table-name env vars pointing to our Retail*
    tables, using setdefault so a team can still override them if they wish.

    Returns a snapshot of our current AWS creds (used by _post_clean_aws_env).
    """
    _AWS_CRED_KEYS = [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "AWS_DEFAULT_REGION", "AWS_REGION", "AWS_PROFILE",
    ]
    snapshot = {}
    for key in _AWS_CRED_KEYS:
        val = os.environ.get(key)
        if val:
            snapshot[key] = val
            os.environ[key] = val  # explicit re-set (prevents accidental unset)

    # Protect judge credentials from team .env contamination.
    # Use values locked at module-import time, captured before any team's load_dotenv()
    # or the Valen-Agentic setdefault below can change AWS_PROFILE / MODEL_ID.
    os.environ["EVAL_JUDGE_MODEL_ID"] = _LOCKED_JUDGE_MODEL
    os.environ["EVAL_JUDGE_REGION"]   = _LOCKED_JUDGE_REGION
    os.environ["EVAL_JUDGE_PROFILE"]  = _LOCKED_JUDGE_PROFILE

    # Ensure region has a default so boto3 clients created at module level work.
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")
    os.environ.setdefault("AWS_REGION", "us-east-2")

    # Our DynamoDB tables use omniretail_* naming.  Inject all common table-name env
    # var patterns so any team that reads table names from the environment will
    # automatically point at our data — without overriding a team's explicit choice.
    _DYNAMO_DEFAULTS = {
        # Prefix pattern (teams using f"{prefix}customers" etc.)
        "DYNAMO_PREFIX":            "omniretail_",
        "DYNAMODB_TABLE_PREFIX":    "omniretail_",
        # Explicit per-table pattern (e.g. juan_david_vela's aws_client.py)
        "DYNAMO_TABLE_CUSTOMERS":   "omniretail_customers",
        "DYNAMO_TABLE_ORDERS":      "omniretail_orders",
        "DYNAMO_TABLE_ORDER_ITEMS": "omniretail_order_items",
        "DYNAMO_TABLE_PRODUCTS":    "omniretail_products",
        "DYNAMO_TABLE_STOCK":       "omniretail_stock",
        "DYNAMO_TABLE_SHIPMENTS":   "omniretail_shipments",
    }
    for key, val in _DYNAMO_DEFAULTS.items():
        os.environ.setdefault(key, val)

    # Athena defaults — dataton-db already exists in the correct account.
    _ATHENA_BUCKET = "dataton-challenge-unicauca"
    os.environ.setdefault("ATHENA_OUTPUT",    f"s3://{_ATHENA_BUCKET}/athena-results/")
    os.environ.setdefault("ATHENA_S3_OUTPUT", f"s3://{_ATHENA_BUCKET}/athena-results/")
    os.environ.setdefault("ATHENA_WORKGROUP", "primary")
    # dataton-db has a hyphen which is invalid in Athena SQL without quoting.
    # _setup_athena_if_needed() creates dataton_db (underscore) as an alias.
    os.environ.setdefault("ATHENA_DB",       "dataton_db")
    os.environ.setdefault("ATHENA_DATABASE", "dataton_db")

    # Policy S3 — policies live in dataton-challenge-unicauca/dataton-policies/
    os.environ.setdefault("POLICIES_S3_BUCKET", _ATHENA_BUCKET)
    os.environ.setdefault("POLICIES_S3_PREFIX", "dataton-policies/")
    os.environ.setdefault("POLICY_S3_BUCKET",   _ATHENA_BUCKET)
    os.environ.setdefault("POLICY_S3_PREFIX",   "dataton-policies/")

    # DynamoDB session table (JULIAN_DAVID uses strata_sessions)
    os.environ.setdefault("DYNAMO_SESSION_TABLE", "strata_sessions")

    # LLM provider — inject a value matching the detected backend so factory-pattern
    # teams (e.g. Eider with llm/factory.py) get the right provider string.
    # For groq/openai/ollama backends we keep their native provider name; the shim
    # then intercepts that provider's SDK and routes it to Bedrock transparently.
    _llm_provider_default = {
        "groq":             "groq",
        "openai":           "openai",
        "ollama":           "ollama",
        "anthropic-direct": "anthropic",
    }.get(backend_tag, "bedrock")
    os.environ.setdefault("LLM_PROVIDER", _llm_provider_default)
    os.environ.setdefault("LLM_BACKEND",  _llm_provider_default)

    # Bedrock model IDs — inject our known-working model so teams that read their
    # model from env vars get a model accessible in this account.
    # setdefault: teams whose shell already has a valid model keep it.
    # This runs BEFORE team's load_dotenv(), so the injected values survive
    # load_dotenv(override=False) calls in team code.
    _WORKING_MODEL = _LOCKED_JUDGE_MODEL  # same as what the project/judge uses
    _WORKING_HAIKU = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    for _mk in ("BEDROCK_MODEL_ID", "CLAUDE_MODEL_ID", "CLAUDE_MODEL",
                "LLM_MODEL_ID", "MODEL_ID"):
        os.environ.setdefault(_mk, _WORKING_MODEL)
    # Haiku 3.5 is deprecated — force-upgrade any existing env var that still
    # references it.  Use os.environ[] (not setdefault) so shell-level values
    # like MODEL_HAIKU=claude-3-5-haiku-... are also replaced.
    _DEPRECATED_HAIKU = "claude-3-5-haiku"
    for _mk in ("MODEL_HAIKU", "HAIKU_MODEL", "BEDROCK_HAIKU_MODEL",
                "CLAUDE_HAIKU_MODEL", "BEDROCK_HAIKU_MODEL_ID"):
        current = os.environ.get(_mk, "")
        if not current or _DEPRECATED_HAIKU in current:
            os.environ[_mk] = _WORKING_HAIKU

    # ── Multi-backend / factory-pattern teams ───────────────────────────────
    # Teams that read a PROVIDER/BACKEND env var to choose their LLM backend
    # (e.g. Harold reads PROVIDER from config.py).  Force Bedrock so they use
    # the working AWS path instead of Anthropic/Ollama/OpenAI.
    os.environ.setdefault("PROVIDER",    "bedrock")
    os.environ.setdefault("USE_BEDROCK", "true")
    os.environ.setdefault("BACKEND",     "bedrock")

    # Catalina_Torres reads AGENT_LLM_PROVIDER (default 'openai') and
    # AGENT_LLM_MODEL from env.  Her build_llm_client() has a native Bedrock path.
    os.environ.setdefault("AGENT_LLM_PROVIDER", "bedrock")
    os.environ.setdefault("AGENT_LLM_MODEL",    _WORKING_MODEL)

    # Juan_Martin_Paz activates his StrandsLLMAgent when OMNIRETAIL_STRANDS_MODEL
    # is set (otherwise falls back to deterministic agent).
    os.environ.setdefault("OMNIRETAIL_STRANDS_MODEL",   _WORKING_MODEL)
    os.environ.setdefault("OMNIRETAIL_STRANDS_BACKEND", "bedrock")

    # daniel_ceron: USE_LOCAL_MODEL=1 switches to OllamaModel.  Keep it off so
    # the primary BedrockModel path is used (shim also covers it as backup).
    os.environ.setdefault("USE_LOCAL_MODEL", "0")

    # Sofia_Moreno checks GROQ_API_KEY at create_agent time and raises ValueError
    # if missing.  A placeholder satisfies the check; the OpenAI/Groq shim
    # intercepts the actual call and routes it to Bedrock — the value is unused.
    os.environ.setdefault("GROQ_API_KEY", "eval-shim-placeholder")

    # juan_sebastian_muñoz uses multiple Bedrock model env vars pointing to
    # mistral.ministral-3-8b-instruct which is inaccessible here.  Redirect to Haiku.
    os.environ.setdefault("BEDROCK_CONSOLIDATOR_MODEL_ID",               _WORKING_HAIKU)
    os.environ.setdefault("BEDROCK_CATALOG_CONSOLIDATOR_MODEL_ID",       _WORKING_HAIKU)
    os.environ.setdefault("BEDROCK_CATALOG_DETAIL_CONSOLIDATOR_MODEL_ID", _WORKING_HAIKU)

    return snapshot


def _post_clean_aws_env(snapshot: dict):
    """
    After team module loads (and their load_dotenv() may have run), fully restore
    the AWS environment to its pre-load state.

    Simply removing empty-string vars is not enough: teams with static AKIA*
    credentials in their .env will overwrite our SSO/profile-based creds, causing
    subsequent BedrockModel calls to hit the wrong AWS account.  Full restoration
    guarantees our evaluator credentials are always in effect after loading any team.
    """
    _CRITICAL = [
        "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
        "AWS_PROFILE",
    ]
    for key in _CRITICAL:
        if key in snapshot:
            # We had this key before team load — restore it unconditionally.
            os.environ[key] = snapshot[key]
        elif key in os.environ:
            # Team introduced this key (e.g. static AKIA* cred) and we didn't
            # have it — purge it so it can't contaminate the next team's load.
            os.environ.pop(key, None)


def _export_dynamo_to_csv(cache_dir: Path) -> Path:
    """
    Export all Retail* DynamoDB tables to CSV files in cache_dir/dynamo_csv/.
    Returns the directory path.  Files are only re-exported if missing or stale
    (checked by presence of a sentinel file).

    Missing / non-existent tables get an empty placeholder CSV so pandas.read_csv
    succeeds and returns an empty DataFrame.
    """
    import csv as _csv

    csv_dir = cache_dir / "dynamo_csv"
    sentinel = csv_dir / ".exported"
    if sentinel.exists():
        return csv_dir

    csv_dir.mkdir(parents=True, exist_ok=True)
    try:
        import boto3
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        dynamo = boto3.resource("dynamodb", region_name=region)

        _TABLES = [
            ("omniretail_customers",       "customers"),
            ("omniretail_orders",          "orders"),
            ("omniretail_order_items",     "order_items"),
            ("omniretail_products",        "products"),
            ("omniretail_stock",           "stock"),
            ("omniretail_shipments",       "shipments"),
            ("omniretail_addresses",       "addresses"),
            ("omniretail_brands",          "brands"),
            ("omniretail_cards",           "cards"),
            ("omniretail_categories",      "categories"),
            ("omniretail_customer_emails", "customer_emails"),
            ("omniretail_promotions",      "promotions"),
            ("omniretail_tracking",        "tracking"),
        ]

        for ddb_name, csv_stem in _TABLES:
            dest = csv_dir / f"{csv_stem}.csv"
            try:
                table = dynamo.Table(ddb_name)
                items = []
                kwargs: dict = {}
                while True:
                    resp = table.scan(**kwargs)
                    items.extend(resp.get("Items", []))
                    last = resp.get("LastEvaluatedKey")
                    if not last:
                        break
                    kwargs["ExclusiveStartKey"] = last

                if items:
                    all_keys = list(dict.fromkeys(k for item in items for k in item))
                    with open(dest, "w", newline="", encoding="utf-8") as f:
                        writer = _csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
                        writer.writeheader()
                        for item in items:
                            writer.writerow({k: item.get(k, "") for k in all_keys})
                    print(f"  [loader] Exported {ddb_name} → {csv_stem}.csv ({len(items)} rows)")
                else:
                    # Empty table — write header-only CSV
                    dest.write_text("id\n", encoding="utf-8")
            except Exception as e:
                print(f"  [loader] WARNING: could not export {ddb_name}: {e}")
                dest.write_text("id\n", encoding="utf-8")

        sentinel.touch()
    except Exception as e:
        print(f"  [loader] WARNING: DynamoDB export failed — {e}")

    return csv_dir


def _setup_athena_if_needed(csv_dir: Path) -> str:
    """
    Create dataton_db (underscore) as a Glue alias for dataton-db (hyphen).
    Athena/Presto SQL cannot parse a hyphen in an unquoted identifier, so student
    queries like `SELECT ... FROM dataton-db.customers` fail with a parse error.
    This copies all table definitions from dataton-db → dataton_db once (sentinel-gated).
    Returns the alias DB name.
    """
    _SOURCE_DB = "dataton-db"
    _ALIAS_DB  = "dataton_db"

    sentinel = csv_dir / ".athena_alias_ready"
    if sentinel.exists():
        os.environ.setdefault("ATHENA_DB",       _ALIAS_DB)
        os.environ.setdefault("ATHENA_DATABASE", _ALIAS_DB)
        return _ALIAS_DB

    try:
        import boto3 as _boto3
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-2")
        glue   = _boto3.client("glue", region_name=region)

        # Create alias database
        try:
            glue.create_database(DatabaseInput={"Name": _ALIAS_DB})
            print(f"  [loader] Created Glue alias DB: {_ALIAS_DB}")
        except glue.exceptions.AlreadyExistsException:
            print(f"  [loader] Glue alias DB already exists: {_ALIAS_DB}")

        # Copy table definitions from dataton-db → dataton_db
        paginator = glue.get_paginator("get_tables")
        copied = 0
        for page in paginator.paginate(DatabaseName=_SOURCE_DB):
            for table in page["TableList"]:
                table_input = {
                    "Name": table["Name"],
                    "StorageDescriptor": table["StorageDescriptor"],
                    "TableType": table.get("TableType", "EXTERNAL_TABLE"),
                    "Parameters": table.get("Parameters", {}),
                }
                try:
                    glue.create_table(DatabaseName=_ALIAS_DB, TableInput=table_input)
                    copied += 1
                except glue.exceptions.AlreadyExistsException:
                    pass

        print(f"  [loader] Alias DB '{_ALIAS_DB}' ready ({copied} tables copied from {_SOURCE_DB})")
        sentinel.touch()
    except Exception as e:
        print(f"  [loader] WARNING: Athena alias setup failed — {e}")
        _ALIAS_DB = _SOURCE_DB  # fall back to hyphenated name

    os.environ.setdefault("ATHENA_DB",       _ALIAS_DB)
    os.environ.setdefault("ATHENA_DATABASE", _ALIAS_DB)
    return _ALIAS_DB


def _inject_data_files(root_dir: Path, csv_dir: Path):
    """
    Copy our exported CSV files into the team's data directory so CSV-loading
    agents (those that do `pd.read_csv('data/customers.csv')`) can run.

    Supports two patterns:
      - root_dir/data/*.csv           (MARIA_PAULA pattern)
      - root_dir/data/datasets/*.csv  (valentina_balcazar pattern)

    Only copies if the team's data dir exists (created during extraction) OR
    if the team's source code references .csv files (we create the directory).
    """
    import shutil as _shutil

    if not csv_dir.exists():
        return

    # Detect if team uses CSV data (check source code)
    agent_path = root_dir / "core" / "agent.py"
    team_sources = list(root_dir.rglob("*.py"))
    combined = ""
    for p in team_sources[:20]:
        try:
            combined += p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    if ".csv" not in combined and "read_csv" not in combined:
        return  # Team doesn't use CSV — skip

    # Candidate data directories
    data_dirs = [
        root_dir / "data",
        root_dir / "data" / "datasets",
    ]
    for data_dir in data_dirs:
        data_dir.mkdir(parents=True, exist_ok=True)
        for csv_src in csv_dir.glob("*.csv"):
            dest = data_dir / csv_src.name
            if not dest.exists():
                _shutil.copy2(csv_src, dest)

    print(f"  [loader] Injected CSV data files into {root_dir.name}/data/")


# ─────────────────────────────────────────────────────────────────────────────
# LLM Backend Shims
# Route non-Bedrock LLM calls through AWS Bedrock transparently.
# Students couldn't get AWS accounts, so many used Ollama/Groq/OpenAI/Anthropic.
# These shims intercept those SDK calls and redirect them to our Bedrock model,
# making their agents work in our eval environment without any code changes.
# ─────────────────────────────────────────────────────────────────────────────

_SHIM_BEDROCK_MODEL  = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
_SHIM_BEDROCK_REGION = "us-east-2"


def _make_openai_sdk_shim():
    """
    Returns a class mimicking openai.OpenAI.
    .chat.completions.create() calls Bedrock invoke_model (Anthropic format).
    Response has .choices[0].message.content  (str).
    """
    import boto3 as _boto3, json as _json

    class _Msg:
        def __init__(self, content):
            self.content  = content
            self.role     = "assistant"
            self.tool_calls = []

    class _Choice:
        def __init__(self, content):
            self.message      = _Msg(content)
            self.finish_reason = "stop"
            self.index        = 0

    class _Completion:
        def __init__(self, content):
            self.choices = [_Choice(content)]
            self.model   = _SHIM_BEDROCK_MODEL

    class _Completions:
        def create(self, model=None, messages=None, **kwargs):
            region   = os.getenv("AWS_DEFAULT_REGION", _SHIM_BEDROCK_REGION)
            model_id = os.getenv("EVAL_JUDGE_MODEL_ID", _SHIM_BEDROCK_MODEL)
            client   = _boto3.client("bedrock-runtime", region_name=region)
            msgs = [m for m in (messages or []) if m.get("role") != "system"]
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": kwargs.get("max_tokens", 512),
                "messages": [{"role": m["role"], "content": m.get("content", "")} for m in msgs],
            }
            sys_msgs = [m["content"] for m in (messages or []) if m.get("role") == "system"]
            if sys_msgs:
                body["system"] = "\n".join(sys_msgs)
            resp   = client.invoke_model(modelId=model_id, body=_json.dumps(body),
                                         contentType="application/json", accept="application/json")
            text   = _json.loads(resp["body"].read()).get("content", [{}])[0].get("text", "")
            return _Completion(text)

    class _Chat:
        completions = _Completions()

    class _BedrockOpenAIShim:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    return _BedrockOpenAIShim


def _make_groq_sdk_shim():
    """
    Returns a class mimicking groq.Groq.
    .chat.completions.create() calls Bedrock Converse API (supports tool_calls).
    Response mimics OpenAI ChatCompletion: .choices[0].message.{content, tool_calls}.
    """
    import boto3 as _boto3, json as _json

    class _ToolFn:
        def __init__(self, name, args):
            self.name      = name
            self.arguments = args   # JSON string

    class _ToolCall:
        def __init__(self, id_, name, args):
            self.id       = id_
            self.type     = "function"
            self.function = _ToolFn(name, args)

    class _Msg:
        def __init__(self, content, tool_calls=None):
            self.content    = content
            self.tool_calls = tool_calls or []
            self.role       = "assistant"

    class _Choice:
        def __init__(self, content, tool_calls=None):
            self.message      = _Msg(content, tool_calls)
            self.finish_reason = "tool_calls" if tool_calls else "stop"
            self.index        = 0

    class _Completion:
        def __init__(self, content, tool_calls=None):
            self.choices = [_Choice(content, tool_calls)]
            self.model   = _SHIM_BEDROCK_MODEL

    class _Completions:
        def create(self, model=None, messages=None, tools=None,
                   tool_choice="auto", temperature=0, **kwargs):
            region   = os.getenv("AWS_DEFAULT_REGION", _SHIM_BEDROCK_REGION)
            model_id = os.getenv("EVAL_JUDGE_MODEL_ID", _SHIM_BEDROCK_MODEL)
            client   = _boto3.client("bedrock-runtime", region_name=region)

            converse_msgs, sys_prompt = [], None
            for m in (messages or []):
                role, content = m.get("role"), m.get("content", "")
                if role == "system":
                    sys_prompt = content
                elif role in ("user", "assistant"):
                    converse_msgs.append({
                        "role": role,
                        "content": [{"text": content}] if isinstance(content, str) else content,
                    })

            kw = {"modelId": model_id, "messages": converse_msgs}
            if sys_prompt:
                kw["system"] = [{"text": sys_prompt}]
            if tools:
                kw["toolConfig"] = {"tools": [
                    {"toolSpec": {
                        "name":        t.get("function", {}).get("name", ""),
                        "description": t.get("function", {}).get("description", ""),
                        "inputSchema": {"json": t.get("function", {}).get("parameters",
                                                      {"type": "object", "properties": {}})},
                    }} for t in tools
                ]}

            resp    = client.converse(**kw)
            blocks  = resp.get("output", {}).get("message", {}).get("content", [])
            texts, tcs = [], []
            for b in blocks:
                if "text" in b:
                    texts.append(b["text"])
                elif "toolUse" in b:
                    tu = b["toolUse"]
                    tcs.append(_ToolCall(tu.get("toolUseId", "tc0"),
                                         tu.get("name", ""),
                                         _json.dumps(tu.get("input", {}))))
            return _Completion("\n".join(texts) if texts else None, tcs or None)

    class _Chat:
        completions = _Completions()

    class _BedrockGroqShim:
        def __init__(self, **kwargs):
            self.chat = _Chat()

    return _BedrockGroqShim


def _make_anthropic_sdk_shim():
    """
    Returns a class mimicking anthropic.Anthropic.
    .messages.create() calls Bedrock invoke_model (Anthropic format).
    Response has .content[0].text  (str).
    """
    import boto3 as _boto3, json as _json

    class _Block:
        def __init__(self, text):
            self.type = "text"
            self.text = text

    class _Response:
        def __init__(self, text):
            self.content     = [_Block(text)]
            self.stop_reason = "end_turn"
            self.model       = _SHIM_BEDROCK_MODEL

    class _Messages:
        def create(self, model=None, messages=None, max_tokens=1024, **kwargs):
            region   = os.getenv("AWS_DEFAULT_REGION", _SHIM_BEDROCK_REGION)
            model_id = os.getenv("EVAL_JUDGE_MODEL_ID", _SHIM_BEDROCK_MODEL)
            client   = _boto3.client("bedrock-runtime", region_name=region)
            body = {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "messages": messages or [],
            }
            if kwargs.get("system"):
                body["system"] = kwargs["system"]
            resp = client.invoke_model(modelId=model_id, body=_json.dumps(body),
                                        contentType="application/json", accept="application/json")
            text = _json.loads(resp["body"].read()).get("content", [{}])[0].get("text", "")
            return _Response(text)

        def stream(self, **kwargs):
            """Context manager mimicking anthropic SDK's messages.stream().
            Supports: with client.messages.stream(...) as s:
                s.text_stream (iterator), s.get_final_message(), iter(s)
            """
            from contextlib import contextmanager as _cm

            @_cm
            def _stream_ctx():
                result = self.create(**kwargs)
                text = result.content[0].text if result.content else ""

                class _StreamWrapper:
                    def __init__(self):
                        self.text_stream = iter([text])
                        self._msg = result
                    def get_final_message(self):
                        return self._msg
                    def get_final_text(self):
                        return text
                    def __iter__(self):
                        return iter([text])

                yield _StreamWrapper()

            return _stream_ctx()

    class _BedrockAnthropicShim:
        def __init__(self, **kwargs):
            self.messages = _Messages()

    return _BedrockAnthropicShim


def _maybe_restore_previous_shims():
    """Undo LLM shims from the previously loaded team, if any."""
    global _active_restore_shims
    if _active_restore_shims is not None:
        print("  [shim] Restoring previous team's LLM shims")
        try:
            _active_restore_shims()
        except Exception as exc:
            print(f"  [shim] WARNING: restore failed: {exc}")
        _active_restore_shims = None


def _install_llm_shims(backend_tag: str) -> Optional[Callable]:
    """
    Monkey-patch non-Bedrock LLM SDK classes to route through AWS Bedrock.
    Must be called AFTER _inject_aws_env() and BEFORE exec_module().

    Returns a zero-arg callable that undoes all patches, or None if no patches
    were needed (standard Bedrock team or unknown).

    Patches are applied globally but are undone when the next team loads,
    so they are effectively per-team for sequential evaluation.
    """
    # Always shim anthropic.Anthropic for teams whose config.py hardcodes
    # PROVIDER="anthropic" even when their agent.py imports Bedrock (e.g. Harold).
    # Backend detection only scans agent.py/requirements.txt, not config.py, so
    # these teams are tagged "bedrock" while still instantiating anthropic.Anthropic.
    # Shimming is safe for all tags — pure-bedrock teams don't import anthropic.
    _always_restorers: list = []

    def _patch_unconditional(obj, attr, replacement):
        original = getattr(obj, attr, _SHIM_MISSING)
        setattr(obj, attr, replacement)
        def _undo():
            if original is _SHIM_MISSING:
                try:
                    delattr(obj, attr)
                except AttributeError:
                    pass
            else:
                setattr(obj, attr, original)
        _always_restorers.append(_undo)

    try:
        import anthropic as _ant_always
        _patch_unconditional(_ant_always, "Anthropic", _make_anthropic_sdk_shim())
        if hasattr(_ant_always, "AsyncAnthropic"):
            _patch_unconditional(_ant_always, "AsyncAnthropic", _make_anthropic_sdk_shim())
    except ImportError:
        pass

    if backend_tag in ("bedrock", "bedrock-nonstandard", "bedrock+athena", "unknown", "local-csv"):
        if _always_restorers:
            def _restore_always():
                for fn in reversed(_always_restorers):
                    try:
                        fn()
                    except Exception:
                        pass
            return _restore_always
        return None

    restorers: list = _always_restorers

    def _patch(obj, attr, replacement):
        original = getattr(obj, attr, _SHIM_MISSING)
        setattr(obj, attr, replacement)
        def _undo():
            if original is _SHIM_MISSING:
                try:
                    delattr(obj, attr)
                except AttributeError:
                    pass
            else:
                setattr(obj, attr, original)
        restorers.append(_undo)

    # Helper: shim a strands model class and update its parent module __dict__
    def _shim_strands_class(submodule_name: str, class_name: str, shim_class):
        try:
            import strands.models as _sm
            submod = importlib.import_module(f"strands.models.{submodule_name}")
            _patch(submod, class_name, shim_class)
            orig_dict = _sm.__dict__.get(class_name, _SHIM_MISSING)
            _sm.__dict__[class_name] = shim_class
            def _undo_dict():
                if orig_dict is _SHIM_MISSING:
                    _sm.__dict__.pop(class_name, None)
                else:
                    _sm.__dict__[class_name] = orig_dict
            restorers.append(_undo_dict)
        except ImportError:
            pass

    # ── Strands shim factories (reuse BedrockModel directly) ─────────────────
    def _make_strands_bedrock_shim(display_name: str):
        """Returns a class whose __new__ returns a BedrockModel instance."""
        class _StrandsBedrockShim:
            def __new__(cls, *args, **kwargs):
                from strands.models import BedrockModel
                model_id = os.getenv("EVAL_JUDGE_MODEL_ID", _SHIM_BEDROCK_MODEL)
                region   = os.getenv("AWS_DEFAULT_REGION", _SHIM_BEDROCK_REGION)
                print(f"  [shim] {display_name}({kwargs.get('model_id', '')!r}) → BedrockModel({model_id!r})")
                return BedrockModel(model_id=model_id, region_name=region)
        _StrandsBedrockShim.__name__ = display_name
        return _StrandsBedrockShim

    # ── Ollama backend ────────────────────────────────────────────────────────
    if backend_tag == "ollama":
        _shim_strands_class("ollama", "OllamaModel", _make_strands_bedrock_shim("OllamaModel"))
        # Some teams (e.g. Harold) have PROVIDER="anthropic" hardcoded in config.py —
        # env var injection can't override Python module constants.  Patch AnthropicModel
        # and the anthropic SDK so their Anthropic path also routes through Bedrock.
        _shim_strands_class("anthropic", "AnthropicModel", _make_strands_bedrock_shim("AnthropicModel"))
        try:
            import anthropic as _ant
            _patch(_ant, "Anthropic", _make_anthropic_sdk_shim())
            if hasattr(_ant, "AsyncAnthropic"):
                _patch(_ant, "AsyncAnthropic", _make_anthropic_sdk_shim())
        except ImportError:
            pass

    # ── OpenAI / Groq backend ─────────────────────────────────────────────────
    if backend_tag == "openai":
        _shim_strands_class("openai", "OpenAIModel", _make_strands_bedrock_shim("OpenAIModel"))
        try:
            import openai as _oai
            _patch(_oai, "OpenAI", _make_openai_sdk_shim())
            if hasattr(_oai, "AsyncOpenAI"):
                _patch(_oai, "AsyncOpenAI", _make_openai_sdk_shim())
        except ImportError:
            pass

    # ── Anthropic-direct backend ──────────────────────────────────────────────
    if backend_tag == "anthropic-direct":
        _shim_strands_class("anthropic", "AnthropicModel", _make_strands_bedrock_shim("AnthropicModel"))
        try:
            import anthropic as _ant
            _patch(_ant, "Anthropic", _make_anthropic_sdk_shim())
            if hasattr(_ant, "AsyncAnthropic"):
                _patch(_ant, "AsyncAnthropic", _make_anthropic_sdk_shim())
        except ImportError:
            pass

    # ── Groq backend (Sofia_Moreno, Eider_Yesid — use Groq SDK as LLM) ───────
    if backend_tag == "groq":
        try:
            import groq as _groq
            _patch(_groq, "Groq", _make_groq_sdk_shim())
            if hasattr(_groq, "AsyncGroq"):
                _patch(_groq, "AsyncGroq", _make_groq_sdk_shim())
        except ImportError:
            pass


    if not restorers:
        return None

    def _restore_all():
        for undo_fn in reversed(restorers):
            try:
                undo_fn()
            except Exception as exc:
                print(f"  [shim] WARNING: restore error: {exc}")

    print(f"  [shim] Installed LLM shims for backend_tag={backend_tag!r}")
    return _restore_all


def _dynamic_import_create_agent(root_dir: Path, team_name: str, backend_tag: str = "unknown") -> tuple:
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

    # Pre-inject our AWS credentials BEFORE exec_module so that team's load_dotenv()
    # (which by default doesn't override existing env vars) keeps our values.
    aws_snapshot = _inject_aws_env(backend_tag=backend_tag)

    # Install LLM shims BEFORE exec_module so that `from strands.models.ollama import
    # OllamaModel` (and similar) at module level resolve to our shim classes.
    # Shims stay active after exec_module (needed when the agent is actually called).
    _maybe_restore_previous_shims()
    _restore_fn = _install_llm_shims(backend_tag)
    global _active_restore_shims
    _active_restore_shims = _restore_fn

    import builtins
    import threading
    _load_exc: list = []

    _base_dict = {k: v for k, v in module.__dict__.items()}

    # Mock builtins.input to return "salir"/"exit" so modules with an interactive
    # REPL loop at module level (e.g. `while True: input(...)`) don't block forever.
    # The mock is installed for the duration of exec_module and then removed.
    _real_input = builtins.input

    def _stub_input(prompt=""):
        print(f"  [loader] input() intercepted (prompt={str(prompt)[:60]!r}) → returning 'salir'")
        return "salir"

    def _do_exec():
        # Retry loop: on NameError for an annotation-only type (e.g. `-> AgentResponse`)
        # inject a stub class and retry.  Avoids needing `from __future__ import annotations`
        # which breaks students who already have it in a non-first position.
        _stubs: dict = {}
        builtins.input = _stub_input
        try:
            for _attempt in range(8):
                try:
                    if _attempt > 0:
                        module.__dict__.clear()
                        module.__dict__.update(_base_dict)
                        module.__dict__.update(_stubs)
                    spec.loader.exec_module(module)
                    return
                except NameError as exc:
                    import re as _re
                    m = _re.search(r"name '(\w+)' is not defined", str(exc))
                    if not m or m.group(1) in _stubs:
                        _load_exc.append(exc)
                        return
                    stub_name = m.group(1)
                    _stubs[stub_name] = type(stub_name, (), {})
                    print(f"  [loader] Injected annotation stub for '{stub_name}'")
                except Exception as exc:
                    _load_exc.append(exc)
                    return
            _load_exc.append(RuntimeError("Demasiadas anotaciones de tipo sin definir"))
        finally:
            builtins.input = _real_input

    _t = threading.Thread(target=_do_exec, daemon=True)
    _t.start()
    _t.join(timeout=90)  # 90 s max — some teams run DynamoDB data loading at startup
    if _t.is_alive():
        raise TimeoutError(
            "El módulo tardó más de 90 s en cargar (posible bloqueo en inicialización de DynamoDB o red)"
        )
    if _load_exc:
        raise _load_exc[0]

    # Post-clean: remove any empty-string AWS vars a student's .env may have set,
    # which would cause boto3 to fail instead of falling back to the credential chain.
    _post_clean_aws_env(aws_snapshot)

    if not hasattr(module, "create_agent"):
        raise AttributeError("create_agent no encontrado en el módulo cargado")

    return module.create_agent, _restore_fn
