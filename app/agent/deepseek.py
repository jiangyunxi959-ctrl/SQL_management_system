"""DeepSeek Chat Completion。密钥只从环境读取，不进入状态或页面。"""
from __future__ import annotations

import json
import os
import time
from urllib import error, request

from app.agent.gateway import ModelNotConfigured

DEFAULT_BASE = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-flash"
_ALLOWED_HOSTS = {"https://api.deepseek.com"}


class ModelError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        self.attempts = 0
        super().__init__(message)


class DeepSeekModel:
    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY") or ""
        self.model = os.getenv("DEEPSEEK_MODEL") or DEFAULT_MODEL
        self.base_url = (os.getenv("DEEPSEEK_BASE_URL") or DEFAULT_BASE).rstrip("/")
        if self.base_url not in _ALLOWED_HOSTS:
            raise ModelError("BAD_ENDPOINT", "模型地址不在允许范围内")

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def complete(self, messages: list, tools: list, *, max_attempts=3, timeout=30,
                 deadline=None, should_stop=None) -> tuple[dict, int]:
        if self.base_url not in _ALLOWED_HOSTS:
            raise ModelError("BAD_ENDPOINT", "模型地址不在允许范围内")
        if not self.configured:
            raise ModelNotConfigured("真实模型联调尚未完成：缺少服务端 DEEPSEEK_API_KEY")
        attempts = 0
        last = None
        while attempts < min(3, max_attempts):
            if should_stop and should_stop():
                stopped = ModelError("CANCELLED", "请求已取消或登录已失效")
                stopped.attempts = attempts
                raise stopped
            remaining = deadline - time.monotonic() if deadline else timeout
            if remaining <= 0:
                stopped = ModelError("TIMEOUT", "本轮分析已达到时间上限")
                stopped.attempts = attempts
                raise stopped
            attempts += 1
            try:
                return _post(self.base_url, self.api_key, self.model, messages, tools,
                             timeout=min(timeout, remaining)), attempts
            except ModelError as exc:
                last = exc
                exc.attempts = attempts
                if exc.code not in {"TIMEOUT", "UNAVAILABLE", "RATE_LIMIT"} or attempts >= min(3, max_attempts):
                    raise
        raise last


def _post(base_url: str, api_key: str, model: str, messages: list, tools: list, *, timeout=30) -> dict:
    payload = {
        "model": model,
        "messages": [_protocol_message(message) for message in messages],
        "tools": tools,
        "tool_choice": "auto",
        "max_tokens": 4096,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"},
    }
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(body) > 200_000:
        raise ModelError("TOO_LARGE", "发给模型的内容过大")
    call = request.Request(
        base_url + "/chat/completions",
        data=body,
        headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(call, timeout=timeout) as response:
            raw = response.read(1_000_001)
            if len(raw) > 1_000_000:
                raise ModelError("TOO_LARGE", "模型响应过大")
            parsed = json.loads(raw.decode("utf-8"))
    except error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise ModelError("UNAUTHORIZED", "模型服务拒绝了密钥") from exc
        if exc.code == 429:
            raise ModelError("RATE_LIMIT", "模型服务繁忙") from exc
        if exc.code >= 500:
            raise ModelError("UNAVAILABLE", "模型服务暂时不可用") from exc
        raise ModelError("BAD_RESPONSE", "模型服务返回了无法使用的响应") from exc
    except TimeoutError as exc:
        raise ModelError("TIMEOUT", "模型请求超时") from exc
    except error.URLError as exc:
        raise ModelError("UNAVAILABLE", "模型服务暂时不可用") from exc
    except json.JSONDecodeError as exc:
        raise ModelError("BAD_RESPONSE", "模型响应不是合法 JSON") from exc
    try:
        message = parsed["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError("BAD_RESPONSE", "模型响应缺少消息") from exc
    return _assistant(message)


def _protocol_message(message: dict) -> dict:
    """Empty assistant tool_calls are invalid in the actual provider protocol."""
    cleaned = {"role": message["role"], "content": message.get("content") or ""}
    if message["role"] == "assistant" and message.get("tool_calls"):
        cleaned["tool_calls"] = message["tool_calls"]
    if message["role"] == "tool":
        cleaned["tool_call_id"] = message["tool_call_id"]
    return cleaned


def _assistant(message: dict) -> dict:
    tool_calls = []
    for call in message.get("tool_calls") or []:
        if not isinstance(call, dict) or not call.get("id"):
            raise ModelError("BAD_RESPONSE", "工具调用缺少编号")
        function = call.get("function") or {}
        arguments = function.get("arguments") or "{}"
        if not isinstance(arguments, str):
            raise ModelError("BAD_RESPONSE", "工具参数不是文本")
        tool_calls.append({
            "id": call["id"],
            "type": "function",
            "function": {"name": function.get("name") or "", "arguments": arguments},
        })
    return {"role": "assistant", "content": message.get("content") or "", "tool_calls": tool_calls}


class ScriptedLoop:
    """测试用的多步模型。它按预先给定的真实协议消息回答，不是 DeepSeek。"""

    def __init__(self, steps: list[dict]):
        self.steps = steps
        self.index = 0

    def complete(self, messages: list, tools: list) -> tuple[dict, int]:
        if self.index >= len(self.steps):
            raise ModelError("BAD_RESPONSE", "测试模型没有下一步")
        step = self.steps[self.index]
        self.index += 1
        return step, 1
