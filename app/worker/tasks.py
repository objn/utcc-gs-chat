import asyncio
import json
import logging
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.redis import get_redis_url
from app.core.time import bangkok_now_str
from app.db.session import DATABASE_URL
from app.models.config import Config
from app.models.contact import Contact
from app.models.message import Message
from app.worker.celery_app import celery

logger = logging.getLogger("gs_chat")

REDIS_EVENTS_CHANNEL = "gs_chat:events"


async def _publish_event(event: dict):
    # Fresh client per call: an async Redis connection is bound to the event
    # loop it was created in, and each Celery task run gets a new loop via
    # asyncio.run(), so a cached client can't be reused across task calls.
    #
    # Best-effort: a broadcast failure must not fail the task, since the
    # reply has already been sent to Facebook and persisted by this point —
    # retrying the whole task here would re-call the chatbot and could
    # double-send to Facebook.
    client = aioredis.from_url(get_redis_url())
    try:
        await client.publish(REDIS_EVENTS_CHANNEL, json.dumps(event))
    except Exception as e:
        logger.error("SSE broadcast failed (Redis unavailable?): %s", e)
    finally:
        await client.aclose()


async def _resolve_combined_text(contact_id: str, generation: int) -> str | None:
    # Consumes the debounce buffer exactly once per generation. Called only
    # on a task's first attempt (see flush_reply_task) — retries reuse the
    # text they already extracted instead of re-reading here, since a
    # failed send must not lose the message the buffer already handed out.
    client = aioredis.from_url(get_redis_url())
    try:
        current_gen = await client.get(f"debounce:gen:{contact_id}")
        if current_gen is None or int(current_gen) != generation:
            # A newer message arrived after this flush was scheduled — a
            # later flush (with a higher generation) already owns replying.
            return None

        buf_key = f"debounce:buf:{contact_id}"
        messages = await client.lrange(buf_key, 0, -1)
        await client.delete(buf_key)
        if not messages:
            return None
        return "\n".join(m.decode() if isinstance(m, bytes) else m for m in messages)
    finally:
        await client.aclose()


async def _send_reply_async(contact_id: str, sender_id: str, combined_text: str, generation: int):
    from app.api.v1.endpoints import CONTACT_STAFF_QUICK_REPLY, _fb_send_text, _relay_to_webhub_or_raise

    # Same reasoning as the Redis client above: build a fresh engine per
    # call rather than reusing app.db.session's shared singleton, since its
    # connection pool is bound to the event loop it was created in.
    engine = create_async_engine(DATABASE_URL)
    redis_client = aioredis.from_url(get_redis_url())
    # Keyed by (contact, generation) so a retry after a mid-flight failure
    # (e.g. the Facebook send succeeded but the DB commit below didn't)
    # replays only the still-unfinished part instead of re-asking the
    # chatbot for a new answer and double-sending it to the user.
    reply_key = f"reply:{contact_id}:{generation}"
    sent_key = f"sent:{contact_id}:{generation}"
    try:
        async with AsyncSession(engine) as session:
            result = await session.exec(
                select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))  # type: ignore[arg-type]
            )
            contact = result.first()
            if not contact or not contact.agent_chat_enabled:
                return

            cached_reply = await redis_client.get(reply_key)
            if cached_reply is not None:
                reply = cached_reply.decode() if isinstance(cached_reply, bytes) else cached_reply
            else:
                reply = await _relay_to_webhub_or_raise(session, combined_text, contact.platform_id)
                if not reply:
                    return
                await redis_client.set(reply_key, reply, ex=3600)

            if not await redis_client.exists(sent_key):
                cfg_result = await session.exec(
                    select(Config).where(Config.config_id == "graph_api", Config.deleted_at.is_(None))  # type: ignore[arg-type]
                )
                cfg = cfg_result.first()
                page_token = cfg.data.get("page_access_token", "") if cfg else ""

                if page_token:
                    try:
                        await _fb_send_text(sender_id, reply, page_token, quick_replies=CONTACT_STAFF_QUICK_REPLY)
                    except Exception as e:
                        logger.error("Facebook send failed (worker): %s", e)

                # Marked delivered before the DB write below, so if the
                # commit fails and this task retries, it skips straight to
                # persisting instead of sending the reply a second time.
                await redis_client.set(sent_key, "1", ex=3600)

            session.add(Message(contact_id=contact.id, direction="out", text=reply))
            contact.last_message = reply[:1000]
            contact.last_message_at = bangkok_now_str()
            contact.updated_at = datetime.utcnow()
            contact_id_str = str(contact.id)

            await session.commit()
            await _publish_event({"type": "update", "contact_id": contact_id_str})
    finally:
        await redis_client.aclose()
        await engine.dispose()


@celery.task(bind=True, max_retries=5, default_retry_delay=15)
def flush_reply_task(self, contact_id: str, sender_id: str, generation: int, combined_text: str | None = None):
    if combined_text is None:
        combined_text = asyncio.run(_resolve_combined_text(contact_id, generation))
        if combined_text is None:
            return

    try:
        asyncio.run(_send_reply_async(contact_id, sender_id, combined_text, generation))
    except Exception as exc:
        if self.request.retries >= self.max_retries:
            logger.error(
                "flush_reply_task: permanently failed for contact %s after %s retries: %s",
                contact_id, self.request.retries, exc,
            )
            return
        backoff = min(15 * (2 ** self.request.retries), 300)
        raise self.retry(exc=exc, countdown=backoff, args=[contact_id, sender_id, generation, combined_text])
