"""FastAPI app. Routes for chat, voice and dashboard are added in later steps."""

from fastapi import FastAPI
from sqlalchemy import text

from app.db import engine

app = FastAPI(title="prototypeRAG")


@app.get("/health")
def health() -> dict:
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return {"status": "ok"}
