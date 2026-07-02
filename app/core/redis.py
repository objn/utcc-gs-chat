import os


def get_redis_url() -> str:
    raw = os.getenv("REDIS_URL", "")
    if "${" in raw or not raw:
        password = os.getenv("REDIS_PASSWORD", "")
        host = os.getenv("REDIS_HOST", "localhost")
        port = os.getenv("REDIS_PORT", "6379")
        auth = f":{password}@" if password else ""
        return f"redis://{auth}{host}:{port}/0"
    return raw
