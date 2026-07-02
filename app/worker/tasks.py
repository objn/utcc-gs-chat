import asyncio
import json
import logging
from datetime import datetime

import redis.asyncio as aioredis
from sqlalchemy.ext.asyncio import create_async_engine
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.redis import get_redis_url
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


async def _relay_message_async(contact_id: str, sender_id: str, message_text: str):
    from app.api.v1.endpoints import _fb_send_text, _relay_to_webhub

    # Same reasoning as the Redis client above: build a fresh engine per
    # call rather than reusing app.db.session's shared singleton, since its
    # connection pool is bound to the event loop it was created in.
    engine = create_async_engine(DATABASE_URL)
    try:
        async with AsyncSession(engine) as session:
            result = await session.exec(
                select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None))  # type: ignore[arg-type]
            )
            contact = result.first()
            if not contact:
                return

            reply = await _relay_to_webhub(session, message_text, sender_id)
            if not reply:
                return

            cfg_result = await session.exec(
                select(Config).where(Config.config_id == "graph_api", Config.deleted_at.is_(None))  # type: ignore[arg-type]
            )
            cfg = cfg_result.first()
            page_token = cfg.data.get("page_access_token", "") if cfg else ""

            if page_token:
                try:
                    await _fb_send_text(sender_id, reply, page_token)
                except Exception as e:
                    logger.error("Facebook send failed (worker): %s", e)

            session.add(Message(contact_id=contact.id, direction="out", text=reply))
            contact.last_message = reply[:1000]
            contact.last_message_at = datetime.utcnow().strftime("%H:%M")
            contact.updated_at = datetime.utcnow()
            contact_id_str = str(contact.id)

            await session.commit()
            await _publish_event({"type": "update", "contact_id": contact_id_str})
    finally:
        await engine.dispose()


@celery.task(bind=True, max_retries=2, default_retry_delay=5)
def relay_message_task(self, contact_id: str, sender_id: str, message_text: str):
    try:
        asyncio.run(_relay_message_async(contact_id, sender_id, message_text))
    except Exception as exc:
        logger.error("relay_message_task failed for contact %s: %s", contact_id, exc)
        raise self.retry(exc=exc)
