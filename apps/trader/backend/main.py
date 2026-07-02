"""HFT3 Trader Dashboard — FastAPI backend.

Run:
    $env:TRADER_VIEW_TOKEN = "<token>"   # optional on loopback
    python -m uvicorn apps.trader.backend.main:app --host 127.0.0.1 --port 8090

Greenfield replacement surface for the deprecated cockpit: every number the
frontend renders traces to a receipt (path + sha256 + freshness) via the run
index and gate/promotion artifacts; unverifiable views render BLOCKED.
Read-only: no order routing, no lifecycle mutation.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (str(_REPO), str(_REPO / "packages")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from fastapi import FastAPI, HTTPException, Request  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402

from apps.trader.backend import views  # noqa: E402

app = FastAPI(title="HFT3 Trader Dashboard", version="0.1.0")

_FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


def _require_view(request: Request) -> None:
    token = os.environ.get("TRADER_VIEW_TOKEN", "")
    client = request.client.host if request.client else ""
    if not token:
        if client in ("127.0.0.1", "::1", "localhost", "testclient"):
            return
        raise HTTPException(status_code=401, detail="TRADER_VIEW_TOKEN required for non-loopback access")
    supplied = request.headers.get("x-trader-token") or request.query_params.get("token")
    if supplied != token:
        raise HTTPException(status_code=401, detail="invalid token")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "app": "hft3-trader", "read_only": True}


@app.get("/api/funnel")
def funnel(request: Request) -> dict:
    _require_view(request)
    return views.build_funnel()


@app.get("/api/models")
def models(request: Request) -> dict:
    _require_view(request)
    return views.build_models()


@app.get("/api/models/{model_id}")
def model_detail(model_id: str, request: Request) -> dict:
    _require_view(request)
    return views.build_model_detail(model_id)


@app.get("/api/campaign")
def campaign(request: Request) -> dict:
    _require_view(request)
    return views.build_campaign()


@app.get("/api/lifecycle")
def lifecycle(request: Request) -> dict:
    _require_view(request)
    return views.build_lifecycle()


@app.get("/")
def index() -> FileResponse:
    index_html = _FRONTEND_DIST / "index.html"
    if not index_html.is_file():
        raise HTTPException(
            status_code=404,
            detail="frontend not built — cd apps/trader/frontend && npm install && npm run build",
        )
    return FileResponse(index_html)


@app.get("/assets/{asset_path:path}")
def assets(asset_path: str) -> FileResponse:
    target = (_FRONTEND_DIST / "assets" / asset_path).resolve()
    if not str(target).startswith(str(_FRONTEND_DIST.resolve())) or not target.is_file():
        raise HTTPException(status_code=404, detail="asset not found")
    return FileResponse(target)
