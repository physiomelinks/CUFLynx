#!/usr/bin/env bash
# Convenience wrapper (macOS / Linux). Windows: run `python scripts\run.py`.
set -e
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" scripts/run.py "$@"
