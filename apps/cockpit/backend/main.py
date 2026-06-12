"""HFT3 Cockpit — FastAPI aggregation service.

Run:  uvicorn apps.cockpit.backend.main:app --host 0.0.0.0 --port 8080
Read-only by default; control endpoints are local-origin only (see auth.py).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

# --- self-contained import path (no dependency on hft3_bootstrap shim) ------
_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import Depends, FastAPI, WebSocket, WebSocketDisconnect  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

from apps.cockpit.backend import paths  # noqa: E402
from apps.cockpit.backend.aggregate import ZONES  # noqa: E402
from apps.cockpit.backend.auth import require_view  # noqa: E402
from apps.cockpit.backend.control import router as control_router  # noqa: E402
from apps.cockpit.backend.hub import Hub  # noqa: E402
from apps.cockpit.backend.watch import Watcher  # noqa: E402

hub = Hub()
watcher = Watcher(hub)


@asynccontextmanager
async def lifespan(app: FastAPI):
    watcher.start(asyncio.get_running_loop())
    try:
        yield
    finally:
        watcher.stop()


app = FastAPI(title="HFT3 Cockpit", version="0.1.0", lifespan=lifespan)

# CORS — front-end origin(s). Tighten in production via COCKPIT_ALLOW_ORIGINS.
import os  # noqa: E402

_origins = os.environ.get("COCKPIT_ALLOW_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(control_router)


def _zone(name: str) -> dict:
    try:
        return ZONES[name]()
    except Exception as exc:
        return {"zone": name, "error": str(exc), "generated_utc": paths.now_iso()}


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "ws_clients": hub.count, "execution_mode": paths.execution_mode()}


@app.get("/api/pipeline")
def pipeline(_: str = Depends(require_view)) -> dict:
    return _zone("pipeline")


@app.get("/api/portfolio")
def portfolio(_: str = Depends(require_view)) -> dict:
    return _zone("portfolio")


@app.get("/api/models")
def models(_: str = Depends(require_view)) -> dict:
    return _zone("models")


@app.get("/api/lifecycle")
def lifecycle(_: str = Depends(require_view)) -> dict:
    return _zone("lifecycle")


@app.get("/api/autonomy")
def autonomy(_: str = Depends(require_view)) -> dict:
    return _zone("autonomy")


@app.get("/api/system")
def system(_: str = Depends(require_view)) -> dict:
    return _zone("system")


@app.get("/api/alerts")
def alerts(_: str = Depends(require_view)) -> dict:
    return _zone("alerts")


@app.get("/api/all")
def all_zones(_: str = Depends(require_view)) -> dict:
    return {name: _zone(name) for name in ZONES}


@app.get("/api/model/{hyp_id}")
def model_detail(hyp_id: int, _: str = Depends(require_view)) -> dict:
    from apps.cockpit.backend.aggregate import model_detail as md

    try:
        return md.build(hyp_id)
    except Exception as exc:
        return {"id": hyp_id, "error": str(exc), "generated_utc": paths.now_iso()}


from fastapi import Body  # noqa: E402
from fastapi.responses import StreamingResponse  # noqa: E402


@app.post("/api/chat")
async def chat(payload: dict = Body(...), _: str = Depends(require_view)):
    """Read-only advisory chat with the local Gemma model. SSE token stream."""
    from apps.cockpit.backend import chat as chat_mod

    query = str((payload or {}).get("query", "")).strip()
    if not query:
        return {"error": "query required"}
    return StreamingResponse(chat_mod.stream_chat(query), media_type="text/event-stream")


@app.websocket("/ws")
async def ws(websocket: WebSocket) -> None:
    # WS auth: token via ?token= query (browsers can't set WS headers easily).
    token = websocket.query_params.get("token")
    vt = os.environ.get("COCKPIT_VIEW_TOKEN")
    ct = os.environ.get("COCKPIT_CONTROL_TOKEN")
    client = websocket.client.host if websocket.client else ""
    if vt or ct:
        if token not in (vt, ct) or token is None:
            await websocket.close(code=4401)
            return
    elif client not in {"127.0.0.1", "::1", "localhost"}:
        await websocket.close(code=4401)
        return

    await hub.connect(websocket)
    # First paint: send every zone immediately.
    for name in ZONES:
        await websocket.send_json({"zone": name, "payload": _zone(name)})
    try:
        while True:
            msg = await websocket.receive_json()
            if isinstance(msg, dict) and msg.get("action") == "subscribe":
                zones = set(msg.get("zones") or [])
                await hub.set_zones(websocket, zones & set(ZONES))
            elif isinstance(msg, dict) and msg.get("action") == "refresh":
                for name in ZONES:
                    await websocket.send_json({"zone": name, "payload": _zone(name)})
    except WebSocketDisconnect:
        await hub.disconnect(websocket)
    except Exception:
        await hub.disconnect(websocket)


# Serve the built SPA (single origin) if built. Hashed assets are served from
# /assets; every other non-API path falls back to index.html so client-side
# routes (/models, /chat, ...) work on deep-link + refresh (the catch-all is
# added LAST, so the explicit /api and /ws routes always win).
_DIST = _REPO / "apps" / "cockpit" / "frontend" / "dist"
_INDEX = _DIST / "index.html"
if _DIST.is_dir() and _INDEX.is_file():
    from fastapi import HTTPException  # noqa: E402
    from fastapi.staticfiles import StaticFiles  # noqa: E402
    from fastapi.responses import FileResponse  # noqa: E402

    _DIST_ROOT = _DIST.resolve()

    if (_DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=str(_DIST / "assets")), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        # Serve a real static file ONLY if it resolves INSIDE dist. The resolve()
        # + parents containment check rejects `../` traversal (incl. URL-encoded
        # %2e%2e%2f) and absolute/drive-letter inputs, so this unauthenticated
        # route cannot leak backend source or any other process-readable file
        # (e.g. a .env with credentials). Otherwise fall back to the SPA
        # entrypoint so client-side routes work on deep-link/refresh.
        if full_path:
            try:
                candidate = (_DIST_ROOT / full_path).resolve()
            except (OSError, ValueError):
                candidate = None
            if candidate is not None and candidate.is_file() and _DIST_ROOT in candidate.parents:
                return FileResponse(candidate)
        if _INDEX.is_file():
            return FileResponse(_INDEX)
        raise HTTPException(status_code=404)
