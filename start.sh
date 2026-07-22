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

# Toggle-menu: navigate with arrow keys, Space to toggle, Enter to confirm.
# Compatible with bash 3.2 (macOS default) — no namerefs.
#
# Usage: config_menu PROXY_VAR TELEGRAM_VAR SLACK_VAR DISCORD_VAR
# Each named var holds "true"/"false"; values are read via ${!var} and written
# back with printf -v.
config_menu() {
    local var_proxy="$1" var_tg="$2" var_sl="$3" var_di="$4"

    local labels=(
        "Chat Proxy        — LLM-powered chat (:8001)"
        "  Telegram bot    — requires TELEGRAM_TOKEN"
        "  Slack bot       — requires SLACK_BOT_TOKEN"
        "  Discord bot     — requires DISCORD_TOKEN"
    )
    local is_connector=(0 1 1 1)
    local count=4
    local cursor=0

    # state array: 0=false 1=true — read initial values from caller vars
    local s0=0 s1=0 s2=0 s3=0
    [[ "${!var_proxy}" == "true" ]] && s0=1
    [[ "${!var_tg}"    == "true" ]] && s1=1
    [[ "${!var_sl}"    == "true" ]] && s2=1
    [[ "${!var_di}"    == "true" ]] && s3=1

    _cm_state() { eval "echo \$s$1"; }
    _cm_set()   { eval "s$1=$2"; }

    _cm_draw() {
        local i proxy_on connector check
        proxy_on=$(( s0 ))
        for ((i = 0; i < count; i++)); do
            connector=${is_connector[$i]}
            local cur_state; cur_state=$(_cm_state "$i")
            if [[ "$connector" == "1" && "$proxy_on" == "0" ]]; then
                [[ "$i" == "$cursor" ]] \
                    && printf "  \033[2m▶ [ ] %s\033[0m\n" "${labels[$i]}" \
                    || printf "    \033[2m[ ] %s\033[0m\n" "${labels[$i]}"
                continue
            fi
            check="  "
            [[ "$cur_state" == "1" ]] && check="\033[0;32m✓\033[0m "
            [[ "$i" == "$cursor" ]] \
                && printf "  \033[1m▶ [%b] %s\033[0m\n" "$check" "${labels[$i]}" \
                || printf "    [%b] %s\n" "$check" "${labels[$i]}"
        done
        printf "  \033[2m(↑↓ navigate · Space toggle · Enter confirm)\033[0m\n"
    }

    tput civis 2>/dev/null || true
    printf "\n\033[1m  Services to start:\033[0m\n"
    printf "\033[2m  MCP Server + Admin is always on.\033[0m\n\n"
    _cm_draw
    local draw_lines=$(( count + 1 ))

    local key rest
    while true; do
        IFS= read -rsn1 key
        if [[ "$key" == $'\x1b' ]]; then
            IFS= read -rsn1 rest; key="$key$rest"
            IFS= read -rsn1 rest; key="$key$rest"
        fi

        case "$key" in
            $'\x1b[A')  (( cursor = (cursor - 1 + count) % count )) ;;
            $'\x1b[B')  (( cursor = (cursor + 1) % count )) ;;
            ' ')
                local cur_state; cur_state=$(_cm_state "$cursor")
                if [[ "${is_connector[$cursor]}" == "1" && "$s0" == "0" ]]; then
                    :  # proxy off — connector rows locked
                else
                    local new_val=$(( 1 - cur_state ))
                    _cm_set "$cursor" "$new_val"
                    # proxy turned off — clear connectors
                    if [[ "$cursor" == "0" && "$new_val" == "0" ]]; then
                        s1=0; s2=0; s3=0
                    fi
                fi
                ;;
            ''|$'\n') break ;;
        esac

        tput cuu "$draw_lines" 2>/dev/null || printf '\033[%dA' "$draw_lines"
        _cm_draw
    done

    tput cnorm 2>/dev/null || true
    printf '\n'

    [[ "$s0" == "1" ]] && printf -v "$var_proxy" "true" || printf -v "$var_proxy" "false"
    [[ "$s1" == "1" ]] && printf -v "$var_tg"    "true" || printf -v "$var_tg"    "false"
    [[ "$s2" == "1" ]] && printf -v "$var_sl"    "true" || printf -v "$var_sl"    "false"
    [[ "$s3" == "1" ]] && printf -v "$var_di"    "true" || printf -v "$var_di"    "false"
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
CACHED_PROXY="false"
CACHED_TELEGRAM="false"
CACHED_SLACK="false"
CACHED_DISCORD="false"

START_PROXY=false
CHAT_CONNECTORS=()
MAX_PORT_INCREMENTS=10

HOST_MCP_PORT=""
HOST_ADMIN_PORT=""
HOST_PROXY_PORT=""

echo ""
echo -e "${BOLD}  meMCP — Docker Launcher${NC}"

if [[ -f "$CACHE_FILE" ]]; then
    source "$CACHE_FILE"
    CACHE_DATE="$(stat -f %Sm -t '%Y-%m-%d %H:%M' "$CACHE_FILE" 2>/dev/null || echo 'unknown date')"
    echo -e "${DIM}  Last saved: $CACHE_DATE${NC}"
fi

# ── Config menu (always shown, pre-filled from cache) ───────────────────────
config_menu CACHED_PROXY CACHED_TELEGRAM CACHED_SLACK CACHED_DISCORD

[[ "$CACHED_PROXY"    == "true" ]] && START_PROXY=true
[[ "$CACHED_TELEGRAM" == "true" ]] && CHAT_CONNECTORS+=("telegram")
[[ "$CACHED_SLACK"    == "true" ]] && CHAT_CONNECTORS+=("slack")
[[ "$CACHED_DISCORD"  == "true" ]] && CHAT_CONNECTORS+=("discord")

# ── Validate .env files ─────────────────────────────────────────────────────
if [[ ! -f .env ]]; then
    if [[ -f .env.example ]]; then
        warn "No .env found. Copying .env.example -> .env"
        cp .env.example .env
        warn "Please edit .env with your admin credentials."
    fi
fi

# ── Check CONFIG_SECRET ──────────────────────────────────────────────────────
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
_cache_telegram="false"; _cache_slack="false"; _cache_discord="false"
for _c in "${CHAT_CONNECTORS[@]-}"; do
    case "$_c" in
        telegram) _cache_telegram="true" ;;
        slack)    _cache_slack="true"    ;;
        discord)  _cache_discord="true"  ;;
    esac
done

cat > "$CACHE_FILE" << EOF
# meMCP start.sh cache — autogenerated
CACHED_PROXY="$START_PROXY"
CACHED_TELEGRAM="$_cache_telegram"
CACHED_SLACK="$_cache_slack"
CACHED_DISCORD="$_cache_discord"
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
echo -e "  Admin UI:   http://localhost:$HOST_ADMIN_PORT/ui"
if $START_PROXY; then
    echo -e "  Chat Proxy: http://localhost:$HOST_PROXY_PORT"
fi
echo ""
echo -e "${DIM}Use 'docker compose logs -f' to follow logs.${NC}"
echo -e "${DIM}Use './stop.sh' or 'docker compose down' to stop services.${NC}"
echo ""
