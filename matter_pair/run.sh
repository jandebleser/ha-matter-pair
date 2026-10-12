#!/bin/sh
set -eu

echo "[matter-pair] starting backend on :8099"
exec python3 /app/app.py
