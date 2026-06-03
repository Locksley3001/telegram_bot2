from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app.config import get_settings
from app.market_engine import MarketEngine

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

settings = get_settings()
engine = MarketEngine(settings)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await engine.start()
    yield
    await engine.stop()


app = FastAPI(
    title="Quotex Short-Term Signal Monitor",
    description="Senales de price action para opciones binarias de corto plazo usando velas de Quotex.",
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class MarketPayload(BaseModel):
    asset: str


class EnabledPayload(BaseModel):
    enabled: bool


class TimeframePayload(BaseModel):
    timeframe: int


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "broker": engine.broker_status,
        "quotex_configured": bool(settings.quotex_email and settings.quotex_password),
        "telegram_configured": bool(settings.telegram_bot_token and settings.telegram_chat_id),
        "quotex_proxy_configured": bool(settings.quotex_proxy_url),
        "quotex_host": settings.quotex_host,
        "version": "quotex-env-diagnostics-2026-06-03",
    }


@app.get("/api/state")
async def get_state():
    return engine.state()


@app.post("/api/markets")
async def add_market(payload: MarketPayload):
    return await engine.add_market(payload.asset)


@app.delete("/api/markets/{asset}")
async def remove_market(asset: str):
    return await engine.remove_market(asset)


@app.post("/api/markets/{asset}/enabled")
async def set_market_enabled(asset: str, payload: EnabledPayload):
    return await engine.set_market_enabled(asset, payload.enabled)


@app.post("/api/timeframe")
async def set_timeframe(payload: TimeframePayload):
    return await engine.set_timeframe(payload.timeframe)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    await engine.subscribe(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        engine.unsubscribe(websocket)
