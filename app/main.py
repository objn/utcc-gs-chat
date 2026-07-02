from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.api.v1.endpoints import router as api_v1_router
from app.db.session import create_db_and_tables, engine
from app.frontend.routes import router as frontend_router
from app.models.config import Config  # noqa: F401 — register model with metadata
from app.models.contact import Contact  # noqa: F401
from app.models.message import Message  # noqa: F401
from app.models.user import User  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_db_and_tables()
    yield


app = FastAPI(lifespan=lifespan)

static_dir = Path(__file__).parent / "frontend" / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

OPEN_PATHS = {"/register", "/static", "/api"}


@app.middleware("http")
async def require_setup(request: Request, call_next):
    path = request.url.path

    if any(path.startswith(p) for p in OPEN_PATHS):
        return await call_next(request)

    async with AsyncSession(engine) as session:
        result = await session.exec(select(User.id).limit(1))
        has_users = result.first() is not None

    if not has_users and path != "/register":
        return RedirectResponse("/register", status_code=302)

    return await call_next(request)


app.include_router(api_v1_router, prefix="/api/v1")
app.include_router(frontend_router)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=3000, reload=True)
