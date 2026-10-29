"""Authenticated analysis, with background requests and an interactive cancel button."""
import streamlit as st
from psycopg import InterfaceError, OperationalError

from app.agent import jobs
from app.agent.deepseek import DeepSeekModel, ScriptedLoop
from app.agent.service import active_preference, cancel, list_threads, save_preference, start_thread, thread_turns
from app.ui.session import require_identity

st.title("AI 分析")
identity, as_of = require_identity()
model = DeepSeekModel()
if model.configured:
    st.success(f"已连接配置：{model.model}。分析结果将附上实际查询依据。")
else:
    st.warning("尚未配置真实模型。固定演示只用于查看界面，不是 DeepSeek 回答。")
universe = st.text_input("股票池", "SYNTH300", key=f"d_ai_pool_{identity.user_id}")
st.caption(f"研究日 {as_of}；股票池 {universe}。切换日期或股票池会切换对话范围。")
thread_key = f"d_ai_thread_{identity.user_id}_{as_of}_{universe}"
available = list_threads(identity, as_of, universe)
if not available:
    start_thread(identity, as_of, universe)
    available = list_threads(identity, as_of, universe)
ids = [row[0] for row in available]
if st.session_state.get(thread_key) not in ids:
    st.session_state[thread_key] = ids[0]
thread = st.selectbox("我的对话", ids, key=thread_key,
                      format_func=lambda value: "对话 " + value[:8])
if st.button("新建对话"):
    start_thread(identity, as_of, universe)
    st.session_state.pop(thread_key, None)
    st.rerun()

version, preference = active_preference(identity)
with st.expander("我的分析偏好", expanded=True):
    st.caption(f"当前版本：{version or '默认'}。保存后从下一次提问开始使用。")
    edited = st.text_area("我的提示词（下一回合生效）", preference, height=120,
                          max_chars=4000, key=f"d_ai_preference_{identity.user_id}")
    if st.button("保存个人提示词"):
        try:
            saved = save_preference(identity, edited)
            st.success(f"已保存第 {saved} 版，下一次提问才会使用。")
        except (ValueError, PermissionError) as exc:
            st.error(str(exc))

demo = st.checkbox("使用固定演示替身（不是 DeepSeek）", value=False, disabled=model.configured,
                    key=f"d_ai_demo_{identity.user_id}")
question = st.text_area("问题", "计算当日成交均价，并说明有效数量。", key=f"d_ai_question_{identity.user_id}")
pending_key = f"d_ai_pending_{identity.user_id}"
if st.button("提问", disabled=(not model.configured and not demo) or bool(st.session_state.get(pending_key))):
    try:
        chosen = None
        if demo:
            chosen = ScriptedLoop([{"role":"assistant", "tool_calls":[], "content":
                '{"answer":"这是固定演示，不是真实模型回答。","citations":[],"claims":[],"expression_draft":null,"limitations":["演示模式"]}'}])
        request_id = jobs.submit(identity, thread, question, chosen)
        st.session_state[pending_key] = request_id
        st.rerun()
    except (ValueError, PermissionError) as exc:
        st.error(str(exc))


def show_result(result):
    labels = {"COMPLETED":"已完成", "CANCELLED":"已取消", "FAILED":"未完成", "INTERRUPTED":"已中断", "RUNNING":"进行中"}
    st.caption(f"{labels.get(result['status'], result['status'])} · 模型请求 {result['model_calls']} 次 · 工具 {result['tool_calls']} 次")
    if result.get("error") and result['status'] != 'CANCELLED':
        st.warning("本次说明未完整生成，已完成的查询依据仍可查看。原因：" + result['error'])
    answer = result.get("answer") or {}
    if answer.get("answer"):
        st.markdown(answer["answer"])
    if answer.get("expression_draft"):
        draft = answer['expression_draft']
        st.code(draft.get('expression','') if isinstance(draft,dict) else draft, language='text')
        st.caption("这是检查后的草稿。请到公式页亲自保存。")
    for item in result.get("evidence") or []:
        payload = item.get("payload") or item
        with st.expander(f"{item.get('evidence_id','')} · {item.get('tool','查询依据')}"):
            st.json({"context":payload.get('context'),"data":payload.get('data'),"warnings":payload.get('warnings')})


@st.fragment(run_every=1)
def running_analysis():
    request_id = st.session_state.get(pending_key)
    if request_id:
        try:
            job = jobs.get(identity, request_id)
            if job.future.done():
                try:
                    job.future.result()
                except Exception:
                    st.session_state[f'd_ai_job_error_{identity.user_id}'] = "本次分析中断，请重新提问。"
                st.session_state.pop(pending_key, None)
                st.rerun()
            if job.stop.is_set():
                st.info("已取消。正在结束当前请求，不会继续查询或计算；最长等待当前网络请求结束。")
            else:
                st.info("正在分析，可以取消，也可以退出登录。")
            if st.button("取消分析", disabled=job.stop.is_set(), key=f"d_ai_cancel_{request_id}"):
                jobs.stop(identity, request_id)
                st.rerun(scope="fragment")
        except PermissionError:
            st.session_state.pop(pending_key, None)
            st.rerun()
        except (OperationalError, InterfaceError):
            st.info("数据库连接暂时中断，页面会自动重试。")
            return
    error = st.session_state.get(f'd_ai_job_error_{identity.user_id}')
    if error:
        st.warning(error)
    try:
        results = thread_turns(identity, thread)
    except (OperationalError, InterfaceError):
        st.info("数据库连接暂时中断，页面会自动重试。")
        return
    for result in results:
        if result['status'] == 'RUNNING' and not request_id:
            st.warning("之前的分析尚未结束。服务重启后，可取消这次分析，再重新提问。")
            if st.button('取消未结束的分析',key=f"d_ai_stale_{result['turn_id']}"):
                cancel(identity,result['turn_id'])
                st.rerun()
        elif result['status'] != 'RUNNING':
            with st.container(border=True):
                show_result(result)


running_analysis()
