#!/usr/bin/env bash
# Lokaler Dev-Start mit Docker-kompatiblem DATA_DIR (./data) und HTTP-Sessions.
set -euo pipefail
cd "$(dirname "$0")"
export DATA_DIR="$PWD/data"
export SESSION_COOKIE_SECURE=false
exec python3 app.py
