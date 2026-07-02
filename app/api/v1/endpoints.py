import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.redis import get_redis_url
from app.core.time import bangkok_now_str
from app.db.session import engine
from app.models.config import Config
from app.models.contact import Contact
from app.models.message import Message

router = APIRouter()
logger = logging.getLogger("gs_chat")

GRAPH_API_BASE = "https://graph.facebook.com/v25.0"

UPLOADS_DIR = Path(__file__).resolve().parents[3] / "app" / "frontend" / "static" / "uploads"


def _save_upload(file_bytes: bytes, filename: str) -> str:
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    ext = Path(filename).suffix.lower()[:10]
    safe_name = f"{uuid.uuid4().hex}{ext}"
    (UPLOADS_DIR / safe_name).write_bytes(file_bytes)
    return f"/static/uploads/{safe_name}"

AGENT_DISABLE_KEYWORDS = ["ติดต่อเจ้าหน้าที่", "ขอคุยกับเจ้าหน้าที่", "ขอคุยกับคน"]

# ── Live update broadcast (WebSocket) ──
# In-process pub/sub so /messages can get pushed updates instead of polling.
# One connected WebSocket per browser tab, held open indefinitely — unlike
# the SSE version this replaced, there's no forced reconnect cycle, so
# there's no gap where an event fires between one connection closing and
# the next opening (that gap silently dropped events under SSE).
_ws_subscribers: set[WebSocket] = set()


async def _broadcast(event: dict):
    dead = []
    for ws in list(_ws_subscribers):
        try:
            await ws.send_json(event)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_subscribers.discard(ws)


@router.websocket("/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    _ws_subscribers.add(websocket)
    try:
        while True:
            # Nothing meaningful is expected from the client — this just
            # blocks until the socket closes, so we can detect disconnects
            # and clean up. Uvicorn answers ping/pong frames at the
            # protocol level, so no app-level heartbeat is needed here.
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_subscribers.discard(websocket)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


async def _get_session():
    async with AsyncSession(engine) as session:
        yield session


class ConfigPayload(BaseModel):
    data: dict


class IncomingMessage(BaseModel):
    platform_id: str
    platform: str = "facebook"
    display_name: str = ""
    page_id: str = ""
    message: str = ""


# ── Health ──


@router.get("/health")
async def health():
    return {"status": "ok"}


# ── Config CRUD ──


@router.get("/config/{config_id}")
async def get_config(config_id: str, session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Config).where(Config.config_id == config_id, Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()
    if not cfg:
        return {"config_id": config_id, "data": {}}
    return {"config_id": cfg.config_id, "data": cfg.data, "updated_at": cfg.updated_at.isoformat()}


@router.put("/config/{config_id}")
async def upsert_config(config_id: str, payload: ConfigPayload, session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Config).where(Config.config_id == config_id, Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()

    if cfg:
        cfg.data = payload.data
        cfg.updated_at = datetime.utcnow()
    else:
        cfg = Config(config_id=config_id, data=payload.data)
        session.add(cfg)

    await session.commit()
    await session.refresh(cfg)
    return {"config_id": cfg.config_id, "data": cfg.data, "updated_at": cfg.updated_at.isoformat()}


@router.delete("/config/{config_id}")
async def delete_config(config_id: str, session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Config).where(Config.config_id == config_id, Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()
    if not cfg:
        return {"ok": False, "detail": "not found"}
    cfg.deleted_at = datetime.utcnow()
    await session.commit()
    return {"ok": True}


@router.post("/graph-api/test-connection")
async def graph_api_test_connection(session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Config).where(Config.config_id == "graph_api", Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()
    page_token = cfg.data.get("page_access_token", "") if cfg else ""

    if not page_token:
        return {"ok": False, "error": "Page Access Token is not configured"}

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GRAPH_API_BASE}/me",
                params={"fields": "id,name", "access_token": page_token},
            )
        body = resp.json()
        if resp.status_code != 200:
            return {"ok": False, "error": body.get("error", {}).get("message", "Connection failed")}
        return {"ok": True, "id": body.get("id"), "name": body.get("name")}
    except Exception as e:
        logger.error("Graph API test-connection failed: %s", e)
        return {"ok": False, "error": str(e)}


@router.post("/webhub/test-connection")
async def webhub_test_connection(session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Config).where(Config.config_id == "webhub_utcc", Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()

    if not cfg or not cfg.data.get("base_url") or not cfg.data.get("username"):
        return {"ok": False, "error": "Base URL / username / password are not configured"}

    try:
        base_url = cfg.data["base_url"].rstrip("/")
        body = await _webhub_login(base_url, cfg.data.get("username", ""), cfg.data.get("password", ""))
        token = body["token"]
        faculty_id = body["faculty_id"]
        expires_at = _decode_jwt_exp(token) or (datetime.utcnow() + timedelta(hours=1))

        cfg.data = {
            **cfg.data,
            "token": token,
            "token_expires_at": expires_at.isoformat(),
            "faculty_id": faculty_id,
        }
        session.add(cfg)
        await session.commit()

        return {
            "ok": True,
            "faculty_name": body.get("faculty_name"),
            "user_id": body.get("user_id"),
            "username": body.get("username"),
        }
    except Exception as e:
        logger.error("Webhub test-connection failed: %s", e)
        return {"ok": False, "error": str(e)}


# ── Contacts ──


@router.get("/contacts")
async def list_contacts(session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Contact).where(Contact.deleted_at.is_(None)).order_by(Contact.updated_at.desc()))  # type: ignore[arg-type]
    contacts = result.all()
    return [
        {
            "id": str(c.id),
            "platform_id": c.platform_id,
            "platform": c.platform,
            "display_name": c.display_name,
            "avatar_color": c.avatar_color,
            "avatar_url": c.avatar_url,
            "agent_chat_enabled": c.agent_chat_enabled,
            "last_message": c.last_message,
            "last_message_at": c.last_message_at,
        }
        for c in contacts
    ]


@router.patch("/contacts/{contact_id}/agent-chat")
async def toggle_agent_chat(contact_id: str, session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None)))  # type: ignore[arg-type]
    contact = result.first()
    if not contact:
        return {"ok": False, "detail": "not found"}
    contact.agent_chat_enabled = not contact.agent_chat_enabled
    await session.commit()
    await session.refresh(contact)
    return {"ok": True, "agent_chat_enabled": contact.agent_chat_enabled}


@router.put("/contacts/{contact_id}/agent-chat/{state}")
async def set_agent_chat(contact_id: str, state: str, session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None)))  # type: ignore[arg-type]
    contact = result.first()
    if not contact:
        return {"ok": False, "detail": "not found"}
    contact.agent_chat_enabled = state == "on"
    await session.commit()
    await session.refresh(contact)
    return {"ok": True, "agent_chat_enabled": contact.agent_chat_enabled}


@router.get("/contacts/{contact_id}/messages")
async def list_messages(contact_id: str, session: AsyncSession = Depends(_get_session)):
    result = await session.exec(
        select(Message).where(Message.contact_id == contact_id).order_by(Message.created_at.asc())  # type: ignore[arg-type]
    )
    messages = result.all()
    return [
        {
            "id": str(m.id),
            "direction": m.direction,
            "text": m.text,
            "attachment_type": m.attachment_type,
            "attachment_url": m.attachment_url,
            "created_at": m.created_at.isoformat() + "Z",
        }
        for m in messages
    ]


@router.post("/contacts/{contact_id}/messages")
async def send_message(
    contact_id: str,
    text: str | None = Form(default=None),
    file: UploadFile | None = File(default=None),
    session: AsyncSession = Depends(_get_session),
):
    result = await session.exec(select(Contact).where(Contact.id == contact_id, Contact.deleted_at.is_(None)))  # type: ignore[arg-type]
    contact = result.first()
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")

    cfg_result = await session.exec(select(Config).where(Config.config_id == "graph_api", Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = cfg_result.first()
    page_token = cfg.data.get("page_access_token", "") if cfg else ""
    if not page_token:
        raise HTTPException(status_code=400, detail="Graph API is not configured")

    text = (text or "").strip()
    created: list[Message] = []

    if file is not None and file.filename:
        content_type = file.content_type or ""
        attachment_type = "image" if content_type.startswith("image/") else "file"
        file_bytes = await file.read()

        try:
            await _fb_send_attachment(contact.platform_id, file_bytes, file.filename, content_type, attachment_type, page_token)
        except Exception as e:
            logger.error("Facebook attachment send failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Failed to send attachment: {e}")

        local_url = _save_upload(file_bytes, file.filename)
        msg = Message(contact_id=contact.id, direction="out", attachment_type=attachment_type, attachment_url=local_url)
        session.add(msg)
        created.append(msg)
        contact.last_message = "📎 Photo" if attachment_type == "image" else f"📎 {file.filename}"

    if text:
        try:
            await _fb_send_text(contact.platform_id, text, page_token)
        except Exception as e:
            logger.error("Facebook text send failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Failed to send message: {e}")

        msg = Message(contact_id=contact.id, direction="out", text=text)
        session.add(msg)
        created.append(msg)
        contact.last_message = text[:1000]

    if not created:
        raise HTTPException(status_code=400, detail="Provide text or a file to send")

    contact.last_message_at = bangkok_now_str()
    contact.updated_at = datetime.utcnow()
    contact_id_str = str(contact.id)

    await session.commit()
    for msg in created:
        await session.refresh(msg)

    await _broadcast({"type": "update", "contact_id": contact_id_str})

    return [
        {
            "id": str(m.id),
            "direction": m.direction,
            "text": m.text,
            "attachment_type": m.attachment_type,
            "attachment_url": m.attachment_url,
            "created_at": m.created_at.isoformat() + "Z",
        }
        for m in created
    ]


# ── Facebook Webhook ──


@router.get("/webhook/facebook")
async def facebook_verify(request: Request, session: AsyncSession = Depends(_get_session)):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    # env var takes priority, fallback to DB config
    verify_token = os.getenv("FB_VERIFY_TOKEN", "")
    if not verify_token:
        result = await session.exec(select(Config).where(Config.config_id == "graph_api", Config.deleted_at.is_(None)))  # type: ignore[arg-type]
        cfg = result.first()
        verify_token = cfg.data.get("verify_token", "") if cfg else ""

    if mode == "subscribe" and token == verify_token:
        logger.info("Facebook webhook verified")
        return PlainTextResponse(challenge or "")

    logger.warning("Facebook webhook verification failed: mode=%s token_match=%s", mode, token == verify_token)
    return PlainTextResponse("Forbidden", status_code=403)


async def _schedule_reply_flush(session: AsyncSession, contact_id: str, sender_id: str, message_text: str) -> None:
    # Buffers this message and (re)schedules a debounced reply so a burst of
    # quick messages from the same user gets answered once, after they stop
    # sending — see flush_reply_task in app/worker/tasks.py.
    from app.worker.tasks import flush_reply_task

    cfg_result = await session.exec(select(Config).where(Config.config_id == "webhub_utcc", Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = cfg_result.first()
    debounce_seconds = int(cfg.data.get("debounce_seconds", 10)) if cfg else 10

    client = aioredis.from_url(get_redis_url())
    try:
        gen = await client.incr(f"debounce:gen:{contact_id}")
        await client.rpush(f"debounce:buf:{contact_id}", message_text)
        await client.expire(f"debounce:buf:{contact_id}", 300)
        await client.expire(f"debounce:gen:{contact_id}", 300)
    finally:
        await client.aclose()

    flush_reply_task.apply_async(args=[contact_id, sender_id, gen], countdown=debounce_seconds)


@router.post("/webhook/facebook")
async def facebook_webhook(request: Request, session: AsyncSession = Depends(_get_session)):
    body = await request.json()

    if body.get("object") != "page":
        return {"status": "ignored"}

    cfg_result = await session.exec(select(Config).where(Config.config_id == "graph_api", Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = cfg_result.first()
    page_token = cfg.data.get("page_access_token", "") if cfg else ""

    for entry in body.get("entry", []):
        for event in entry.get("messaging", []):
            message_data = event.get("message", {})
            if message_data.get("is_echo"):
                continue

            sender_id = event.get("sender", {}).get("id", "")
            page_id = event.get("recipient", {}).get("id", "")
            message_text = message_data.get("text", "")
            attachments = message_data.get("attachments", [])

            if not sender_id or (not message_text and not attachments):
                continue

            msg = IncomingMessage(
                platform_id=sender_id,
                platform="facebook",
                display_name=sender_id,
                page_id=page_id,
                message=message_text,
            )
            contact = await _get_or_create_contact(session, msg, page_token)

            if message_text:
                session.add(Message(contact_id=contact.id, direction="in", text=message_text))
                contact.last_message = message_text[:1000]

            for att in attachments:
                att_type = att.get("type", "file")
                att_url = att.get("payload", {}).get("url", "")
                session.add(Message(contact_id=contact.id, direction="in", attachment_type=att_type, attachment_url=att_url))
                if not message_text:
                    contact.last_message = "📎 Photo" if att_type == "image" else f"📎 {att_type.title()}"

            contact.last_message_at = bangkok_now_str()
            contact.updated_at = datetime.utcnow()

            for kw in AGENT_DISABLE_KEYWORDS:
                if kw in message_text:
                    contact.agent_chat_enabled = False
                    break

            contact_id_str = str(contact.id)
            agent_chat_enabled = contact.agent_chat_enabled
            await session.commit()

            if message_text and agent_chat_enabled:
                await _schedule_reply_flush(session, contact_id_str, sender_id, message_text)

            await _broadcast({"type": "update", "contact_id": contact_id_str})

    return {"status": "ok"}


def _raise_for_graph_error(resp: httpx.Response):
    if resp.status_code == 200:
        return
    try:
        detail = resp.json().get("error", {}).get("message", resp.text)
    except Exception:
        detail = resp.text
    raise RuntimeError(detail)


async def _fb_send_text(recipient_id: str, text: str, page_token: str):
    url = f"{GRAPH_API_BASE}/me/messages?access_token={page_token}"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload)
    _raise_for_graph_error(resp)


async def _fb_send_attachment(
    recipient_id: str,
    file_bytes: bytes,
    filename: str,
    content_type: str,
    attachment_type: str,
    page_token: str,
):
    url = f"{GRAPH_API_BASE}/me/messages?access_token={page_token}"
    data = {
        "recipient": '{"id":"%s"}' % recipient_id,
        "message": '{"attachment":{"type":"%s","payload":{}}}' % attachment_type,
    }
    files = {"filedata": (filename, file_bytes, content_type or "application/octet-stream")}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, data=data, files=files)
    _raise_for_graph_error(resp)


# ── Webhook: incoming message (generic) ──


async def _fetch_fb_profile(psid: str, page_token: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                f"{GRAPH_API_BASE}/{psid}",
                params={"fields": "first_name,last_name,profile_pic", "access_token": page_token},
            )
        if resp.status_code != 200:
            logger.error("Facebook profile fetch failed for %s: %s %s", psid, resp.status_code, resp.text)
            return None
        body = resp.json()
        name = " ".join(filter(None, [body.get("first_name"), body.get("last_name")])).strip()
        return {"name": name or None, "avatar_url": body.get("profile_pic")}
    except Exception as e:
        logger.error("Failed to fetch Facebook profile: %s", e)
        return None


async def _get_or_create_contact(session: AsyncSession, msg: IncomingMessage, page_token: str | None = None) -> Contact:
    result = await session.exec(
        select(Contact).where(
            Contact.platform_id == msg.platform_id,
            Contact.platform == msg.platform,
            Contact.deleted_at.is_(None),  # type: ignore[arg-type]
        )
    )
    contact = result.first()
    if not contact:
        colors = ["#1877f2", "#e74c3c", "#27ae60", "#8e44ad", "#f39c12", "#2c3e50", "#16a085", "#d35400"]
        import hashlib
        idx = int(hashlib.md5(msg.platform_id.encode()).hexdigest(), 16) % len(colors)

        display_name = msg.display_name or msg.platform_id
        avatar_url = None
        if page_token and msg.platform == "facebook":
            profile = await _fetch_fb_profile(msg.platform_id, page_token)
            if profile:
                if profile["name"]:
                    display_name = profile["name"]
                avatar_url = profile["avatar_url"]

        contact = Contact(
            platform_id=msg.platform_id,
            platform=msg.platform,
            display_name=display_name,
            avatar_color=colors[idx],
            avatar_url=avatar_url,
            page_id=msg.page_id,
            agent_chat_enabled=True,
        )
        session.add(contact)
        await session.commit()
        await session.refresh(contact)
    elif contact.display_name == contact.platform_id and page_token and msg.platform == "facebook":
        # The profile fetch failed or had no token available when this
        # contact was first created, so display_name fell back to the raw
        # platform id. Retry it on a later message now that a token exists,
        # instead of leaving the id stuck as the display name forever.
        profile = await _fetch_fb_profile(msg.platform_id, page_token)
        if profile and profile["name"]:
            contact.display_name = profile["name"]
            if profile["avatar_url"]:
                contact.avatar_url = profile["avatar_url"]
            session.add(contact)
    return contact


def _decode_jwt_exp(token: str) -> datetime | None:
    try:
        import base64

        payload_b64 = token.split(".")[1]
        payload_b64 += "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        exp = payload.get("exp")
        return datetime.utcfromtimestamp(exp) if exp else None
    except Exception:
        return None


async def _webhub_login(base_url: str, username: str, password: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{base_url}/auth/login",
            json={"username": username, "password": password},
        )
    if resp.status_code != 200:
        try:
            detail = resp.json().get("message", resp.text)
        except Exception:
            detail = resp.text
        raise RuntimeError(detail)
    return resp.json()


async def _get_webhub_token(session: AsyncSession, cfg: Config, force_refresh: bool = False) -> tuple[str, int]:
    now = datetime.utcnow()
    cached_token = cfg.data.get("token")
    cached_expires_at = cfg.data.get("token_expires_at")
    cached_faculty_id = cfg.data.get("faculty_id")

    if not force_refresh and cached_token and cached_expires_at and cached_faculty_id is not None:
        expires_at = datetime.fromisoformat(cached_expires_at)
        if (expires_at - now).total_seconds() > 60:
            return cached_token, cached_faculty_id

    base_url = cfg.data["base_url"].rstrip("/")
    body = await _webhub_login(base_url, cfg.data.get("username", ""), cfg.data.get("password", ""))
    token = body["token"]
    faculty_id = body["faculty_id"]
    expires_at = _decode_jwt_exp(token) or (now + timedelta(hours=1))

    cfg.data = {
        **cfg.data,
        "token": token,
        "token_expires_at": expires_at.isoformat(),
        "faculty_id": faculty_id,
    }
    session.add(cfg)
    await session.commit()
    await session.refresh(cfg)

    return token, faculty_id


async def _relay_to_webhub_or_raise(session: AsyncSession, message: str, session_id: str) -> str | None:
    result = await session.exec(select(Config).where(Config.config_id == "webhub_utcc", Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()
    if not cfg or not cfg.data.get("base_url") or not cfg.data.get("username"):
        return None

    base_url = cfg.data["base_url"].rstrip("/")
    timeout = int(cfg.data.get("timeout", 30))

    async def _send(force_refresh: bool) -> httpx.Response:
        token, faculty_id = await _get_webhub_token(session, cfg, force_refresh=force_refresh)
        async with httpx.AsyncClient(timeout=timeout) as client:
            return await client.post(
                f"{base_url}/chat_webui/message",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "use_utcc_workflow": True,
                    "faculty_id": faculty_id,
                    "user_input": message,
                    "session_id": session_id,
                },
            )

    resp = await _send(force_refresh=False)
    if resp.status_code == 401:
        resp = await _send(force_refresh=True)
    resp.raise_for_status()
    return resp.json().get("response")


async def _relay_to_webhub(session: AsyncSession, message: str, session_id: str) -> str | None:
    try:
        return await _relay_to_webhub_or_raise(session, message, session_id)
    except Exception as e:
        logger.error("Webhub relay failed: %s", e)
        return None


@router.post("/webhook/incoming")
async def webhook_incoming(msg: IncomingMessage, session: AsyncSession = Depends(_get_session)):
    contact = await _get_or_create_contact(session, msg)

    if msg.message:
        session.add(Message(contact_id=contact.id, direction="in", text=msg.message))

    contact.last_message = msg.message[:1000] if msg.message else ""
    contact.last_message_at = bangkok_now_str()
    contact.updated_at = datetime.utcnow()

    # Check for agent-disable keyword
    for kw in AGENT_DISABLE_KEYWORDS:
        if kw in msg.message:
            contact.agent_chat_enabled = False
            contact_id_str = str(contact.id)
            await session.commit()
            await session.refresh(contact)
            await _broadcast({"type": "update", "contact_id": contact_id_str})
            return {
                "contact_id": contact_id_str,
                "agent_chat_enabled": False,
                "reply": None,
                "reason": "agent_disabled_by_keyword",
            }

    if not contact.agent_chat_enabled:
        contact_id_str = str(contact.id)
        await session.commit()
        await _broadcast({"type": "update", "contact_id": contact_id_str})
        return {
            "contact_id": contact_id_str,
            "agent_chat_enabled": False,
            "reply": None,
            "reason": "agent_chat_off",
        }

    # Relay to webhub
    reply = await _relay_to_webhub(session, msg.message, msg.platform_id)
    if reply:
        session.add(Message(contact_id=contact.id, direction="out", text=reply))
        contact.last_message = reply[:1000]
        contact.last_message_at = bangkok_now_str()

    contact_id_str = str(contact.id)
    await session.commit()
    await session.refresh(contact)
    await _broadcast({"type": "update", "contact_id": contact_id_str})
    return {
        "contact_id": contact_id_str,
        "agent_chat_enabled": True,
        "reply": reply,
    }
