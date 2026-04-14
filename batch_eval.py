"""
batch_eval.py — Run all submissions in submissions/ and save results.

Usage:
    python batch_eval.py                        # evaluate all ZIPs
    python batch_eval.py --output results/      # custom output dir (default: results/)
    python batch_eval.py --category security    # filter by category
    python batch_eval.py --level basic          # filter by level
"""

import argparse
import csv
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path


def run_all(submissions_dir: str, output_dir: str, category_filter=None, level_filter=None):
    from submission_loader import SubmissionLoader
    from multi_engine import MultiEngine

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"\n=== Batch Eval | run_id={run_id} | submissions={submissions_dir} ===\n")

    loader = SubmissionLoader(submissions_dir)
    submissions = loader.load_all()

    if not submissions:
        print(f"No ZIPs found in {submissions_dir}")
        sys.exit(1)

    print(f"Found {len(submissions)} submission(s):\n")
    for s in submissions:
        print(f"  {s.summary()}")
    print()

    summary_rows = []

    for sub in submissions:
        print(f"── Evaluating: {sub.team_name} ──────────────────────────────")
        if not sub.ready:
            print(f"  SKIP — {sub.load_error}\n")
            summary_rows.append({
                "run_id": run_id,
                "team": sub.team_name,
                "status": "load_error",
                "error": sub.load_error,
                "pass_rate": None,
                "passed": None,
                "total": None,
                "disqualified": None,
                "hard_gate_failed": None,
            })
            continue

        engine = MultiEngine(
            create_agent_fn=sub.create_agent,
            team_name=sub.team_name,
            session_fns=sub._session_fns,
        )

        t0 = time.perf_counter()
        try:
            result = engine.run_all(
                category_filter=[category_filter] if category_filter else None,
                level_filter=[level_filter] if level_filter else None,
            )
        except Exception as e:
            elapsed = round(time.perf_counter() - t0, 1)
            print(f"  ERROR after {elapsed}s: {e}\n")
            summary_rows.append({
                "run_id": run_id,
                "team": sub.team_name,
                "status": "eval_error",
                "error": str(e),
                "pass_rate": None,
                "passed": None,
                "total": None,
                "disqualified": None,
                "hard_gate_failed": None,
            })
            continue

        elapsed = round(time.perf_counter() - t0, 1)
        summary = result["summary"]

        # ── Print quick summary ──────────────────────────────────────────
        print(f"  Done in {elapsed}s")
        print(f"  Pass rate : {summary['pass_rate']}%  ({summary['passed_scenarios']}/{summary['total_scenarios']} scenarios)")
        if summary.get("disqualified"):
            print(f"  DISQUALIFIED — hard gate(s) failed: {summary['hard_gate_failed_ids']}")
        print(f"  Category breakdown:")
        for cat, stats in summary.get("category_breakdown", {}).items():
            print(f"    {cat:12s}  avg={stats['avg_score']:5.1f}  passed={stats['passed']}/{stats['total']}")
        print()

        # ── Save full JSON result ────────────────────────────────────────
        result["run_id"] = run_id
        result["elapsed_s"] = elapsed
        json_path = out / f"{sub.team_name}_{run_id}.json"
        json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  Saved → {json_path}\n")

        # ── Collect summary row ──────────────────────────────────────────
        row = {
            "run_id": run_id,
            "team": sub.team_name,
            "status": "ok",
            "error": None,
            "pass_rate": summary["pass_rate"],
            "passed": summary["passed_scenarios"],
            "total": summary["total_scenarios"],
            "disqualified": summary.get("disqualified", False),
            "hard_gate_failed": ",".join(summary.get("hard_gate_failed_ids", [])),
            "elapsed_s": elapsed,
        }
        for cat, stats in summary.get("category_breakdown", {}).items():
            row[f"avg_{cat}"] = stats["avg_score"]
            row[f"pass_{cat}"] = f"{stats['passed']}/{stats['total']}"
        summary_rows.append(row)

    # ── Save summary CSV ─────────────────────────────────────────────────────
    if summary_rows:
        csv_path = out / f"summary_{run_id}.csv"
        all_keys = list(dict.fromkeys(k for row in summary_rows for k in row))
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
            writer.writeheader()
            for row in summary_rows:
                writer.writerow({k: row.get(k, "") for k in all_keys})
        print(f"Summary CSV → {csv_path}")

    print("\n=== Done ===")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--submissions", default="submissions/", help="Dir with ZIP files")
    parser.add_argument("--output", default="results/", help="Output dir for JSON + CSV")
    parser.add_argument("--category", default=None, help="Filter: security|business|data|rag|memory")
    parser.add_argument("--level", default=None, help="Filter: basic|intermediate|advanced")
    args = parser.parse_args()

    run_all(
        submissions_dir=args.submissions,
        output_dir=args.output,
        category_filter=args.category,
        level_filter=args.level,
    )
