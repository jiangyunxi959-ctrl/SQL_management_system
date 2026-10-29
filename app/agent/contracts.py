"""模型不能提交身份、角色、研究日或连接信息。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class ToolRejected(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class RequestContext:
    user_id: int
    as_of: date
    universe: str
    data_mode: str
    thread_id: str
    turn_id: int

    def public(self) -> dict:
        return {
            "as_of": self.as_of.isoformat(),
            "universe": self.universe,
            "data_mode": self.data_mode,
            "turn_id": self.turn_id,
        }


FORBIDDEN_ARGUMENT_NAMES = {
    "sql", "query", "statement", "role", "user_id", "as_of", "session", "token",
    "password", "api_key", "base_url", "connection",
}
