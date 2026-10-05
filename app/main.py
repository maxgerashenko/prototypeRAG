"""FastAPI app. Twilio voice routes and the dashboard are added in later steps."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.businesses import router as businesses_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.custom_replies import router as custom_replies_router
from app.config import get_settings
from app.db import engine
from app.voice import google_auth
from app.voice.browser_ws import router as voice_browser_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    google_auth.warm_up()  # missing Google credentials show at startup, not mid-call (V7)
    yield


app = FastAPI(title="prototypeRAG", lifespan=lifespan)
app.include_router(chat_router)
app.include_router(custom_replies_router)
app.include_router(voice_browser_router)
app.include_router(businesses_router)
app.include_router(conversations_router)
if get_settings().dev_reload:
    from app.api.dev_reload import router as dev_reload_router

    app.include_router(dev_reload_router)


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """The voice app lives under /web/ (DEC-42)."""
    return RedirectResponse("/web/")


@app.get("/web/mic-test.html", include_in_schema=False)
def old_voice_page() -> RedirectResponse:
    """The voice app used to be web/mic-test.html; registered before the /web mount so it wins."""
    return RedirectResponse("/web/")


# React + TypeScript pages, built by `npm run build` in web/ into web/dist (not committed).
# Mounted under /web, not /, so it can never shadow an API route like POST /chat;
# fetch('/chat') from /web/chat.html still resolves same-origin either way.
# check_dir=False: the API (and the tests) still start before the frontend is built.
app.mount("/web", StaticFiles(directory="web/dist", html=True, check_dir=False), name="web")


@app.get("/health")
def health() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
