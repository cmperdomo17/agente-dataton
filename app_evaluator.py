"""
app_evaluator.py — OmniJudge Dashboard

Tabs:
  ▶️  Evaluar   — carga ZIPs, valida contratos, evalúa uno o varios equipos
  📂  Resultados — explora resultados guardados (app + batch_eval.py)
  🏆  Comparar  — ranking y comparación por escenario entre equipos/runs

Iniciar:
    streamlit run app_evaluator.py
"""

import json
import traceback as _tb
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

RESULTS_DIR = Path("results")
RESULTS_DIR.mkdir(exist_ok=True)

# ─────────────────────────────────────────────────────────────────────────────
# Persistence helpers — handle both app format and batch_eval.py format
# ─────────────────────────────────────────────────────────────────────────────

def save_run(teams: Dict[str, Any]) -> tuple:
    """Save multi-team results. Returns (path, run_id)."""
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {
        "run_id": run_id,
        "saved_at": datetime.now().isoformat(),
        "teams": teams,
    }
    path = RESULTS_DIR / f"{run_id}.json"
    def _default(o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=_default), encoding="utf-8")
    return path, run_id


def _file_to_run(path: Path) -> Optional[Dict[str, Any]]:
    """
    Parse any result JSON into {run_id, saved_at, teams}.
    Handles:
      - App format:       {"run_id":..., "saved_at":..., "teams":{...}}
      - batch_eval.py:    {"team_name":..., "summary":..., "run_id":..., ...}
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if "teams" in data:
        # App multi-team format
        return {
            "run_id": data.get("run_id", path.stem),
            "saved_at": data.get("saved_at", ""),
            "teams": data["teams"],
            "_path": path,
        }

    if "team_name" in data and "summary" in data:
        # batch_eval.py single-team format
        run_id = data.get("run_id", path.stem)
        return {
            "run_id": run_id,
            "saved_at": run_id,
            "teams": {data["team_name"]: data},
            "_path": path,
        }

    return None


def _group_runs() -> Dict[str, Dict[str, Any]]:
    """Return all runs keyed by run_id. Files sharing a run_id are merged."""
    by_run: Dict[str, Dict] = {}
    for f in sorted(RESULTS_DIR.glob("*.json"), reverse=True):
        parsed = _file_to_run(f)
        if not parsed:
            continue
        rid = parsed["run_id"]
        if rid not in by_run:
            by_run[rid] = {
                "run_id": rid,
                "saved_at": parsed["saved_at"],
                "teams": {},
                "_paths": [],
            }
        by_run[rid]["teams"].update(parsed["teams"])
        by_run[rid]["_paths"].append(f)
    return by_run


def list_saved_runs() -> List[Dict[str, Any]]:
    runs = _group_runs()
    return [
        {
            "run_id": v["run_id"],
            "saved_at": v["saved_at"],
            "teams": list(v["teams"].keys()),
            "_paths": v["_paths"],
        }
        for v in sorted(runs.values(), key=lambda x: x["run_id"], reverse=True)
    ]


def load_run(run_id: str) -> Optional[Dict[str, Any]]:
    runs = _group_runs()
    run = runs.get(run_id)
    return run["teams"] if run else None


def delete_run(run_id: str):
    runs = _group_runs()
    run = runs.get(run_id)
    if run:
        for p in run.get("_paths", []):
            p.unlink(missing_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────

def _icon(v: bool) -> str:
    return "✅" if bool(v) else "❌"


def _ms(v) -> str:
    try:
        return f"{float(v):.0f} ms"
    except Exception:
        return "—"


def _pct(a, b) -> str:
    return f"{round(a / b * 100, 1)}%" if b else "—"


# ─────────────────────────────────────────────────────────────────────────────
# DataFrames
# ─────────────────────────────────────────────────────────────────────────────

def build_scenario_df(scenario_results: List[Dict]) -> pd.DataFrame:
    rows = []
    for s in scenario_results:
        rows.append({
            "id": s.get("id", ""),
            "nombre": s.get("name", ""),
            "categoría": s.get("category", ""),
            "nivel": s.get("level", "—"),
            "hard gate": _icon(s.get("hard_gate", False)),
            "score": s.get("scenario_score", 0),
            "aprobado": _icon(s.get("passed", False)),
            "estado": s.get("status", "—"),
            "steps": s.get("steps_run", 0),
        })
    return pd.DataFrame(rows)


def build_comparison_df(payloads: Dict[str, Dict]) -> pd.DataFrame:
    rows = []
    for team, payload in payloads.items():
        summary = payload.get("summary", {})
        scenarios = payload.get("scenario_results", []) or []
        total = summary.get("total_scenarios", len(scenarios))
        passed = summary.get("passed_scenarios", sum(1 for s in scenarios if s.get("passed")))
        disq = summary.get("disqualified", bool(summary.get("hard_gate_failed_ids")))

        cat_scores: Dict[str, List[float]] = {}
        for s in scenarios:
            cat = s.get("category", "?")
            cat_scores.setdefault(cat, [])
            cat_scores[cat].append(float(s.get("scenario_score", 0)))

        avg = round(sum(s.get("scenario_score", 0) for s in scenarios) / len(scenarios), 1) if scenarios else 0.0

        row: Dict[str, Any] = {
            "equipo": team,
            "aprobados": f"{passed}/{total}",
            "pass %": round(passed / total * 100, 1) if total else 0.0,
            "score avg": avg,
            "descalificado": "Sí ❌" if disq else "No ✅",
            "hard gates": ", ".join(summary.get("hard_gate_failed_ids", [])) or "—",
        }
        for cat in ["security", "business", "rag", "data", "memory"]:
            sc = cat_scores.get(cat, [])
            row[cat] = round(sum(sc) / len(sc), 1) if sc else "—"
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty and "pass %" in df.columns:
        df = df.sort_values("pass %", ascending=False).reset_index(drop=True)
        df.insert(0, "#", range(1, len(df) + 1))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Filters (read from session_state, set in sidebar)
# ─────────────────────────────────────────────────────────────────────────────

def _apply_filters(scenarios: List[Dict]) -> List[Dict]:
    lvl = st.session_state.get("_lvl", ["basic", "intermediate", "advanced", "legacy"])
    cat = st.session_state.get("_cat", ["security", "business", "rag", "data", "memory"])
    only_fail = st.session_state.get("_only_fail", False)
    only_gate = st.session_state.get("_only_gate", False)

    out = [
        s for s in scenarios
        if s.get("level", "legacy") in lvl and s.get("category", "") in cat
    ]
    if only_fail:
        out = [s for s in out if not s.get("passed", False)]
    if only_gate:
        out = [s for s in out if s.get("hard_gate", False)]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Render: single team results
# ─────────────────────────────────────────────────────────────────────────────

def render_team(payload: Dict, team_name: str = ""):
    if payload.get("error"):
        st.error(f"Error durante evaluación: {payload['error']}")
        return

    scenarios = _apply_filters(payload.get("scenario_results", []) or [])

    if not scenarios:
        st.info("No hay escenarios con los filtros actuales.")
        return

    total = len(scenarios)
    passed = sum(1 for s in scenarios if s.get("passed"))
    avg_score = round(sum(s.get("scenario_score", 0) for s in scenarios) / total, 1) if total else 0
    hard_fails = [s["id"] for s in scenarios if s.get("hard_gate") and not s.get("passed")]
    disq = len(hard_fails) > 0

    # Latency across all steps
    all_steps = [st for s in scenarios for st in (s.get("step_results") or [])]
    trt_vals = [m for st in all_steps for m in [(st.get("metrics") or {}).get("trt_ms")] if m is not None]
    avg_trt = round(sum(trt_vals) / len(trt_vals)) if trt_vals else None

    # KPIs
    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Escenarios", total)
    k2.metric("Aprobados", f"{passed} / {total}")
    k3.metric("Pass rate", _pct(passed, total))
    k4.metric("Score promedio", f"{avg_score}")
    k5.metric("TRT promedio", f"{avg_trt} ms" if avg_trt is not None else "—")
    k6.metric("Hard gates fallidos", len(hard_fails))

    if disq:
        st.error(f"⛔ **Descalificado** · Hard gates: {', '.join(hard_fails)}")
    else:
        st.success("✅ Sin descalificación por hard gates")

    # Category breakdown
    st.markdown("#### Por categoría")
    cat_data: Dict[str, Any] = {}
    for s in scenarios:
        c = s.get("category", "?")
        cat_data.setdefault(c, {"passed": 0, "total": 0, "scores": []})
        cat_data[c]["total"] += 1
        cat_data[c]["scores"].append(s.get("scenario_score", 0))
        if s.get("passed"):
            cat_data[c]["passed"] += 1

    cat_rows = []
    for c, d in sorted(cat_data.items()):
        sc = d["scores"]
        avg = round(sum(sc) / len(sc), 1) if sc else 0
        cat_rows.append({
            "categoría": c,
            "aprobados": f"{d['passed']}/{d['total']}",
            "pass %": _pct(d["passed"], d["total"]),
            "score avg": avg,
        })
    st.dataframe(pd.DataFrame(cat_rows), use_container_width=True, hide_index=True)

    # Scenario table
    st.markdown("#### Escenarios")
    df = build_scenario_df(scenarios)
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)

    # Scenario inspection
    st.markdown("#### Detalle por escenario")
    for scenario in scenarios:
        icon = _icon(scenario.get("passed"))
        hg = "  🔒" if scenario.get("hard_gate") else ""
        score = scenario.get("scenario_score", 0)
        title = f"{icon} **{scenario.get('id')}** · {scenario.get('name')} · {score}/100{hg}"

        with st.expander(title):
            c1, c2, c3, c4 = st.columns(4)
            c1.caption(f"**Categoría:** {scenario.get('category')}")
            c2.caption(f"**Nivel:** {scenario.get('level')}")
            c3.caption(f"**Estado:** {scenario.get('status')}")
            c4.caption(f"**Reset policy:** {scenario.get('reset_policy')}")

            if scenario.get("error"):
                st.error(scenario["error"])

            for step in (scenario.get("step_results") or []):
                with st.container(border=True):
                    h1, h2 = st.columns([1, 3])
                    h1.markdown(
                        f"**Step {int(step.get('step_index', 0)) + 1}: {step.get('step_name')}**"
                    )
                    h1.caption(f"Juez: `{step.get('judge_category')}`")
                    h1.caption(f"Score: **{step.get('score', 0)}** / 100")
                    metrics = step.get("metrics") or {}
                    trt = metrics.get("trt_ms")
                    e2e = metrics.get("e2e_ms")
                    ttft = metrics.get("ttft_ms")
                    if any(v is not None for v in (trt, e2e, ttft)):
                        h1.caption(f"TRT: {_ms(trt)}  ·  E2E: {_ms(e2e)}  ·  TTFT: {_ms(ttft)}")
                    if step.get("passed"):
                        h1.success("Aprobado ✅")
                    else:
                        h1.error("Fallido ❌")

                    with h2:
                        st.chat_message("user").write(step.get("input") or "—")
                        st.chat_message("assistant").write(step.get("response") or "—")

                    fb = step.get("feedback")
                    if fb:
                        if step.get("passed"):
                            st.success(fb)
                        else:
                            st.warning(fb)

                    # Always show tool names used (even when full trace toggle is off)
                    trace = step.get("trace") or []
                    if trace:
                        seen = {}
                        for e in trace:
                            n = e.get("tool") or e.get("tool_name") or e.get("name") or "?"
                            seen[n] = True
                        st.caption(f"🔧 Tools: {', '.join(seen.keys())}")
                    else:
                        st.caption("🔧 Tools: (ninguna)")

                    col_exp1, col_exp2 = st.columns(2)
                    with col_exp1:
                        if step.get("expected_data"):
                            with st.expander("Expected data"):
                                st.json(step["expected_data"])
                    with col_exp2:
                        if st.session_state.get("_show_trace") and trace:
                            with st.expander(f"Tool trace ({len(trace)} calls)"):
                                st.json(trace)


# ─────────────────────────────────────────────────────────────────────────────
# Render: comparison ranking + scenario grid
# ─────────────────────────────────────────────────────────────────────────────

def render_comparison(payloads: Dict[str, Dict]):
    if len(payloads) < 2:
        st.info("Selecciona al menos 2 equipos.")
        return

    st.markdown("#### Ranking")
    df = build_comparison_df(payloads)
    if not df.empty:
        st.dataframe(df, use_container_width=True, hide_index=True)

        # Category bar chart
        cat_cols = ["security", "business", "rag", "data", "memory"]
        chart_df = df.set_index("equipo")[cat_cols].copy()
        chart_df = chart_df.replace("—", None)
        for col in cat_cols:
            chart_df[col] = pd.to_numeric(chart_df[col], errors="coerce")
        st.markdown("#### Score por categoría")
        st.bar_chart(chart_df)

    # Disqualified notice
    disq_teams = df[df["descalificado"] == "Sí ❌"]["equipo"].tolist() if not df.empty else []
    if disq_teams:
        st.error(f"⛔ Descalificados: {', '.join(disq_teams)}")

    # Per-scenario grid
    st.markdown("#### Comparación por escenario")
    all_ids = sorted({
        s["id"]
        for p in payloads.values()
        for s in (p.get("scenario_results") or [])
    })

    if all_ids:
        cmp_rows = []
        for sid in all_ids:
            scenario_ref = next(
                (s for p in payloads.values() for s in (p.get("scenario_results") or []) if s.get("id") == sid),
                {}
            )
            row: Dict[str, Any] = {
                "id": sid,
                "cat": scenario_ref.get("category", ""),
                "nivel": scenario_ref.get("level", ""),
                "hard gate": "🔒" if scenario_ref.get("hard_gate") else "",
            }
            for team, payload in payloads.items():
                sc = next(
                    (s for s in (payload.get("scenario_results") or []) if s.get("id") == sid),
                    None,
                )
                if sc is not None:
                    row[team] = f"{'✅' if sc.get('passed') else '❌'} {sc.get('scenario_score', 0)}"
                else:
                    row[team] = "—"
            cmp_rows.append(row)

        st.dataframe(pd.DataFrame(cmp_rows), use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────────────
# Page config + sidebar
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(page_title="OmniJudge", layout="wide", page_icon="🛡️")

with st.sidebar:
    st.title("🛡️ OmniJudge")
    st.caption("OmniRetail · Evaluación de Agentes")
    st.markdown("---")

    st.markdown("**Filtros de visualización**")
    lvl = st.multiselect(
        "Nivel",
        ["basic", "intermediate", "advanced", "legacy"],
        default=["basic", "intermediate", "advanced", "legacy"],
        key="sb_lvl",
    )
    cat = st.multiselect(
        "Categoría",
        ["security", "business", "rag", "data", "memory"],
        default=["security", "business", "rag", "data", "memory"],
        key="sb_cat",
    )
    only_fail = st.checkbox("Solo fallidos", False, key="sb_fail")
    only_gate = st.checkbox("Solo hard gates", False, key="sb_gate")
    show_trace = st.checkbox("Mostrar tool traces", False, key="sb_trace")

    # Persist to session_state keys read by _apply_filters
    st.session_state["_lvl"] = lvl
    st.session_state["_cat"] = cat
    st.session_state["_only_fail"] = only_fail
    st.session_state["_only_gate"] = only_gate
    st.session_state["_show_trace"] = show_trace

    st.markdown("---")
    submissions_dir = st.text_input("Carpeta de submissions", "submissions/", key="sb_subdir")


# ─────────────────────────────────────────────────────────────────────────────
# Main tabs
# ─────────────────────────────────────────────────────────────────────────────

st.title("🛡️ OmniRetail · Evaluación de Agentes")

tab_eval, tab_results, tab_compare = st.tabs([
    "▶️ Evaluar",
    "📂 Resultados",
    "🏆 Comparar",
])


# ─────────────────────────────────────────────────────────────────────────────
# TAB 1 — Evaluar
# ─────────────────────────────────────────────────────────────────────────────

with tab_eval:
    st.markdown(
        "Carga ZIPs de equipos, valida el contrato `core/agent.py::create_agent()` "
        "y ejecuta la evaluación completa."
    )

    # Source: folder or drag-and-drop
    source = st.radio("Fuente de submissions", ["📁 Carpeta", "⬆️ Subir ZIPs"], horizontal=True)

    subs_path: Optional[Path] = None

    if source == "⬆️ Subir ZIPs":
        import tempfile

        uploaded = st.file_uploader(
            "Arrastrá los ZIPs de los equipos aquí",
            type="zip",
            accept_multiple_files=True,
        )
        if uploaded:
            tmp = Path(tempfile.mkdtemp(prefix="omni_ui_"))
            for uf in uploaded:
                (tmp / uf.name).write_bytes(uf.getvalue())
            subs_path = tmp
            st.success(f"Subidos: {', '.join(uf.name for uf in uploaded)}")
    else:
        subs_path = Path(submissions_dir)
        if subs_path.exists():
            zips = sorted(subs_path.glob("*.zip"))
            if zips:
                st.info(f"**{len(zips)}** ZIP(s) en `{subs_path}`: " + ", ".join(z.stem for z in zips))
            else:
                st.warning(f"No hay ZIPs en `{subs_path}`.")
        else:
            st.warning(f"Carpeta `{subs_path}` no existe.")

    # Load & validate
    col_load, col_clear = st.columns([1, 1])
    if col_load.button("🔍 Cargar y validar", key="load_btn", type="secondary"):
        if subs_path and subs_path.exists():
            from submission_loader import SubmissionLoader
            with st.spinner("Cargando y validando submissions..."):
                loader = SubmissionLoader(submissions_dir=str(subs_path))
                loaded = loader.load_all()
            st.session_state["subs"] = loaded
            st.session_state.pop("eval_results", None)
        else:
            st.error("Carpeta no válida o sin ZIPs.")

    if col_clear.button("🔄 Limpiar sesión", key="clear_btn"):
        for k in ("subs", "eval_results"):
            st.session_state.pop(k, None)
        st.rerun()

    submissions = st.session_state.get("subs", [])

    if submissions:
        st.markdown("---")
        st.markdown("#### Estado de los submissions")

        status_rows = []
        for sub in submissions:
            checks = "  ·  ".join(
                f"{'✓' if c.passed else '✗'} {c.name}" for c in sub.contract_checks
            )
            status_rows.append({
                "equipo": sub.team_name,
                "listo": _icon(sub.ready),
                "contrato": checks,
                "error": sub.load_error or "—",
            })
        st.dataframe(pd.DataFrame(status_rows), use_container_width=True, hide_index=True)

        ready = [s for s in submissions if s.ready]
        broken = [s for s in submissions if not s.ready]

        if broken:
            with st.expander(f"⚠️ {len(broken)} submission(s) con errores"):
                for sub in broken:
                    st.error(f"**{sub.team_name}**: {sub.load_error}")
                    for c in [c for c in sub.contract_checks if not c.passed]:
                        st.caption(f"  ✗ {c.name}: {c.detail}")

        if ready:
            st.markdown("---")
            st.markdown("#### Configurar evaluación")

            team_names = [s.team_name for s in ready]
            selected = st.multiselect(
                "Equipos a evaluar", team_names, default=team_names, key="sel_teams"
            )

            col_cats, col_lvls = st.columns(2)
            eval_cats = col_cats.multiselect(
                "Categorías",
                ["security", "business", "rag", "data", "memory"],
                default=["security", "business", "rag", "data", "memory"],
                key="eval_cats",
            )
            eval_levels = col_lvls.multiselect(
                "Niveles",
                ["basic", "intermediate", "advanced"],
                default=["basic", "intermediate", "advanced"],
                key="eval_levels",
            )

            run_btn = st.button(
                f"▶️ Evaluar {len(selected)} equipo(s)",
                type="primary",
                disabled=len(selected) == 0,
                key="run_eval",
            )

            if run_btn and selected:
                from multi_engine import MultiEngine

                to_eval = [s for s in ready if s.team_name in selected]
                eval_results: Dict[str, Any] = {}

                progress = st.progress(0.0, text="Iniciando evaluación...")
                status_box = st.empty()

                for i, sub in enumerate(to_eval):
                    progress.progress(
                        i / len(to_eval),
                        text=f"Evaluando {sub.team_name} ({i + 1}/{len(to_eval)})...",
                    )
                    status_box.info(f"⏳ Evaluando **{sub.team_name}**...")

                    try:
                        engine = MultiEngine(
                            create_agent_fn=sub.create_agent,
                            team_name=sub.team_name,
                            session_fns=sub._session_fns,  # team-specific trace fns
                            backend_tag=sub.backend_tag,
                            backend_notes=sub.backend_notes,
                        )
                        result = engine.run_all(
                            category_filter=eval_cats or None,
                            level_filter=eval_levels or None,
                        )
                        eval_results[sub.team_name] = result
                        rate = result.get("summary", {}).get("pass_rate", 0)
                        status_box.success(f"✅ **{sub.team_name}** · pass rate: {rate}%")
                    except Exception as e:
                        detail = _tb.format_exc()
                        status_box.error(f"❌ **{sub.team_name}** · {e}")
                        st.code(detail, language="python")
                        eval_results[sub.team_name] = {
                            "team_name": sub.team_name,
                            "summary": {},
                            "scenario_results": [],
                            "error": str(e),
                        }

                progress.progress(1.0, text="✅ Evaluación completa")
                st.session_state["eval_results"] = eval_results

                saved_path, run_id = save_run(eval_results)
                st.success(f"💾 Resultados guardados → `{saved_path.name}`  ·  Run ID: `{run_id}`")
                st.rerun()

        # Show current session results
        eval_results = st.session_state.get("eval_results", {})
        if eval_results:
            st.markdown("---")
            st.markdown("#### Resultados de la sesión")

            comp_df = build_comparison_df(eval_results)
            if not comp_df.empty:
                st.dataframe(comp_df, use_container_width=True, hide_index=True)

            st.markdown("---")
            team_tabs = st.tabs([f"📊 {t}" for t in eval_results])
            for tab, (team, payload) in zip(team_tabs, eval_results.items()):
                with tab:
                    render_team(payload, team)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 2 — Resultados guardados
# ─────────────────────────────────────────────────────────────────────────────

with tab_results:
    st.markdown(
        "Explora resultados de evaluaciones anteriores. "
        "Compatible con archivos guardados por esta app y por `batch_eval.py`."
    )

    saved = list_saved_runs()

    if not saved:
        st.info(f"No hay resultados en `{RESULTS_DIR}/`. Evalúa equipos en la pestaña **Evaluar**.")
    else:
        run_map = {r["run_id"]: r for r in saved}
        run_ids = list(run_map.keys())

        # Auto-load from URL ?run=<id>
        url_run = st.query_params.get("run")
        default_idx = run_ids.index(url_run) if url_run and url_run in run_ids else 0

        col_sel, col_del = st.columns([5, 1])
        selected_run = col_sel.selectbox(
            "Run guardado",
            run_ids,
            index=default_idx,
            format_func=lambda rid: (
                f"{rid}  —  "
                + ", ".join(run_map[rid]["teams"][:4])
                + ("…" if len(run_map[rid]["teams"]) > 4 else "")
                + f"  [{run_map[rid]['saved_at'][:10]}]"
            ),
        )
        st.query_params["run"] = selected_run

        meta = run_map[selected_run]
        run_files = meta.get("_paths", [])

        col_info, col_dl, col_del2 = st.columns([3, 1, 1])
        col_info.caption(
            f"Equipos: {', '.join(meta['teams'])}  ·  "
            f"Fecha: {meta['saved_at'][:19].replace('T', ' ')}"
        )
        if run_files:
            raw = "\n\n---\n\n".join(
                f.read_text(encoding="utf-8") for f in run_files
            )
            col_dl.download_button(
                "⬇️ JSON",
                data=raw,
                file_name=f"{selected_run}.json",
                mime="application/json",
            )
        if col_del2.button("🗑️ Eliminar", key="del_run"):
            delete_run(selected_run)
            st.query_params.pop("run", None)
            st.rerun()

        st.markdown("---")

        data = load_run(selected_run)
        if not data:
            st.error(f"No se pudo cargar el run `{selected_run}`.")
        else:
            comp_df = build_comparison_df(data)
            if not comp_df.empty:
                st.markdown("#### Ranking")
                st.dataframe(comp_df, use_container_width=True, hide_index=True)

                chart_df = comp_df.set_index("equipo")[["pass %"]].copy()
                chart_df["pass %"] = pd.to_numeric(chart_df["pass %"], errors="coerce")
                st.bar_chart(chart_df)

            st.markdown("---")
            st.markdown("#### Detalle por equipo")
            team_tabs = st.tabs([f"📊 {t}" for t in data])
            for tab, (team, payload) in zip(team_tabs, data.items()):
                with tab:
                    render_team(payload, team)


# ─────────────────────────────────────────────────────────────────────────────
# TAB 3 — Comparar
# ─────────────────────────────────────────────────────────────────────────────

with tab_compare:
    st.markdown(
        "Compara equipos de distintos runs a nivel de ranking y de escenario individual."
    )

    saved = list_saved_runs()

    if not saved:
        st.info("No hay resultados guardados para comparar.")
    else:
        run_ids_all = [r["run_id"] for r in saved]
        run_map_all = {r["run_id"]: r for r in saved}

        # Run selectors
        col_ra, col_rb = st.columns(2)
        run_a = col_ra.selectbox(
            "Run A",
            run_ids_all,
            index=0,
            key="cmp_ra",
            format_func=lambda rid: f"{rid}  [{', '.join(run_map_all[rid]['teams'][:3])}]",
        )
        run_b = col_rb.selectbox(
            "Run B",
            run_ids_all,
            index=0,
            key="cmp_rb",
            format_func=lambda rid: f"{rid}  [{', '.join(run_map_all[rid]['teams'][:3])}]",
        )

        data_a = load_run(run_a) or {}
        data_b = load_run(run_b) or {}

        # Team selectors
        col_ta, col_tb = st.columns(2)
        teams_a = col_ta.multiselect(
            "Equipos de A", list(data_a.keys()),
            default=list(data_a.keys()), key="cmp_ta"
        )
        teams_b = col_tb.multiselect(
            "Equipos de B", list(data_b.keys()),
            default=list(data_b.keys()), key="cmp_tb"
        )

        # Merge — suffix (B) when same team name in both
        merged: Dict[str, Dict] = {}
        for t in teams_a:
            if t in data_a:
                merged[t] = data_a[t]
        for t in teams_b:
            if t in data_b:
                key = f"{t} [B]" if t in merged else t
                merged[key] = data_b[t]

        if merged:
            render_comparison(merged)
        else:
            st.info("Selecciona al menos 1 equipo de cada run.")
