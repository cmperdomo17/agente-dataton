"""
app_evaluator.py — OmniJudge Dashboard (v2)
────────────────────────────────────────────
Modos de uso:
  Tab 1 "Mi Agente"    — corre el agente propio (core/agent.py) igual que antes
  Tab 2 "Submissions"  — carga ZIPs de equipos, los evalúa individualmente
  Tab 3 "Comparación"  — ranking comparativo entre equipos evaluados

Iniciar:
    streamlit run app_evaluator.py
"""

import math
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

st.set_page_config(page_title="OmniJudge Dashboard", layout="wide")

st.title("🛡️ OmniRetail · Auditoría de Agentes Inteligentes")
st.markdown(
    "Evaluación automática: escenarios multi-turn, hard gates, memoria, "
    "grounding, telemetría y comparación entre equipos."
)
st.markdown("---")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers (igual que v1)
# ─────────────────────────────────────────────────────────────────────────────

def safe_bool_icon(value: bool) -> str:
    return "✅" if bool(value) else "❌"


def format_ms(value) -> str:
    if value is None:
        return "N/D"
    try:
        return f"{float(value):.0f} ms"
    except Exception:
        return "N/D"


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
        "herramienta" in feedback or "tool" in feedback or "ground" in feedback
    ):
        return "no consultó datos reales"
    if expected.get("must_not_reveal_order_data") and "revela" in feedback:
        return "reveló datos sin autorización"
    return "otro"


def summarize_expectation(expected: Dict[str, Any]) -> str:
    goal = expected.get("goal", "")
    must_use = expected.get("must_use_retrieval") or expected.get("must_ground_answer")
    must_id = expected.get("must_request_identification")
    must_not_id = expected.get("must_not_request_identification")
    parts = []
    if goal:
        parts.append(goal)
    if must_use:
        parts.append("requiere grounding")
    if must_id:
        parts.append("debe pedir ID")
    if must_not_id:
        parts.append("sin pedir ID")
    return " | ".join(parts) if parts else "—"


def build_scenario_df(scenario_results: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for s in scenario_results:
        step_results = s.get("step_results") or []
        failed_steps = [st for st in step_results if not st.get("passed", False)]
        primary_failure = parse_failure_reason(failed_steps[0]) if failed_steps else ""
        rows.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "category": s.get("category"),
            "level": s.get("level", "—"),
            "hard_gate": s.get("hard_gate", False),
            "reset_policy": s.get("reset_policy"),
            "steps_run": s.get("steps_run", 0),
            "scenario_score": s.get("scenario_score", 0),
            "passed": s.get("passed", False),
            "status": s.get("status", "unknown"),
            "primary_failure_reason": primary_failure,
        })
    return pd.DataFrame(rows)


def build_step_df(scenario_results: List[Dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for s in scenario_results:
        for step in (s.get("step_results") or []):
            metrics = step.get("metrics") or {}
            expected_data = step.get("expected_data") or {}
            rows.append({
                "scenario_id": s.get("id"),
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
            })
    return pd.DataFrame(rows)


def normalize_old_payload(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_results = []
    for res in results:
        scenario_results.append({
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
            "step_results": [{
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
            }],
        })
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
        },
        "scenario_results": scenario_results,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar (filtros compartidos)
# ─────────────────────────────────────────────────────────────────────────────

st.sidebar.header("⚙️ Filtros globales")
show_only_failed       = st.sidebar.checkbox("Mostrar solo fallidos", value=False)
show_only_hard_gates   = st.sidebar.checkbox("Mostrar solo hard gates", value=False)
show_trace             = st.sidebar.checkbox("Mostrar trazas técnicas", value=True)
show_metrics           = st.sidebar.checkbox("Mostrar métricas de latencia", value=True)
show_scenario_trace    = st.sidebar.checkbox("Mostrar trace agregado del escenario", value=False)

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

st.sidebar.markdown("---")
st.sidebar.header("📁 Submissions")
submissions_dir = st.sidebar.text_input(
    "Carpeta de submissions (ZIPs)",
    value="submissions/",
    help="Cada ZIP debe contener core/agent.py con create_agent(streaming)",
)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: render single-agent results (used by both Tab 1 and Tab 2)
# ─────────────────────────────────────────────────────────────────────────────

def render_results(payload: Dict[str, Any], team_label: str = ""):
    """Render full evaluation results for a single agent/team."""
    summary = payload.get("summary", {})
    scenario_results = payload.get("scenario_results", []) or []

    # Apply filters
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

    total_scenarios   = len(scenario_df)
    total_steps       = len(step_df)
    passed_scenarios  = int(scenario_df["passed"].sum()) if not scenario_df.empty else 0
    avg_score         = round(float(scenario_df["scenario_score"].mean()), 1) if not scenario_df.empty else 0.0
    avg_trt           = round(float(step_df["trt_ms"].mean()), 1) if not step_df.empty and step_df["trt_ms"].notna().any() else None
    hard_gate_failed  = [s.get("id") for s in scenario_results if s.get("hard_gate") and not s.get("passed")]
    disqualified      = len(hard_gate_failed) > 0

    if team_label:
        st.subheader(f"📊 Resultados — {team_label}")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Escenarios", total_scenarios)
    c2.metric("Aprobados", passed_scenarios)
    c3.metric("Descalificado", "Sí ❌" if disqualified else "No ✅")
    c4.metric("Hard gates fallidos", len(hard_gate_failed))
    c5.metric("Steps totales", total_steps)

    c6, c7, c8 = st.columns(3)
    c6.metric("Score promedio", f"{avg_score}%")
    c7.metric("TRT promedio step", f"{avg_trt} ms" if avg_trt is not None else "N/D")
    failure_steps = int((~step_df["passed"]).sum()) if not step_df.empty else 0
    c8.metric("Steps fallidos", failure_steps)

    if disqualified:
        st.error(f"⛔ Descalificado por hard gates: {', '.join(hard_gate_failed)}")
    else:
        st.success("✅ Sin descalificación por hard gates.")

    # Level breakdown
    st.subheader("📊 Resumen por nivel")
    if not scenario_df.empty:
        level_rows = []
        for level in ["basic", "intermediate", "advanced", "legacy"]:
            subset = scenario_df[scenario_df["level"] == level]
            if subset.empty:
                continue
            t = len(subset)
            p = int(subset["passed"].sum())
            level_rows.append({
                "nivel": level, "total": t, "aprobados": p,
                "fallidos": t - p,
                "pass_rate": f"{round((p/t)*100,1)}%" if t else "—",
            })
        st.dataframe(pd.DataFrame(level_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No hay escenarios con los filtros actuales.")

    # Failure reasons
    st.subheader("🚨 Motivos de fallo más frecuentes")
    if not step_df.empty:
        failed_df = step_df[step_df["passed"] == False]  # noqa: E712
        if not failed_df.empty:
            counts = (
                failed_df["failure_reason"].fillna("otro").replace("", "otro")
                .value_counts().reset_index()
            )
            counts.columns = ["motivo", "cantidad"]
            st.dataframe(counts, use_container_width=True, hide_index=True)
        else:
            st.success("No hubo steps fallidos con los filtros actuales.")

    # Scenario table
    st.subheader("📋 Escenarios evaluados")
    if not scenario_df.empty:
        display_cols = ["id", "name", "category", "level", "hard_gate",
                        "reset_policy", "steps_run", "scenario_score", "passed",
                        "status", "primary_failure_reason"]
        st.dataframe(scenario_df[display_cols], use_container_width=True, hide_index=True)

    # Step table
    st.subheader("🧩 Detalle por step")
    if not step_df.empty:
        step_cols = ["scenario_id", "step_index", "step_name", "judge_category",
                     "expectation", "score", "passed", "status", "failure_reason",
                     "trt_ms", "e2e_ms", "ttft_ms"]
        st.dataframe(step_df[step_cols], use_container_width=True, hide_index=True)

    # Scenario inspection
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

            for step in (scenario.get("step_results") or []):
                step_icon = safe_bool_icon(step.get("passed"))
                reason = parse_failure_reason(step) if not step.get("passed", False) else ""
                step_title = (
                    f"{step_icon} Step {int(step.get('step_index', 0)) + 1}: "
                    f"{step.get('step_name')} | juez={step.get('judge_category')} "
                    f"| score={step.get('score', 0)}"
                )
                with st.container(border=True):
                    st.markdown(f"**{step_title}**")
                    if reason:
                        st.caption(f"Motivo de fallo: **{reason}**")

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

                    if (expected := step.get("expected_data") or {}):
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


# ─────────────────────────────────────────────────────────────────────────────
# Helper: build comparison table from multiple payloads
# ─────────────────────────────────────────────────────────────────────────────

def build_comparison_df(payloads: Dict[str, Dict[str, Any]]) -> pd.DataFrame:
    """Build a comparison DataFrame: one row per team."""
    rows = []
    for team_name, payload in payloads.items():
        summary = payload.get("summary", {})
        scenario_results = payload.get("scenario_results", []) or []

        total = summary.get("total_scenarios", 0)
        passed = summary.get("passed_scenarios", 0)
        pass_rate = round((passed / total) * 100, 1) if total else 0
        disq = summary.get("disqualified", False)
        hard_fails = ", ".join(summary.get("hard_gate_failed_ids", [])) or "—"

        avg_score = 0.0
        if scenario_results:
            avg_score = round(
                sum(s.get("scenario_score", 0) for s in scenario_results) / len(scenario_results), 1
            )

        # Category breakdown
        cat_scores: Dict[str, List[float]] = {}
        for s in scenario_results:
            cat = s.get("category", "unknown")
            cat_scores.setdefault(cat, [])
            cat_scores[cat].append(s.get("scenario_score", 0))

        row: Dict[str, Any] = {
            "equipo": team_name,
            "total": total,
            "aprobados": passed,
            "pass_rate_%": pass_rate,
            "score_promedio": avg_score,
            "descalificado": "Sí ❌" if disq else "No ✅",
            "hard_gates_fallidos": hard_fails,
        }
        for cat in ["security", "business", "rag", "data", "memory"]:
            scores = cat_scores.get(cat, [])
            row[f"score_{cat}"] = round(sum(scores) / len(scores), 1) if scores else "—"

        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty and "score_promedio" in df.columns:
        df = df.sort_values("score_promedio", ascending=False).reset_index(drop=True)
        df.insert(0, "ranking", range(1, len(df) + 1))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# TABS
# ─────────────────────────────────────────────────────────────────────────────

tab_mine, tab_submissions, tab_compare = st.tabs([
    "🤖 Mi Agente",
    "📦 Submissions",
    "🏆 Comparación de equipos",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Mi Agente (comportamiento original intacto)
# ─────────────────────────────────────────────────────────────────────────────

with tab_mine:
    st.markdown("Evalúa tu agente propio (`core/agent.py`) con todos los escenarios del golden dataset.")

    if st.button("🚀 Ejecutar Evaluación", key="run_mine"):
        try:
            from evaluator.engine import EvaluationEngine
            engine = EvaluationEngine()
            with st.spinner("Evaluando escenarios..."):
                raw_payload = engine.run_all()

            payload = normalize_old_payload(raw_payload) if isinstance(raw_payload, list) else raw_payload
            render_results(payload)

        except Exception as e:
            st.error("Error ejecutando la evaluación")
            st.exception(e)
    else:
        st.info("Presiona el botón para iniciar la evaluación automática de tu agente.")


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Submissions
# ─────────────────────────────────────────────────────────────────────────────

with tab_submissions:
    st.markdown(
        "Cargá los ZIPs de los equipos participantes desde la carpeta configurada en la barra lateral. "
        "Cada ZIP debe tener `core/agent.py` con la función `create_agent(streaming: bool)`."
    )

    subs_path = Path(submissions_dir)

    # ── Detect available ZIPs ─────────────────────────────────────────
    if not subs_path.exists():
        st.warning(f"La carpeta `{submissions_dir}` no existe. Creala y copiá los ZIPs ahí.")
    else:
        zip_files = sorted(subs_path.glob("*.zip"))
        if not zip_files:
            st.info(f"No hay archivos `.zip` en `{submissions_dir}`.")
        else:
            st.success(f"Se encontraron **{len(zip_files)}** submission(s): "
                       + ", ".join(z.stem for z in zip_files))

            # ── Load & validate all submissions ───────────────────────
            if st.button("🔍 Cargar y validar submissions", key="load_subs"):
                from submission_loader import SubmissionLoader
                loader = SubmissionLoader(submissions_dir=submissions_dir)
                with st.spinner("Cargando submissions..."):
                    submissions = loader.load_all()
                st.session_state["submissions"] = submissions

            submissions = st.session_state.get("submissions", [])

            if submissions:
                st.subheader("📋 Estado de los submissions")
                status_rows = []
                for sub in submissions:
                    checks_str = " | ".join(
                        f"{'✓' if c.passed else '✗'} {c.name}"
                        for c in sub.contract_checks
                    )
                    status_rows.append({
                        "equipo": sub.team_name,
                        "listo": "✅" if sub.ready else "❌",
                        "contrato": checks_str,
                        "error": sub.load_error or "—",
                    })
                st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)

                ready_subs = [s for s in submissions if s.ready]
                broken_subs = [s for s in submissions if not s.ready]

                if broken_subs:
                    with st.expander(f"⚠️ {len(broken_subs)} submission(s) con errores"):
                        for sub in broken_subs:
                            st.error(f"**{sub.team_name}**: {sub.load_error}")
                            for c in sub.contract_checks:
                                if not c.passed:
                                    st.caption(f"  ✗ {c.name}: {c.detail}")

                if ready_subs:
                    # ── Team selector ─────────────────────────────────
                    team_names = [s.team_name for s in ready_subs]
                    selected_teams = st.multiselect(
                        "Seleccioná qué equipos evaluar:",
                        options=team_names,
                        default=team_names,
                        key="selected_teams",
                    )

                    col_run, col_clear = st.columns([3, 1])
                    run_btn = col_run.button(
                        f"▶️ Evaluar {len(selected_teams)} equipo(s)",
                        key="run_subs",
                        disabled=len(selected_teams) == 0,
                    )
                    if col_clear.button("🗑️ Limpiar resultados", key="clear_results"):
                        st.session_state.pop("sub_results", None)
                        st.rerun()

                    if run_btn:
                        from multi_engine import MultiEngine
                        sub_results: Dict[str, Any] = {}
                        to_evaluate = [s for s in ready_subs if s.team_name in selected_teams]

                        progress = st.progress(0, text="Iniciando evaluación...")
                        for i, sub in enumerate(to_evaluate):
                            progress.progress(
                                (i) / len(to_evaluate),
                                text=f"Evaluando {sub.team_name} ({i+1}/{len(to_evaluate)})..."
                            )
                            try:
                                engine = MultiEngine(
                                    create_agent_fn=sub.create_agent,
                                    team_name=sub.team_name,
                                )
                                result = engine.run_all(
                                    level_filter=selected_levels if selected_levels else None,
                                    category_filter=selected_categories if selected_categories else None,
                                )
                                sub_results[sub.team_name] = result
                            except Exception as e:
                                st.error(f"Error evaluando {sub.team_name}: {e}")
                                sub_results[sub.team_name] = {
                                    "team_name": sub.team_name,
                                    "summary": {},
                                    "scenario_results": [],
                                    "error": str(e),
                                }

                        progress.progress(1.0, text="✅ Evaluación completa")
                        st.session_state["sub_results"] = sub_results
                        st.rerun()

                    # ── Show results per team ─────────────────────────
                    sub_results = st.session_state.get("sub_results", {})
                    if sub_results:
                        team_tabs = st.tabs([f"📊 {t}" for t in sub_results.keys()])
                        for team_tab, (team_name, payload) in zip(team_tabs, sub_results.items()):
                            with team_tab:
                                if payload.get("error"):
                                    st.error(f"Error durante evaluación: {payload['error']}")
                                else:
                                    render_results(payload, team_label=team_name)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — Comparación de equipos
# ─────────────────────────────────────────────────────────────────────────────

with tab_compare:
    st.markdown(
        "Ranking comparativo entre todos los equipos evaluados en la pestaña **Submissions**. "
        "Incluye scores por categoría, estado de hard gates y clasificación final."
    )

    sub_results: Dict[str, Any] = st.session_state.get("sub_results", {})

    if not sub_results:
        st.info("Primero evaluá los equipos en la pestaña **Submissions**.")
    else:
        # ── Ranking table ─────────────────────────────────────────────
        st.subheader("🏆 Ranking general")
        comp_df = build_comparison_df(sub_results)
        if not comp_df.empty:
            st.dataframe(comp_df, use_container_width=True, hide_index=True)

            # ── Score bars per team ───────────────────────────────────
            st.subheader("📊 Score promedio por equipo")
            chart_df = comp_df[["equipo", "score_promedio"]].copy()
            chart_df = chart_df.set_index("equipo")
            st.bar_chart(chart_df)

            # ── Category heatmap ──────────────────────────────────────
            st.subheader("🗂️ Scores por categoría")
            cat_cols = [c for c in comp_df.columns if c.startswith("score_")]
            if cat_cols:
                cat_df = comp_df[["equipo"] + cat_cols].copy()
                cat_df.columns = ["equipo"] + [c.replace("score_", "") for c in cat_cols]
                st.dataframe(cat_df, use_container_width=True, hide_index=True)

            # ── Hard gate failures ────────────────────────────────────
            disq_teams = comp_df[comp_df["descalificado"] == "Sí ❌"]
            if not disq_teams.empty:
                st.error(
                    "⛔ **Equipos descalificados por hard gates**: "
                    + ", ".join(disq_teams["equipo"].tolist())
                )
            else:
                st.success("✅ Ningún equipo fue descalificado por hard gates.")

            # ── Per-category leaders ──────────────────────────────────
            st.subheader("🥇 Líder por categoría")
            leader_rows = []
            for cat in ["security", "business", "rag", "data", "memory"]:
                col = f"score_{cat}"
                if col in comp_df.columns:
                    numeric = comp_df[comp_df[col] != "—"].copy()
                    if not numeric.empty:
                        numeric[col] = pd.to_numeric(numeric[col], errors="coerce")
                        best = numeric.loc[numeric[col].idxmax()]
                        leader_rows.append({
                            "categoría": cat,
                            "equipo líder": best["equipo"],
                            "score": best[col],
                        })
            if leader_rows:
                st.dataframe(pd.DataFrame(leader_rows), use_container_width=True, hide_index=True)

        else:
            st.warning("No hay datos para comparar.")
