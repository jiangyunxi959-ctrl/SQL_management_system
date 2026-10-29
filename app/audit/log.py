"""请求记录使用独立连接。业务回滚不会抹掉失败证据。"""
from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from app.db import transaction


class AuditError(Exception):
    pass


def begin(user_id: int | None, as_of: date | None, tool: str) -> UUID:
    request_id = uuid4()
    try:
        finish(request_id, "START", user_id=user_id, as_of=as_of, tool=tool, summary=None, error_class=None)
    except Exception as exc:
        raise AuditError("暂时不能记录这次操作，已拒绝执行") from exc
    return request_id


def finish(request_id: UUID, event_type: str, *, user_id: int | None, as_of: date | None,
           tool: str, summary: str | None, error_class: str | None) -> None:
    safe_summary = _redact(summary)
    safe_error = _redact(error_class)
    with transaction(role="audit") as conn:
        conn.execute(
            "SELECT audit.fn_append_request(%s, %s, %s, %s, %s, %s, %s)",
            (request_id, event_type, user_id, as_of, tool, safe_summary, safe_error),
        )


def _redact(value: str | None) -> str | None:
    if value is None:
        return None
    text = str(value).split("CONTEXT:")[0].strip()
    for secret_name in ("PASSWORD", "password", "token"):
        if secret_name in text and "://" in text:
            return "内部连接失败"
    return text[:500]
