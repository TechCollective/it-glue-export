#!/bin/bash
# IT Glue Backup Runner Script
# This script is a wrapper to set up the virtual environment, install requirements,
# and run the IT Glue backup Python script. Perfect for cron automation.

set -e

# Resolve the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR"

# Ensure the temp directory exists
mkdir -p ./temp

# Create virtual environment if it doesn't exist
if [ ! -d "venv" ]; then
    echo "[$(date)] Virtual environment 'venv' not found. Creating..."
    python3 -m venv venv
    ./venv/bin/pip install --upgrade pip
    ./venv/bin/pip install -r requirements.txt
fi

# Run the python script
echo "[$(date)] Starting backup runner..."
./venv/bin/python itglue_backup.py "$@"
echo "[$(date)] Backup runner finished."
