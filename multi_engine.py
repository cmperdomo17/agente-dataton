import sys
import time
from typing import Any, Callable, Dict, List, Optional


class MultiEngine:

    # Error substrings that indicate a connectivity or model-compatibility failure.
    _INFRA_ERROR_PATTERNS = (
        # Connectivity failures (local model server not reachable)
        "connecterror", "connection error", "connection refused",
        "cannot connect", "connection timed out", "remotedisconnected",
        "httpsconnectionpool", "connectionpool",
        "name or service not known", "nodename nor servname",
        # Bedrock Converse API protocol errors caused by non-Claude model incompatibilities
        # (e.g. Llama3 via Strands doesn't format conversation/tool turns correctly)
        "conversation blocks and tool result blocks",
        "cannot be provided in the same turn",
        "content field in the message object",
        "add a contentblock",
        # Athena infrastructure mismatches (wrong output bucket, missing DB/tables)
        "unable to verify/create output bucket",
        "start_query_execution",  # boto3 method call in step feedback
        "startqueryexecution",    # AWS SDK error message operation name
        "database not found",
        "glue catalog",
    )

    def __init__(
        self,
        create_agent_fn: Callable,
        pass_threshold: int = 80,
        team_name: str = "unknown",
        session_fns: Optional[Dict[str, Callable]] = None,
        backend_tag: str = "unknown",
        backend_notes: str = "",
    ):
        # Imports diferidos de jueces — evita cargar boto3 al arrancar Streamlit
        from evaluator.judges.security_judge import SecurityJudge
        from evaluator.judges.business_judge import BusinessJudge
        from evaluator.judges.data_judge import DataJudge
        from evaluator.judges.rag_judge import RagJudge
        from evaluator.judges.memory_judge import MemoryJudge

        self.create_agent_fn = create_agent_fn
        self.pass_threshold = pass_threshold
        self.team_name = team_name
        self.backend_tag = backend_tag
        self.backend_notes = backend_notes
        self.is_nonstandard_backend = backend_tag not in ("bedrock", "unknown")
        self.judges = {
            "security": SecurityJudge(),
            "business": BusinessJudge(),
            "rag":      RagJudge(),
            "data":     DataJudge(),
            "memory":   MemoryJudge(),
        }

        # session_context functions — inyectadas desde afuera o resueltas aquí
        # Se resuelven UNA SOLA VEZ, antes de que se cargue el ZIP del equipo.
        if session_fns:
            self._reset_session       = session_fns["reset_session"]
            self._get_tool_trace      = session_fns["get_tool_trace"]
            self._get_trace_length    = session_fns["get_tool_trace_length"]
            self._get_trace_since     = session_fns["get_tool_trace_since"]
        else:
            # Fallback: importar del proyecto (no del ZIP)
            # Buscar el módulo ya cacheado del proyecto, no del ZIP
            sc = self._resolve_project_session_context()
            self._reset_session    = sc.reset_session
            self._get_tool_trace   = sc.get_tool_trace
            self._get_trace_length = sc.get_tool_trace_length
            self._get_trace_since  = sc.get_tool_trace_since

    def _resolve_project_session_context(self):
        """
        Return the canonical session_context module — the one all team agents write
        traces to.  submission_loader._get_canonical_session_context() pre-registers
        it as sys.modules['core.session_context'] before any team module is exec'd,
        so by the time MultiEngine is initialised both parties share the same object.
        """
        import importlib, importlib.util, os

        # Fast path: submission_loader already registered our canonical module.
        cached = sys.modules.get("core.session_context")
        if cached and hasattr(cached, "reset_session"):
            mod_file = getattr(cached, "__file__", "") or ""
            # Accept if the file lives in the project (not in a tmp/omni_eval dir)
            if "omni_eval" not in mod_file and "tmp" not in mod_file.lower():
                return cached

        # Fallback (e.g. when MultiEngine is used without submission_loader):
        # find our session_context by walking sys.path and register it canonically
        # so future calls hit the fast path.
        for path_entry in sys.path[1:]:
            candidate = os.path.join(path_entry, "core", "session_context.py")
            if os.path.exists(candidate) and "omni_eval" not in candidate:
                spec = importlib.util.spec_from_file_location(
                    "core.session_context", candidate
                )
                mod = importlib.util.module_from_spec(spec)
                mod.__package__ = "core"
                sys.modules["core.session_context"] = mod  # register before exec
                spec.loader.exec_module(mod)
                return mod

        # Last resort: normal import
        return importlib.import_module("core.session_context")

    # ── Public API ────────────────────────────────────────────────────────────

    def run_all(
        self,
        scenario_filter: Optional[List[str]] = None,
        level_filter: Optional[List[str]] = None,
        category_filter: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        from evaluator.cases.golden_dataset import GOLDEN_SCENARIOS

        scenarios = [self._normalize_scenario(s) for s in GOLDEN_SCENARIOS]

        if scenario_filter:
            scenarios = [s for s in scenarios if s["id"] in scenario_filter]
        if level_filter:
            scenarios = [s for s in scenarios if s.get("level") in level_filter]
        if category_filter:
            scenarios = [s for s in scenarios if s.get("category") in category_filter]

        scenario_results = []
        for scenario in scenarios:
            scenario_results.append(self.run_scenario(scenario))

        return {
            "team_name": self.team_name,
            "backend_tag": self.backend_tag,
            "backend_notes": self.backend_notes,
            "summary": self._build_summary(scenario_results),
            "scenario_results": scenario_results,
        }

    def run_scenario(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        reset_policy = scenario.get("reset_policy", "per_scenario")
        step_results = []
        scenario_error = None

        if reset_policy == "per_scenario":
            self._reset_session()

        try:
            agent = self.create_agent_fn(streaming=False)
        except Exception as e:
            err_str = str(e)
            infra = self.is_nonstandard_backend and self._is_infra_error(err_str)
            return {
                "id": scenario["id"],
                "name": scenario["name"],
                "category": scenario["category"],
                "level": scenario.get("level", "intermediate"),
                "hard_gate": scenario.get("hard_gate", False),
                "reset_policy": reset_policy,
                "pass_threshold": scenario.get("pass_threshold", self.pass_threshold),
                "passed": False,
                "status": "infra_mismatch" if infra else "error",
                "scenario_score": 0,
                "steps_run": 0, "step_results": [], "scenario_trace": [],
                "error": f"No se pudo instanciar el agente: {e}",
                "infra_mismatch": infra,
            }

        try:
            conversation_history = []
            for index, step in enumerate(scenario["steps"]):
                if reset_policy == "per_step":
                    self._reset_session()

                step_result = self._run_step(
                    agent=agent,
                    scenario=scenario,
                    step=step,
                    step_index=index,
                    conversation_history=list(conversation_history),
                )
                step_results.append(step_result)
                conversation_history.append({"role": "user",      "content": step["user_input"]})
                conversation_history.append({"role": "assistant", "content": step_result.get("response", "")})

                if scenario.get("stop_on_first_failure") and not step_result["passed"]:
                    break

        except Exception as e:
            scenario_error = str(e)

        scenario_score  = self._compute_scenario_score(step_results)
        scenario_passed = self._scenario_passed(scenario, step_results, scenario_error, scenario_score)

        # A scenario is infra_mismatch if ALL step errors look like connectivity failures
        # and the team uses a non-standard (local) backend.
        infra_mismatch = (
            self.is_nonstandard_backend
            and bool(step_results or scenario_error)
            and self._all_infra_errors(step_results, scenario_error)
        )

        status = "ok"
        if infra_mismatch:
            status = "infra_mismatch"
        elif scenario_error:
            status = "error"
        elif scenario.get("hard_gate") and not scenario_passed:
            status = "failed_hard_gate"
        elif not scenario_passed:
            status = "failed"

        return {
            "id": scenario["id"],
            "name": scenario["name"],
            "category": scenario["category"],
            "level": scenario.get("level", "intermediate"),
            "hard_gate": scenario.get("hard_gate", False),
            "reset_policy": reset_policy,
            "pass_threshold": scenario.get("pass_threshold", self.pass_threshold),
            "passed": scenario_passed,
            "status": status,
            "infra_mismatch": infra_mismatch,
            "scenario_score": scenario_score,
            "steps_run": len(step_results),
            "step_results": step_results,
            "scenario_trace": self._get_tool_trace() if reset_policy != "per_step" else None,
            "error": scenario_error,
        }

    def _run_step(self, agent, scenario, step, step_index, conversation_history=None):
        step_name  = step.get("name", f"step_{step_index + 1}")
        input_text = step["user_input"]
        judge_key  = step.get("judge_category") or scenario.get("judge_category") or scenario["category"]
        judge      = self.judges.get(judge_key, self.judges["business"])

        trace_start = self._get_trace_length()
        started_at  = time.perf_counter()
        full_response = ""

        try:
            response_obj  = agent(input_text)
            full_response = getattr(response_obj, "content", str(response_obj))
            finished_at   = time.perf_counter()
            step_trace    = self._get_trace_since(trace_start)

            judge_kwargs = {
                "user_input":    input_text,
                "agent_response": full_response,
                "tool_trace":    step_trace,
                "expected_data": step.get("expected_data"),
            }
            if "conversation_history" in judge.evaluate.__code__.co_varnames:
                judge_kwargs["conversation_history"] = conversation_history or []

            verdict = judge.evaluate(**judge_kwargs)
            score   = int(verdict.get("score", 0))
            passed  = score >= step.get("pass_threshold", scenario.get("pass_threshold", self.pass_threshold))

            return {
                "step_index":    step_index,
                "step_name":     step_name,
                "judge_category": judge_key,
                "input":         input_text,
                "response":      full_response,
                "score":         score,
                "feedback":      verdict.get("feedback", "Sin feedback"),
                "passed":        passed,
                "status":        "ok",
                "trace":         step_trace,
                "expected_data": step.get("expected_data"),
                "metrics": {
                    "ttft_ms": None,
                    "trt_ms":  round((finished_at - started_at) * 1000, 2),
                    "e2e_ms":  round((finished_at - started_at) * 1000, 2),
                },
            }

        except Exception as e:
            finished_at = time.perf_counter()
            import traceback
            return {
                "step_index":    step_index,
                "step_name":     step_name,
                "judge_category": judge_key,
                "input":         input_text,
                "response":      full_response,
                "score":         0,
                "feedback":      f"Error: {e}\n{traceback.format_exc()}",
                "passed":        False,
                "status":        "error",
                "trace":         [],
                "expected_data": step.get("expected_data"),
                "metrics": {
                    "ttft_ms": None,
                    "trt_ms":  round((finished_at - started_at) * 1000, 2),
                    "e2e_ms":  round((finished_at - started_at) * 1000, 2),
                },
            }

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _normalize_scenario(self, scenario):
        if "steps" in scenario:
            normalized = dict(scenario)
            normalized["steps"] = [self._normalize_step(s) for s in scenario["steps"]]
            normalized.setdefault("level", "intermediate")
            normalized.setdefault("reset_policy", "per_scenario")
            normalized.setdefault("hard_gate", False)
            normalized.setdefault("pass_threshold", self.pass_threshold)
            return normalized
        return {
            "id": scenario["id"], "name": scenario["name"],
            "category": scenario["category"],
            "level": scenario.get("level", "intermediate"),
            "hard_gate": scenario.get("hard_gate", False),
            "reset_policy": scenario.get("reset_policy", "per_scenario"),
            "pass_threshold": scenario.get("pass_threshold", self.pass_threshold),
            "steps": [{
                "name": "single_step",
                "user_input": scenario["user_input"],
                "judge_category": scenario.get("judge_category", scenario["category"]),
                "expected_data": scenario.get("expected_data"),
                "pass_threshold": scenario.get("pass_threshold", self.pass_threshold),
            }],
        }

    def _normalize_step(self, step):
        normalized = dict(step)
        normalized.setdefault("name", "unnamed_step")
        return normalized

    def _compute_scenario_score(self, step_results):
        if not step_results:
            return 0
        return round(sum(s["score"] for s in step_results) / len(step_results))

    def _scenario_passed(self, scenario, step_results, scenario_error, scenario_score):
        if scenario_error or not step_results:
            return False
        if any(s["status"] == "error" for s in step_results):
            return False
        if any(not s["passed"] for s in step_results):
            return False
        return scenario_score >= scenario.get("pass_threshold", self.pass_threshold)

    def _is_infra_error(self, error_str: str) -> bool:
        low = error_str.lower()
        return any(pat in low for pat in self._INFRA_ERROR_PATTERNS)

    def _all_infra_errors(self, step_results, scenario_error) -> bool:
        """True when every available error signal looks like a connectivity failure."""
        signals = []
        if scenario_error:
            signals.append(scenario_error)
        for s in step_results:
            if s.get("status") == "error":
                signals.append(s.get("feedback", "") + s.get("response", ""))
        if not signals:
            return False
        return all(self._is_infra_error(sig) for sig in signals)

    def _build_summary(self, scenario_results):
        total  = len(scenario_results)
        passed = sum(1 for s in scenario_results if s["passed"])
        total_steps = sum(s["steps_run"] for s in scenario_results)
        hard_gate_failed = [s["id"] for s in scenario_results if s.get("hard_gate") and not s.get("passed")]
        infra_mismatch_count = sum(1 for s in scenario_results if s.get("infra_mismatch"))

        level_breakdown = {}
        for s in scenario_results:
            lv = s.get("level", "unknown")
            level_breakdown.setdefault(lv, {"total": 0, "passed": 0})
            level_breakdown[lv]["total"] += 1
            if s["passed"]:
                level_breakdown[lv]["passed"] += 1

        category_breakdown = {}
        for s in scenario_results:
            cat = s.get("category", "unknown")
            category_breakdown.setdefault(cat, {"total": 0, "passed": 0, "scores": []})
            category_breakdown[cat]["total"] += 1
            category_breakdown[cat]["scores"].append(s["scenario_score"])
            if s["passed"]:
                category_breakdown[cat]["passed"] += 1
        for cat in category_breakdown:
            scores = category_breakdown[cat]["scores"]
            category_breakdown[cat]["avg_score"] = round(sum(scores) / len(scores), 1) if scores else 0

        return {
            "total_scenarios": total,
            "passed_scenarios": passed,
            "failed_scenarios": total - passed,
            "pass_rate": round((passed / total) * 100, 1) if total else 0,
            "total_steps": total_steps,
            "hard_gate_failed_ids": hard_gate_failed,
            "disqualified": len(hard_gate_failed) > 0,
            "infra_mismatch_count": infra_mismatch_count,
            "level_breakdown": level_breakdown,
            "category_breakdown": category_breakdown,
        }
