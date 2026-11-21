#!/usr/bin/env bash
# Sync Rudra's venv from uv.lock WITHOUT uninstalling laya.
#
# laya (laya-mlx on Apple Silicon, laya[onnx] elsewhere) is a REQUIRED but
# deliberately UNLOCKED sidecar -- see CLAUDE.md section 3 / the decide/ module.
# Putting it in uv.lock would force numpy<2 or pull torch/nvidia on everyone,
# so it is installed out of band and is NOT in the lock. The cost: a plain
# `uv sync` treats it as extraneous and uninstalls it (its plan shows
# `- laya-mlx==...`). `uv sync --inexact` leaves packages that are not in the
# lock alone, which is what this script runs. It then reinstalls the right
# wheel for this machine if laya is ever missing, so a plain `uv sync` that
# already dropped it is one command to recover.
#
# Usage: scripts/dev-sync.sh [extra uv sync args...]
set -euo pipefail

cd "$(dirname "$0")/.."

uv sync --inexact "$@"

# Pick the wheel the way src/rudra/decide/backends.py probes for it:
# MLX build imports `laya_mlx`, the ONNX build imports `laya`.
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    probe="laya_mlx"
    pkg="laya-mlx"
else
    probe="laya"
    pkg="laya[onnx]"
fi

if .venv/bin/python -c "import ${probe}" >/dev/null 2>&1; then
    echo "dev-sync: ${pkg} present."
else
    echo "dev-sync: ${pkg} missing -- reinstalling the laya sidecar."
    uv pip install "${pkg}"
fi
