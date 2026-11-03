#!/bin/bash
# IT Glue Backup Runner Script
# Wrapper for cron: resolves the repo directory and runs the backup command through uv,
# which creates the virtual environment and installs dependencies on first run.

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR"

mkdir -p ./temp

echo "[$(date)] Starting backup runner..."
uv run --extra gdrive itglue-export backup "$@"
echo "[$(date)] Backup runner finished."
