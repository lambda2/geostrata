"""Turn packages/registry/dist/health.json into a GitHub step summary.

    python3 health-summary.py <health.json> <summary.md> [healthcheck exit code]

The Markdown goes to <summary.md> (pass "$GITHUB_STEP_SUMMARY"); workflow
annotations (::warning::) go to stdout. Always exits 0: reporting never blocks.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

MAX_LISTED = 60


def main() -> int:
    health_path = Path(sys.argv[1])
    summary_path = Path(sys.argv[2])
    exit_code = sys.argv[3] if len(sys.argv) > 3 and sys.argv[3] else "?"
    lines = ["### HTTP healthcheck", ""]

    try:
        report = json.loads(health_path.read_text(encoding="utf-8"))
        results = report["results"]
        assert isinstance(results, dict)
    except Exception:
        lines.append(
            f"`npm run healthcheck` did not produce a usable `health.json` (exit code {exit_code}). "
            "The catalog carries no fresh health status."
        )
        print(f"::warning::Healthcheck did not complete (exit code {exit_code}): no health status in this build")
    else:
        counts: dict = {}
        for entry in results.values():
            status = entry.get("status", "?") if isinstance(entry, dict) else "?"
            counts[status] = counts.get(status, 0) + 1
        dead = sorted(
            (source_id, entry.get("detail", ""))
            for source_id, entry in results.items()
            if isinstance(entry, dict) and entry.get("status") == "dead"
        )
        lines.append(
            f"{len(results)} sources checked at {report.get('checked_at', '?')}: "
            + ", ".join(f"{count} {status}" for status, count in sorted(counts.items()))
            + "."
        )
        if dead:
            lines += [
                "",
                f"**{len(dead)} dead link(s)**: flagged `health_status = dead` in the catalog and hidden from plugin search.",
                "",
                "| id | detail |",
                "|---|---|",
            ]
            for source_id, detail in dead[:MAX_LISTED]:
                lines.append(f"| `{source_id}` | {str(detail).replace('|', '/')[:160]} |")
            if len(dead) > MAX_LISTED:
                lines.append(f"| … | {len(dead) - MAX_LISTED} more in health.json |")
            print(f"::warning::Healthcheck found {len(dead)} dead link(s): {', '.join(i for i, _ in dead[:15])}")

    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
