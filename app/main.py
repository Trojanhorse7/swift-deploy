"""SwiftDeploy API — stable/canary modes, chaos (canary only), Prometheus /metrics."""

from __future__ import annotations

import asyncio
import math
import os
import random
import time
from collections import deque
from enum import Enum

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from pydantic import BaseModel, Field, model_validator

MODE = os.environ.get("MODE", "stable").lower()
if MODE not in ("stable", "canary"):
    raise RuntimeError("MODE must be 'stable' or 'canary'")

APP_VERSION = os.environ.get("APP_VERSION", "0.0.0")
APP_PORT = int(os.environ.get("APP_PORT", "3000"))
METRICS_WINDOW_SECONDS = float(os.environ.get("METRICS_WINDOW_SECONDS", "30"))

METADATA = {
    "version": os.environ.get("METADATA_VERSION", ""),
    "service_name": os.environ.get("METADATA_SERVICE_NAME", ""),
    "contact": os.environ.get("METADATA_CONTACT", ""),
    "deployed_by": os.environ.get("METADATA_DEPLOYED_BY", ""),
}

START_MONO = time.monotonic()

REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "path", "status_code"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency seconds",
    ["method", "path"],
    buckets=(
        0.005,
        0.01,
        0.025,
        0.05,
        0.075,
        0.1,
        0.25,
        0.5,
        0.75,
        1.0,
        2.5,
        5.0,
        7.5,
        10.0,
    ),
)

APP_UPTIME = Gauge("app_uptime_seconds", "Process uptime in seconds")
APP_MODE_METRIC = Gauge("app_mode", "Deployment mode: 0=stable 1=canary")
CHAOS_ACTIVE = Gauge(
    "chaos_active",
    "Chaos state: 0=none 1=slow 2=error",
)

WINDOW_REQUESTS = Gauge(
    "swiftdeploy_window_requests_total",
    "Requests recorded in rolling METRICS_WINDOW_SECONDS window",
)
WINDOW_ERRORS = Gauge(
    "swiftdeploy_window_errors_total",
    "Responses with HTTP status >= 500 in rolling window",
)
WINDOW_P99_SECONDS = Gauge(
    "swiftdeploy_window_p99_latency_seconds",
    "P99 latency in seconds within rolling window",
)

_window: deque[tuple[float, float, bool]] = deque()


def _normalize_path(path: str) -> str:
    p = path.rstrip("/") or "/"
    if p.startswith("/metrics"):
        return "/metrics"
    if p.startswith("/healthz"):
        return "/healthz"
    if p.startswith("/chaos"):
        return "/chaos"
    if p == "/":
        return "/"
    return p


def _refresh_window(duration_s: float, status_code: int) -> None:
    now = time.monotonic()
    _window.append((now, duration_s, status_code >= 500))
    cutoff = now - METRICS_WINDOW_SECONDS
    while _window and _window[0][0] < cutoff:
        _window.popleft()

    n = len(_window)
    WINDOW_REQUESTS.set(float(n))
    errors = sum(1 for _, _, err in _window if err)
    WINDOW_ERRORS.set(float(errors))
    if n == 0:
        WINDOW_P99_SECONDS.set(0.0)
        return
    durs = sorted(w[1] for w in _window)
    idx = int(math.ceil(0.99 * n)) - 1
    idx = max(0, min(idx, n - 1))
    WINDOW_P99_SECONDS.set(float(durs[idx]))


def _refresh_chaos_gauge(slow: float, rate: float | None) -> None:
    if slow > 0:
        CHAOS_ACTIVE.set(1.0)
    elif rate is not None:
        CHAOS_ACTIVE.set(2.0)
    else:
        CHAOS_ACTIVE.set(0.0)


class ChaosMode(str, Enum):
    slow = "slow"
    error = "error"
    recover = "recover"


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


class ChaosState:
    """Process-global chaos flags (shared across all requests, not per-connection)."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.slow_seconds: float = 0.0
        self.error_rate: float | None = None

    async def apply_slow(self, duration: float) -> None:
        async with self.lock:
            self.slow_seconds = float(duration)
            _refresh_chaos_gauge(self.slow_seconds, self.error_rate)

    async def apply_error(self, rate: float) -> None:
        async with self.lock:
            self.error_rate = float(rate)
            _refresh_chaos_gauge(self.slow_seconds, self.error_rate)

    async def recover(self) -> None:
        async with self.lock:
            self.slow_seconds = 0.0
            self.error_rate = None
            _refresh_chaos_gauge(0.0, None)


chaos = ChaosState()

app = FastAPI(title="SwiftDeploy API", version=APP_VERSION)

APP_MODE_METRIC.set(1.0 if MODE == "canary" else 0.0)
CHAOS_ACTIVE.set(0.0)


@app.middleware("http")
async def chaos_and_mode_headers(request: Request, call_next):
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


@app.middleware("http")
async def prometheus_metrics_middleware(request: Request, call_next):
    """Observe latency and counts (includes chaos delays — registered after chaos so this wraps inner stack)."""
    method = request.method
    path_norm = _normalize_path(request.url.path)
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - start
    status = str(response.status_code)
    REQUEST_COUNT.labels(method=method, path=path_norm, status_code=status).inc()
    REQUEST_DURATION.labels(method=method, path=path_norm).observe(elapsed)
    _refresh_window(elapsed, response.status_code)
    APP_UPTIME.set(time.monotonic() - START_MONO)
    APP_MODE_METRIC.set(1.0 if MODE == "canary" else 0.0)
    async with chaos.lock:
        _refresh_chaos_gauge(chaos.slow_seconds, chaos.error_rate)
    return response


@app.get("/metrics")
async def metrics_endpoint():
    APP_UPTIME.set(time.monotonic() - START_MONO)
    APP_MODE_METRIC.set(1.0 if MODE == "canary" else 0.0)
    async with chaos.lock:
        _refresh_chaos_gauge(chaos.slow_seconds, chaos.error_rate)
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


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


@app.get("/healthz")
async def healthz():
    uptime_s = round(time.monotonic() - START_MONO, 3)
    body = {"status": "ok", "uptime_seconds": uptime_s, "mode": MODE}
    return JSONResponse(content=body)


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
