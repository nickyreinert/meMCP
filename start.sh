#!/usr/bin/env bash
# start.sh — Interactive launcher for meMCP Docker services
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

CACHE_FILE="$SCRIPT_DIR/.memcp_start_cache"

# ── Colors ───────────────────────────────────────────────────────────────────
BOLD='\033[1m'
DIM='\033[2m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

# ── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo -e "${CYAN}[info]${NC} $*"; }
warn()  { echo -e "${YELLOW}[warn]${NC} $*"; }
error() { echo -e "${RED}[error]${NC} $*" >&2; }

ask_yn() {
    local prompt="$1" default="${2:-n}"
    local yn
    if [[ "$default" == "y" ]]; then
        read -rp "$(echo -e "${BOLD}$prompt [Y/n]:${NC} ")" yn
        yn="${yn:-y}"
    else
        read -rp "$(echo -e "${BOLD}$prompt [y/N]:${NC} ")" yn
        yn="${yn:-n}"
    fi
    [[ "$yn" =~ ^[Yy] ]]
}

port_is_in_use() {
    local port="$1"

    if command -v lsof &>/dev/null; then
        lsof -nP -iTCP:"$port" -sTCP:LISTEN &>/dev/null
        return $?
    fi

    if command -v ss &>/dev/null; then
        ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN
        return $?
    fi

    if command -v netstat &>/dev/null; then
        netstat -an 2>/dev/null | grep -E "[\.:]$port[[:space:]].*LISTEN" &>/dev/null
        return $?
    fi

    warn "No port inspection tool found (lsof/ss/netstat). Assuming port $port is free."
    return 1
}

find_available_port() {
    local base_port="$1" max_increments="$2"
    shift 2
    local excluded_ports=("$@")
    local i candidate excluded

    for ((i = 0; i <= max_increments; i++)); do
        candidate=$((base_port + i))
        excluded=false
        for excluded_port in "${excluded_ports[@]-}"; do
            [[ -n "$excluded_port" ]] || continue
            if [[ "$candidate" == "$excluded_port" ]]; then
                excluded=true
                break
            fi
        done

        if $excluded; then
            continue
        fi

        if ! port_is_in_use "$candidate"; then
            echo "$candidate"
            return 0
        fi
    done

    return 1
}

# ── Pre-flight checks ───────────────────────────────────────────────────────
if ! command -v docker &>/dev/null; then
    error "Docker is not installed or not in PATH."
    exit 1
fi

if ! docker info &>/dev/null 2>&1; then
    error "Docker daemon is not running."
    exit 1
fi

# ── Load cached settings ────────────────────────────────────────────────────
CACHED_PROXY=""
CACHED_CONNECTORS=""
USE_CACHE=false

if [[ -f "$CACHE_FILE" ]]; then
    source "$CACHE_FILE"
    echo ""
    echo -e "${DIM}Found last selection ($(stat -f %Sm -t '%Y-%m-%d %H:%M' "$CACHE_FILE" 2>/dev/null || echo 'unknown date'))${NC}"
    if ask_yn "Use last selection?"; then
        USE_CACHE=true
    fi
fi

# ── Banner ───────────────────────────────────────────────────────────────────
if [[ "$USE_CACHE" != "true" ]]; then
    echo ""
    echo -e "${BOLD}  meMCP — Docker Launcher${NC}"
    echo -e "${DIM}  Select which services to start${NC}"
    echo ""
fi

# ── Step 1: MCP Server + Admin ───────────────────────────────────────────────
if [[ "$USE_CACHE" != "true" ]]; then
    echo -e "${BOLD}1) MCP Server${NC} ${DIM}(API on :8000, Admin UI on :8081)${NC}"
    echo -e "   This is the core service. It includes the MCP API server"
    echo -e "   and the admin interface for managing your profile."
    echo ""

    if ! ask_yn "Start MCP Server + Admin?" "y"; then
        info "Nothing to start. Exiting."
        exit 0
    fi
fi

START_PROXY=false
CHAT_CONNECTORS=()
MAX_PORT_INCREMENTS=10

HOST_MCP_PORT=""
HOST_ADMIN_PORT=""
HOST_PROXY_PORT=""

# ── Step 2: Chat Proxy ──────────────────────────────────────────────────────
if [[ "$USE_CACHE" == "true" ]]; then
    if [[ "$CACHED_PROXY" == "true" ]]; then
        START_PROXY=true
        if [[ -n "$CACHED_CONNECTORS" ]]; then
            IFS=',' read -ra CHAT_CONNECTORS <<< "$CACHED_CONNECTORS"
        fi
    fi
else
    echo ""
    echo -e "${BOLD}2) Chat Proxy${NC} ${DIM}(on :8001)${NC}"
    echo -e "   Enables conversational access to your profile via LLM-powered chat."
    echo -e "   Required if you want Telegram, Slack, or Discord bots."
    echo ""

    if ask_yn "Start Chat Proxy?"; then
        START_PROXY=true

        # ── Step 3: Chat Connectors ──────────────────────────────────────────────
        echo ""
        echo -e "${BOLD}3) Chat Connectors${NC}"
        echo -e "   Which bot adapters should connect to the proxy?"
        echo ""

        if ask_yn "  a) Telegram bot?"; then
            CHAT_CONNECTORS+=("telegram")
        fi
        if ask_yn "  b) Slack bot?"; then
            CHAT_CONNECTORS+=("slack")
        fi
        if ask_yn "  c) Discord bot?"; then
            CHAT_CONNECTORS+=("discord")
        fi
    fi
fi

# ── Validate .env files ─────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        warn "No .env found. Copying .env.example -> .env"
        cp .env.example .env
        warn "Please edit .env with your admin credentials."
    fi
fi

# ── Check CONFIG_SECRET ──────────────────────────────────────────────────────
if [[ "$USE_CACHE" != "true" ]]; then
    if ! grep -q 'CONFIG_SECRET=[^[:space:]]*' .env 2>/dev/null || grep -q 'CONFIG_SECRET=$' .env; then
        echo ""
        echo -e "${BOLD}CONFIG_SECRET${NC} ${DIM}(for sensitive data encryption)${NC}"
        if ask_yn "Generate a new CONFIG_SECRET?"; then
            SECRET=$(openssl rand -hex 32)
            if grep -q 'CONFIG_SECRET=' .env; then
                sed -i '' "s/CONFIG_SECRET=.*/CONFIG_SECRET=$SECRET/" .env
            else
                echo "CONFIG_SECRET=$SECRET" >> .env
            fi
            info "CONFIG_SECRET generated and saved to .env"
        fi
    fi
fi

if $START_PROXY && [[ ! -f connectors/.env ]]; then
    if [[ -f connectors/.env.example ]]; then
        warn "No connectors/.env found. Copying .env.example -> connectors/.env"
        cp connectors/.env.example connectors/.env
        warn "Please edit connectors/.env with your API keys and tokens."
    else
        error "connectors/.env is missing and no .env.example found."
        exit 1
    fi
fi

# Check for required tokens
if [[ ${#CHAT_CONNECTORS[@]-0} -gt 0 ]]; then
    echo ""
    for connector in "${CHAT_CONNECTORS[@]}"; do
        case "$connector" in
            telegram)
                if ! grep -q 'TELEGRAM_TOKEN=.' connectors/.env 2>/dev/null; then
                    warn "TELEGRAM_TOKEN is not set in connectors/.env"
                fi
                ;;
            slack)
                if ! grep -q 'SLACK_BOT_TOKEN=.' connectors/.env 2>/dev/null; then
                    warn "SLACK_BOT_TOKEN is not set in connectors/.env"
                fi
                ;;
            discord)
                if ! grep -q 'DISCORD_TOKEN=.' connectors/.env 2>/dev/null; then
                    warn "DISCORD_TOKEN is not set in connectors/.env"
                fi
                ;;
        esac
    done
fi

# ── Dynamic host port selection ─────────────────────────────────────────────
HOST_MCP_PORT="$(find_available_port 8000 "$MAX_PORT_INCREMENTS")" || {
    error "Could not find a free host port for MCP API in range 8000-$((8000 + MAX_PORT_INCREMENTS))."
    exit 1
}
HOST_ADMIN_PORT="$(find_available_port 8081 "$MAX_PORT_INCREMENTS" "$HOST_MCP_PORT")" || {
    error "Could not find a free host port for Admin UI in range 8081-$((8081 + MAX_PORT_INCREMENTS))."
    exit 1
}

if $START_PROXY; then
    HOST_PROXY_PORT="$(find_available_port 8001 "$MAX_PORT_INCREMENTS" "$HOST_MCP_PORT" "$HOST_ADMIN_PORT")" || {
        error "Could not find a free host port for Chat Proxy in range 8001-$((8001 + MAX_PORT_INCREMENTS))."
        exit 1
    }
fi

if [[ "$HOST_MCP_PORT" != "8000" ]]; then
    warn "Port 8000 is occupied. Using MCP API host port $HOST_MCP_PORT instead."
fi
if [[ "$HOST_ADMIN_PORT" != "8081" ]]; then
    warn "Port 8081 is occupied. Using Admin UI host port $HOST_ADMIN_PORT instead."
fi
if $START_PROXY && [[ "$HOST_PROXY_PORT" != "8001" ]]; then
    warn "Port 8001 is occupied. Using Chat Proxy host port $HOST_PROXY_PORT instead."
fi

# ── Summary ──────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}Starting services:${NC}"
echo -e "  ${GREEN}*${NC} mcp-server  (API :$HOST_MCP_PORT)"
echo -e "  ${GREEN}*${NC} admin       (UI  :$HOST_ADMIN_PORT)"
if $START_PROXY; then
    echo -e "  ${GREEN}*${NC} proxy       (Chat :$HOST_PROXY_PORT)"
fi
for c in "${CHAT_CONNECTORS[@]-}"; do
    [[ -n "$c" ]] || continue
    echo -e "  ${GREEN}*${NC} $c"
done
echo ""

# ── Cache selection for next run ─────────────────────────────────────────────
CONNECTORS_STR=""
if [[ ${#CHAT_CONNECTORS[@]-0} -gt 0 ]]; then
    CONNECTORS_STR=$(IFS=,; echo "${CHAT_CONNECTORS[*]}")
fi

cat > "$CACHE_FILE" << EOF
# meMCP start.sh cache — autogenerated
CACHED_PROXY="$START_PROXY"
CACHED_CONNECTORS="$CONNECTORS_STR"
EOF

info "Selection cached for next run (remove $CACHE_FILE to reset)"
echo ""

# ── Build & Start ────────────────────────────────────────────────────────────

# Always start the core services
info "Building and starting MCP Server + Admin..."
HOST_MCP_PORT="$HOST_MCP_PORT" HOST_ADMIN_PORT="$HOST_ADMIN_PORT" docker compose up -d --build

if $START_PROXY; then
    # Build connector services list
    CONNECTOR_SERVICES="proxy"
    for c in "${CHAT_CONNECTORS[@]-}"; do
        [[ -n "$c" ]] || continue
        CONNECTOR_SERVICES="$CONNECTOR_SERVICES $c"
    done

    info "Building and starting Chat Proxy + connectors..."
    MEMCP_URL="http://host.docker.internal:${HOST_MCP_PORT}" HOST_PROXY_PORT="$HOST_PROXY_PORT" docker compose -f connectors/docker-compose.yml up -d --build $CONNECTOR_SERVICES
fi

# ── Done ─────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}All selected services are starting.${NC}"
echo ""
echo -e "  MCP API:    http://localhost:$HOST_MCP_PORT"
echo -e "  Admin UI:   http://localhost:$HOST_ADMIN_PORT"
if $START_PROXY; then
    echo -e "  Chat Proxy: http://localhost:$HOST_PROXY_PORT"
fi
echo ""
echo -e "${DIM}Use 'docker compose logs -f' to follow logs.${NC}"
echo -e "${DIM}Use './stop.sh' or 'docker compose down' to stop services.${NC}"
echo ""
