"""Report generation for energy experiments (plan step 8 outputs).

Produces:
  reports/<name>.json  - machine-readable full results
  reports/<name>.md    - human-readable Markdown report

Both include the methodology notes and the AMD package-only limitation so
numbers are never presented without their measurement context.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from energy.rapl_reader import amd_note
from energy.stats import Summary

REPORTS_DIR = Path("reports")


def _summary_dict(s: Summary) -> dict:
    return {
        "config": s.config,
        "n": s.n,
        "median": s.median,
        "mean": s.mean,
        "iqr_low": s.iqr_low,
        "iqr_high": s.iqr_high,
        "ci_low": s.ci_low,
        "ci_high": s.ci_high,
        "unit": s.unit,
    }


def write_report(
    name: str,
    title: str,
    results: list,
    summaries: dict,
    reader_description: str,
    config: dict,
    notes: list[str] | None = None,
) -> tuple[Path, Path]:
    """Write JSON + Markdown reports; returns their paths."""
    REPORTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    notes = notes or []

    payload = {
        "name": name,
        "title": title,
        "generated": stamp,
        "energy_source": reader_description,
        "harness_config": config,
        "notes": notes + [amd_note()],
        "summaries": {
            kind: {cfg: _summary_dict(s) for cfg, s in table.items()}
            for kind, table in summaries.items() if table
        },
        "savings": summaries.get("savings"),
        "trials": [
            {
                "config": r.config,
                "trial_index": r.trial_index,
                "duration_s": r.duration_s,
                "raw_energy_j": r.raw_energy_j,
                "idle_energy_j": r.idle_energy_j,
                "net_energy_j": r.net_energy_j,
                "batch_repeats": r.batch_repeats,
            }
            for r in results
        ],
    }

    json_path = REPORTS_DIR / f"{name}.json"
    json_path.write_text(json.dumps(payload, indent=2))

    md: list[str] = []
    md.append(f"# {title}")
    md.append("")
    md.append(f"*Generated {stamp}.*")
    md.append("")
    md.append(f"**Energy source:** {reader_description}")
    md.append("")
    md.append("**Harness configuration:**")
    for k, v in config.items():
        md.append(f"- {k}: `{v}`")
    md.append("")
    if notes:
        md.append("**Notes:**")
        for n in notes:
            md.append(f"- {n}")
        md.append("")
    md.append("## Results")
    md.append("")
    time_table = summaries.get("time", {})
    if time_table:
        md.append("| Metric | Config | Median | IQR | 95% CI | Mean | n |")
        md.append("|---|---|---|---|---|---|---|")
        for kind, label in (("time", "Time/invocation"), ("energy", "Energy/invocation")):
            table = summaries.get(kind)
            if not table:
                continue
            for cfg, s in table.items():
                md.append(
                    f"| {label} ({s.unit}) | {cfg} | {s.median:.4f} "
                    f"| {s.iqr_low:.4f} - {s.iqr_high:.4f} "
                    f"| [{s.ci_low:.4f}, {s.ci_high:.4f}] "
                    f"| {s.mean:.4f} | {s.n} |"
                )
        md.append("")

    savings = summaries.get("savings")
    if savings:
        md.append("## Savings vs baseline")
        md.append("")
        md.append("| Config | Energy savings | Time savings |")
        md.append("|---|---|---|")
        for cfg, sv in savings.items():
            md.append(f"| {cfg} | {sv['energy_savings_pct']:.2f}% | {sv['time_savings_pct']:.2f}% |")
        md.append("")
    else:
        md.append("> No energy source available; time-only mode. "
                  "Run `sudo energy/setup_rapl_access.sh` to enable joule measurements.")
        md.append("")

    md.append("## Methodology notes")
    md.append("")
    md.append("- Trials are interleaved (balanced rotation) to cancel thermal/cache drift bias.")
    md.append("- Primary summary is the median with IQR spread; 95% CI from percentile bootstrap.")
    md.append("- Net energy = raw counter delta - P_idle x trial duration (plan step 6).")
    md.append(f"- {amd_note()}")

    md_path = REPORTS_DIR / f"{name}.md"
    md_path.write_text("\n".join(md) + "\n")
    return json_path, md_path
