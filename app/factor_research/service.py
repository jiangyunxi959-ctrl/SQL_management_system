"""按研究日计算因子与随后收益。标签不进入选股。"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
import atexit
import hashlib
import json
import threading
import time
import uuid

from psycopg.rows import dict_row

from app.auth.service import Identity
from app.db import transaction
from app.factor_research.metrics import assign_groups, official_ic
from app.factors.reference import evaluate
from app.factors.validate import validate

MAX_NAMES = 100
MAX_DAYS = 126
HORIZONS = (1, 5, 20)
DEADLINE_SECONDS = 180
ENGINE = "d3.4"
WORKER_ID = uuid.uuid4().hex


def canonical_digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def data_version(identity: Identity) -> str:
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        return conn.execute("SELECT research.fn_data_version()").fetchone()[0]


def _require_same_data_version(conn, table: str, id_column: str, row_id: int) -> None:
    current = conn.execute("SELECT research.fn_data_version()").fetchone()[0]
    stamped = conn.execute(
        f"SELECT data_fingerprint FROM {table} WHERE {id_column}=%s",
        (row_id,),
    ).fetchone()
    if stamped is None or stamped[0] != current:
        raise RuntimeError("计算期间数据版本已变化，不发布与实际读取不一致的结果")


def start_evaluation(identity: Identity, factor_id: int, version: int, universe: str, start: date, end: date, horizons=HORIZONS, key: str | None = None, as_of: date | None = None) -> int:
    if as_of is None:
        raise ValueError("必须提供研究日")
    if end < start or (end - start).days > 400 or end > as_of or start > as_of:
        raise ValueError("评价日期不能晚于研究日")
    chosen = tuple(sorted(set(horizons)))
    if not chosen or any(item not in HORIZONS for item in chosen):
        raise ValueError("持有期只允许 1、5、20")
    payload = {
        "engine": ENGINE,
        "factor_id": factor_id,
        "version": version,
        "universe": universe,
        "start": start.isoformat(),
        "end": end.isoformat(),
        "as_of": as_of.isoformat(),
        "horizons": list(chosen),
        "data": data_version(identity),
    }
    digest = canonical_digest(payload)
    idem = key or digest[:64]
    with transaction(user_id=identity.user_id) as conn:
        expression = conn.execute(
            "SELECT expression FROM factor.version WHERE factor_id=%s AND version_no=%s",
            (factor_id, version),
        ).fetchone()
        if expression is None:
            raise PermissionError("公式不存在或当前不可访问")
        parsed = validate(expression[0])
        existing = conn.execute(
            "SELECT eval_id, status, snapshot, engine_version FROM research.factor_eval_run WHERE idempotency_key=%s FOR UPDATE",
            (idem,),
        ).fetchone()
        if existing:
            if existing[2] != digest or existing[3] != ENGINE:
                raise ValueError("提交编号已用于其他参数，或属于旧引擎结果；请使用新的提交编号")
            if existing[1] in {"COMPLETED", "RUNNING"}:
                return existing[0]
            claimed = conn.execute(
                """
                UPDATE research.factor_eval_run
                SET status='RUNNING', worker_id=%s, finished_at=NULL, error_class=NULL
                WHERE eval_id=%s AND status=%s
                RETURNING eval_id
                """,
                (WORKER_ID, existing[0], existing[1]),
            ).fetchone()
            if claimed is None:
                return existing[0]
            eval_id = claimed[0]
        else:
            eval_id = conn.execute(
                """
                INSERT INTO research.factor_eval_run(
                    owner_id, factor_id, version_no, universe, start_date, end_date, horizons,
                    idempotency_key, status, snapshot, worker_id, engine_version, data_fingerprint, as_of_date
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'RUNNING',%s,%s,%s,%s,%s)
                RETURNING eval_id
                """,
                (identity.user_id, factor_id, version, universe, start, end, list(chosen), idem, digest, WORKER_ID, ENGINE, payload["data"], as_of),
            ).fetchone()[0]
    threading.Thread(
        target=_safe_compute,
        args=(identity, eval_id, parsed, universe, start, end, chosen, as_of),
        daemon=True,
    ).start()
    return eval_id


def _safe_compute(identity, eval_id, expr, universe, start, end, horizons, as_of):
    try:
        _compute(identity, eval_id, expr, universe, start, end, horizons, as_of)
    except Exception as exc:
        _mark(identity, eval_id, "FAILED", exc.__class__.__name__)


def _compute(identity, eval_id, expr, universe, start, end, horizons, as_of):
    with transaction(user_id=identity.user_id, as_of=as_of, read_only=True, timeout_ms=30000) as conn:
        _require_same_data_version(conn, "research.factor_eval_run", "eval_id", eval_id)
        calendar = [row[0] for row in conn.execute(
            "SELECT DISTINCT trade_date FROM pit.daily_bar_hfq WHERE trade_date<=%s ORDER BY trade_date",
            (as_of,),
        )]
        members = conn.execute(
            "SELECT stock_code, valid FROM pit.index_member WHERE index_code=%s",
            (universe,),
        ).fetchall()
        prices = conn.execute(
            """
            SELECT stock_code, trade_date, open, close, is_suspended
            FROM pit.daily_bar_hfq WHERE trade_date<=%s ORDER BY stock_code, trade_date
            """,
            (as_of,),
        ).fetchall()
    by_stock = {}
    for code, day, open_, close, suspended in prices:
        by_stock.setdefault(code, []).append({
            "trade_date": day, "open_hfq": open_, "close_hfq": close, "has_bar": True,
            "is_suspended": suspended, "ca_in_window": False, "volume": 1, "amount": 1,
            "open_raw": open_, "close_raw": close, "high_raw": close, "low_raw": close,
            "high_hfq": close, "low_hfq": close,
        })
    signals = [day for day in calendar if start <= day <= end]
    if len(signals) > MAX_DAYS:
        raise ValueError("信号日超过 126 个，请缩短区间")
    started = time.monotonic()
    values = []
    dailies = []
    groups = []
    for signal in signals:
        if time.monotonic() - started > DEADLINE_SECONDS:
            raise TimeoutError("评价超过 180 秒")
        _cancelled(identity, eval_id)
        index = calendar.index(signal)
        panel = _panel(identity, universe, signal, expr.lookback)
        active = sorted(panel)
        if len(active) > MAX_NAMES:
            raise ValueError("股票池超过 100 只，没有截断")
        for horizon in horizons:
            entry = calendar[index + 1] if index + 1 < len(calendar) else None
            exit_day = calendar[index + horizon] if index + horizon < len(calendar) else None
            paired_x = []
            paired_y = []
            for code in active:
                factor_value, reason = _factor_on(expr, panel.get(code, []), signal)
                label, label_reason = _label(by_stock.get(code, []), entry, exit_day, as_of)
                valid_row = factor_value is not None and label is not None
                values.append((signal, code, horizon, factor_value, entry, exit_day, label, valid_row, reason or label_reason))
                if valid_row:
                    paired_x.append(factor_value)
                    paired_y.append(label)
            ic, rank, reason = official_ic(paired_x, paired_y)
            usable = [(row[1], row[3], row[6]) for row in values if row[0] == signal and row[2] == horizon and row[7]]
            grouped, group_reason = assign_groups([(item[0], item[1]) for item in usable])
            dailies.append((signal, horizon, len(active), len(usable), ic, rank, reason or group_reason, len(grouped)))
            labels = {item[0]: item[2] for item in usable}
            for group in grouped:
                nums = [labels[stock] for stock in group["stocks"] if stock in labels]
                avg = sum(nums) / len(nums) if nums else None
                groups.append((signal, horizon, group["group_no"], group["lower"], group["upper"], group["count"], avg, group_reason))
    _publish_evaluation(identity, eval_id, values, dailies, groups)


def _publish_evaluation(identity, eval_id, values, dailies, groups) -> bool:
    with transaction(user_id=identity.user_id) as conn:
        locked = conn.execute(
            "SELECT status FROM research.factor_eval_run WHERE eval_id=%s FOR UPDATE",
            (eval_id,),
        ).fetchone()
        if locked is None or locked[0] != "RUNNING":
            return False
        _require_same_data_version(conn, "research.factor_eval_run", "eval_id", eval_id)
        conn.execute("DELETE FROM research.factor_eval_group WHERE eval_id=%s", (eval_id,))
        conn.execute("DELETE FROM research.factor_eval_daily WHERE eval_id=%s", (eval_id,))
        conn.execute("DELETE FROM research.factor_eval_value WHERE eval_id=%s", (eval_id,))
        for row in values:
            conn.execute(
                """
                INSERT INTO research.factor_eval_value(
                    eval_id, owner_id, signal_date, stock_code, horizon, factor_value, entry_date, exit_date, label, valid, reason_code
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (eval_id, identity.user_id, *row),
            )
        for row in dailies:
            conn.execute(
                """
                INSERT INTO research.factor_eval_daily(
                    eval_id, owner_id, signal_date, horizon, candidate_count, valid_count, ic, rank_ic, reason_code, group_count
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (eval_id, identity.user_id, *row),
            )
        for row in groups:
            conn.execute(
                """
                INSERT INTO research.factor_eval_group(
                    eval_id, owner_id, signal_date, horizon, group_no, lower_value, upper_value, member_count, avg_label, reason_code
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (eval_id, identity.user_id, *row),
            )
        published = conn.execute(
            """
            UPDATE research.factor_eval_run
            SET status='COMPLETED', finished_at=now()
            WHERE eval_id=%s AND status='RUNNING'
            RETURNING eval_id
            """,
            (eval_id,),
        ).fetchone()
        if published is None:
            raise RuntimeError("评价已取消，不发布结果")
    return True


def report(identity: Identity, eval_id: int, as_of: date) -> dict:
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        run = conn.execute(
            """
            SELECT eval_id, status, start_date, end_date, universe, horizons, error_class, as_of_date, data_fingerprint
            FROM research.factor_eval_run WHERE eval_id=%s
            """,
            (eval_id,),
        ).fetchone()
        if run is None:
            raise PermissionError("评价不存在或当前不可访问")
        if run[1] == "COMPLETED" and run[7] is None:
            raise PermissionError("该评价没有可靠的计算截止日，不能作为当前研究依据，请重新计算")
        cutoff = run[7]
        if cutoff is None:
            if run[2] > as_of or run[3] > as_of:
                raise PermissionError("评价超出当前研究日")
        elif run[2] > as_of or run[3] > as_of or cutoff > as_of:
            raise PermissionError("评价超出当前研究日")
        days = conn.execute(
            """
            SELECT signal_date, horizon, candidate_count, valid_count, ic, rank_ic, reason_code, group_count
            FROM research.factor_eval_daily WHERE eval_id=%s ORDER BY signal_date, horizon
            """,
            (eval_id,),
        ).fetchall()
        groups = conn.execute(
            """
            SELECT signal_date, horizon, group_no, member_count, avg_label, reason_code
            FROM research.factor_eval_group WHERE eval_id=%s
            ORDER BY signal_date, horizon, group_no
            """,
            (eval_id,),
        ).fetchall()
    summary = {}
    for horizon in run[5] or []:
        rows = [row for row in days if row[1] == horizon and row[4] is not None]
        values = [Decimal(str(row[4])) for row in rows]
        ranks = [Decimal(str(row[5])) for row in days if row[1] == horizon and row[5] is not None]
        mean = sum(values) / len(values) if values else None
        mean_rank = sum(ranks) / len(ranks) if ranks else None
        variance = sum((item - mean) ** 2 for item in values) / (len(values) - 1) if len(values) > 1 else None
        std = variance.sqrt() if variance not in (None, 0) else None
        summary[horizon] = {
            "days": len(values),
            "mean_ic": mean,
            "mean_rank_ic": mean_rank,
            "std_ic": std,
            "icir": (mean / std) if mean is not None and std not in (None, 0) else None,
            "positive_ratio": (sum(item > 0 for item in values) / len(values)) if values else None,
        }
    headline = {}
    for horizon, item in summary.items():
        headline[f"h{horizon}_days"] = item["days"]
        if item["mean_ic"] is not None:
            headline[f"h{horizon}_mean_ic"] = str(item["mean_ic"])
        if item["mean_rank_ic"] is not None:
            headline[f"h{horizon}_mean_rank_ic"] = str(item["mean_rank_ic"])
        if item["positive_ratio"] is not None:
            headline[f"h{horizon}_positive_ratio"] = str(item["positive_ratio"])
    return {
        "eval_id": run[0], "status": run[1], "start": str(run[2]), "end": str(run[3]),
        "universe": run[4], "horizons": list(run[5] or []), "as_of": None if cutoff is None else str(cutoff),
        "data_fingerprint": run[8],
        "headline": headline, "summary": {str(key): value for key, value in summary.items()},
        "days": days, "groups": groups,
    }


def compare_evaluations(identity: Identity, left_id: int, right_id: int, as_of: date) -> dict:
    left, right = report(identity, left_id, as_of), report(identity, right_id, as_of)
    if left["universe"] != right["universe"]:
        raise ValueError("两次评价的股票池不同，不能直接比较")
    if left["start"] != right["start"] or left["end"] != right["end"]:
        raise ValueError("两次评价的信号区间不同，不能直接比较")
    if list(left["horizons"]) != list(right["horizons"]):
        raise ValueError("两次评价的标签期限不同，不能直接比较")
    if left["as_of"] != right["as_of"]:
        raise ValueError("两次评价的计算截止日不同，不能直接比较")
    if left["data_fingerprint"] != right["data_fingerprint"]:
        raise ValueError("两次评价的数据版本不同，不能直接比较")
    return {"left": left, "right": right}


def _panel(identity, universe, signal, lookback):
    with transaction(user_id=identity.user_id, as_of=signal, read_only=True, timeout_ms=20000) as conn:
        rows = conn.execute(
            """
            SELECT stock_code, trade_date, open_raw, high_raw, low_raw, close_raw,
                   open_hfq, high_hfq, low_hfq, close_hfq, volume, amount,
                   is_suspended, has_bar, ca_in_window
            FROM pit.fn_factor_panel(%s, %s)
            """,
            (universe, lookback),
        ).fetchall()
    grouped = {}
    for row in rows:
        grouped.setdefault(row[0], []).append({
            "trade_date": row[1],
            "open_raw": row[2], "high_raw": row[3], "low_raw": row[4], "close_raw": row[5],
            "open_hfq": row[6], "high_hfq": row[7], "low_hfq": row[8], "close_hfq": row[9],
            "volume": row[10] or 0, "amount": row[11] or 0,
            "is_suspended": bool(row[12]), "has_bar": bool(row[13]), "ca_in_window": bool(row[14]),
        })
    return grouped


def _factor_on(expr, rows, signal):
    prefix = [row for row in rows if row["trade_date"] <= signal]
    if len(prefix) < expr.lookback:
        return None, "INSUFFICIENT_HISTORY"
    value, mask = evaluate(expr, prefix)
    if mask or value is None:
        return None, "FACTOR_MISSING"
    return value, None


def _label(rows, entry, exit_day, cutoff):
    if entry is None or exit_day is None or exit_day > cutoff:
        return None, "NOT_MATURED"
    entry_row = next((row for row in rows if row["trade_date"] == entry), None)
    exit_row = next((row for row in rows if row["trade_date"] == exit_day), None)
    if entry_row is None or exit_row is None or entry_row["is_suspended"] or exit_row["is_suspended"]:
        return None, "SUSPENDED"
    if not entry_row["open_hfq"] or not exit_row["close_hfq"]:
        return None, "MISSING_PRICE"
    return Decimal(str(exit_row["close_hfq"])) / Decimal(str(entry_row["open_hfq"])) - 1, None


def _contains(valid, day) -> bool:
    return valid is not None and day in valid if hasattr(valid, "__contains__") else True


def _cancelled(identity, eval_id):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        status = conn.execute("SELECT status FROM research.factor_eval_run WHERE eval_id=%s", (eval_id,)).fetchone()
    if status and status[0] in {"CANCELLED", "INTERRUPTED"}:
        raise RuntimeError("评价已取消")


def _mark(identity, eval_id, status, error):
    with transaction(user_id=identity.user_id) as conn:
        conn.execute(
            "UPDATE research.factor_eval_run SET status=%s, error_class=%s, finished_at=now() WHERE eval_id=%s AND status='RUNNING'",
            (status, error, eval_id),
        )


def interrupt_worker(worker_id: str | None = None) -> int:
    """只中断本进程启动的作业。另一个进程正在跑的任务保持不动。"""
    wid = worker_id or WORKER_ID
    from app.db import connect_admin
    with connect_admin() as conn:
        if conn.execute("SELECT to_regclass('research.factor_eval_run')").fetchone()[0] is None:
            return 0
        first = conn.execute(
            """
            UPDATE research.factor_eval_run
            SET status='INTERRUPTED', error_class='PROCESS_EXIT', finished_at=now()
            WHERE status='RUNNING' AND worker_id=%s
            """,
            (wid,),
        ).rowcount
        second = 0
        if conn.execute("SELECT to_regclass('research.factor_bt_run')").fetchone()[0]:
            second = conn.execute(
                "UPDATE research.factor_bt_run SET status='INTERRUPTED' WHERE status='RUNNING' AND worker_id=%s",
                (wid,),
            ).rowcount
        conn.commit()
    return first + second


def cancel_owner_jobs(identity: Identity) -> None:
    with transaction(user_id=identity.user_id) as conn:
        if conn.execute("SELECT to_regclass('research.factor_eval_run')").fetchone()[0]:
            conn.execute(
                "UPDATE research.factor_eval_run SET status='CANCELLED', finished_at=now(), error_class='LOGOUT' WHERE status='RUNNING'"
            )
        if conn.execute("SELECT to_regclass('research.factor_bt_run')").fetchone()[0]:
            conn.execute("UPDATE research.factor_bt_run SET status='CANCELLED' WHERE status='RUNNING'")


atexit.register(interrupt_worker)


def cancel_eval(identity: Identity, eval_id: int) -> None:
    with transaction(user_id=identity.user_id) as conn:
        conn.execute(
            "UPDATE research.factor_eval_run SET status='CANCELLED', finished_at=now() WHERE eval_id=%s AND status='RUNNING'",
            (eval_id,),
        )
