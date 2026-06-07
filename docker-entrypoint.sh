#!/bin/sh
set -eu

mkdir -p /app/data /app/logs /app/backups
chown -R appuser:appuser /app/data /app/logs /app/backups
chmod 755 /app/data /app/logs /app/backups

exec gosu appuser "$@"
