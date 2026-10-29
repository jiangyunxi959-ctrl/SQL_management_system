"""受控工具。模型 Schema 和本地校验使用同一份注册表。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
import math

from psycopg.types.json import Json

from app.agent.contracts import FORBIDDEN_ARGUMENT_NAMES, RequestContext, ToolRejected
from app.auth.service import Identity
from app.db import transaction
from app.factors.errors import FactorError
from app.factors.service import list_definitions, read_values, run_factor
from app.factors.statistics import summarize_run
from app.factors.validate import validate

_SCREEN_FIELDS = {"roe", "eps", "ret_20d", "ret_60d", "vol_20d", "amount_20d"}
_SCREEN_OPS = {">", ">=", "<", "<="}
_SORTS = {"stock_code", "value"}


def _schema(properties: dict, required: list[str]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


REGISTRY = {
    "list_factors": {
        "description": "列出当前用户可见的公共公式和本人私有公式摘要。",
        "schema": _schema({
            "category": {"type": "string"},
            "keyword": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, []),
    },
    "describe_factor": {
        "description": "查看本人有权看到的某个公式版本。",
        "schema": _schema({
            "factor_id": {"type": "integer", "minimum": 1},
            "version": {"type": "integer", "minimum": 1},
        }, ["factor_id", "version"]),
    },
    "validate_expression": {
        "description": "检查表达式是否符合语法。只检查，不保存。",
        "schema": _schema({"expression": {"type": "string"}}, ["expression"]),
    },
    "list_my_runs": {
        "description": "列出本人在当前研究日和股票池的运行。",
        "schema": _schema({
            "factor_id": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 20},
        }, []),
    },
    "run_factor": {
        "description": "按当前锁定的研究日和股票池计算一个公式版本。",
        "schema": _schema({
            "factor_id": {"type": "integer", "minimum": 1},
            "version": {"type": "integer", "minimum": 1},
        }, ["factor_id", "version"]),
    },
    "factor_values": {
        "description": "读取本人某次运行的结果。",
        "schema": _schema({
            "run_id": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            "sort": {"type": "string", "enum": ["stock_code", "value"]},
        }, ["run_id"]),
    },
    "factor_statistics": {
        "description": "读取本人某次运行的截面统计。缺失不会被当成 0。",
        "schema": _schema({"run_id": {"type": "integer", "minimum": 1}}, ["run_id"]),
    },
    "compare_factor_runs": {
        "description": "比较本人两次相同日期和股票池的运行分布，不比较单位不同的均值。",
        "schema": _schema({
            "run_ids": {"type": "array", "items": {"type": "integer", "minimum": 1}, "minItems": 2, "maxItems": 2},
        }, ["run_ids"]),
    },
    "price_history": {
        "description": "查询单只股票不晚于研究日的行情，最多 252 个自然日跨度内的记录。",
        "schema": _schema({
            "code": {"type": "string"},
            "start": {"type": "string"},
            "end": {"type": "string"},
            "adj": {"type": "string", "enum": ["raw", "hfq", "qfq"]},
        }, ["code", "start", "end", "adj"]),
    },
    "fundamentals": {
        "description": "查询研究日已经可见的财报，最多 8 期。",
        "schema": _schema({
            "code": {"type": "string"},
            "periods": {"type": "integer", "minimum": 1, "maximum": 8},
        }, ["code", "periods"]),
    },
    "index_members": {
        "description": "查询当前锁定股票池在研究日的成员。",
        "schema": _schema({}, []),
    },
    "industry_peers": {
        "description": "查询研究日同一行业的其他公司。",
        "schema": _schema({
            "code": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, ["code"]),
    },
    "evaluate_factor": {
        "description": "提交因子评价作业。只返回作业编号，不在本轮里等待全部计算。",
        "schema": _schema({
            "factor_id": {"type": "integer", "minimum": 1},
            "version": {"type": "integer", "minimum": 1},
            "start": {"type": "string"},
            "end": {"type": "string"},
        }, ["factor_id", "version", "start", "end"]),
    },
    "evaluation_report": {
        "description": "读取本人的评价摘要。未完成只返回状态。引用数值时使用 data.headline 里的 h1_days、h1_mean_ic 等字段。",
        "schema": _schema({"eval_id": {"type": "integer", "minimum": 1}}, ["eval_id"]),
    },
    "compare_evaluations": {
        "description": "比较本人两次评价。引用 data.left_headline 或 data.right_headline 的 h1_days、h1_mean_ic。样本天数不同不能直接说哪次更好。",
        "schema": _schema({"eval_ids": {"type": "array"}}, ["eval_ids"]),
    },
    "draft_factor_strategy": {
        "description": "生成策略草稿，不保存、不回测。",
        "schema": _schema({
            "factor_id": {"type": "integer", "minimum": 1},
            "version": {"type": "integer", "minimum": 1},
            "direction": {"type": "string", "enum": ["HIGH", "LOW"]},
            "top_n": {"type": "integer", "minimum": 1, "maximum": 20},
            "rebalance": {"type": "string", "enum": ["DAY", "WEEK", "MONTH"]},
        }, ["factor_id", "version", "direction", "top_n", "rebalance"]),
    },
    "estimate_strategy_cost": {
        "description": "按一笔假设成交估计费用，不读取完整个人费率方案。",
        "schema": _schema({
            "profile_id": {"type": "integer", "minimum": 1},
            "version": {"type": "integer", "minimum": 1},
            "side": {"type": "string", "enum": ["B", "S"]},
            "amount": {"type": "integer", "minimum": 1, "maximum": 100000000},
        }, ["profile_id", "version", "side", "amount"]),
    },
    "backtest_report": {
        "description": "读取本人回测的净权益、同成交毛权益和累计费用。",
        "schema": _schema({"bt_id": {"type": "integer", "minimum": 1}}, ["bt_id"]),
    },
    "financial_report_context": {
        "description": "读取研究日已可见的结构化财报摘要，不抓取网页。",
        "schema": _schema({
            "code": {"type": "string"},
            "periods": {"type": "integer", "minimum": 1, "maximum": 8},
        }, ["code", "periods"]),
    },
    "screen": {
        "description": "按允许的字段和比较符筛选当前股票池。filters 会真正生效。",
        "schema": _schema({
            "filters": {"type": "array"},
            "order_by": {"type": "string", "enum": sorted(_SCREEN_FIELDS)},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }, ["filters", "order_by", "limit"]),
    },
}


def tool_specs() -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec["description"],
                "parameters": spec["schema"],
            },
        }
        for name, spec in REGISTRY.items()
    ]


def validate_arguments(name: str, args: dict) -> dict:
    if name not in REGISTRY:
        raise ToolRejected("UNKNOWN_TOOL", "不允许的工具")
    if not isinstance(args, dict):
        raise ToolRejected("BAD_ARGUMENTS", "工具参数必须是对象")
    if set(args) & FORBIDDEN_ARGUMENT_NAMES:
        raise ToolRejected("FORBIDDEN_ARGUMENT", "不能提交身份、日期、角色或数据库命令")
    schema = REGISTRY[name]["schema"]
    unknown = set(args) - set(schema["properties"])
    if unknown:
        raise ToolRejected("BAD_ARGUMENTS", "存在未声明的参数")
    missing = [key for key in schema["required"] if key not in args]
    if missing:
        raise ToolRejected("BAD_ARGUMENTS", "缺少必要参数")
    cleaned = {}
    for key, value in args.items():
        rule = schema["properties"][key]
        cleaned[key] = _coerce(key, value, rule)
    if name == "screen":
        cleaned["filters"] = _filters(args["filters"])
    if name == "compare_factor_runs" and len(cleaned["run_ids"]) != 2:
        raise ToolRejected("BAD_ARGUMENTS", "只能比较两次运行")
    return cleaned


def _coerce(key: str, value, rule: dict):
    kind = rule["type"]
    if kind == "string":
        if not isinstance(value, str):
            raise ToolRejected("BAD_ARGUMENTS", f"{key} 必须是字符串")
        if any(word in value.lower() for word in ("select ", "delete ", "drop ", ";")):
            raise ToolRejected("BAD_ARGUMENTS", "参数里不能包含数据库命令")
        if "enum" in rule and value not in rule["enum"]:
            raise ToolRejected("BAD_ARGUMENTS", f"{key} 不在允许范围内")
        if key == "expression" and len(value) > 1024:
            raise ToolRejected("BAD_ARGUMENTS", "表达式过长")
        if key == "code" and len(value) > 12:
            raise ToolRejected("BAD_ARGUMENTS", "证券代码不合法")
        return value
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ToolRejected("BAD_ARGUMENTS", f"{key} 必须是整数")
        if value < rule.get("minimum", value) or value > rule.get("maximum", value):
            raise ToolRejected("BAD_ARGUMENTS", f"{key} 超出范围")
        return value
    if kind == "array":
        if not isinstance(value, list) or len(value) > 5:
            raise ToolRejected("BAD_ARGUMENTS", f"{key} 必须是最多 5 项的数组")
        if "items" in rule:
            return [_coerce(key, item, rule["items"]) for item in value]
        return value
    raise ToolRejected("BAD_ARGUMENTS", "参数类型不支持")


def _filters(raw) -> list[dict]:
    if not isinstance(raw, list) or len(raw) > 5:
        raise ToolRejected("BAD_ARGUMENTS", "筛选条件最多 5 条")
    cleaned = []
    for item in raw:
        if not isinstance(item, dict) or set(item) != {"field", "op", "value"}:
            raise ToolRejected("BAD_ARGUMENTS", "筛选条件只能包含 field、op 和 value")
        if item["field"] not in _SCREEN_FIELDS or item["op"] not in _SCREEN_OPS:
            raise ToolRejected("BAD_ARGUMENTS", "筛选字段或比较符不被允许")
        if isinstance(item["value"], bool) or not isinstance(item["value"], (int, float)):
            raise ToolRejected("BAD_ARGUMENTS", "筛选值必须是数字")
        if not math.isfinite(item["value"]):
            raise ToolRejected("BAD_ARGUMENTS", "筛选值必须是有限数字")
        cleaned.append({"field": item["field"], "op": item["op"], "value": item["value"]})
    return cleaned


def execute_tool(identity: Identity, ctx: RequestContext, name: str, args: dict) -> dict:
    from app.agent.service import _assert_identity
    _assert_identity(identity)
    if ctx.user_id != identity.user_id:
        raise ToolRejected("NOT_VISIBLE", "当前上下文不可访问")
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        bound = conn.execute("""SELECT 1 FROM agent.turn t JOIN agent.conversation c USING(thread_id,owner_id)
            WHERE t.turn_id=%s AND t.thread_id=%s AND t.status='RUNNING' AND c.as_of=%s AND c.universe=%s""",
            (ctx.turn_id,ctx.thread_id,ctx.as_of,ctx.universe)).fetchone()
    if not bound:
        raise ToolRejected("NOT_VISIBLE", "当前回合已结束或上下文不可访问")
    cleaned = validate_arguments(name, args)
    if name == "run_factor":
        return _cached_run(identity, ctx, cleaned)
    return _run(identity, ctx, name, cleaned)


def _run(identity: Identity, ctx: RequestContext, name: str, args: dict) -> dict:
    if name == "list_factors":
        return _list_factors(identity, ctx, args)
    if name == "describe_factor":
        return _describe(identity, ctx, args)
    if name == "validate_expression":
        return _validate(ctx, args["expression"])
    if name == "list_my_runs":
        return _runs(identity, ctx, args)
    if name == "factor_values":
        return _values(identity, ctx, args)
    if name == "factor_statistics":
        return _stats(identity, ctx, int(args["run_id"]))
    if name == "compare_factor_runs":
        return _compare(identity, ctx, args["run_ids"])
    if name == "price_history":
        return _prices(ctx, args)
    if name == "fundamentals":
        return _fundamentals(ctx, args)
    if name == "index_members":
        rows = _query(ctx, "SELECT * FROM pit.fn_index_members(%s, %s)", (ctx.universe, ctx.as_of))
        return _ok(ctx, "index_members", ctx.universe, [_plain(row) for row in rows[:100]], len(rows) > 100)
    if name == "industry_peers":
        rows = _query(ctx, "SELECT * FROM pit.fn_industry_peers(%s)", (args["code"],))
        limit = args.get("limit", 50)
        return _ok(ctx, "industry_peers", args["code"], [_plain(row) for row in rows[:limit]], len(rows) > limit)
    if name == "evaluate_factor":
        from app.factor_research.service import start_evaluation
        start = date.fromisoformat(args["start"])
        end = date.fromisoformat(args["end"])
        if start > ctx.as_of or end > ctx.as_of:
            raise ToolRejected("BAD_ARGUMENTS", "评价日期不能晚于研究日")
        eval_id = start_evaluation(identity, args["factor_id"], args["version"], ctx.universe, start, end, as_of=ctx.as_of)
        return _ok(ctx, "evaluate_factor", str(eval_id), {"status": "RUNNING", "job_id": eval_id}, False)
    if name == "evaluation_report":
        from app.factor_research.service import report
        data = report(identity, args["eval_id"], ctx.as_of)
        return _ok(ctx, "evaluation_report", str(args["eval_id"]), _jsonable(data), False)
    if name == "compare_evaluations":
        from app.factor_research.service import compare_evaluations
        if len(args["eval_ids"]) != 2:
            raise ToolRejected("BAD_ARGUMENTS", "只能比较两次评价")
        try:
            compared = compare_evaluations(identity, int(args["eval_ids"][0]), int(args["eval_ids"][1]), ctx.as_of)
        except ValueError as exc:
            raise ToolRejected("BAD_ARGUMENTS", str(exc)) from exc
        except PermissionError as exc:
            raise ToolRejected("NOT_VISIBLE", str(exc)) from exc
        left, right = compared["left"], compared["right"]
        return _ok(ctx, "compare_evaluations", ",".join(map(str, args["eval_ids"])), {
            "left_headline": _jsonable(left["headline"]),
            "right_headline": _jsonable(right["headline"]),
            "same_universe": True,
            "same_window": True,
            "same_horizons": True,
            "same_as_of": True,
            "same_data_version": True,
        }, False)
    if name == "draft_factor_strategy":
        with transaction(user_id=identity.user_id, read_only=True) as conn:
            visible = conn.execute(
                "SELECT expression FROM factor.version WHERE factor_id=%s AND version_no=%s",
                (args["factor_id"], args["version"]),
            ).fetchone()
        if visible is None:
            raise ToolRejected("NOT_VISIBLE", "公式不存在或当前不可访问")
        return _ok(ctx, "draft_factor_strategy", None, {
            "factor_id": args["factor_id"], "version": args["version"], "expression": visible[0],
            "direction": args["direction"], "top_n": args["top_n"], "rebalance": args["rebalance"],
            "saved": False, "note": "草稿未保存，需要在页面确认",
        }, False)
    if name == "estimate_strategy_cost":
        from app.costs.service import quote_for
        quoted = quote_for(identity, args["profile_id"], args["version"], args["side"], args["amount"])
        return _ok(ctx, "estimate_strategy_cost", None, {"total": str(quoted.total), "side": args["side"], "amount": args["amount"]}, False)
    if name == "backtest_report":
        with transaction(user_id=identity.user_id, read_only=True) as conn:
            row = conn.execute(
                "SELECT trade_date, net_equity, gross_same_fills, cumulative_fees FROM research.backtest_pnl WHERE bt_id=%s ORDER BY trade_date DESC LIMIT 1",
                (args["bt_id"],),
            ).fetchone()
        if row is None:
            raise ToolRejected("NOT_VISIBLE", "回测不存在或当前不可访问")
        return _ok(ctx, "backtest_report", str(args["bt_id"]), {"trade_date": row[0].isoformat(), "net_equity": str(row[1]), "gross_same_fills": str(row[2]), "cumulative_fees": str(row[3])}, False)
    if name == "financial_report_context":
        return _fundamentals(ctx, args)
    if name == "screen":
        rows = _query(
            ctx,
            "SELECT * FROM pit.fn_screen(%s, %s::jsonb, %s, %s)",
            (ctx.universe, Json(args["filters"]), args["order_by"], args["limit"]),
        )
        return _ok(ctx, "screen", ctx.universe, [_plain(row) for row in rows], False)
    raise ToolRejected("UNKNOWN_TOOL", "不允许的工具")


def _cached_run(identity, ctx, args) -> dict:
    key = hashlib.sha256(
        f"{ctx.user_id}|{ctx.thread_id}|{ctx.turn_id}|run_factor|{args['factor_id']}|{args['version']}|{ctx.as_of}|{ctx.universe}".encode()
    ).hexdigest()
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        saved = conn.execute(
            """
            SELECT result_ref FROM agent.tool_execution
            WHERE thread_id=%s AND turn_id=%s AND operation_key=%s AND status='SUCCEEDED'
            """,
            (ctx.thread_id, ctx.turn_id, key),
        ).fetchone()
    if saved:
        return _stats_pointer(ctx, int(saved[0]), reused=True)
    run_id, _plan = run_factor(identity, args["factor_id"], args["version"], ctx.universe, ctx.as_of, key)
    with transaction(user_id=identity.user_id) as conn:
        conn.execute(
            """
            INSERT INTO agent.tool_execution(owner_id, thread_id, turn_id, operation_key, status, result_ref)
            VALUES (%s, %s, %s, %s, 'SUCCEEDED', %s)
            ON CONFLICT (owner_id, thread_id, turn_id, operation_key) DO NOTHING
            """,
            (identity.user_id, ctx.thread_id, ctx.turn_id, key, str(run_id)),
        )
    return _stats_pointer(ctx, int(run_id), reused=False)


def _stats_pointer(ctx, run_id: int, reused: bool) -> dict:
    body = _ok(ctx, "run_factor", str(run_id), {"run_id": run_id, "reused": reused}, False)
    return body


def _list_factors(identity, ctx, args) -> dict:
    rows = list_definitions(identity)
    latest = {}
    for row in rows:
        latest[row[0]] = row
    items = []
    for row in latest.values():
        category = row[15] if len(row) > 15 else None
        if args.get("category") and category != args["category"]:
            continue
        text = f"{row[2] or ''} {row[3]}"
        if args.get("keyword") and args["keyword"] not in text:
            continue
        items.append({
            "factor_id": row[0], "visibility": row[1], "code": row[2], "name": row[3],
            "version": row[5], "category": category, "unit": row[7], "lookback": row[8],
        })
    limit = args.get("limit", 50)
    return _ok(ctx, "list_factors", "catalog", items[:limit], len(items) > limit)


def _describe(identity, ctx, args) -> dict:
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        row = conn.execute(
            """
            SELECT d.factor_id, d.code, d.name, d.visibility, v.version_no, v.expression, v.unit_label,
                   v.lookback, v.explanation, v.adaptation_note, v.source_name, v.source_page, m.category, m.source_kind
            FROM factor.version v
            JOIN factor.definition d ON d.factor_id = v.factor_id
            LEFT JOIN factor.public_meta m ON m.code = d.code
            WHERE d.factor_id=%s AND v.version_no=%s
            """,
            (args["factor_id"], args["version"]),
        ).fetchone()
    if row is None:
        raise ToolRejected("NOT_VISIBLE", "公式不存在或当前不可访问")
    data = {
        "factor_id": row[0], "code": row[1], "name": row[2], "visibility": row[3], "version": row[4],
        "expression": row[5], "unit": row[6], "lookback": row[7], "explanation": row[8],
        "adaptation_note": row[9], "source_name": row[10], "source_page": row[11],
        "category": row[12], "source_kind": row[13],
    }
    return _ok(ctx, "describe_factor", f"{row[0]}:{row[4]}", data, False)


def _validate(ctx, expression: str) -> dict:
    try:
        checked = validate(expression)
    except FactorError as exc:
        return _ok(ctx, "validate_expression", None, {
            "ok": False, "category": exc.category, "message": exc.message, "start": exc.start, "end": exc.end,
        }, False)
    return _ok(ctx, "validate_expression", None, {
        "ok": True, "expression": expression, "unit": checked.unit_label, "lookback": checked.lookback,
        "explanation": checked.explanation, "fields": list(checked.fields),
    }, False)


def _runs(identity, ctx, args) -> dict:
    limit = args.get("limit", 20)
    params = [ctx.as_of, ctx.universe]
    extra = ""
    if args.get("factor_id"):
        extra = " AND r.factor_id=%s"
        params.append(args["factor_id"])
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        rows = conn.execute(
            f"""
            SELECT r.run_id, r.factor_id, r.version_no, r.as_of, r.universe, d.name
            FROM factor.run r JOIN factor.definition d USING (factor_id)
            WHERE r.status='SUCCEEDED' AND r.as_of=%s AND r.universe=%s {extra}
            ORDER BY r.run_id DESC LIMIT 20
            """,
            params,
        ).fetchall()
    data = [
        {"run_id": row[0], "factor_id": row[1], "version": row[2], "as_of": row[3].isoformat(),
         "universe": row[4], "name": row[5]}
        for row in rows[:limit]
    ]
    return _ok(ctx, "list_my_runs", ctx.universe, data, False)


def _values(identity, ctx, args) -> dict:
    rows = read_values(identity, int(args["run_id"]))
    if not rows:
        owned = _owns_run(identity, int(args["run_id"]))
        if not owned:
            raise ToolRejected("NOT_VISIBLE", "运行不存在或当前不可访问")
    sort = args.get("sort", "stock_code")
    if sort not in _SORTS:
        raise ToolRejected("BAD_ARGUMENTS", "排序字段不被允许")
    materialized = [
        {"stock_code": row[0], "trade_date": row[1].isoformat(), "value": _num(row[2]),
         "quality": row[3], "reason_codes": list(row[4] or []), "reason_detail": row[5]}
        for row in rows
    ]
    materialized.sort(key=lambda item: (item["value"] is None, item["value"] if sort == "value" else item["stock_code"]))
    limit = args.get("limit", 100)
    return _ok(ctx, "factor_values", str(args["run_id"]), materialized[:limit], len(materialized) > limit)


def _stats(identity, ctx, run_id: int) -> dict:
    try:
        stats = summarize_run(identity, run_id)
    except ValueError as exc:
        raise ToolRejected("NOT_VISIBLE", "运行不存在或当前不可访问") from exc
    data = {key: _num(value) if key != "reason_counts" else value for key, value in stats.items()}
    data["as_of"] = stats["as_of"].isoformat() if hasattr(stats["as_of"], "isoformat") else stats["as_of"]
    result = _ok(ctx, "factor_statistics", str(run_id), data, False)
    result["context"].update(as_of=data["as_of"], universe=data["universe"],
                             factor_id=data["factor_id"], version=data["version_no"], unit=data["unit_label"])
    return result


def _compare(identity, ctx, run_ids) -> dict:
    left, right = _stats(identity, ctx, int(run_ids[0])), _stats(identity, ctx, int(run_ids[1]))
    if left["data"].get("as_of") != right["data"].get("as_of") or left["data"].get("universe") != right["data"].get("universe"):
        raise ToolRejected("BAD_ARGUMENTS", "两次运行的研究日或股票池不同，不能放在一起比较")
    return _ok(ctx, "compare_factor_runs", f"{run_ids[0]},{run_ids[1]}", {
        "left": left["data"], "right": right["data"],
        "same_unit": left["data"].get("unit_label") == right["data"].get("unit_label"),
        "note": "只比较覆盖和分布。单位不同时不要比较均值高低。",
    }, False)


def _prices(ctx, args) -> dict:
    try:
        start, end = date.fromisoformat(args["start"]), date.fromisoformat(args["end"])
    except ValueError as exc:
        raise ToolRejected("BAD_ARGUMENTS", "日期格式应为YYYY-MM-DD") from exc
    if end > ctx.as_of or start > end or (end - start).days > 400:
        raise ToolRejected("BAD_ARGUMENTS", "行情日期不能晚于研究日，范围也过大")
    rows = _query(
        ctx,
        "SELECT trade_date, open, high, low, close, volume, amount FROM pit.fn_price_history(%s, %s, %s, %s)",
        (args["code"], start, end, args["adj"]),
    )
    data = [
        {"trade_date": row[0].isoformat(), "open": _num(row[1]), "high": _num(row[2]), "low": _num(row[3]),
         "close": _num(row[4]), "volume": _num(row[5]), "amount": _num(row[6])}
        for row in rows[:252]
    ]
    return _ok(ctx, "price_history", args["code"], data, len(rows) > 252)


def _fundamentals(ctx, args) -> dict:
    rows = _query(ctx, "SELECT report_period, announce_date, net_profit, roe FROM pit.fn_fundamentals(%s, %s)", (args["code"], args["periods"]))
    data = [{"report_period": row[0].isoformat(), "announce_date": row[1].isoformat(), "net_profit": _num(row[2]), "roe": _num(row[3])} for row in rows]
    return _ok(ctx, "fundamentals", args["code"], data, False)


def _owns_run(identity, run_id: int) -> bool:
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        return conn.execute("SELECT 1 FROM factor.run WHERE run_id=%s", (run_id,)).fetchone() is not None


def _query(ctx, sql, params):
    with transaction(user_id=None, as_of=ctx.as_of, read_only=True, role="agent", timeout_ms=5000) as conn:
        return conn.execute(sql, params).fetchall()


def _ok(ctx, tool, ref, data, truncated: bool) -> dict:
    context = ctx.public()
    context["tool"] = tool
    body = {"ok": True, "error_code": None, "context": context, "data": data, "warnings": []}
    if truncated:
        body["warnings"].append("结果已按上限截断，没有查看全部")
    encoded = json.dumps(body, ensure_ascii=False, default=str)
    if len(encoded.encode("utf-8")) > 32000:
        body["data"] = data[:20] if isinstance(data, list) else {"truncated": True}
        body["warnings"].append("结果超过大小上限，只保留部分")
    body["object_ref"] = ref
    return body


def _jsonable(value):
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    return value


def _plain(row):
    return [_num(item) if not hasattr(item, "isoformat") else item.isoformat() for item in row]


def _num(value):
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (int, float)) or value is None:
        return value
    return str(value)
