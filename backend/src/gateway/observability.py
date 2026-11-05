from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import Request

logger = logging.getLogger("agent.api")


def configure_logging() -> None:
    if logger.handlers:
        return
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


async def log_http_request(request: Request, call_next: Any) -> Any:
    start = time.perf_counter()
    response = await call_next(request)
    duration_ms = int((time.perf_counter() - start) * 1000)
    request_id = str(getattr(request.state, "request_id", ""))
    payload = {
        "type": "http_request",
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": duration_ms,
        "request_id": request_id,
    }
    logger.info(json.dumps(payload, ensure_ascii=False))
    return response
