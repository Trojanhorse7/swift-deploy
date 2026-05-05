"""SwiftDeploy API — stable/canary modes with optional chaos (canary only)."""

from __future__ import annotations

import asyncio
import os
import random
import time
from enum import Enum

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, model_validator

MODE = os.environ.get("MODE", "stable").lower()
if MODE not in ("stable", "canary"):
    raise RuntimeError("MODE must be 'stable' or 'canary'")

APP_VERSION = os.environ.get("APP_VERSION", "0.0.0")
APP_PORT = int(os.environ.get("APP_PORT", "3000"))

# Mirrors manifest metadata.* (injected by docker-compose from manifest.yaml).
METADATA = {
    "version": os.environ.get("METADATA_VERSION", ""),
    "service_name": os.environ.get("METADATA_SERVICE_NAME", ""),
    "contact": os.environ.get("METADATA_CONTACT", ""),
    "deployed_by": os.environ.get("METADATA_DEPLOYED_BY", ""),
}

START_MONO = time.monotonic()

# Chaos modes
class ChaosMode(str, Enum):
    slow = "slow"
    error = "error"
    recover = "recover"

# Chaos payload model
class ChaosPayload(BaseModel):
    mode: ChaosMode
    duration: float | None = Field(default=None, ge=0, le=3600)
    rate: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def check_fields(self) -> ChaosPayload:
        if self.mode == ChaosMode.slow:
            if self.duration is None:
                raise ValueError("'duration' required when mode is 'slow'")
        elif self.mode == ChaosMode.error:
            if self.rate is None:
                raise ValueError("'rate' required when mode is 'error'")
        return self

# Chaos state class
class ChaosState:
    """Process-global chaos flags (shared across all requests, not per-connection)."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.slow_seconds: float = 0.0
        self.error_rate: float | None = None

    async def apply_slow(self, duration: float) -> None:
        async with self.lock:
            self.slow_seconds = float(duration)

    async def apply_error(self, rate: float) -> None:
        async with self.lock:
            self.error_rate = float(rate)

    async def recover(self) -> None:
        async with self.lock:
            self.slow_seconds = 0.0
            self.error_rate = None


chaos = ChaosState()

app = FastAPI(title="SwiftDeploy API", version=APP_VERSION)

# Chaos and mode headers middleware
@app.middleware("http")
async def chaos_and_mode_headers(request: Request, call_next):
    """Apply chaos to normal traffic only.

    POST /chaos is excluded so operators can always arm/disarm chaos quickly
    (otherwise a large slow duration could block recovery).
    Chaos state lives on ``chaos`` (global), protected by ``chaos.lock``.
    """
    if MODE == "canary" and not (
        request.method == "POST"
        and request.url.path.rstrip("/") == "/chaos"
    ):
        async with chaos.lock:
            slow = chaos.slow_seconds
            rate = chaos.error_rate
        if slow > 0:
            await asyncio.sleep(slow)
        if rate is not None and random.random() < rate:
            return JSONResponse(
                status_code=500,
                content={"error": "chaos_injected", "mode": "error"},
                headers={"X-Mode": "canary"},
            )

    response = await call_next(request)
    if MODE == "canary":
        response.headers["X-Mode"] = "canary"
    return response

# Root endpoint
@app.get("/")
async def root():
    return {
        "message": "SwiftDeploy API",
        "mode": MODE,
        "version": APP_VERSION,
        "metadata": METADATA,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "unix_timestamp": time.time(),
    }

# Health check endpoint
@app.get("/healthz")
async def healthz():
    uptime_s = round(time.monotonic() - START_MONO, 3)
    body = {"status": "ok", "uptime_seconds": uptime_s, "mode": MODE}
    return JSONResponse(content=body)

# Chaos endpoint
@app.post("/chaos")
async def chaos_endpoint(payload: ChaosPayload):
    if MODE != "canary":
        raise HTTPException(status_code=403, detail="chaos is only available in canary mode")

    if payload.mode == ChaosMode.slow:
        assert payload.duration is not None
        await chaos.apply_slow(payload.duration)
        return {"status": "slow_armed", "duration": payload.duration}
    if payload.mode == ChaosMode.error:
        assert payload.rate is not None
        await chaos.apply_error(payload.rate)
        return {"status": "error_armed", "rate": payload.rate}
    await chaos.recover()
    return {"status": "recovered"}
