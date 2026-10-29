"""真正编译并运行的 LangGraph。模型看到工具结果后才能决定下一步。"""
from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime, timezone
import json
import hashlib
import time
from typing import TypedDict

from langgraph.graph import END, StateGraph

from app.agent.checkpoint import OWNER, OwnerCheckpointSaver
from app.agent.contracts import ToolRejected
from app.agent.deepseek import DeepSeekModel, ModelError
from app.agent.evidence import check_draft, parse_draft
from app.agent.gateway import ModelNotConfigured
from app.agent.tools import execute_tool, tool_specs
from app.db import transaction

RUNTIME: ContextVar = ContextVar("d2_runtime")
GRAPH = None


class FlowState(TypedDict, total=False):
    messages: list
    model_calls: int
    tool_calls: int
    repair_used: bool
    status: str
    evidence: list
    answer: dict
    error: str


def compiled_graph():
    global GRAPH
    if GRAPH is None:
        graph = StateGraph(FlowState)
        graph.add_node("model", _model)
        graph.add_node("tools", _tools)
        graph.add_node("finish", lambda state: state)
        graph.set_entry_point("model")
        graph.add_conditional_edges("model", _route, {"tools": "tools", "model": "model", "finish": "finish"})
        graph.add_edge("tools", "model")
        graph.add_edge("finish", END)
        GRAPH = graph.compile(checkpointer=OwnerCheckpointSaver())
    return GRAPH


def _model(state: FlowState) -> FlowState:
    runtime = RUNTIME.get()
    calls = state.get("model_calls", 0)
    if runtime.cancelled() or calls >= runtime.max_model_calls or _late(runtime):
        return {**state, "status": "stopped", "error": state.get("error") or "已达到调用上限或已取消"}
    try:
        if isinstance(runtime.model, DeepSeekModel):
            remaining = runtime.deadline_seconds - (datetime.now(timezone.utc) - runtime.started).total_seconds()
            message, attempts = runtime.model.complete(
                state["messages"], tool_specs(), max_attempts=runtime.max_model_calls-calls,
                deadline=time.monotonic()+remaining, should_stop=runtime.cancelled,
            )
        else:
            message, attempts = runtime.model.complete(state["messages"], tool_specs())
    except (ModelNotConfigured, ModelError) as exc:
        code = getattr(exc, "code", "MODEL_NOT_CONFIGURED")
        return {**state, "model_calls": calls+getattr(exc,"attempts",0), "status": "stopped", "error": code}
    calls += attempts
    if runtime.cancelled() or _late(runtime):
        return {**state, "model_calls": calls, "status":"stopped", "error":"CANCELLED_OR_TIMEOUT"}
    messages = list(state["messages"]) + [message]
    if message.get("tool_calls"):
        return {**state, "messages": messages, "model_calls": calls, "status": "tools"}
    draft = parse_draft(message.get("content") or "")
    evidence = {item["evidence_id"]: item["payload"] for item in state.get("evidence", [])}
    if draft is None:
        return _repair_or_stop(state, messages, calls, "回答不是可核对的 JSON")
    ok, reason = check_draft(draft, evidence)
    if not ok:
        return _repair_or_stop(state, messages, calls, reason)
    return {**state, "messages": messages, "model_calls": calls, "answer": draft, "status": "done"}


def _repair_or_stop(state, messages, calls, reason: str) -> FlowState:
    if not state.get("repair_used") and calls < RUNTIME.get().max_model_calls:
        messages = messages + [{
            "role": "system",
            "content": "数值核对没有通过：" + reason + "。请只依据已有工具结果重写 JSON，不要编造新的数字。",
            "tool_calls": [],
        }]
        return {**state, "messages": messages, "model_calls": calls, "repair_used": True, "status": "repair"}
    return {**state, "messages": messages, "model_calls": calls, "status": "cards_only", "error": reason}


def _tools(state: FlowState) -> FlowState:
    runtime = RUNTIME.get()
    messages = list(state["messages"])
    evidence = list(state.get("evidence") or [])
    used = state.get("tool_calls", 0)
    for call in messages[-1].get("tool_calls") or []:
        call_id = call["id"]
        if runtime.cancelled() or used >= runtime.max_tool_calls or _late(runtime):
            messages.append(_tool_message(call_id, {"ok": False, "error_code": "LIMIT", "message": "已达到工具或时间上限"}))
            continue
        used += 1
        name = (call.get("function") or {}).get("name") or ""
        outcome, error_code, ref = "SUCCEEDED", None, None
        try:
            args = json.loads((call.get("function") or {}).get("arguments") or "{}")
            result = execute_tool(runtime.identity, runtime.context, name, args)
            evidence_id = f"E{len(evidence) + 1}"
            result["evidence_id"] = evidence_id
            ref = result.get("object_ref")
            evidence.append({"evidence_id": evidence_id, "tool": name, "payload": result})
            messages.append(_tool_message(call_id, result))
        except ToolRejected as exc:
            outcome, error_code = "REJECTED", exc.code
            messages.append(_tool_message(call_id, {"ok": False, "error_code": exc.code, "message": str(exc)}))
        except PermissionError:
            outcome, error_code = "REJECTED", "NOT_VISIBLE"
            messages.append(_tool_message(call_id, {"ok": False, "error_code": "NOT_VISIBLE", "message": "当前上下文不可访问"}))
        except json.JSONDecodeError:
            outcome, error_code = "REJECTED", "BAD_ARGUMENTS"
            messages.append(_tool_message(call_id, {"ok": False, "error_code": "BAD_ARGUMENTS", "message": "工具参数不是 JSON"}))
        with transaction(user_id=runtime.identity.user_id) as conn:
            conn.execute("""INSERT INTO agent.tool_execution(owner_id,thread_id,turn_id,operation_key,call_id,status,result_ref,error_class)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                (runtime.identity.user_id,runtime.context.thread_id,runtime.context.turn_id,
                 'protocol:'+hashlib.sha256(call_id.encode()).hexdigest(),call_id,outcome,
                 name+(':'+str(ref) if ref is not None else ''),error_code))
    return {**state, "messages": messages, "evidence": evidence, "tool_calls": used, "status": "tools"}


def _route(state: FlowState) -> str:
    if state.get("status") == "repair":
        return "model"
    if state.get("status") in {"done", "stopped", "cards_only"}:
        return "finish"
    last = state.get("messages", [{}])[-1]
    if last.get("role") == "assistant" and last.get("tool_calls"):
        return "tools"
    return "finish"


def _tool_message(call_id: str, payload: dict) -> dict:
    return {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload, ensure_ascii=False, default=str)[:32000]}


def _late(runtime) -> bool:
    return (datetime.now(timezone.utc) - runtime.started).total_seconds() >= runtime.deadline_seconds


def invoke(initial: dict, thread_id: str, owner_id: int, runtime) -> dict:
    token_owner = OWNER.set(owner_id)
    token_runtime = RUNTIME.set(runtime)
    try:
        return compiled_graph().invoke(
            initial,
            config={"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
        )
    finally:
        RUNTIME.reset(token_runtime)
        OWNER.reset(token_owner)
