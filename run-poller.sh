#!/usr/bin/env bash
# Run the poller once against the local emulator.
cd "$(dirname "$0")"
set -a; . ./.env; set +a
exec ./venv/bin/python poller.py
