#!/usr/bin/env bash
# Run on the packaged plugin the two scanners that block an upload on plugins.qgis.org:
# Bandit (any finding) and detect-secrets (any finding). Needs `pip install bandit detect-secrets`.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
zip_path="${1:-${script_dir}/../dist/geostrata.zip}"
work_dir="$(mktemp -d)"
trap 'rm -rf "${work_dir}"' EXIT

unzip -q "${zip_path}" -d "${work_dir}"
cd "${work_dir}"

echo "== Bandit"
bandit -r geostrata

echo "== detect-secrets"
detect-secrets scan --all-files geostrata > secrets.json
python3 - <<'PY'
import json
import sys

results = json.load(open("secrets.json")).get("results", {})
findings = [(path, item["line_number"], item["type"]) for path, items in results.items() for item in items]
for path, line, kind in findings:
    print(f"{path}:{line}: {kind}")
print(f"{len(findings)} potential secret(s)")
sys.exit(1 if findings else 0)
PY
