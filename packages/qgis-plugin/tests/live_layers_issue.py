"""Build the body of the single "catalog health" GitHub issue.

Input: one or more JSON results of ``test_live_layers.py`` (one per QGIS
version), optionally ``health.json`` from ``npm run healthcheck`` and the body
of the previous issue. Output: a Markdown body and a few ``key=value`` lines
for ``$GITHUB_OUTPUT``. Standard library only; the workflow does the ``gh`` calls.

The issue body carries a small hidden JSON state so that a layer which is
"unreachable" several weekly runs in a row is escalated: one timeout is noise,
three in a row is a dead provider.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

STATE_RE = re.compile(r"<!-- geostrata-live-state (\{.*?\}) -->", re.S)
ESCALATE_AFTER_RUNS = 3
MAX_ROWS = 150
SEVERITY = {"broken": 4, "allowlisted": 3, "unreachable": 2, "skipped": 1, "ok": 0}


def load_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def parse_state(previous_body: str) -> Dict[str, Any]:
    match = STATE_RE.search(previous_body or "")
    if not match:
        return {}
    try:
        state = json.loads(match.group(1))
    except ValueError:
        return {}
    return state if isinstance(state, dict) else {}


def cell(text: Any, limit: int = 180) -> str:
    cleaned = " ".join(str(text or "").split()).replace("|", "\\|")
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def merge_results(reports: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Worst outcome per layer across QGIS versions."""
    merged: Dict[str, Dict[str, Any]] = {}
    for report in reports:
        version = str(report.get("qgis_version") or "?").split("-")[0]
        for item in report.get("results", []):
            current = merged.get(item["id"])
            if current is None or SEVERITY.get(item["status"], 0) > SEVERITY.get(
                current["status"], 0
            ):
                merged[item["id"]] = {**item, "versions": [version]}
            elif item["status"] == current["status"] and version not in current["versions"]:
                current["versions"].append(version)
    return merged


def build_issue(
    reports: Sequence[Dict[str, Any]],
    health: Optional[Dict[str, Any]],
    previous_state: Dict[str, Any],
    run_url: str,
    today: str,
) -> Dict[str, Any]:
    merged = merge_results(reports)
    broken = sorted(
        (item for item in merged.values() if item["status"] == "broken"),
        key=lambda item: item["id"],
    )
    unreachable_ids = sorted(
        item["id"] for item in merged.values() if item["status"] == "unreachable"
    )

    previous_streaks = previous_state.get("unreachable_streak") or {}
    previous_first_seen = previous_state.get("first_seen") or {}
    streaks = {
        source_id: int(previous_streaks.get(source_id, 0)) + 1
        for source_id in unreachable_ids
    }
    first_seen = {
        item["id"]: previous_first_seen.get(item["id"], today) for item in broken
    }
    persistent = [
        source_id for source_id in unreachable_ids if streaks[source_id] >= ESCALATE_AFTER_RUNS
    ]

    broken_ids = {item["id"] for item in broken}
    dead_links: List[Dict[str, str]] = []
    health_results = (health or {}).get("results")
    if isinstance(health_results, dict):
        for source_id in sorted(health_results):
            entry = health_results[source_id]
            if isinstance(entry, dict) and entry.get("status") == "dead" and source_id not in broken_ids:
                dead_links.append({"id": source_id, "detail": str(entry.get("detail") or "")})

    versions = sorted(
        {str(report.get("qgis_version") or "?").split("-")[0] for report in reports}
    )
    checked = max((report["summary"]["checked"] for report in reports), default=0)
    lines: List[str] = []
    lines.append(
        f"Weekly live test of {today}: **{len(broken)} broken layer(s)**, "
        f"{len(dead_links)} more dead link(s) per the HTTP healthcheck, "
        f"{len(persistent)} layer(s) unreachable {ESCALATE_AFTER_RUNS}+ runs in a row."
    )
    lines.append("")
    lines.append(
        f"{checked} layers loaded in headless QGIS {', '.join(versions) or '?'}. "
        f"[Workflow run]({run_url}). This issue is rewritten by every run and closed when everything passes."
    )

    if broken:
        lines += ["", "### Broken layers", ""]
        lines.append("Fix the entry, remove it, or add it to `packages/qgis-plugin/tests/live_layers_allowlist.txt` with a reason.")
        lines += ["", "| id | type | reason | detail | QGIS | since |", "|---|---|---|---|---|---|"]
        for item in broken[:MAX_ROWS]:
            lines.append(
                f"| `{item['id']}` | {item['type']} | {cell(item.get('reason'), 60)} "
                f"| {cell(item.get('detail'))} | {', '.join(item['versions'])} | {first_seen[item['id']]} |"
            )
        if len(broken) > MAX_ROWS:
            lines.append(f"| … | | | {len(broken) - MAX_ROWS} more in the run artifacts | | |")
    if dead_links:
        lines += ["", "### Dead links (HTTP healthcheck only)", ""]
        lines.append("`npm run healthcheck` got a 4xx for these; they are hidden from search until fixed.")
        lines += ["", "| id | detail |", "|---|---|"]
        for dead in dead_links[:MAX_ROWS]:
            lines.append(f"| `{dead['id']}` | {cell(dead['detail'])} |")
    if persistent:
        lines += ["", f"### Unreachable {ESCALATE_AFTER_RUNS}+ runs in a row", ""]
        lines.append("Timeouts, 5xx or access denied every time: probably gone for good.")
        lines += ["", "| id | type | reason | runs |", "|---|---|---|---:|"]
        for source_id in persistent[:MAX_ROWS]:
            item = merged[source_id]
            lines.append(
                f"| `{source_id}` | {item['type']} | {cell(item.get('reason'), 80)} | {streaks[source_id]} |"
            )
    lines += [
        "",
        "### Reproduce",
        "",
        "```sh",
        "cd packages/registry && npm run ingest && npm run build && cd ../..",
        "QT_QPA_PLATFORM=offscreen <qgis-python> packages/qgis-plugin/tests/test_live_layers.py --id '<id>'",
        "```",
        "",
    ]
    state = {"unreachable_streak": streaks, "first_seen": first_seen, "updated": today}
    lines.append(f"<!-- geostrata-live-state {json.dumps(state, sort_keys=True)} -->")
    lines.append("")
    return {
        "body": "\n".join(lines),
        "broken": len(broken),
        "dead_links": len(dead_links),
        "persistent_unreachable": len(persistent),
        "needs_issue": bool(broken or dead_links or persistent),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--result", action="append", default=[], help="JSON result of test_live_layers.py (repeatable)")
    parser.add_argument("--results-dir", help="directory searched recursively for live-result*.json")
    parser.add_argument("--health", help="packages/registry/dist/health.json")
    parser.add_argument("--previous-body", help="file holding the body of the previous issue")
    parser.add_argument("--run-url", default="", help="link to the workflow run")
    parser.add_argument("--out", required=True, help="where to write the issue body")
    parser.add_argument("--github-output", help="append key=value lines to this file")
    args = parser.parse_args(argv)

    result_paths = [Path(path) for path in args.result]
    if args.results_dir:
        result_paths += sorted(Path(args.results_dir).rglob("live-result*.json"))
    reports = [
        report
        for report in (load_json(path) for path in result_paths)
        if isinstance(report, dict) and isinstance(report.get("results"), list)
    ]

    outputs: Dict[str, Any]
    if not reports:
        # The test itself did not run: say nothing rather than close the issue.
        outputs = {"status": "no-results", "needs_issue": "unknown", "broken": 0}
        Path(args.out).write_text("", encoding="utf-8")
    else:
        health = load_json(Path(args.health)) if args.health else None
        previous_body = ""
        if args.previous_body and Path(args.previous_body).is_file():
            previous_body = Path(args.previous_body).read_text(encoding="utf-8")
        issue = build_issue(
            reports,
            health if isinstance(health, dict) else None,
            parse_state(previous_body),
            args.run_url,
            time.strftime("%Y-%m-%d", time.gmtime()),
        )
        Path(args.out).write_text(issue["body"], encoding="utf-8")
        outputs = {
            "status": "ok",
            "needs_issue": "true" if issue["needs_issue"] else "false",
            "broken": issue["broken"],
            "dead_links": issue["dead_links"],
            "persistent_unreachable": issue["persistent_unreachable"],
        }

    text = "".join(f"{key}={value}\n" for key, value in outputs.items())
    sys.stdout.write(text)
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
