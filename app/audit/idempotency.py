"""重复提交必须同时匹配业务类型和全部参数，旧的不完整记录保守拒绝。"""
import hashlib
import json
from decimal import Decimal

from app.db import transaction


def signature(operation, payload):
    def encode(value):
        if isinstance(value, Decimal):
            return format(value.normalize(), 'f')
        if hasattr(value, 'isoformat'):
            return value.isoformat()
        raise TypeError('不支持的提交参数')
    text = json.dumps([operation, payload], sort_keys=True, separators=(',', ':'), default=encode)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def find(user_id, key, fingerprint):
    if not key:
        return None
    with transaction(user_id=user_id, read_only=True) as conn:
        row = conn.execute('SELECT result_ref FROM audit.idempotency WHERE idempotency_key=%s', [key]).fetchone()
    if row is None:
        return None
    try:
        saved = json.loads(row[0])
    except (TypeError, ValueError):
        saved = None
    if not isinstance(saved, dict) or saved.get('signature') != fingerprint:
        raise ValueError('提交编号已用于其他操作或参数，或属于无法核对的旧记录；请使用新的提交编号')
    return str(saved['ref'])


def result(reference, fingerprint):
    return json.dumps({'ref': str(reference), 'signature': fingerprint}, sort_keys=True)
