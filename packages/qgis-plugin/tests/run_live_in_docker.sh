#!/usr/bin/env bash
# Run tests/test_live_layers.py inside an official QGIS Docker image, as CI does.
#
#   packages/qgis-plugin/tests/run_live_in_docker.sh 3.44-noble --smoke
#   packages/qgis-plugin/tests/run_live_in_docker.sh stable --all --json live-result.json
#
# First argument: tag of https://hub.docker.com/r/qgis/qgis. The rest goes to the test.
# Paths given to the test are relative to the repository root. `--changed` needs
# git, which the image may not have: resolve it on the host instead, e.g.
#   python3 packages/qgis-plugin/tests/test_live_layers.py --changed origin/main --list > ids.txt
#   packages/qgis-plugin/tests/run_live_in_docker.sh 3.44-noble --ids-file ids.txt
set -euo pipefail

if [ "$#" -lt 1 ]; then
	echo "usage: $0 <qgis-image-tag> [test_live_layers.py arguments]" >&2
	exit 2
fi

image_tag="$1"
shift
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

# The official images are linux/amd64 only: emulate on Apple Silicon and other arm64 hosts.
platform_args=()
case "$(uname -m)" in
arm64 | aarch64) platform_args=(--platform linux/amd64) ;;
esac

exec docker run --rm \
	${platform_args[@]+"${platform_args[@]}"} \
	--volume "${repo_root}:/repo" \
	--workdir /repo \
	--env QT_QPA_PLATFORM=offscreen \
	--env PYTHONDONTWRITEBYTECODE=1 \
	"qgis/qgis:${image_tag}" \
	python3 packages/qgis-plugin/tests/test_live_layers.py "$@"
