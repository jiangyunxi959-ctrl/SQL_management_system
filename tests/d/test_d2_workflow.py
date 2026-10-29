from datetime import date
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from app.agent.contracts import ToolRejected
from app.agent.deepseek import DeepSeekModel, ModelError, _assistant, _post
from app.agent.evidence import check_draft
from app.agent.service import ask, save_preference, start_thread
from app.agent.tools import validate_arguments
from app.db import connect_admin
from app.factors.catalog import ORIGINAL_CODES
from app.factors.reference import evaluate
from app.factors.validate import validate
from app.factors.zero_source import zero_bit


def test_zero_amount_is_not_labeled_as_zero_volume():
    amount = validate("close_raw / amount")
    volume = validate("amount / volume")
    assert zero_bit(amount.tree.right) == 256
    assert zero_bit(volume.tree.right) == 8
    rows = [{
        "trade_date": date(2025, 5, 15), "has_bar": True, "is_suspended": False, "ca_in_window": False,
        "close_raw": 10, "amount": 0, "volume": 100, "open_raw": 10,
    }]
    _value, mask = evaluate(amount, rows)
    assert mask & 256 and not mask & 8
    rows[0]["amount"] = 1000
    rows[0]["volume"] = 0
    _value, mask = evaluate(volume, rows)
    assert mask & 8 and not mask & 256


def test_catalog_keeps_original_ten_and_adds_forty(identities):
    with connect_admin() as conn:
        codes = {row[0] for row in conn.execute("SELECT code FROM factor.definition WHERE visibility='SYSTEM_PUBLIC'")}
        digests = dict(conn.execute(
            """
            SELECT d.code, v.content_digest FROM factor.definition d
            JOIN factor.version v ON v.factor_id=d.factor_id AND v.version_no=1
            WHERE d.code = ANY(%s)
            """,
            (list(ORIGINAL_CODES),),
        ))
        sources = conn.execute("SELECT count(*) FROM factor.source_entry").fetchone()[0]
    assert ORIGINAL_CODES <= tuple(codes) or set(ORIGINAL_CODES) <= codes
    assert len(codes) == 50
    assert len(digests) == 10
    assert sources == 370


def test_model_loop_uses_run_id_before_statistics(identities):
    identity, _token = identities[0]
    with connect_admin() as conn:
        factor_id = conn.execute("SELECT factor_id FROM factor.definition WHERE code='VWAP_D'").fetchone()[0]
    from app.agent.deepseek import ScriptedLoop
    thread = start_thread(identity, date(2025, 5, 30), "SYNTH300")
    model = ScriptedLoop([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_run", "type": "function",
            "function": {"name": "run_factor", "arguments": json.dumps({"factor_id": factor_id, "version": 1})},
        }]},
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call_stats", "type": "function",
            "function": {"name": "factor_statistics", "arguments": ""},
        }]},
    ])
    # 第二步必须使用第一步真实返回的 run_id。测试模型在 complete 时读取上一条工具消息。
    model.complete = _follow_run(model, factor_id)
    result = ask(identity, thread, "计算当日成交均价并统计", "req-" + identity.username, model)
    assert result["tool_calls"] >= 2
    assert result["status"] == "COMPLETED"
    assert result["answer"] is not None and result["error"] is None
    tools = [item["tool"] for item in result["evidence"]]
    assert tools[:2] == ["run_factor", "factor_statistics"]
    other, _token = identities[1]
    with pytest.raises(PermissionError):
        ask(other, thread, "偷看", "req-other-" + other.username, model)


def _follow_run(model, factor_id):
    def complete(messages, tools):
        if model.index == 0:
            model.index += 1
            return model.steps[0], 1
        if model.index == 1:
            payload = json.loads(messages[-1]["content"])
            run_id = payload["data"]["run_id"]
            model.index += 1
            return {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_stats", "type": "function",
                "function": {"name": "factor_statistics", "arguments": json.dumps({"run_id": run_id})},
            }]}, 1
        count = payload_count(messages)
        model.index += 1
        return {"role": "assistant", "content": json.dumps({
            "answer": "统计已核对。",
            "citations": ["E2"],
            "claims": [{"evidence_id": "E2", "path": "data.valid_count", "raw_value": count, "display_unit": "count", "decimals": 0}],
            "expression_draft": None,
            "limitations": [],
        }, ensure_ascii=False), "tool_calls": []}, 1
    return complete


def payload_count(messages):
    for message in reversed(messages):
        if message.get("role") == "tool" and "valid_count" in message.get("content", ""):
            return json.loads(message["content"])["data"]["valid_count"]
    return 0


def test_other_user_cannot_read_preference(identities):
    owner, _token = identities[0]
    other, _other = identities[1]
    save_preference(owner, "只给甲看的偏好，不要改变权限。")
    with connect_admin() as conn:
        hidden = conn.execute("SELECT count(*) FROM agent.prompt_profile").fetchone()[0]
    assert hidden >= 1
    from app.db import transaction
    with transaction(user_id=other.user_id, read_only=True) as conn:
        assert conn.execute("SELECT content FROM agent.prompt_version").fetchall() == []


def test_prompt_and_tool_result_cannot_request_sql():
    with pytest.raises(ToolRejected):
        validate_arguments("list_factors", {"user_id": 1})
    with pytest.raises(ToolRejected):
        validate_arguments("screen", {"filters": [{"field": "roe", "op": ">", "value": 0.1}], "order_by": "ret_20d", "limit": 5, "sql": "delete"})
    ok, _reason = check_draft(
        {"answer": "没有数字", "citations": [], "claims": [], "expression_draft": None, "limitations": []},
        {},
    )
    assert ok


def test_deepseek_protocol_uses_returned_call_id():
    seen = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length))
            seen["auth"] = self.headers.get("Authorization")
            seen["body"] = body
            message = {"role": "assistant", "content": "", "tool_calls": [{
                "id": "call_from_server", "type": "function",
                "function": {"name": "list_factors", "arguments": "{}"},
            }]}
            payload = json.dumps({"choices": [{"message": message}]}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        blocked = DeepSeekModel()
        blocked.api_key = "test-key"
        blocked.base_url = f"http://127.0.0.1:{server.server_address[1]}"
        with pytest.raises(ModelError, match="模型地址"):
            blocked.complete([], [])
        message = _post(
            f"http://127.0.0.1:{server.server_address[1]}", "test-key", "deepseek-flash",
            [{"role": "user", "content": "ping"},
             {"role":"assistant","content":"pong","tool_calls":[]},
             {"role":"system","content":"Return JSON","tool_calls":[]}], [],
        )
        assert message["tool_calls"][0]["id"] == "call_from_server"
        assert seen["body"]["tool_choice"] == "auto"
        assert seen["body"]["thinking"] == {"type": "disabled"}
        assert seen['body']['response_format'] == {'type':'json_object'}
        assert all('tool_calls' not in item for item in seen['body']['messages'])
        assert seen["auth"] == "Bearer test-key"
        assert "test-key" not in json.dumps(seen["body"])
    finally:
        server.shutdown()
