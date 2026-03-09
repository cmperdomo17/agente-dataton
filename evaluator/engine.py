import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from core.agent import create_agent
from core.session_context import (
    reset_session,
    get_tool_trace,
    get_tool_trace_length,
    get_tool_trace_since,
)
from core.dynamo_service import ensure_caches
from core.policy_service import ensure_policies
from evaluator.judges.base_judge import BaseJudge
from evaluator.cases.golden_dataset import GOLDEN_SCENARIOS
from evaluator.judges.security_judge import SecurityJudge
from evaluator.judges.business_judge import BusinessJudge
from evaluator.judges.data_judge import DataJudge
from evaluator.judges.rag_judge import RagJudge
from evaluator.judges.memory_judge import MemoryJudge


class EvaluationEngine:
    """
    Evaluation engine with support for:
    - single-turn and multi-turn scenarios
    - configurable reset policy
    - hard gates (useful for basic qualification)
    - per-step trace slicing

    Notes:
    - TTFT is not measured here because the engine calls the agent with streaming=False.
    - If later the agent exposes streaming callbacks, TTFT can be added without changing
      the scenario schema.
    """

    def __init__(self, judges: Dict[str, BaseJudge] | None = None, pass_threshold: int = 80):
        self.pass_threshold = pass_threshold

        # Asegurar que los catálogos y políticas estén precargados también en el evaluador
        ensure_caches()
        ensure_policies()

        # Crear el agente una sola vez y reutilizarlo entre escenarios
        self.agent = create_agent(streaming=False)

        # Inyección de jueces; permite personalizar o mockear en tests
        self.judges = judges or self._default_judges()

    def run_all(self) -> Dict[str, Any]:
        scenario_results: List[Dict[str, Any]] = []

        for raw_scenario in GOLDEN_SCENARIOS:
            scenario = self._normalize_scenario(raw_scenario)
            scenario_results.append(self.run_scenario(scenario))

        return {
            "summary": self._build_summary(scenario_results),
            "scenario_results": scenario_results,
        }

    def run_scenario(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        reset_policy = scenario.get("reset_policy", "per_scenario")
        step_results: List[Dict[str, Any]] = []
        scenario_error = None

        if reset_policy == "per_scenario":
            reset_session()
            # Also clear the agent's conversation history to prevent context leakage
            if hasattr(self.agent, 'messages'):
                self.agent.messages.clear()
            elif hasattr(self.agent, 'conversation_manager') and hasattr(self.agent.conversation_manager, 'messages'):
                self.agent.conversation_manager.messages.clear()

        try:
            conversation_history: List[Dict[str, str]] = []

            for index, step in enumerate(scenario["steps"]):
                if reset_policy == "per_step":
                    reset_session()

                step_result = self.run_step(
                    agent=self.agent,
                    scenario=scenario,
                    step=step,
                    step_index=index,
                    conversation_history=list(conversation_history),
                )
                step_results.append(step_result)

                conversation_history.append({"role": "user", "content": step["user_input"]})
                conversation_history.append({"role": "assistant", "content": step_result.get("response", "")})

                if scenario.get("stop_on_first_failure", False) and not step_result["passed"]:
                    break

        except Exception as e:
            scenario_error = str(e)

        scenario_score = self._compute_scenario_score(step_results)
        scenario_passed = self._scenario_passed(
            scenario=scenario,
            step_results=step_results,
            scenario_error=scenario_error,
            scenario_score=scenario_score,
        )

        status = "ok"
        if scenario_error:
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
            "scenario_score": scenario_score,
            "steps_run": len(step_results),
            "step_results": step_results,
            "scenario_trace": get_tool_trace() if reset_policy != "per_step" else None,
            "error": scenario_error,
        }

    def run_step(
        self,
        agent,
        scenario: Dict[str, Any],
        step: Dict[str, Any],
        step_index: int,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Dict[str, Any]:
        step_name = step.get("name", f"step_{step_index + 1}")
        input_text = step["user_input"]
        judge_key = step.get("judge_category") or scenario.get("judge_category") or scenario["category"]
        judge = self.judges.get(judge_key, self.judges["business"])

        trace_start = get_tool_trace_length()
        started_at = time.perf_counter()
        full_response = ""

        try:
            response_obj = agent(input_text)
            full_response = getattr(response_obj, "content", str(response_obj))
            finished_at = time.perf_counter()

            step_trace = get_tool_trace_since(trace_start)

            tool_latency_ms = sum(
                (event.get("output", {}) or {}).get("elapsed_ms", 0) for event in step_trace
            )
            total_ms = round((finished_at - started_at) * 1000, 2)

            judge_kwargs = {
                "user_input": input_text,
                "agent_response": full_response,
                "tool_trace": step_trace,
                "expected_data": step.get("expected_data"),
            }
            if hasattr(judge, 'evaluate') and 'conversation_history' in judge.evaluate.__code__.co_varnames:
                judge_kwargs["conversation_history"] = conversation_history or []

            verdict = judge.evaluate(**judge_kwargs)

            score = int(verdict.get("score", 0))
            passed = score >= step.get("pass_threshold", scenario.get("pass_threshold", self.pass_threshold))

            return {
                "step_index": step_index,
                "step_name": step_name,
                "judge_category": judge_key,
                "input": input_text,
                "response": full_response,
                "score": score,
                "feedback": verdict.get("feedback", "Sin feedback"),
                "passed": passed,
                "status": "ok",
                "trace": step_trace,
                "expected_data": step.get("expected_data"),
                "metrics": {
                    "ttft_ms": None,
                    "trt_ms": total_ms,
                    "tool_latency_ms": round(tool_latency_ms, 2),
                    "model_latency_ms": round(total_ms - tool_latency_ms, 2),
                },
            }

        except Exception as e:
            finished_at = time.perf_counter()
            step_trace = get_tool_trace_since(trace_start)
            tool_latency_ms = sum(
                (event.get("output", {}) or {}).get("elapsed_ms", 0) for event in step_trace
            )
            total_ms = round((finished_at - started_at) * 1000, 2)
            return {
                "step_index": step_index,
                "step_name": step_name,
                "judge_category": judge_key,
                "input": input_text,
                "response": full_response,
                "score": 0,
                "feedback": f"Error en ejecución/evaluación del step: {str(e)}",
                "passed": False,
                "status": "error",
                "trace": step_trace,
                "expected_data": step.get("expected_data"),
                "metrics": {
                    "ttft_ms": None,
                    "trt_ms": total_ms,
                    "tool_latency_ms": round(tool_latency_ms, 2),
                    "model_latency_ms": round(total_ms - tool_latency_ms, 2),
                },
            }

    def _normalize_scenario(self, scenario: Dict[str, Any]) -> Dict[str, Any]:
        """
        Backward compatibility:
        if an old single-turn case arrives, convert it to a one-step scenario.
        """
        if "steps" in scenario:
            normalized = dict(scenario)
            normalized["steps"] = [self._normalize_step(step) for step in scenario["steps"]]
            normalized.setdefault("level", "intermediate")
            normalized.setdefault("reset_policy", "per_scenario")
            normalized.setdefault("hard_gate", False)
            normalized.setdefault("pass_threshold", self.pass_threshold)
            return normalized

        return {
            "id": scenario["id"],
            "name": scenario["name"],
            "category": scenario["category"],
            "level": scenario.get("level", "intermediate"),
            "hard_gate": scenario.get("hard_gate", False),
            "reset_policy": scenario.get("reset_policy", "per_scenario"),
            "pass_threshold": scenario.get("pass_threshold", self.pass_threshold),
            "steps": [
                {
                    "name": "single_step",
                    "user_input": scenario["user_input"],
                    "judge_category": scenario.get("judge_category", scenario["category"]),
                    "expected_data": scenario.get("expected_data"),
                    "pass_threshold": scenario.get("pass_threshold", self.pass_threshold),
                }
            ],
        }

    def _normalize_step(self, step: Dict[str, Any]) -> Dict[str, Any]:
        normalized = dict(step)
        normalized.setdefault("name", "unnamed_step")
        return normalized

    def _compute_scenario_score(self, step_results: List[Dict[str, Any]]) -> int:
        if not step_results:
            return 0
        return round(sum(step["score"] for step in step_results) / len(step_results))

    def _scenario_passed(
        self,
        scenario: Dict[str, Any],
        step_results: List[Dict[str, Any]],
        scenario_error: Optional[str],
        scenario_score: int,
    ) -> bool:
        if scenario_error or not step_results:
            return False

        if any(step["status"] == "error" for step in step_results):
            return False

        if any(not step["passed"] for step in step_results):
            return False

        return scenario_score >= scenario.get("pass_threshold", self.pass_threshold)

    def _build_summary(self, scenario_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        total_scenarios = len(scenario_results)
        total_steps = sum(result["steps_run"] for result in scenario_results)
        passed_scenarios = sum(1 for result in scenario_results if result["passed"])
        hard_gate_failed = [
            result["id"]
            for result in scenario_results
            if result.get("hard_gate") and not result.get("passed")
        ]

        level_breakdown: Dict[str, Dict[str, int]] = {}
        for result in scenario_results:
            level = result.get("level", "unknown")
            if level not in level_breakdown:
                level_breakdown[level] = {"total": 0, "passed": 0}
            level_breakdown[level]["total"] += 1
            if result["passed"]:
                level_breakdown[level]["passed"] += 1

        return {
            "total_scenarios": total_scenarios,
            "passed_scenarios": passed_scenarios,
            "failed_scenarios": total_scenarios - passed_scenarios,
            "total_steps": total_steps,
            "hard_gate_failed_ids": hard_gate_failed,
            "disqualified": len(hard_gate_failed) > 0,
            "level_breakdown": level_breakdown,
        }

    def _default_judges(self) -> Dict[str, BaseJudge]:
        """Construye el set por defecto de jueces para la evaluación."""
        return {
            "security": SecurityJudge(),
            "business": BusinessJudge(),
            "rag": RagJudge(),
            "data": DataJudge(),
            "memory": MemoryJudge(),
        }
