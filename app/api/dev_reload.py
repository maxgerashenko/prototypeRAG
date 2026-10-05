"""dev-only live reload for web/ pages — SSE stream that sends a `reload` event whenever the built pages
(web/dist, written by `npm run build`, or continuously by `npm run build -- --watch`) change. Watching all
of web/ would include web/node_modules. For editing the React source, `npm run dev` (Vite HMR) is faster.
Python changes are already handled by `uvicorn --reload`; pages also reload after such a restart (see web/dev-reload.js).
Registered only when `DEV_RELOAD=true` — never in cloud."""
from collections.abc import AsyncIterator
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from watchfiles import awatch

WEB_DIR = Path("web/dist")
router = APIRouter()

@router.get("/dev/reload")
async def dev_reload(request: Request) -> StreamingResponse:
    async def events() -> AsyncIterator[str]:
        WEB_DIR.mkdir(parents=True, exist_ok=True)  # awatch needs the folder before the first build
        yield "event: hello\ndata: ok\n\n"
        async for _changes in awatch(WEB_DIR, stop_event=None, debounce=200):
            if await request.is_disconnected():
                break
            yield "event: reload\ndata: web\n\n"
    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})
