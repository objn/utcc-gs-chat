from datetime import datetime, timedelta

# UTCC is in Bangkok (UTC+7); all timestamps are stored in UTC, this is only
# for user-facing display strings like Contact.last_message_at.
BANGKOK_OFFSET = timedelta(hours=7)


def bangkok_now_str(fmt: str = "%H:%M") -> str:
    return (datetime.utcnow() + BANGKOK_OFFSET).strftime(fmt)


def bangkok_now() -> datetime:
    return datetime.utcnow() + BANGKOK_OFFSET


def bangkok_day_start_utc(days_ago: int = 0) -> datetime:
    """UTC instant corresponding to local midnight, `days_ago` days back."""
    local_midnight = bangkok_now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days_ago)
    return local_midnight - BANGKOK_OFFSET
