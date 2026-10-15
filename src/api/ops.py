"""Operations: metrics, rate limits, security headers and structured logs.

* ``GET /metrics``: Prometheus text format. Request counts and latency histograms per
  route template (never per raw URL, so rule ids do not explode the series), plus
  gauges read from the store at scrape time (rules by status, programs, jobs).
* Rate limits: a token bucket per user (or client address before authentication)
  and request class. Defaults are generous; set ``COBOL_RATE_LIMIT_PER_MIN``
  (reads), ``COBOL_RATE_LIMIT_WRITE_PER_MIN`` (POST/PUT/PATCH/DELETE) and
  ``COBOL_RATE_LIMIT_SCORE_PER_MIN`` (``/v1/score``). Over the limit: 429 with
  ``Retry-After``.
* Security headers on every response: CSP limited to this origin, no framing,
  no MIME sniffing, no referrer; HSTS when served over TLS.
* Access log: one line per request (JSON when ``COBOL_LOG_JSON=1``) with method,
  route, status, duration, user and request id. Request bodies are never logged
  (scoring inputs may be personal data, security.md T8).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import defaultdict
from typing import Any, Optional

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
log = logging.getLogger("cobol.access")


class Metrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.requests: dict[tuple[str, str, int], int] = defaultdict(int)
        self.hist: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0] * (len(BUCKETS) + 1))
        self.sums: dict[tuple[str, str], float] = defaultdict(float)
        self.limited: dict[str, int] = defaultdict(int)
        self.started = time.time()

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        with self._lock:
            self.requests[(method, route, status)] += 1
            h = self.hist[(method, route)]
            for i, b in enumerate(BUCKETS):
                if seconds <= b:
                    h[i] += 1
            h[-1] += 1
            self.sums[(method, route)] += seconds

    def render(self, gauges: dict[str, Any]) -> str:
        def esc(v: str) -> str:
            return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        out = ["# HELP cobol_http_requests_total HTTP requests by method, route and status.",
               "# TYPE cobol_http_requests_total counter"]
        with self._lock:
            for (m, r, s), n in sorted(self.requests.items()):
                out.append(f'cobol_http_requests_total{{method="{m}",route="{esc(r)}",status="{s}"}} {n}')
            out += ["# HELP cobol_http_request_seconds Request latency.", "# TYPE cobol_http_request_seconds histogram"]
            for (m, r), h in sorted(self.hist.items()):
                for i, b in enumerate(BUCKETS):
                    out.append(f'cobol_http_request_seconds_bucket{{method="{m}",route="{esc(r)}",le="{b}"}} {h[i]}')
                out.append(f'cobol_http_request_seconds_bucket{{method="{m}",route="{esc(r)}",le="+Inf"}} {h[-1]}')
                out.append(f'cobol_http_request_seconds_sum{{method="{m}",route="{esc(r)}"}} {self.sums[(m, r)]:.6f}')
                out.append(f'cobol_http_request_seconds_count{{method="{m}",route="{esc(r)}"}} {h[-1]}')
            out += ["# HELP cobol_rate_limited_total Requests refused by the rate limiter.",
                    "# TYPE cobol_rate_limited_total counter"]
            for cls, n in sorted(self.limited.items()):
                out.append(f'cobol_rate_limited_total{{class="{cls}"}} {n}')
        out += ["# HELP cobol_uptime_seconds Seconds since the API started.", "# TYPE cobol_uptime_seconds gauge",
                f"cobol_uptime_seconds {time.time() - self.started:.0f}"]
        for name, (help_, values) in gauges.items():
            out += [f"# HELP {name} {help_}", f"# TYPE {name} gauge"]
            for labels, v in values:
                lab = ",".join(f'{k}="{esc(x)}"' for k, x in labels.items())
                out.append(f"{name}{{{lab}}} {v}" if lab else f"{name} {v}")
        return "\n".join(out) + "\n"


class RateLimiter:
    """Token bucket per (key, class): ``rate`` requests per minute, bursts up to ``rate``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.buckets: dict[tuple[str, str], tuple[float, float]] = {}

    @staticmethod
    def limits() -> dict[str, int]:
        return {"read": int(os.environ.get("COBOL_RATE_LIMIT_PER_MIN", "1200")),
                "write": int(os.environ.get("COBOL_RATE_LIMIT_WRITE_PER_MIN", "120")),
                "score": int(os.environ.get("COBOL_RATE_LIMIT_SCORE_PER_MIN", "6000"))}

    def take(self, key: str, cls: str) -> Optional[float]:
        """None when allowed, else seconds until a token is free."""
        rate = self.limits()[cls]
        if rate <= 0:
            return None
        per_sec = rate / 60.0
        now = time.monotonic()
        with self._lock:
            tokens, last = self.buckets.get((key, cls), (float(rate), now))
            tokens = min(float(rate), tokens + (now - last) * per_sec)
            if tokens < 1.0:
                self.buckets[(key, cls)] = (tokens, now)
                return (1.0 - tokens) / per_sec
            self.buckets[(key, cls)] = (tokens - 1.0, now)
            return None


def request_class(method: str, path: str) -> str:
    if path.startswith("/v1/score"):
        return "score"
    return "write" if method in ("POST", "PUT", "PATCH", "DELETE") else "read"


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # the UI uses only same-origin scripts; inline style attributes draw the confidence bars
    "Content-Security-Policy": ("default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                                "form-action 'self'"),
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {"ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"), "level": record.levelname,
                "logger": record.name, "msg": record.getMessage()}
        for k in ("request_id", "user", "method", "route", "status", "ms"):
            if hasattr(record, k):
                data[k] = getattr(record, k)
        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        return json.dumps(data)


def configure_logging() -> None:
    if os.environ.get("COBOL_LOG_JSON") == "1":
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger("cobol")
        root.handlers = [handler]
        root.setLevel(os.environ.get("COBOL_LOG_LEVEL", "INFO"))
        root.propagate = False
