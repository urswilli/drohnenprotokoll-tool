#!/usr/bin/env bash
# Produktionsdaten aus prod-import/ nach data/ übernehmen (manuell vom User kopiert).
# Erwartet: prod-import/drohnen.db  (optional: prod-import/output/)
set -euo pipefail
cd "$(dirname "$0")"

SRC="prod-import"
DB="$SRC/drohnen.db"

if [[ ! -f "$DB" ]]; then
  echo "Fehlt: $DB" >&2
  echo "Bitte drohnen.db von der VM docker-public nach prod-import/ kopieren." >&2
  exit 1
fi

mkdir -p data/output data/backup
STAMP=$(date +%Y%m%d-%H%M%S)
if [[ -f data/drohnen.db ]]; then
  cp -p data/drohnen.db "data/backup/drohnen-before-import-${STAMP}.db"
fi

cp -p "$DB" data/drohnen.db
if [[ -d "$SRC/output" ]]; then
  mkdir -p data/output
  cp -p "$SRC/output/"*.pdf data/output/ 2>/dev/null || true
fi

sqlite3 data/drohnen.db "UPDATE settings SET value='false' WHERE key='test_mode';"
echo "Import OK → data/drohnen.db. Start: ./run-local.sh"
