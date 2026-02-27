import math
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

st.set_page_config(page_title="OmniJudge Dashboard", layout="wide")

st.title("🛡️ OmniRetail: Auditoría de Agentes Inteligentes")
st.markdown(
    "Evaluación automática de agentes con escenarios multi-turn, hard gates, memoria, grounding y telemetría por step."
)
st.markdown("---")


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def safe_bool_icon(value: bool) -> str:
    return "✅" if bool(value) else "❌"


def parse_failure_reason(step: Dict[str, Any]) -> str:
    feedback = str(step.get("feedback", "") or "").lower()
    expected = step.get("expected_data", {}) or {}
    judge = str(step.get("judge_category", "") or "")

    if step.get("status") == "error":
        return "error técnico"

    if judge == "memory":
        if "identific" in feedback:
            return "pidió identificación innecesaria"
        if "no recordó" in feedback or "memoria" in feedback:
            return "fallo de memoria"
        return "memoria"

    if expected.get("must_not_request_identification") and (
        "identific" in feedback or "fricción" in feedback or "friccion" in feedback
    ):
        return "pidió identificación innecesaria"

    if expected.get("must_request_identification") and "identific" in feedback:
        return "no manejó identificación correctamente"

    if expected.get("must_use_retrieval") and (
        "retrieval" in feedback or "document" in feedback or "ground" in feedback
    ):
        return "fallo de retrieval/grounding"

    if expected.get("must_ground_answer") and (
        "athena" in feedback or "dynamo" in feedback or "ground" in feedback or "numeric" in feedback or "valor" in feedback
    ):
        return "fallo de grounding/data"

    if "hallucin" in feedback or "alucin" in feedback:
        return "alucinación"

    if judge == "security":
        return "protocolo de seguridad"
    if judge == "business":
        return "lógica de negocio / cx"
    if judge == "rag":
        return "rag / grounding"
    if judge == "data":
        return "data / cálculo"

    return "otro"


def summarize_expectation(expected: Dict[str, Any]) -> str:
    if not expected:
        return ""
    goal = expected.get("goal")
    if goal:
        return str(goal)
    keys = [k for k in expected.keys() if k not in {"goal", "expected_values", "required_any_of_tools", "required_tools"}]
    return ", ".join(keys[:3])


def build_scenario_df(scenario_results: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for scenario in scenario_results:
        step_results = scenario.get("step_results", []) or []
        fail_reasons = [parse_failure_reason(step) for step in step_results if not step.get("passed", False)]
        rows.append(
            {
                "id": scenario.get("id"),
                "name": scenario.get("name"),
                "category": scenario.get("category"),
                "level": scenario.get("level"),
                "hard_gate": scenario.get("hard_gate"),
                "reset_policy": scenario.get("reset_policy"),
                "steps_run": scenario.get("steps_run", 0),
                "scenario_score": scenario.get("scenario_score", 0),
                "passed": scenario.get("passed", False),
                "status": scenario.get("status", "unknown"),
                "error": scenario.get("error"),
                "primary_failure_reason": fail_reasons[0] if fail_reasons else "",
            }
        )
    return pd.DataFrame(rows)


def build_step_df(scenario_results: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for scenario in scenario_results:
        for step in scenario.get("step_results", []) or []:
            metrics = step.get("metrics", {}) or {}
            expected_data = step.get("expected_data", {}) or {}
            rows.append(
                {
                    "scenario_id": scenario.get("id"),
                    "scenario_name": scenario.get("name"),
                    "level": scenario.get("level"),
                    "scenario_category": scenario.get("category"),
                    "scenario_status": scenario.get("status"),
                    "hard_gate": scenario.get("hard_gate"),
                    "step_index": step.get("step_index"),
                    "step_name": step.get("step_name"),
                    "judge_category": step.get("judge_category"),
                    "expectation": summarize_expectation(expected_data),
                    "score": step.get("score", 0),
                    "passed": step.get("passed", False),
                    "status": step.get("status", "unknown"),
                    "failure_reason": "" if step.get("passed", False) else parse_failure_reason(step),
                    "trt_ms": metrics.get("trt_ms"),
                    "e2e_ms": metrics.get("e2e_ms"),
                    "ttft_ms": metrics.get("ttft_ms"),
                }
            )
    return pd.DataFrame(rows)


def normalize_old_payload(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_results = []
    for res in results:
        scenario_results.append(
            {
                "id": res.get("id"),
                "name": res.get("name"),
                "category": res.get("category"),
                "level": res.get("level", "legacy"),
                "hard_gate": False,
                "reset_policy": "per_step",
                "pass_threshold": 80,
                "passed": (res.get("score", 0) >= 80),
                "status": res.get("status", "ok"),
                "scenario_score": res.get("score", 0),
                "steps_run": 1,
                "scenario_trace": res.get("trace"),
                "error": res.get("error"),
                "step_results": [
                    {
                        "step_index": 0,
                        "step_name": "single_step",
                        "judge_category": res.get("category"),
                        "input": res.get("input", ""),
                        "response": res.get("response", ""),
                        "score": res.get("score", 0),
                        "feedback": res.get("feedback", ""),
                        "passed": (res.get("score", 0) >= 80),
                        "status": res.get("status", "ok"),
                        "trace": res.get("trace", []),
                        "expected_data": res.get("expected_data", {}),
                        "metrics": {},
                    }
                ],
            }
        )

    total = len(scenario_results)
    passed = sum(1 for s in scenario_results if s.get("passed"))
    return {
        "summary": {
            "total_scenarios": total,
            "passed_scenarios": passed,
            "failed_scenarios": total - passed,
            "total_steps": total,
            "hard_gate_failed_ids": [],
            "disqualified": False,
            "level_breakdown": {},
        },
        "scenario_results": scenario_results,
    }


def format_ms(value: Any) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "N/D"
    try:
        return f"{float(value):.1f} ms"
    except Exception:
        return "N/D"


# ---------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------
st.sidebar.header("Configuración")
show_only_failed = st.sidebar.checkbox("Mostrar solo fallidos", value=False)
show_only_hard_gates = st.sidebar.checkbox("Mostrar solo hard gates", value=False)
show_trace = st.sidebar.checkbox("Mostrar trazas técnicas", value=True)
show_metrics = st.sidebar.checkbox("Mostrar métricas de latencia", value=True)
show_scenario_trace = st.sidebar.checkbox("Mostrar trace agregado del escenario", value=False)

selected_levels = st.sidebar.multiselect(
    "Filtrar por nivel",
    options=["basic", "intermediate", "advanced", "legacy"],
    default=["basic", "intermediate", "advanced", "legacy"],
)
selected_categories = st.sidebar.multiselect(
    "Filtrar por categoría",
    options=["security", "business", "rag", "data", "memory"],
    default=["security", "business", "rag", "data", "memory"],
)


# ---------------------------------------------------------------------
# Main action
# ---------------------------------------------------------------------
if st.sidebar.button("🚀 Ejecutar Evaluación"):
    try:
        from evaluator.engine import EvaluationEngine

        engine = EvaluationEngine()

        with st.spinner("Evaluando escenarios..."):
            raw_payload = engine.run_all()

        if isinstance(raw_payload, list):
            payload = normalize_old_payload(raw_payload)
        else:
            payload = raw_payload

        summary = payload.get("summary", {})
        scenario_results = payload.get("scenario_results", []) or []

        # Filters
        scenario_results = [
            s for s in scenario_results
            if s.get("level", "legacy") in selected_levels
            and s.get("category") in selected_categories
        ]
        if show_only_failed:
            scenario_results = [s for s in scenario_results if not s.get("passed", False)]
        if show_only_hard_gates:
            scenario_results = [s for s in scenario_results if s.get("hard_gate", False)]

        scenario_df = build_scenario_df(scenario_results)
        step_df = build_step_df(scenario_results)

        # Top summary
        total_scenarios = len(scenario_df)
        total_steps = len(step_df)
        passed_scenarios = int(scenario_df["passed"].sum()) if not scenario_df.empty else 0
        avg_scenario_score = round(float(scenario_df["scenario_score"].mean()), 1) if not scenario_df.empty else 0.0
        avg_trt = round(float(step_df["trt_ms"].mean()), 1) if not step_df.empty and step_df["trt_ms"].notna().any() else None
        hard_gate_failed = [
            s.get("id") for s in scenario_results if s.get("hard_gate") and not s.get("passed")
        ]
        disqualified = len(hard_gate_failed) > 0

        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Escenarios", total_scenarios)
        c2.metric("Aprobados", passed_scenarios)
        c3.metric("Descalificado", "Sí" if disqualified else "No")
        c4.metric("Hard gates fallidos", len(hard_gate_failed))
        c5.metric("Steps totales", total_steps)

        c6, c7, c8 = st.columns(3)
        c6.metric("Score promedio escenario", f"{avg_scenario_score}%")
        c7.metric("TRT promedio step", f"{avg_trt} ms" if avg_trt is not None else "N/D")
        failure_steps = int((~step_df["passed"]).sum()) if not step_df.empty else 0
        c8.metric("Steps fallidos", failure_steps)

        if disqualified:
            st.error(
                "El agente quedó descalificado por fallar hard gates básicos. "
                f"IDs: {', '.join(hard_gate_failed)}"
            )
        else:
            st.success("No hubo descalificación por hard gates en esta corrida.")

        st.subheader("📊 Resumen por nivel")
        if not scenario_df.empty:
            level_rows = []
            for level in ["basic", "intermediate", "advanced", "legacy"]:
                subset = scenario_df[scenario_df["level"] == level]
                if subset.empty:
                    continue
                total = len(subset)
                passed = int(subset["passed"].sum())
                level_rows.append(
                    {
                        "level": level,
                        "total": total,
                        "passed": passed,
                        "failed": total - passed,
                        "pass_rate": round((passed / total) * 100, 1) if total else 0.0,
                    }
                )
            st.dataframe(pd.DataFrame(level_rows), width="stretch", hide_index=True)
        else:
            st.info("No hay escenarios para mostrar con los filtros actuales.")

        st.subheader("🚨 Motivos de fallo más frecuentes")
        if not step_df.empty:
            failed_reason_df = step_df[step_df["passed"] == False]  # noqa: E712
            if not failed_reason_df.empty:
                reason_counts = (
                    failed_reason_df["failure_reason"]
                    .fillna("otro")
                    .replace("", "otro")
                    .value_counts()
                    .reset_index()
                )
                reason_counts.columns = ["motivo", "cantidad"]
                st.dataframe(reason_counts, width="stretch", hide_index=True)
            else:
                st.success("No hubo steps fallidos con los filtros actuales.")

        st.subheader("📋 Escenarios evaluados")
        if scenario_df.empty:
            st.warning("No hay escenarios para mostrar con los filtros actuales.")
        else:
            scenario_display_cols = [
                "id",
                "name",
                "category",
                "level",
                "hard_gate",
                "reset_policy",
                "steps_run",
                "scenario_score",
                "passed",
                "status",
                "primary_failure_reason",
            ]
            st.dataframe(scenario_df[scenario_display_cols], width="stretch", hide_index=True)

        st.subheader("🧩 Detalle por step")
        if step_df.empty:
            st.info("No hay steps para mostrar.")
        else:
            step_display_cols = [
                "scenario_id",
                "step_index",
                "step_name",
                "judge_category",
                "expectation",
                "score",
                "passed",
                "status",
                "failure_reason",
                "trt_ms",
                "e2e_ms",
                "ttft_ms",
            ]
            st.dataframe(step_df[step_display_cols], width="stretch", hide_index=True)

        st.subheader("🔍 Inspección de escenarios")
        for scenario in scenario_results:
            passed_icon = safe_bool_icon(scenario.get("passed"))
            hard_gate_tag = " | HARD GATE" if scenario.get("hard_gate") else ""
            title = (
                f"{passed_icon} {scenario.get('id')}: {scenario.get('name')}"
                f" | {scenario.get('scenario_score', 0)}/100"
                f" | {scenario.get('status', 'unknown')}{hard_gate_tag}"
            )
            with st.expander(title):
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.write(f"**Categoría:** {scenario.get('category')}")
                m2.write(f"**Nivel:** {scenario.get('level')}")
                m3.write(f"**Hard gate:** {scenario.get('hard_gate')}")
                m4.write(f"**Reset policy:** {scenario.get('reset_policy')}")
                m5.write(f"**Steps:** {scenario.get('steps_run')}")

                if scenario.get("error"):
                    st.error(scenario.get("error"))

                for step in scenario.get("step_results", []) or []:
                    step_icon = safe_bool_icon(step.get("passed"))
                    reason = parse_failure_reason(step) if not step.get("passed", False) else ""
                    step_title = (
                        f"{step_icon} Step {int(step.get('step_index', 0)) + 1}: {step.get('step_name')}"
                        f" | juez={step.get('judge_category')} | score={step.get('score', 0)}"
                    )
                    with st.container(border=True):
                        st.markdown(f"**{step_title}**")
                        if reason:
                            st.caption(f"Motivo de fallo detectado: **{reason}**")

                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.write("**Conversación**")
                            st.chat_message("user").write(step.get("input", ""))
                            st.chat_message("assistant").write(step.get("response", ""))
                        with col_b:
                            st.write("**Veredicto del juez**")
                            if step.get("passed"):
                                st.success(step.get("feedback", "Sin feedback"))
                            else:
                                st.warning(step.get("feedback", "Sin feedback"))
                            st.write(f"**Estado:** {step.get('status', 'unknown')}")
                            st.write(f"**Aprobado:** {step.get('passed', False)}")
                            st.write(f"**Score:** {step.get('score', 0)}")

                        expected = step.get("expected_data") or {}
                        if expected:
                            st.write("**Expected data / contrato del caso**")
                            st.json(expected)

                        if show_metrics:
                            metrics = step.get("metrics", {}) or {}
                            if metrics:
                                mx1, mx2, mx3 = st.columns(3)
                                mx1.metric("TRT", format_ms(metrics.get("trt_ms")))
                                mx2.metric("E2E", format_ms(metrics.get("e2e_ms")))
                                mx3.metric("TTFT", format_ms(metrics.get("ttft_ms")))

                        if show_trace and step.get("trace"):
                            st.write("**Trace técnico del step**")
                            st.json(step.get("trace"))

                if show_scenario_trace and scenario.get("scenario_trace"):
                    st.write("**Trace agregado del escenario**")
                    st.json(scenario.get("scenario_trace"))

    except Exception as e:
        st.error("Error ejecutando la evaluación")
        st.exception(e)
else:
    st.info("Presiona el botón en la barra lateral para iniciar la evaluación automática.")
    st.caption(
        "La UI está preparada para escenarios multi-turn, hard gates, filtros por nivel/categoría y diagnóstico por step."
    )
