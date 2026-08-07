#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if ! command -v python3 >/dev/null 2>&1; then echo "Python 3 is not installed."; exit 1; fi
if [ ! -f data/installed.flag ]; then
    echo "First run - installing dependencies..."
    python3 -m pip install --quiet -r requirements.txt
    mkdir -p data && echo installed > data/installed.flag
fi
exec python3 run.py "$@"
