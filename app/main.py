"""FastAPI app. Twilio voice routes and the dashboard are added in later steps."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from app.api.businesses import router as businesses_router
from app.api.chat import router as chat_router
from app.api.conversations import router as conversations_router
from app.api.custom_replies import router as custom_replies_router
from app.config import get_settings
from app.db import engine
from app.voice.browser_ws import router as voice_browser_router

app = FastAPI(title="prototypeRAG")
app.include_router(chat_router)
app.include_router(custom_replies_router)
app.include_router(voice_browser_router)
app.include_router(businesses_router)
app.include_router(conversations_router)
if get_settings().dev_reload:
    from app.api.dev_reload import router as dev_reload_router

    app.include_router(dev_reload_router)
# mounted under /web, not /, so it can never shadow an API route like POST /chat;
# fetch('/chat') from web/chat.html still resolves same-origin either way.
app.mount("/web", StaticFiles(directory="web", html=True), name="web")


@app.get("/health")
def health() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
