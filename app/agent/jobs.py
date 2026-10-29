"""Keep network work outside Streamlit reruns so cancel/logout remain usable."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from uuid import uuid4

from app.agent.service import _assert_identity, _conversation, ask, cancel, _turn_by_request

POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="d2-analysis")
LOCK = Lock()
JOBS = {}


@dataclass
class Job:
    owner_id: int
    thread_id: str
    request_id: str
    stop: Event
    future: object = None


def submit(identity, thread_id, question, model=None):
    _conversation(identity, thread_id)
    with LOCK:
        if any(job.owner_id == identity.user_id and not job.future.done() for job in JOBS.values()):
            raise ValueError("已有分析正在结束，请稍后再提问")
        for key in list(JOBS):
            if len(JOBS) < 100:
                break
            if JOBS[key].future.done():
                del JOBS[key]
        if sum(not job.future.done() for job in JOBS.values()) >= 4:
            raise ValueError("当前分析较多，请稍后重试")
        request_id = "ui-" + uuid4().hex
        job = Job(identity.user_id, thread_id, request_id, Event())
        job.future = POOL.submit(ask, identity, thread_id, question, request_id, model,
                                 cancel_event=job.stop)
        JOBS[request_id] = job
    return request_id


def get(identity, request_id):
    _assert_identity(identity)
    with LOCK:
        job = JOBS.get(request_id)
    if not job or job.owner_id != identity.user_id:
        raise PermissionError("分析不存在或当前不可访问")
    return job


def stop(identity, request_id):
    job = get(identity, request_id)
    job.stop.set()
    turn = _turn_by_request(identity, request_id)
    if turn:
        cancel(identity, turn[0])


def cancel_owner(identity):
    with LOCK:
        owned = [key for key, job in JOBS.items() if job.owner_id == identity.user_id and not job.future.done()]
    for request_id in owned:
        stop(identity, request_id)
