#!/usr/bin/env bash
# stop.sh — Stop all meMCP Docker services
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping all meMCP services..."

# Stop connectors (if running)
if docker compose -f connectors/docker-compose.yml ps --quiet 2>/dev/null | grep -q .; then
    docker compose -f connectors/docker-compose.yml down
fi

# Stop core services
docker compose down

echo "All services stopped."
