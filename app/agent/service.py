"""对话、个人提示词和一轮提问。身份只来自已登录用户。"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import json
import os
from uuid import UUID, uuid4

from app.agent.contracts import RequestContext
from app.agent.deepseek import DeepSeekModel
from app.agent.prompts import default_preferences, preference_message, system_hash, system_text
from app.agent.workflow import invoke
from app.auth.service import Identity
from app.db import transaction


@dataclass
class Runtime:
    identity: Identity
    context: RequestContext
    model: object
    started: datetime
    max_model_calls: int = 6
    max_tool_calls: int = 12
    deadline_seconds: int = 90
    cancel_event: object = None

    def cancelled(self) -> bool:
        if self.cancel_event is not None and self.cancel_event.is_set():
            return True
        with transaction(user_id=self.identity.user_id, read_only=True) as conn:
            active = conn.execute("SELECT * FROM core.fn_session_user(%s)", (self.identity.token_hash,)).fetchone()
            row = conn.execute("SELECT status FROM agent.turn WHERE turn_id=%s", (self.context.turn_id,)).fetchone()
        return not active or active[0] != self.identity.user_id or row is None or row[0] == "CANCELLED"


def data_mode() -> str:
    name = os.getenv("PGDATABASE", "")
    return "REAL" if "real" in name else "TEACHING"


def start_thread(identity: Identity, as_of: date, universe: str) -> str:
    _assert_identity(identity)
    if not isinstance(universe, str) or not 1 <= len(universe) <= 32:
        raise ValueError("股票池名称长度不合法")
    thread_id = uuid4()
    with transaction(user_id=identity.user_id) as conn:
        conn.execute(
            """
            INSERT INTO agent.conversation(thread_id, owner_id, as_of, universe, data_mode)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (thread_id, identity.user_id, as_of, universe, data_mode()),
        )
    return str(thread_id)


def ask(identity: Identity, thread_id: str, text: str, client_request_id: str, model=None, *, cancel_event=None) -> dict:
    if not text.strip() or len(text) > 4000:
        raise ValueError("问题为空或过长")
    conversation = _conversation(identity, thread_id)
    existing = _turn_by_request(identity, client_request_id)
    if existing and existing[2] != str(thread_id):
        raise ValueError("提交编号与当前对话不一致")
    if existing and existing[1] != "RUNNING":
        return _turn_view(identity, existing[0])
    if existing:
        raise ValueError("这次提问仍在处理中，请等待或取消")
    prompt_version, preference = active_preference(identity)
    turn_id = _begin_turn(identity, conversation, client_request_id, prompt_version)
    ctx = RequestContext(
        identity.user_id, conversation[1], conversation[2], conversation[3], thread_id, turn_id,
    )
    runtime = Runtime(
        identity, ctx, model or DeepSeekModel(), datetime.now(timezone.utc),
        int(os.getenv("AGENT_MAX_MODEL_CALLS", "6")),
        int(os.getenv("AGENT_MAX_TOOL_CALLS", "12")),
        int(os.getenv("AGENT_DEADLINE_SECONDS", "90")),
        cancel_event,
    )
    initial = {
        "messages": [
            {"role": "system", "content": system_text(), "tool_calls": []},
            {"role": "system", "content": "应用固定上下文（不能由用户偏好更改）：" + json.dumps(ctx.public(), ensure_ascii=False)
             + "。目录摘要含版本和历史长度，一般无须逐项describe。若用户只说上次/这个而历史无法唯一定位，请先补充。没有对话历史时，数据库中已有的运行不能当作用户口中的那个/这个因子；必须先询问具体因子或运行编号，不查任意运行，不自行比较多个因子。最终只返回JSON，不在JSON外附加文字。"},
            preference_message(preference),
            *_history(identity, thread_id),
            {"role": "user", "content": text, "tool_calls": []},
        ],
        "model_calls": 0,
        "tool_calls": 0,
        "repair_used": False,
        "status": "",
        "evidence": [],
        "answer": None,
        "error": None,
    }
    try:
        result = invoke(initial, thread_id, identity.user_id, runtime)
    except Exception:
        _finish_turn(identity, turn_id, "INTERRUPTED", None, "INTERRUPTED", 0, 0)
        raise
    status = {"done": "COMPLETED", "cards_only": "FAILED", "stopped": "FAILED"}.get(result.get("status"), "FAILED")
    if runtime.cancelled():
        status = "CANCELLED"
    _store_messages(identity, turn_id, result.get("messages") or [])
    _store_evidence(identity, turn_id, result.get("evidence") or [])
    _finish_turn(
        identity, turn_id, status, result.get("answer"), result.get("error"),
        result.get("model_calls") or 0, result.get("tool_calls") or 0,
    )
    view = _turn_view(identity, turn_id)
    view["cards"] = [item["payload"] for item in result.get("evidence") or []]
    return view


def _assert_identity(identity):
    with transaction(read_only=True) as conn:
        row = conn.execute("SELECT * FROM core.fn_session_user(%s)", (identity.token_hash,)).fetchone()
    if not row or row[0] != identity.user_id:
        raise PermissionError("登录已失效，请重新登录")


def _history(identity, thread_id):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        rows = conn.execute("""
            SELECT t.turn_id,t.answer,
              (SELECT content FROM agent.message m WHERE m.turn_id=t.turn_id AND m.role='user'
               ORDER BY seq DESC LIMIT 1)
            FROM agent.turn t WHERE t.thread_id=%s AND t.status='COMPLETED' AND t.answer IS NOT NULL
            ORDER BY t.turn_id DESC LIMIT 4
        """, (thread_id,)).fetchall()
    messages = []
    for _tid, answer, question in reversed(rows):
        if question:
            messages += [{"role":"user","content":question},
                         {"role":"assistant","content":json.dumps(answer,ensure_ascii=False)}]
    return messages


def list_threads(identity, as_of, universe):
    _assert_identity(identity)
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        return conn.execute("""SELECT thread_id::text,created_at FROM agent.conversation
            WHERE as_of=%s AND universe=%s ORDER BY created_at DESC LIMIT 20""", (as_of, universe)).fetchall()


def thread_turns(identity, thread_id):
    _conversation(identity, thread_id)
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        ids = conn.execute("SELECT turn_id FROM agent.turn WHERE thread_id=%s ORDER BY turn_id DESC LIMIT 10", (thread_id,)).fetchall()
    return [_turn_view(identity, row[0]) for row in reversed(ids)]


def cancel(identity: Identity, turn_id: int) -> None:
    with transaction(user_id=identity.user_id) as conn:
        conn.execute(
            """
            UPDATE agent.turn SET status='CANCELLED', finished_at=now(), error_class='CANCELLED'
            WHERE turn_id=%s AND status='RUNNING'
            """,
            (turn_id,),
        )


def save_preference(identity: Identity, content: str) -> int:
    _assert_identity(identity)
    text = content.strip()
    if not text or len(text) > 4000:
        raise ValueError("个人提示词不能为空，且不能超过 4000 字")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    with transaction(user_id=identity.user_id) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", [f"d2-prompt:{identity.user_id}"])
        profile = conn.execute(
            "SELECT profile_id FROM agent.prompt_profile WHERE name='default'"
        ).fetchone()
        if profile is None:
            profile_id = conn.execute(
                """
                INSERT INTO agent.prompt_profile(owner_id, name) VALUES (%s, 'default')
                RETURNING profile_id
                """,
                (identity.user_id,),
            ).fetchone()[0]
            version = 1
        else:
            profile_id = profile[0]
            version = conn.execute(
                "SELECT coalesce(max(version_no), 0) + 1 FROM agent.prompt_version WHERE profile_id=%s",
                (profile_id,),
            ).fetchone()[0]
        conn.execute(
            """
            INSERT INTO agent.prompt_version(profile_id, version_no, content, digest)
            VALUES (%s, %s, %s, %s)
            """,
            (profile_id, version, text, digest),
        )
        conn.execute(
            "UPDATE agent.prompt_profile SET active_version=%s WHERE profile_id=%s",
            (version, profile_id),
        )
    return version


def active_preference(identity: Identity) -> tuple[int | None, str]:
    _assert_identity(identity)
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        row = conn.execute(
            """
            SELECT v.version_no, v.content
            FROM agent.prompt_profile p
            JOIN agent.prompt_version v ON v.profile_id = p.profile_id AND v.version_no = p.active_version
            WHERE p.name='default'
            """
        ).fetchone()
    if row is None:
        return None, default_preferences()
    return row[0], row[1]


def _conversation(identity, thread_id):
    _assert_identity(identity)
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        row = conn.execute(
            "SELECT thread_id::text, as_of, universe, data_mode FROM agent.conversation WHERE thread_id=%s",
            (thread_id,),
        ).fetchone()
    if row is None:
        raise PermissionError("对话不存在或当前不可访问")
    return row


def _turn_by_request(identity, client_request_id):
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        return conn.execute(
            "SELECT turn_id, status, thread_id::text FROM agent.turn WHERE client_request_id=%s",
            (client_request_id,),
        ).fetchone()


def _begin_turn(identity, conversation, client_request_id, prompt_version) -> int:
    model = DeepSeekModel()
    with transaction(user_id=identity.user_id) as conn:
        return conn.execute(
            """
            INSERT INTO agent.turn(
                thread_id, owner_id, client_request_id, status, prompt_version, system_hash, provider, model_name
            ) VALUES (%s, %s, %s, 'RUNNING', %s, %s, 'deepseek', %s)
            RETURNING turn_id
            """,
            (conversation[0], identity.user_id, client_request_id, prompt_version, system_hash(), model.model),
        ).fetchone()[0]


def _finish_turn(identity, turn_id, status, answer, error, model_calls, tool_calls) -> None:
    with transaction(user_id=identity.user_id) as conn:
        current = conn.execute("SELECT status FROM agent.turn WHERE turn_id=%s", (turn_id,)).fetchone()
        if current and current[0] == "CANCELLED":
            conn.execute("UPDATE agent.turn SET model_calls=%s,tool_calls=%s WHERE turn_id=%s",(model_calls,tool_calls,turn_id))
            return
        conn.execute(
            """
            UPDATE agent.turn
            SET status=%s, answer=%s::jsonb, error_class=%s, model_calls=%s, tool_calls=%s, finished_at=now()
            WHERE turn_id=%s AND status='RUNNING'
            """,
            (status, json.dumps(answer) if answer is not None else None, error, model_calls, tool_calls, turn_id),
        )


def _store_messages(identity, turn_id, messages) -> None:
    with transaction(user_id=identity.user_id) as conn:
        for index, message in enumerate(messages, start=1):
            conn.execute(
                """
                INSERT INTO agent.message(turn_id, owner_id, seq, role, content, tool_call_id, tool_calls)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
                """,
                (
                    turn_id, identity.user_id, index, message.get("role") or "user",
                    (message.get("content") or "")[:32000], message.get("tool_call_id"),
                    json.dumps(message.get("tool_calls") or []),
                ),
            )


def _store_evidence(identity, turn_id, evidence) -> None:
    with transaction(user_id=identity.user_id) as conn:
        for item in evidence:
            payload = json.dumps(item["payload"], ensure_ascii=False, default=str)
            conn.execute(
                """
                INSERT INTO agent.evidence(turn_id, evidence_id, owner_id, tool_name, object_ref, context, payload, digest)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s)
                """,
                (
                    turn_id, item["evidence_id"], identity.user_id, item["tool"],
                    item["payload"].get("object_ref"), json.dumps(item["payload"].get("context") or {}),
                    payload, hashlib.sha256(payload.encode("utf-8")).hexdigest(),
                ),
            )


def _turn_view(identity, turn_id) -> dict:
    with transaction(user_id=identity.user_id, read_only=True) as conn:
        turn = conn.execute(
            """
            SELECT turn_id, status, answer, error_class, model_calls, tool_calls, prompt_version, system_hash
            FROM agent.turn WHERE turn_id=%s
            """,
            (turn_id,),
        ).fetchone()
        evidence = conn.execute(
            "SELECT evidence_id, tool_name, payload FROM agent.evidence WHERE turn_id=%s ORDER BY evidence_id",
            (turn_id,),
        ).fetchall()
    if turn is None:
        raise PermissionError("回合不存在或当前不可访问")
    return {
        "turn_id": turn[0], "status": turn[1], "answer": turn[2], "error": turn[3],
        "model_calls": turn[4], "tool_calls": turn[5], "prompt_version": turn[6],
        "system_hash": turn[7],
        "evidence": [{"evidence_id": row[0], "tool": row[1], "payload": row[2]} for row in evidence],
    }
