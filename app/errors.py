from __future__ import annotations

from typing import Any

from fastapi import HTTPException


def http_error(*, status_code: int, code: str, message: str, request_id: str | None = None, extra: dict[str, Any] | None = None) -> HTTPException:
    payload: dict[str, Any] = {"error": {"code": code, "message": message}}
    if request_id:
        payload["request_id"] = request_id
    if extra:
        payload["error"].update(extra)
    return HTTPException(status_code=status_code, detail=payload)
