"""受控工具。模型只能提出允许清单里的调用，不能提交 SQL 或用户编号。"""
from __future__ import annotations

from datetime import date
import hashlib
import json

from psycopg.types.json import Json

from app.audit import begin, finish
from app.auth.service import Identity
from app.db import transaction
from app.factors.service import list_definitions, read_values, run_factor
from app.factors.statistics import summarize_run


class ModelNotConfigured(Exception):
    pass


class ToolRejected(Exception):
    pass


class UnconfiguredModel:
    """没有外部模型配置时使用。固定菜单不算真实问答。"""

    def plan(self, text: str):
        raise ModelNotConfigured("真实模型联调待配置")


class ScriptedModel:
    """测试替身。它的返回值不是模型生成的结论。"""

    def __init__(self, calls: list[dict]):
        self.calls = calls

    def plan(self, text: str):
        return self.calls


_TOOLS = {
    "price_history": {"code", "start", "end", "adj"},
    "fundamentals": {"code", "periods"},
    "index_members": {"universe"},
    "industry_peers": {"code"},
    "screen": {"universe", "order_by", "limit"},
    "list_factors": set(),
    "run_factor": {"factor_id", "version", "universe"},
    "factor_values": {"run_id"},
    "factor_statistics": {"run_id"},
}
_FORBIDDEN = {"sql", "query", "statement", "role", "user_id", "as_of", "session", "token"}


def handle(identity: Identity, as_of: date, text: str, model) -> dict:
    request = begin(identity.user_id, as_of, "agent.handle")
    try:
        session_id = _open_session(identity, as_of)
        calls = model.plan(text)
    except ModelNotConfigured as exc:
        finish(request, "REJECTED", user_id=identity.user_id, as_of=as_of, tool="agent.handle",
               summary=None, error_class="MODEL_NOT_CONFIGURED")
        return {"status": "model_not_configured", "message": str(exc), "calls": []}
    except Exception as exc:
        finish(request, 'FAILURE', user_id=identity.user_id, as_of=as_of, tool='agent.handle',
               summary=None, error_class=exc.__class__.__name__)
        raise
    results = []
    try:
        for call in calls:
            results.append(_one(identity, as_of, session_id, request, call))
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=as_of, tool="agent.handle",
               summary=f"{len(results)} calls", error_class=None)
        return {"status": "ok", "calls": results}
    except Exception as exc:
        finish(request, "REJECTED" if isinstance(exc, ToolRejected) else "FAILURE",
               user_id=identity.user_id, as_of=as_of, tool="agent.handle",
               summary=None, error_class=exc.__class__.__name__)
        raise


def _one(identity: Identity, as_of: date, session_id: int, request, call: dict) -> dict:
    if not isinstance(call, dict) or set(call) - {"tool", "args"}:
        raise ToolRejected("工具调用格式不合法")
    name = call.get("tool")
    args = call.get("args") or {}
    if name not in _TOOLS or not isinstance(args, dict):
        raise ToolRejected("不允许的工具")
    if set(args) & _FORBIDDEN or set(args) != _TOOLS[name]:
        raise ToolRejected("工具参数不合法")
    if any(isinstance(value, str) and any(word in value.lower() for word in ("select ", "delete ", ";")) for value in args.values()):
        raise ToolRejected("参数里不能包含数据库命令")
    outcome = _dispatch(identity, as_of, name, args)
    digest = hashlib.sha256(json.dumps(args, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    with transaction(user_id=identity.user_id) as conn:
        conn.execute(
            """
            INSERT INTO agent.tool_call(agent_session_id, request_id, tool_name, args_digest, object_ref)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (session_id, request, name, digest, str(outcome.get("ref") or "")[:120]),
        )
    return {"tool": name, "result": outcome["result"]}


def _dispatch(identity: Identity, as_of: date, name: str, args: dict) -> dict:
    if name == "price_history":
        rows = _pit(
            as_of,
            "SELECT * FROM pit.fn_price_history(%s, %s, %s, %s)",
            (args["code"], date.fromisoformat(args["start"]), date.fromisoformat(args["end"]), args["adj"]),
        )
        return {"result": rows, "ref": args["code"]}
    if name == "fundamentals":
        rows = _pit(as_of, "SELECT * FROM pit.fn_fundamentals(%s, %s)", (args["code"], int(args["periods"])))
        return {"result": rows, "ref": args["code"]}
    if name == "index_members":
        rows = _pit(as_of, "SELECT * FROM pit.fn_index_members(%s, %s)", (args["universe"], as_of))
        return {"result": rows, "ref": args["universe"]}
    if name == "industry_peers":
        rows = _pit(as_of, "SELECT * FROM pit.fn_industry_peers(%s)", (args["code"],))
        return {"result": rows, "ref": args["code"]}
    if name == "screen":
        rows = _pit(
            as_of,
            "SELECT * FROM pit.fn_screen(%s, %s::jsonb, %s, %s)",
            (args["universe"], Json([]), args["order_by"], int(args["limit"])),
        )
        return {"result": rows, "ref": args["universe"]}
    if name == "list_factors":
        rows = list_definitions(identity)
        return {"result": [(row[0], row[1], row[2] or row[3], row[5]) for row in rows], "ref": "catalog"}
    if name == "run_factor":
        run_id, _plan = run_factor(identity, int(args["factor_id"]), int(args["version"]), args["universe"], as_of)
        return {"result": run_id, "ref": run_id}
    if name == "factor_values":
        rows = read_values(identity, int(args["run_id"]))
        return {"result": rows, "ref": args["run_id"]}
    if name == 'factor_statistics':
        stats = summarize_run(identity, int(args['run_id']))
        return {'result': stats, 'ref': args['run_id']}
    raise ToolRejected("不允许的工具")


def _pit(as_of: date, query: str, params: tuple):
    with transaction(role="agent", as_of=as_of, read_only=True, timeout_ms=5000) as conn:
        return conn.execute(query, params).fetchall()


def _open_session(identity: Identity, as_of: date) -> int:
    with transaction(user_id=identity.user_id) as conn:
        return conn.execute(
            """
            INSERT INTO agent.session(user_id, as_of)
            VALUES (%s, %s) RETURNING agent_session_id
            """,
            (identity.user_id, as_of),
        ).fetchone()[0]
