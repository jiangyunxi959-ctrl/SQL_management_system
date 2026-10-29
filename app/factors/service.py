"""因子目录和运行。所有者来自已验证身份，不来自调用参数。"""
from __future__ import annotations

from datetime import date

from psycopg.types.json import Json
import psycopg

from app.audit import begin, finish
from app.audit.idempotency import find as find_submission, signature, result as submission_result
from app.auth.service import Identity
from app.db import transaction
from app.factors.errors import FactorError
from app.factors.execution import compute, save_run
from app.factors.validate import validate


def list_definitions(identity: Identity):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        return conn.execute(
            """
            SELECT d.factor_id, d.visibility, d.code, d.name, d.archived,
                   v.version_no, v.expression, v.unit_label, v.lookback, v.explanation,
                   v.adaptation_note, v.source_doc, v.source_name, v.source_page, v.ca_sensitive,
                   m.category, m.source_kind
            FROM factor.definition d
            JOIN factor.version v ON v.factor_id = d.factor_id
            LEFT JOIN factor.public_meta m ON m.code = d.code
            ORDER BY d.visibility, coalesce(d.code, d.name), v.version_no
            """
        ).fetchall()


def create_private(identity: Identity, name: str, expression: str) -> int:
    request = begin(identity.user_id, None, "factor.create")
    try:
        checked = validate(expression)
        with transaction(user_id=identity.user_id) as conn:
            _bind_request(conn, request)
            factor_id = conn.execute(
                """SELECT factor.fn_create_private(%s::text,%s::text,%s::jsonb,%s::text,%s::text,
                   %s::text,%s::jsonb,%s::text,%s::int,%s::text[],%s::text,%s::boolean)""",
                _version_args(name, checked),
            ).fetchone()[0]
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=None, tool="factor.create",
               summary=f"factor {factor_id}", error_class=None)
        return factor_id
    except Exception as exc:
        _fail(request, identity.user_id, None, "factor.create", exc)
        raise


def new_version(identity: Identity, factor_id: int, expression: str) -> int:
    request = begin(identity.user_id, None, "factor.version")
    try:
        checked = validate(expression)
        with transaction(user_id=identity.user_id) as conn:
            _bind_request(conn, request)
            version_no = conn.execute(
                """SELECT factor.fn_new_version(%s::bigint,%s::text,%s::jsonb,%s::text,%s::text,
                   %s::text,%s::jsonb,%s::text,%s::int,%s::text[],%s::text,%s::boolean)""",
                (factor_id, *_version_args(None, checked)),
            ).fetchone()[0]
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=None, tool="factor.version",
               summary=f"{factor_id}:{version_no}", error_class=None)
        return version_no
    except Exception as exc:
        _fail(request, identity.user_id, None, "factor.version", exc)
        raise


def copy_public(identity: Identity, factor_id: int, version_no: int, name: str) -> int:
    request = begin(identity.user_id, None, "factor.copy")
    try:
        with transaction(user_id=identity.user_id) as conn:
            _bind_request(conn, request)
            copied = conn.execute(
                "SELECT factor.fn_copy_public(%s::bigint, %s::int, %s::text)",
                (factor_id, version_no, name),
            ).fetchone()[0]
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=None, tool="factor.copy",
               summary=f"factor {copied}", error_class=None)
        return copied
    except Exception as exc:
        _fail(request, identity.user_id, None, "factor.copy", exc)
        raise


def archive_private(identity: Identity, factor_id: int) -> None:
    request = begin(identity.user_id, None, "factor.archive")
    try:
        with transaction(user_id=identity.user_id) as conn:
            _bind_request(conn, request)
            conn.execute("SELECT factor.fn_archive(%s::bigint)", (factor_id,))
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=None, tool="factor.archive",
               summary=str(factor_id), error_class=None)
    except Exception as exc:
        _fail(request, identity.user_id, None, "factor.archive", exc)
        raise


def run_factor(identity: Identity, factor_id: int, version_no: int, universe: str, as_of: date,
               idempotency_key: str | None = None):
    request = begin(identity.user_id, as_of, "factor.run")
    fingerprint = signature('factor.run', [factor_id, version_no, universe, as_of])
    try:
        existing = find_submission(identity.user_id, idempotency_key, fingerprint)
        if existing:
            finish(request, "SUCCESS", user_id=identity.user_id, as_of=as_of, tool="factor.run",
                   summary=f"replay {existing}", error_class=None)
            return int(existing), ""
        with transaction(user_id=identity.user_id, as_of=as_of, isolation="repeatable_read", timeout_ms=5000) as conn:
            _bind_request(conn, request)
            stored = _load_version(conn, factor_id, version_no)
            checked = validate(stored[0])
            if checked.digest != stored[1] or checked.normalized != stored[2]:
                raise FactorError("SYNTAX", "保存的公式结构与表达式不一致", 0, len(stored[0]))
            payload, digest, plan, _panel = compute(conn, checked, universe)
            run_id = save_run(conn, factor_id, version_no, as_of, universe, digest, payload)
            if idempotency_key:
                conn.execute(
                    """
                    INSERT INTO audit.idempotency(user_id, idempotency_key, request_id, result_ref)
                    VALUES (%s, %s, %s, %s)
                    """,
                    (identity.user_id, idempotency_key, request, submission_result(run_id, fingerprint)),
                )
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=as_of, tool="factor.run",
               summary=f"run {run_id}", error_class=None)
        return run_id, plan
    except psycopg.errors.UniqueViolation:
        try:
            existing = find_submission(identity.user_id, idempotency_key, fingerprint)
            if existing is None:
                raise ValueError('提交冲突无法核对，请重新检查请求')
        except Exception as exc:
            _fail(request, identity.user_id, as_of, 'factor.run', exc)
            raise
        finish(request, "SUCCESS", user_id=identity.user_id, as_of=as_of, tool="factor.run",
               summary=f"replay {existing}", error_class=None)
        return int(existing), ""
    except Exception as exc:
        _fail(request, identity.user_id, as_of, "factor.run", exc)
        raise


def read_values(identity: Identity, run_id: int):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        return conn.execute(
            """
            SELECT stock_code, trade_date, value, quality, reason_codes, reason_detail
            FROM factor.value WHERE run_id = %s ORDER BY stock_code
            """,
            (run_id,),
        ).fetchall()


def _version_args(name: str | None, checked):
    payload = (
        checked.source, Json(checked.normalized), checked.digest, checked.syntax_version,
        checked.unit_label, Json({"p": checked.unit.p, "h": checked.unit.h, "q": checked.unit.q}),
        checked.price_basis, checked.lookback, list(checked.fields), checked.explanation, checked.ca_sensitive,
    )
    if name is None:
        return payload
    return (name, *payload)


def _load_version(conn, factor_id: int, version_no: int):
    row = conn.execute(
        """
        SELECT expression, content_digest, normalized_tree
        FROM factor.version WHERE factor_id = %s AND version_no = %s
        """,
        (factor_id, version_no),
    ).fetchone()
    if row is None:
        raise FactorError("FIELD", "公式版本不存在或不能查看", 0, 1)
    return row


def _bind_request(conn, request) -> None:
    conn.execute("SELECT set_config('app.request_id', %s, true)", [str(request)])


def _replay(identity: Identity, key: str | None):
    if not key:
        return None
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        row = conn.execute(
            "SELECT result_ref FROM audit.idempotency WHERE idempotency_key = %s",
            (key,),
        ).fetchone()
    return None if row is None else row[0]


def _fail(request, user_id, as_of, tool, exc) -> None:
    kind = "REJECTED" if isinstance(exc, (FactorError, ValueError)) else "FAILURE"
    message = exc.__class__.__name__
    if isinstance(exc, FactorError):
        message = exc.category
    elif isinstance(exc, psycopg.Error):
        message = exc.sqlstate or "DB"
    try:
        finish(request, kind, user_id=user_id, as_of=as_of, tool=tool, summary=None, error_class=message)
    except Exception:
        finish(request, "PENDING_CHECK", user_id=user_id, as_of=as_of, tool=tool,
               summary=None, error_class="UNCONFIRMED")
