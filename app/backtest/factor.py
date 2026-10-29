"""单因子策略：t 日收盘选股，下一交易日开盘才成交。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import math
import threading
import time

from psycopg.types.json import Json

from app.auth.service import Identity
from app.costs.calculator import execution_price, money, quote_cn_a, quote_liquidity
from app.db import transaction
from app.factor_research.service import ENGINE, WORKER_ID, _factor_on, _panel, canonical_digest, data_version
from app.factors.validate import validate


def round_trip_cash(initial, buy_price, sell_price, qty, rates):
    """测试夹具用的一手买卖。资金不够就整笔拒绝，零费率可以成交。"""
    buy_amount = Decimal(str(buy_price)) * qty
    sell_amount = Decimal(str(sell_price)) * qty
    if rates is None:
        return "FILLED", Decimal(str(initial)) - buy_amount + sell_amount, Decimal("0")
    buy = quote_cn_a("B", buy_amount, raw_price=buy_price, **rates)
    if Decimal(str(initial)) < buy_amount + buy.total:
        return "REJECTED", Decimal(str(initial)), Decimal("0")
    sell = quote_cn_a("S", sell_amount, raw_price=sell_price, **rates)
    cash = Decimal(str(initial)) - buy_amount - buy.total + sell_amount - sell.total
    return "FILLED", cash, buy.total + sell.total


def create_strategy(identity: Identity, name: str, factor_id: int, version: int, direction: str, top_n: int | None = None, rebalance: str = "DAY", top_pct=None) -> tuple[int, int]:
    if direction not in {"HIGH", "LOW"} or rebalance not in {"DAY", "WEEK", "MONTH"}:
        raise ValueError("策略参数不合法")
    if (top_n is None) == (top_pct is None):
        raise ValueError("持有只数和前百分比只能填一个")
    if top_n is not None and not 1 <= int(top_n) <= 20:
        raise ValueError("持有只数须为 1 到 20")
    if top_pct is not None and not 0 < Decimal(str(top_pct)) <= 1:
        raise ValueError("前百分比须在 0 到 1 之间")
    digest = hashlib.sha256(f"{factor_id}|{version}|{direction}|{top_n}|{top_pct}|{rebalance}".encode()).hexdigest()
    with transaction(user_id=identity.user_id) as conn:
        visible = conn.execute(
            "SELECT 1 FROM factor.version WHERE factor_id=%s AND version_no=%s",
            (factor_id, version),
        ).fetchone()
        if visible is None:
            raise PermissionError("公式不存在或当前不可访问")
        row = conn.execute("SELECT strategy_id FROM research.factor_strategy WHERE name=%s", (name,)).fetchone()
        if row is None:
            strategy_id = conn.execute(
                "INSERT INTO research.factor_strategy(owner_id, name) VALUES (%s,%s) RETURNING strategy_id",
                (identity.user_id, name),
            ).fetchone()[0]
            version_no = 1
        else:
            strategy_id = row[0]
            version_no = conn.execute(
                "SELECT coalesce(max(version_no),0)+1 FROM research.factor_strategy_version WHERE strategy_id=%s",
                (strategy_id,),
            ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO research.factor_strategy_version(
                strategy_id, owner_id, version_no, factor_id, factor_version, direction, top_n, top_pct, rebalance, digest
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (strategy_id, identity.user_id, version_no, factor_id, version, direction, top_n, top_pct, rebalance, digest),
        )
    return strategy_id, version_no


def run_factor_backtest(identity: Identity, strategy_id: int, strategy_version: int, profile_id: int, cost_version: int,
                        universe: str, start: date, end: date, cash="10000", key: str | None = None, zero_cost=False, as_of: date | None = None) -> int:
    if as_of is None:
        as_of = end
    if end > as_of or start > as_of or end < start:
        raise ValueError("回测日期不能晚于研究日")
    cash_text = format(Decimal(str(cash)).quantize(Decimal("0.01")), "f")
    with transaction(user_id=identity.user_id) as conn:
        spec = conn.execute(
            """
            SELECT factor_id, factor_version, direction, top_n, top_pct, rebalance, digest
            FROM research.factor_strategy_version WHERE strategy_id=%s AND version_no=%s
            """,
            (strategy_id, strategy_version),
        ).fetchone()
        cost = None if zero_cost else conn.execute(
            """
            SELECT mode, commission_rate, min_commission, transfer_rate, stamp_rate, maker_rate, taker_rate,
                   liquidity, slippage_bps, buy_commission_rate, sell_commission_rate, digest
            FROM sim.cost_version WHERE profile_id=%s AND version_no=%s
            """,
            (profile_id, cost_version),
        ).fetchone()
        if spec is None or (cost is None and not zero_cost):
            raise PermissionError("策略或费用不存在，或当前不可访问")
        expression = conn.execute(
            "SELECT expression FROM factor.version WHERE factor_id=%s AND version_no=%s",
            (spec[0], spec[1]),
        ).fetchone()
        snapshot = {
            "engine": ENGINE,
            "strategy_id": strategy_id,
            "strategy_version": strategy_version,
            "strategy_digest": spec[6],
            "profile_id": None if zero_cost else profile_id,
            "cost_version": None if zero_cost else cost_version,
            "cost_digest": None if cost is None else cost[11],
            "direction": spec[2],
            "top_n": spec[3],
            "top_pct": None if spec[4] is None else str(spec[4]),
            "rebalance": spec[5],
            "zero_cost": zero_cost,
            "universe": universe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "as_of": as_of.isoformat(),
            "cash": cash_text,
            "data": data_version(identity),
        }
        digest = canonical_digest(snapshot)
        idem = key or digest[:64]
        existing = conn.execute(
            "SELECT bt_id, status, rule_snapshot->>'engine', rule_snapshot FROM research.factor_bt_run WHERE idempotency_key=%s FOR UPDATE",
            (idem,),
        ).fetchone()
        if existing:
            saved = existing[3] or {}
            if saved.get("cash") != cash_text or saved.get("universe") != universe or saved.get("engine") != ENGINE or canonical_digest(saved) != digest:
                raise ValueError("提交编号已用于其他参数，或属于旧引擎结果；请使用新的提交编号")
            if existing[1] in {"COMPLETED", "RUNNING"}:
                return existing[0]
            claimed = conn.execute(
                """
                UPDATE research.factor_bt_run
                SET status='RUNNING', worker_id=%s
                WHERE bt_id=%s AND status=%s
                RETURNING bt_id
                """,
                (WORKER_ID, existing[0], existing[1]),
            ).fetchone()
            bt_id = existing[0] if claimed is None else claimed[0]
            spawn = claimed is not None
        else:
            bt_id = conn.execute(
                """
                INSERT INTO research.factor_bt_run(
                    owner_id, strategy_id, strategy_version, profile_id, cost_version, zero_cost,
                    universe, start_date, end_date, initial_cash, status, idempotency_key,
                    rule_snapshot, worker_id, engine_version, data_fingerprint
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'RUNNING',%s,%s,%s,%s,%s)
                RETURNING bt_id
                """,
                (identity.user_id, strategy_id, strategy_version, None if zero_cost else profile_id, None if zero_cost else cost_version,
                 zero_cost, universe, start, end, cash_text, idem, Json(snapshot), WORKER_ID, ENGINE, snapshot["data"]),
            ).fetchone()[0]
            spawn = True
    if not spawn:
        return bt_id
    threading.Thread(
        target=_run_saved,
        args=(identity, bt_id, validate(expression[0]), spec, cost, universe, start, end, Decimal(str(cash)), zero_cost),
        daemon=True,
    ).start()
    return bt_id


def wait_backtest(identity: Identity, bt_id: int, seconds: int = 90) -> str:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        with transaction(user_id=identity.user_id, read_only=True) as conn:
            row = conn.execute("SELECT status FROM research.factor_bt_run WHERE bt_id=%s", (bt_id,)).fetchone()
        if row and row[0] != "RUNNING":
            return row[0]
        time.sleep(0.2)
    return "RUNNING"


def _run_saved(identity, bt_id, expr, spec, cost, universe, start, end, cash, zero_cost):
    try:
        result = simulate(identity, bt_id, expr, spec, cost, universe, start, end, cash, zero_cost)
        _store(identity, bt_id, result)
    except Exception as exc:
        with transaction(user_id=identity.user_id) as conn:
            conn.execute(
                """
                UPDATE research.factor_bt_run
                SET status='FAILED', error_class=%s
                WHERE bt_id=%s AND status='RUNNING'
                """,
                (str(exc)[:200], bt_id),
            )


def simulate(identity, bt_id, expr, spec, cost, universe, start, end, cash, zero_cost):
    direction, top_n, top_pct, rebalance = spec[2], spec[3], spec[4], spec[5]
    started = time.monotonic()
    with transaction(user_id=identity.user_id, as_of=end, read_only=True, timeout_ms=30000) as conn:
        from app.factor_research.service import _require_same_data_version
        _require_same_data_version(conn, "research.factor_bt_run", "bt_id", bt_id)
        prices = conn.execute(
            """
            SELECT stock_code, trade_date, open, close, is_suspended, volume
            FROM pit.daily_bar_raw WHERE trade_date<=%s ORDER BY 1, 2
            """,
            (end,),
        ).fetchall()
    by_code = {}
    days = []
    for code, day, open_, close, suspended, volume in prices:
        by_code.setdefault(code, []).append({
            "trade_date": day, "open_raw": open_, "close_raw": close, "is_suspended": suspended, "volume": volume,
        })
        if day not in days:
            days.append(day)
    days = sorted(set(days))
    book = {}
    cash_now = cash
    fees = Decimal("0")
    trades = []
    curve = []
    for index, day in enumerate(days):
        if day < start or day > end:
            continue
        if time.monotonic() - started > 180:
            raise TimeoutError("回测超过 180 秒")
        if not _still_running(identity, bt_id):
            break
        previous = days[index - 1] if index else None
        for code, qty in book.items():
            if qty <= 0 or previous is None:
                continue
            with transaction(user_id=identity.user_id, as_of=end, read_only=True) as conn:
                holding = conn.execute(
                    "SELECT research.fn_holding_event(%s,%s,%s)",
                    (code, previous, day),
                ).fetchone()[0]
            if holding:
                raise RuntimeError(f"{code} 在 {day} {holding}，已停止并放弃本次收益")
        due = [item for item in trades if item["trade_date"] == day and item["status"] == "PENDING"]
        buy_cash = cash_now
        for trade in due:
            row = next((item for item in by_code[trade["code"]] if item["trade_date"] == day), None)
            raw = money(row["open_raw"]) if row and row["open_raw"] else None
            if row is None or row["is_suspended"] or not row["volume"] or not raw:
                trade.update(status="REJECTED", reason="SUSPENDED", raw_price=raw or 0, price=raw or 0)
                continue
            price = execution_price(trade["side"], raw, 0 if zero_cost or cost is None else cost[8])
            with transaction(user_id=identity.user_id, as_of=end, read_only=True) as conn:
                blocked = conn.execute(
                    "SELECT research.fn_execution_block(%s,%s,%s,%s,%s)",
                    (trade["code"], day, trade["side"], price, trade["signal_date"]),
                ).fetchone()[0]
            if blocked:
                trade.update(status="REJECTED", reason=blocked, raw_price=raw, price=price)
                continue
            amount = price * trade["qty"]
            quote = _fee(cost, trade["side"], amount, zero_cost)
            if trade["side"] == "B" and buy_cash < amount + quote.total:
                trade.update(status="REJECTED", reason="INSUFFICIENT_CASH", raw_price=raw, price=price)
                continue
            if trade["side"] == "B":
                buy_cash -= amount + quote.total
                cash_now -= amount + quote.total
            else:
                cash_now += amount - quote.total
            fees += quote.total
            book[trade["code"]] = book.get(trade["code"], 0) + (trade["qty"] if trade["side"] == "B" else -trade["qty"])
            trade.update(status="FILLED", price=price, amount=amount, fee=quote.total, components=quote.components, raw_price=raw)
        if (day == end or _rebalance(day, days, rebalance)) and index + 1 < len(days) and days[index + 1] <= end:
            nxt = days[index + 1]
            panel = _panel(identity, universe, day, expr.lookback)
            scored = []
            for code, series in panel.items():
                value, _reason = _factor_on(expr, series, day)
                close_raw = series[-1]["close_raw"] if series else None
                if value is not None and close_raw:
                    scored.append((code, value, Decimal(str(close_raw))))
            scored.sort(key=lambda item: (item[1] if direction == "LOW" else -item[1], item[0]))
            take = _selection_count(len(scored), top_n, top_pct)
            chosen = [code for code, _value, _close in scored[:take]]
            held = {code for code, qty in book.items() if qty > 0}
            for code in sorted(held - set(chosen)):
                trades.append({"code": code, "side": "S", "qty": book[code], "signal_date": day, "trade_date": nxt, "status": "PENDING"})
            slots = max(len(chosen), 1)
            budget = cash_now / slots
            for code, _value, close_raw in scored[:take]:
                if code in held:
                    continue
                estimate = execution_price("B", money(close_raw), 0 if zero_cost or cost is None else cost[8])
                qty = int(budget / estimate / 100) * 100
                while qty >= 100 and estimate * qty + _fee(cost, "B", estimate * qty, zero_cost).total > budget:
                    qty -= 100
                if qty >= 100:
                    trades.append({"code": code, "side": "B", "qty": qty, "signal_date": day, "trade_date": nxt, "status": "PENDING"})
        equity = cash_now
        for code, qty in book.items():
            if qty <= 0:
                continue
            row = next((item for item in by_code[code] if item["trade_date"] == day), None)
            if row and row["close_raw"]:
                equity += Decimal(str(row["close_raw"])) * qty
        curve.append({"date": day, "net": equity, "gross": equity + fees, "fees": fees})
    return {"trades": [item for item in trades if item["status"] != "PENDING"], "curve": curve}


def _selection_count(valid, top_n, top_pct):
    if valid <= 0:
        return 0
    if top_pct is not None:
        return min(20, valid, max(1, math.ceil(valid * float(top_pct))))
    return min(int(top_n), valid)


def _still_running(identity, bt_id) -> bool:
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        row = conn.execute("SELECT status FROM research.factor_bt_run WHERE bt_id=%s", (bt_id,)).fetchone()
    return bool(row and row[0] == "RUNNING")


def _fee(cost, side, amount, zero_cost):
    if zero_cost or cost is None:
        from app.costs.calculator import CostQuote
        return CostQuote(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), "ZERO")
    rate = cost[1]
    if side == "B" and cost[9] is not None:
        rate = cost[9]
    if side == "S" and cost[10] is not None:
        rate = cost[10]
    if cost[0] == "CN_A":
        return quote_cn_a(side, amount, commission_rate=rate, min_commission=cost[2], transfer_rate=cost[3], stamp_rate=cost[4], slippage_bps=0)
    return quote_liquidity(side, amount, maker_rate=cost[5], taker_rate=cost[6], assumption=cost[7], slippage_bps=0)


def _rebalance(day, days, mode):
    if mode == "DAY":
        return True
    if mode == "WEEK":
        return day.isocalendar().week != _next(days, day).isocalendar().week if _next(days, day) else True
    return day.month != _next(days, day).month if _next(days, day) else True


def _next(days, day):
    later = [item for item in days if item > day]
    return later[0] if later else None


def _holds(valid, day):
    try:
        return day in valid
    except TypeError:
        return True


def _store(identity, bt_id, result):
    with transaction(user_id=identity.user_id) as conn:
        locked = conn.execute(
            "SELECT status FROM research.factor_bt_run WHERE bt_id=%s FOR UPDATE",
            (bt_id,),
        ).fetchone()
        if locked is None or locked[0] != "RUNNING":
            return
        from app.factor_research.service import _require_same_data_version
        _require_same_data_version(conn, "research.factor_bt_run", "bt_id", bt_id)
        for trade in result["trades"]:
            if trade["status"] != "FILLED":
                conn.execute(
                    """
                    INSERT INTO research.factor_trade(
                        bt_id, owner_id, signal_date, trade_date, stock_code, side, qty, raw_price, price, amount, fee, status, reason
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,0,0,'REJECTED',%s)
                    """,
                    (bt_id, identity.user_id, trade["signal_date"], trade["trade_date"], trade["code"], trade["side"], trade["qty"], trade.get("raw_price") or 0, trade.get("price") or 0, trade.get("reason")),
                )
                continue
            trade_id = conn.execute(
                """
                INSERT INTO research.factor_trade(
                    bt_id, owner_id, signal_date, trade_date, stock_code, side, qty, raw_price, price, amount, fee, status
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'FILLED') RETURNING trade_id
                """,
                (bt_id, identity.user_id, trade["signal_date"], trade["trade_date"], trade["code"], trade["side"], trade["qty"], trade["raw_price"], trade["price"], trade["amount"], trade["fee"]),
            ).fetchone()[0]
            for name, amount in trade["components"].items():
                if amount:
                    conn.execute(
                        "INSERT INTO research.factor_trade_cost(trade_id, owner_id, component, amount) VALUES (%s,%s,%s,%s)",
                        (trade_id, identity.user_id, name, amount),
                    )
        for point in result["curve"]:
            conn.execute(
                """
                INSERT INTO research.backtest_pnl(bt_id, owner_id, trade_date, net_equity, gross_same_fills, cumulative_fees, basis)
                VALUES (%s,%s,%s,%s,%s,%s,'MARK_TO_MARKET')
                """,
                (bt_id, identity.user_id, point["date"], point["net"], point["gross"], point["fees"]),
            )
        published = conn.execute(
            "UPDATE research.factor_bt_run SET status='COMPLETED' WHERE bt_id=%s AND status='RUNNING' RETURNING bt_id",
            (bt_id,),
        ).fetchone()
        if published is None:
            raise RuntimeError("回测已取消，不发布结果")
