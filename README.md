# UTCC GS Chat

Management console for the UTCC Graduate School's Facebook Messenger page. Incoming Messenger messages are relayed to a Webhub UTCC chatbot backend for an automatic reply, with staff able to monitor conversations live and take over any chat at any time.

## Features

- **Facebook Messenger integration** — receives messages via the Graph API webhook, sends replies back through the same page.
- **AI auto-reply via Webhub UTCC** — relays messages to a configured Webhub UTCC chat backend and returns its response to the user.
- **Message debounce** — when a user sends several short messages in quick succession, the bot waits for a quiet period and replies once to the combined text instead of replying to each message separately.
- **Resilient delivery** — replies are generated in a background worker with retry-with-backoff if Webhub UTCC is temporarily unreachable; a reply is never sent twice even if a retry occurs after a partial failure.
- **Live dashboard** — `/messages` shows all conversations and updates in real time over WebSocket as new messages arrive or the bot replies.
- **Per-contact agent toggle** — staff can turn the AI off for a specific conversation (auto-disabled on keywords like "ติดต่อเจ้าหน้าที่") and reply manually instead.
- **Config UI** — Graph API and Webhub UTCC credentials/settings are managed from the web UI (`/graph-api`, `/webhub`), stored in the database rather than redeployed.

## Tech stack

| Layer | Choice |
|---|---|
| API / web UI | FastAPI + Jinja2 templates |
| Background jobs | Celery |
| Database | PostgreSQL (via SQLModel / SQLAlchemy async) |
| Broker / pub-sub / cache | Redis |
| Package manager | [uv](https://github.com/astral-sh/uv) |

## Installation

### Option 1 — Docker (recommended)

Requires [Docker](https://docs.docker.com/get-docker/) and Docker Compose.

```bash
git clone <repo-url>
cd "GS Chat"
cp .env.example .env
```

Edit `.env` and set real values, at minimum:
- `POSTGRES_PASSWORD`, `REDIS_PASSWORD` — pick strong passwords
- `API_SECRET_KEY` — random secret
- `FB_VERIFY_TOKEN` — the token you'll register in the Facebook App webhook setup

Then bring the full stack up (Postgres, Redis, the FastAPI app, and the Celery worker):

```bash
docker compose --profile app up -d --build
```

The app is served at `http://localhost:3000` (or whatever `APP_PORT` is set to in `.env`).

Useful follow-up commands:

```bash
docker compose logs -f app worker    # tail app + worker logs
docker compose ps                    # container status
docker compose --profile app down    # stop everything (keeps data volumes)
```

### Option 2 — Local development (no Docker for the app)

Requires Python 3.12+, [uv](https://github.com/astral-sh/uv), and a running Postgres + Redis (the easiest way to get those two without running the app in Docker too is `docker compose up -d postgres redis`).

```bash
uv sync
cp .env.example .env
```

Edit `.env` with the same values as above. `POSTGRES_PORT`/`REDIS_PORT` should point at your local Postgres/Redis (`localhost` by default when not running in Docker).

Run the app and the worker in two separate terminals:

```bash
# Terminal 1 — web app
uv run uvicorn app.main:app --host 0.0.0.0 --port 3000 --reload

# Terminal 2 — Celery worker
uv run celery -A app.worker.celery_app worker --loglevel=info

# if run in windows
uv run celery -A app.worker.celery_app worker --loglevel=info --pool=solo
```

> **Windows note:** Celery's default `prefork` pool requires Unix `fork()`, which isn't available natively on Windows. Add `--pool=solo` (single-threaded) or `--pool=threads --concurrency=4` (parallel) to the worker command above. This isn't needed inside Docker, since the container runs Linux.

### First run

1. Open the app in a browser and register the first admin account at `/register`.
2. Go to **Graph API** settings and fill in your Facebook Page's app credentials, page access token, and verify token (must match `FB_VERIFY_TOKEN`).
3. Go to **Webhub** settings and fill in the Webhub UTCC base URL, username/password, and optionally adjust the reply debounce window (default 10s).
4. Point your Facebook App's Messenger webhook at `https://<your-domain>/api/v1/webhook/facebook`.

## Project structure

```
app/
  api/v1/endpoints.py     # webhook handlers, contact/message API, live-update WebSocket
  frontend/                # web UI (routes, Jinja2 templates, static assets)
  worker/                  # Celery app + background tasks (debounced reply delivery)
  models/                  # SQLModel tables (Contact, Message, Config, User)
  core/                    # env loading, Redis URL helper
  db/                      # async engine/session setup
docker-compose.yml          # postgres, redis, app, worker services
Dockerfile                  # shared image for app + worker
```
