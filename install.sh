#!/usr/bin/env bash
# Convenience wrapper (macOS / Linux). Windows: run `python scripts\install.py`.
set -e
cd "$(dirname "$0")"
exec "${PYTHON:-python3}" scripts/install.py "$@"
