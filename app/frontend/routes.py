from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.security import hash_password, verify_password
from app.db.session import engine
from app.models.config import Config
from app.models.contact import Contact
from app.models.user import User

router = APIRouter()

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


async def _get_session():
    async with AsyncSession(engine) as session:
        yield session


async def _get_current_user(request: Request) -> User | None:
    user_id = request.cookies.get("session_user")
    if not user_id:
        return None
    async with AsyncSession(engine) as session:
        result = await session.exec(select(User).where(User.id == user_id, User.deleted_at.is_(None)))  # type: ignore[arg-type]
        return result.first()


async def _load_config(session: AsyncSession, config_id: str) -> Config:
    result = await session.exec(select(Config).where(Config.config_id == config_id, Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()
    if cfg:
        return cfg
    return Config(config_id=config_id, data={})


async def _save_config(session: AsyncSession, config_id: str, data: dict) -> Config:
    result = await session.exec(select(Config).where(Config.config_id == config_id, Config.deleted_at.is_(None)))  # type: ignore[arg-type]
    cfg = result.first()
    if cfg:
        cfg.data = data
        cfg.updated_at = datetime.utcnow()
    else:
        cfg = Config(config_id=config_id, data=data)
        session.add(cfg)
    await session.commit()
    await session.refresh(cfg)
    return cfg


# ── Auth pages ──


@router.get("/")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@router.post("/login")
async def login(request: Request, session: AsyncSession = Depends(_get_session)):
    form = await request.form()
    email = form.get("email", "")
    password = form.get("password", "")

    result = await session.exec(select(User).where(User.email == email, User.deleted_at.is_(None)))  # type: ignore[arg-type]
    user = result.first()

    if not user or not verify_password(str(password), user.hashed_password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Invalid email or password"}, status_code=401
        )

    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("session_user", str(user.id), httponly=True, max_age=86400)
    return response


@router.get("/register")
async def register_page(request: Request, session: AsyncSession = Depends(_get_session)):
    result = await session.exec(select(User.id).limit(1))
    has_users = result.first() is not None
    return templates.TemplateResponse(request, "register.html", {"has_users": has_users})


@router.post("/register")
async def register(request: Request, session: AsyncSession = Depends(_get_session)):
    form = await request.form()
    full_name = str(form.get("full_name", "")).strip()
    email = str(form.get("email", "")).strip()
    password = str(form.get("password", ""))
    password_confirm = str(form.get("password_confirm", ""))

    result = await session.exec(select(User.id).limit(1))
    has_users = result.first() is not None
    ctx = {"has_users": has_users}

    if not full_name or not email:
        return templates.TemplateResponse(
            request, "register.html", {**ctx, "error": "All fields are required"}
        )

    if password != password_confirm:
        return templates.TemplateResponse(
            request, "register.html", {**ctx, "error": "Passwords do not match"}
        )

    if len(password) < 8:
        return templates.TemplateResponse(
            request, "register.html", {**ctx, "error": "Password must be at least 8 characters"}
        )

    existing = await session.exec(select(User).where(User.email == email))
    if existing.first():
        return templates.TemplateResponse(
            request, "register.html", {**ctx, "error": "Email already registered"}
        )

    user = User(
        full_name=full_name,
        email=email,
        hashed_password=hash_password(password),
        role="admin",
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    response = RedirectResponse("/dashboard", status_code=303)
    response.set_cookie("session_user", str(user.id), httponly=True, max_age=86400)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse("/", status_code=302)
    response.delete_cookie("session_user")
    return response


# ── Dashboard pages ──


@router.get("/dashboard")
async def dashboard(request: Request):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(
        request, "dashboard.html", {"active_page": "dashboard", "current_user": user}
    )


@router.get("/messages")
async def messages(request: Request, session: AsyncSession = Depends(_get_session)):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    result = await session.exec(
        select(Contact).where(Contact.deleted_at.is_(None)).order_by(Contact.updated_at.desc())  # type: ignore[arg-type]
    )
    contacts = result.all()
    return templates.TemplateResponse(
        request, "messages.html", {"active_page": "messages", "current_user": user, "contacts": contacts}
    )


# ── Integration pages ──


@router.get("/graph-api")
async def graph_api_page(request: Request, session: AsyncSession = Depends(_get_session)):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    config = await _load_config(session, "graph_api")
    return templates.TemplateResponse(
        request, "graph_api.html", {"active_page": "graph_api", "current_user": user, "config": config}
    )


@router.post("/graph-api")
async def graph_api_save(request: Request, session: AsyncSession = Depends(_get_session)):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    fields = form.getlist("subscribed_fields")

    data = {
        "app_id": str(form.get("app_id", "")).strip(),
        "app_secret": str(form.get("app_secret", "")).strip(),
        "page_access_token": str(form.get("page_access_token", "")).strip(),
        "verify_token": str(form.get("verify_token", "")).strip(),
        "webhook_url": str(form.get("webhook_url", "")).strip(),
        "page_id": str(form.get("page_id", "")).strip(),
        "subscribed_fields": ",".join(fields) if fields else "",
        "welcome_message_enabled": form.get("welcome_message_enabled") == "on",
        "welcome_message": str(form.get("welcome_message", "")).strip(),
    }

    config = await _save_config(session, "graph_api", data)
    return templates.TemplateResponse(
        request, "graph_api.html",
        {"active_page": "graph_api", "current_user": user, "config": config, "save_success": True},
    )


@router.get("/webhub")
async def webhub_page(request: Request, session: AsyncSession = Depends(_get_session)):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)
    config = await _load_config(session, "webhub_utcc")
    return templates.TemplateResponse(
        request, "webhub.html", {"active_page": "webhub", "current_user": user, "config": config}
    )


@router.post("/webhub")
async def webhub_save(request: Request, session: AsyncSession = Depends(_get_session)):
    user = await _get_current_user(request)
    if not user:
        return RedirectResponse("/", status_code=302)

    form = await request.form()
    data = {
        "base_url": str(form.get("base_url", "")).strip(),
        "username": str(form.get("username", "")).strip(),
        "password": str(form.get("password", "")).strip(),
        "timeout": str(form.get("timeout", "30")).strip(),
        "webhook_path": str(form.get("webhook_path", "/webhook/webhub")).strip(),
        "sync_interval": str(form.get("sync_interval", "5")).strip(),
        "debounce_seconds": str(form.get("debounce_seconds", "10")).strip(),
    }

    config = await _save_config(session, "webhub_utcc", data)
    return templates.TemplateResponse(
        request, "webhub.html",
        {"active_page": "webhub", "current_user": user, "config": config, "save_success": True},
    )
