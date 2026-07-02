import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def load_env() -> None:
    """Load .env for local (non-docker) runs.

    docker-compose injects .env into containers via env_file, so this is a
    no-op there. Outside docker, nothing loads .env automatically, so
    running "uv run uvicorn ..." or "uv run celery ..." directly left vars
    like DATABASE_URL/REDIS_URL/POSTGRES_PASSWORD unset.

    DATABASE_URL and REDIS_URL in .env intentionally target the docker
    network (postgres/redis service hostnames — see the "ใช้ใน docker
    network" comment in .env) — they only resolve inside a container. If we
    blindly loaded them outside docker, DNS lookups for "postgres"/"redis"
    would fail. So outside docker, drop those two and let
    app.db.session / app.core.redis reconstruct a localhost-based URL from
    the individual POSTGRES_*/REDIS_* pieces instead (their existing
    fallback already does this whenever DATABASE_URL/REDIS_URL are unset).
    """
    load_dotenv(PROJECT_ROOT / ".env")

    if not Path("/.dockerenv").exists():
        os.environ.pop("DATABASE_URL", None)
        os.environ.pop("REDIS_URL", None)
