from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import RedirectResponse

from app import models  # noqa: F401  (ensure models are registered before create_all)
from app.database import Base, SessionLocal, engine, ensure_schema
from app.deps import WebAuthRequired
from app.routers import auth, exercises, logs, users, web
from app.seed import seed_exercises


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    with SessionLocal() as db:
        seed_exercises(db)  # populate the Exercise Library (idempotent)
    yield


app = FastAPI(title="Gym Log", lifespan=lifespan)


@app.exception_handler(WebAuthRequired)
async def web_auth_required_handler(request: Request, exc: WebAuthRequired):
    return RedirectResponse(url=f"/login?next={request.url.path}", status_code=status.HTTP_303_SEE_OTHER)


app.include_router(auth.router)
app.include_router(users.router)
app.include_router(exercises.router)
app.include_router(logs.router)
app.include_router(web.router)
