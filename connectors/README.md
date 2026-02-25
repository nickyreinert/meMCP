# meMCP Chat Connectors

Connect Telegram, Slack or Discord to a meMCP server via a **Chat Proxy** that orchestrates conversations between the user, an LLM and the meMCP API.

## Architecture

```
Telegram / Slack / Discord
    ↓  Bot Adapter  (platform-specific, ~50 lines, lives in connectors/<platform>/)
    ↓  POST /chat  { chat_id, message }   X-Proxy-Secret: •••
Chat Proxy  (port 8001)   ← the brain: sessions, LLM loop, rate limiting
    ↓  POST /mcp/tools/call  Authorization: Bearer <user-token>
meMCP  (port 8000)        ← unchanged, enforces token tiers
```

The proxy does **not** connect to any platform directly.
Each platform needs its own adapter that receives messages from that platform and forwards them here.

## Session flow

```
First message  → proxy asks for a meMCP access token
Next message   → token verified against meMCP, session becomes active
All messages   → LLM queries meMCP tools, returns natural-language reply

/disconnect    → clear session
/status        → show state and backend info
```

## Setup

### 1. Env file

```bash
cp connectors/.env.example connectors/.env
```

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | when `chat.host: groq` | Groq API key |
| `PROXY_SECRET` | recommended | Auth header secret + token encryption key |

### 2. `config.tech.yaml` — `chat:` section

```yaml
chat:
  host: groq                       # groq | ollama
  model: llama-3.3-70b-versatile
  ollama_url: http://localhost:11434
  memcp_url: http://localhost:8000
  db_path: data/proxy.db
  rate_limit_per_minute: 20
  max_history: 10
```

### 3. Start the proxy

```bash
cd connectors
docker compose up          # Docker
# or
cd connectors/proxy && pip install -r requirements.txt
uvicorn proxy:app --port 8001   # local dev
```

`curl localhost:8001/health`

---

## Security

| Layer | How |
|---|---|
| Adapter → proxy | `X-Proxy-Secret` header must match `PROXY_SECRET` |
| Token storage | Fernet-encrypted (key derived from `PROXY_SECRET`) |
| Rate limiting | `rate_limit_per_minute` per `chat_id`, HTTP 429 on breach |
| Tier enforcement | meMCP returns 403 → LLM tells user they need a higher-tier token |

`PROXY_SECRET` unset = no auth, plaintext storage — dev only.

---

## Bot adapters

- each adapter connects to the particular platform via long-polling, Socket Mode or Gateway
- on every incoming message: `POST /chat` to the proxy
- the proxy manages the conversation state and LLM interactions, then returns a reply that the adapter sends back to the user on the platform.

`chat_id` is namespaced per platform (`tg_…`, `slack_…`, `discord_…`) so sessions never collide.

### Telegram

**Required env vars:** `TELEGRAM_TOKEN`

#### Steps

1. Open Telegram, message [@BotFather](https://t.me/botfather) → `/newbot`
2. Copy the token it gives you
3. Add to `.env`:
   ```
   TELEGRAM_TOKEN=123456:ABC-DEF…
   ```
4. Start:
   ```bash
   # Docker (recommended)
   docker compose up telegram

   # Local dev
   cd connectors/telegram
   pip install -r requirements.txt
   TELEGRAM_TOKEN=… python bot.py
   ```

The bot uses **long-polling** — no public URL or webhook needed.
Users message the bot directly; the bot responds in the same chat.

---

### Slack

**Required env vars:** `SLACK_BOT_TOKEN` (xoxb-…), `SLACK_APP_TOKEN` (xapp-…)

The adapter uses **Socket Mode** — no public URL or inbound port required.

#### Steps

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → "From scratch"
2. **OAuth & Permissions** → Bot Token Scopes → add:
   - `chat:write`
   - `im:history`
   - `im:read`
   - `im:write`
3. Install the app to your workspace → copy **Bot User OAuth Token** (`xoxb-…`)
4. **Socket Mode** → Enable Socket Mode → generate an **App-Level Token** with scope `connections:write` → copy (`xapp-…`)
5. **Event Subscriptions** → Enable Events → Subscribe to bot events: `message.im`
6. Add to `.env`:
   ```
   SLACK_BOT_TOKEN=xoxb-…
   SLACK_APP_TOKEN=xapp-…
   ```
7. Start:
   ```bash
   # Docker
   docker compose up slack

   # Local dev
   cd connectors/slack
   pip install -r requirements.txt
   SLACK_BOT_TOKEN=… SLACK_APP_TOKEN=… python bot.py
   ```

Users send the bot a **direct message**. The bot responds in the same DM thread.

---

### Discord

**Required env vars:** `DISCORD_TOKEN`

The adapter responds to **DMs** and **@mentions** in servers.

#### Steps

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. **Bot** tab → **Add Bot** → copy the token
3. **Bot** tab → Privileged Gateway Intents → enable:
   - **Message Content Intent**
4. **OAuth2 → URL Generator** → Scopes: `bot` → Bot Permissions: `Send Messages`, `Read Message History`
5. Open the generated URL to invite the bot to your server
6. Add to `.env`:
   ```
   DISCORD_TOKEN=…
   ```
7. Start:
   ```bash
   # Docker
   docker compose up discord

   # Local dev
   cd connectors/discord
   pip install -r requirements.txt
   DISCORD_TOKEN=… python bot.py
   ```

The bot responds to DMs and to messages that @mention it in a channel.

---

### Start all adapters at once

```bash
cd connectors
docker compose up
```

Only include the adapters you need — comment out unused services in `docker-compose.yml`.
