from datetime import datetime, timedelta

# UTCC is in Bangkok (UTC+7); all timestamps are stored in UTC, this is only
# for user-facing display strings like Contact.last_message_at.
BANGKOK_OFFSET = timedelta(hours=7)


def bangkok_now_str(fmt: str = "%H:%M") -> str:
    return (datetime.utcnow() + BANGKOK_OFFSET).strftime(fmt)
