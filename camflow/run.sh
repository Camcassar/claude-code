#!/usr/bin/env bash
# One-shot setup + launch for CamFlow.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
    echo "Creating virtualenv (first run only)..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
fi

# (Re)install dependencies whenever requirements.txt changes.
if [ ! -f .venv/.deps-installed ] || [ requirements.txt -nt .venv/.deps-installed ]; then
    echo "Installing dependencies..."
    .venv/bin/pip install --quiet -r requirements.txt
    touch .venv/.deps-installed
fi

exec .venv/bin/python -m camflow
