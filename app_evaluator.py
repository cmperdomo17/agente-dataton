import streamlit as st
import pandas as pd

st.set_page_config(page_title="OmniJudge Dashboard", layout="wide")

st.title("🛡️ OmniRetail: Auditoría de Agentes Inteligentes")
st.markdown("Evaluación automática de agentes con soporte para escenarios multi-turn, hard gates y telemetría por step.")
st.markdown("---")

st.sidebar.header("Configuración")
show_only_failed = st.sidebar.checkbox("Mostrar solo escenarios fallidos", value=False)
show_trace = st.sidebar.checkbox("Mostrar trazas técnicas", value=True)
show_metrics = st.sidebar.checkbox("Mostrar métricas de latencia", value=True)


def build_scenario_df(scenario_results):
    rows = []
    for scenario in scenario_results:
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
            }
        )
    return pd.DataFrame(rows)


def build_step_df(scenario_results):
    rows = []
    for scenario in scenario_results:
        for step in scenario.get("step_results", []):
            metrics = step.get("metrics", {}) or {}
            rows.append(
                {
                    "scenario_id": scenario.get("id"),
                    "scenario_name": scenario.get("name"),
                    "level": scenario.get("level"),
                    "scenario_category": scenario.get("category"),
                    "step_index": step.get("step_index"),
                    "step_name": step.get("step_name"),
                    "judge_category": step.get("judge_category"),
                    "score": step.get("score", 0),
                    "passed": step.get("passed", False),
                    "status": step.get("status", "unknown"),
                    "trt_ms": metrics.get("trt_ms"),
                    "e2e_ms": metrics.get("e2e_ms"),
                    "ttft_ms": metrics.get("ttft_ms"),
                }
            )
    return pd.DataFrame(rows)


if st.sidebar.button("🚀 Ejecutar Evaluación"):
    try:
        from evaluator.engine import EvaluationEngine  # import lazy

        engine = EvaluationEngine()

        with st.spinner("Evaluando escenarios..."):
            payload = engine.run_all()

        summary = payload.get("summary", {})
        scenario_results = payload.get("scenario_results", [])

        if show_only_failed:
            scenario_results = [s for s in scenario_results if not s.get("passed", False)]

        scenario_df = build_scenario_df(scenario_results)
        step_df = build_step_df(scenario_results)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Escenarios", summary.get("total_scenarios", len(scenario_df)))
        c2.metric("Aprobados", summary.get("passed_scenarios", int(scenario_df["passed"].sum()) if not scenario_df.empty else 0))
        c3.metric("Descalificado", "Sí" if summary.get("disqualified", False) else "No")
        c4.metric("Steps totales", summary.get("total_steps", len(step_df)))

        c5, c6, c7 = st.columns(3)
        avg_scenario_score = round(float(scenario_df["scenario_score"].mean()), 1) if not scenario_df.empty else 0.0
        failed_hard_gates = len(summary.get("hard_gate_failed_ids", []))
        avg_trt = round(float(step_df["trt_ms"].mean()), 1) if not step_df.empty and step_df["trt_ms"].notna().any() else None

        c5.metric("Score promedio escenario", f"{avg_scenario_score}%")
        c6.metric("Hard gates fallidos", failed_hard_gates)
        c7.metric("TRT promedio step", f"{avg_trt} ms" if avg_trt is not None else "N/D")

        st.subheader("📊 Resumen por nivel")
        level_breakdown = summary.get("level_breakdown", {})
        if level_breakdown:
            level_rows = []
            for level, values in level_breakdown.items():
                total = values.get("total", 0)
                passed = values.get("passed", 0)
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
            st.info("No hay desglose por nivel disponible.")

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
                "score",
                "passed",
                "status",
                "trt_ms",
                "e2e_ms",
                "ttft_ms",
            ]
            st.dataframe(step_df[step_display_cols], width="stretch", hide_index=True)

        st.subheader("🔍 Inspección de escenarios")
        for scenario in scenario_results:
            passed_badge = "✅" if scenario.get("passed") else "❌"
            title = (
                f"{passed_badge} {scenario.get('id')}: {scenario.get('name')} "
                f"| {scenario.get('scenario_score', 0)}/100 "
                f"| {scenario.get('status', 'unknown')}"
            )
            with st.expander(title):
                meta_a, meta_b, meta_c, meta_d = st.columns(4)
                meta_a.write(f"**Categoría:** {scenario.get('category')}")
                meta_b.write(f"**Nivel:** {scenario.get('level')}")
                meta_c.write(f"**Hard gate:** {scenario.get('hard_gate')}")
                meta_d.write(f"**Reset policy:** {scenario.get('reset_policy')}")

                if scenario.get("error"):
                    st.error(scenario["error"])

                for step in scenario.get("step_results", []):
                    step_ok = "✅" if step.get("passed") else "❌"
                    step_title = (
                        f"{step_ok} Step {step.get('step_index', 0) + 1}: {step.get('step_name')} "
                        f"| juez={step.get('judge_category')} | score={step.get('score', 0)}"
                    )
                    with st.container(border=True):
                        st.markdown(f"**{step_title}**")
                        col_a, col_b = st.columns(2)
                        with col_a:
                            st.write("**Conversación**")
                            st.chat_message("user").write(step.get("input", ""))
                            st.chat_message("assistant").write(step.get("response", ""))
                        with col_b:
                            st.write("**Veredicto del juez**")
                            st.warning(step.get("feedback", "Sin feedback"))
                            st.write(f"**Estado:** {step.get('status', 'unknown')}")
                            st.write(f"**Aprobado:** {step.get('passed', False)}")
                            if step.get("expected_data") is not None:
                                st.write("**Expected data**")
                                st.json(step.get("expected_data"))

                        if show_metrics:
                            metrics = step.get("metrics", {}) or {}
                            m1, m2, m3 = st.columns(3)
                            m1.metric("TRT", f"{metrics.get('trt_ms')} ms" if metrics.get("trt_ms") is not None else "N/D")
                            m2.metric("E2E", f"{metrics.get('e2e_ms')} ms" if metrics.get("e2e_ms") is not None else "N/D")
                            m3.metric("TTFT", f"{metrics.get('ttft_ms')} ms" if metrics.get("ttft_ms") is not None else "N/D")

                        if show_trace and step.get("trace"):
                            st.write("**Tool trace del step**")
                            st.json(step.get("trace"))

                if show_trace and scenario.get("scenario_trace"):
                    st.write("**Tool trace agregado del escenario**")
                    st.json(scenario.get("scenario_trace"))

    except Exception as e:
        st.error("Error ejecutando la evaluación")
        st.exception(e)

else:
    st.info("Presiona el botón en la barra lateral para iniciar la evaluación automática.")
    st.caption("La UI está preparada para resultados por escenario y por step.")
