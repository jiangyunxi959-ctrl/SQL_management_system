"""模拟下单仍走 C 的检查。重复提交键避免同一次请求产生两笔订单。"""
from __future__ import annotations

from decimal import Decimal

import psycopg

from app.audit import begin, finish
from app.audit.idempotency import find as find_submission, signature, result as submission_result
from app.auth.service import Identity
from app.db import transaction


def submit_order(identity: Identity, account_id: int, code: str, side: str, exec_point: str,
                 qty: int, limit_price: Decimal | None, idempotency_key: str):
    request = begin(identity.user_id, None, "sim.order")
    fingerprint = signature('sim.order', [account_id, code, side, exec_point, qty, limit_price])
    try:
        existing = find_submission(identity.user_id, idempotency_key, fingerprint)
        if existing:
            finish(request, "SUCCESS", user_id=identity.user_id, as_of=None, tool="sim.order",
                   summary=f"replay {existing}", error_class=None)
            return int(existing)
        with transaction(user_id=identity.user_id) as conn:
            conn.execute("SELECT set_config('app.request_id', %s, true)", [str(request)])
            order_id = conn.execute(
                "SELECT sim.fn_place_order(%s::bigint, %s::varchar, %s::char, %s::varchar, %s::int, %s::numeric)",
                (account_id, code, side, exec_point, qty, limit_price),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO audit.idempotency(user_id, idempotency_key, request_id, result_ref)
                VALUES (%s, %s, %s, %s)
                """,
                (identity.user_id, idempotency_key, request, submission_result(order_id, fingerprint)),
            )
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=None, tool="sim.order",
               summary=f"order {order_id}", error_class=None)
        return order_id
    except psycopg.errors.UniqueViolation:
        try:
            existing = find_submission(identity.user_id, idempotency_key, fingerprint)
            if existing is None:
                raise ValueError('提交冲突无法核对，请重新检查请求')
        except Exception as exc:
            finish(request, 'REJECTED', user_id=identity.user_id, as_of=None, tool='sim.order',
                   summary=None, error_class=exc.__class__.__name__)
            raise
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=None, tool="sim.order",
               summary=f"replay {existing}", error_class=None)
        return int(existing)
    except Exception as exc:
        kind = "FAILURE"
        try:
            finish(request, kind, user_id=identity.user_id, as_of=None, tool="sim.order",
                   summary=None, error_class=exc.sqlstate if isinstance(exc, psycopg.Error) else exc.__class__.__name__)
        except Exception:
            finish(request, "PENDING_CHECK", user_id=identity.user_id, as_of=None, tool="sim.order",
                   summary=None, error_class="UNCONFIRMED")
        raise


def _find(identity: Identity, key: str):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        row = conn.execute(
            "SELECT result_ref FROM audit.idempotency WHERE idempotency_key = %s",
            (key,),
        ).fetchone()
    return None if row is None else row[0]
