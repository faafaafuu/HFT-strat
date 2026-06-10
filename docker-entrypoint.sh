#!/bin/sh
set -eu

mkdir -p /app/data /app/logs /app/backups /app/models
chown -R appuser:appuser /app/data /app/logs /app/backups /app/models
chmod 755 /app/data /app/logs /app/backups /app/models

exec gosu appuser "$@"
