#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

# Find the package without changing where relative database paths are resolved.
export PYTHONPATH="$script_dir${PYTHONPATH:+:$PYTHONPATH}"

# Replace Bash so the server receives signals and returns its exit status directly.
exec "${PY:-python3}" -m payload_store "$@"
