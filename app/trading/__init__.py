"""供页面和 D 调用的交易服务；不在 Python 中重复实现资金规则。"""
from .db import transaction


def create_account(owner_id, name, start_date, cash):
    with transaction(user_id=owner_id) as conn:
        return conn.execute('SELECT sim.fn_create_account(%s,%s,%s,%s)',
                            [owner_id, name, start_date, cash]).fetchone()[0]


def place_order(owner_id, account_id, code, side, exec_point, qty, limit_price=None):
    with transaction(user_id=owner_id) as conn:
        return conn.execute('SELECT sim.fn_place_order(%s,%s,%s,%s,%s,%s)',
                            [account_id, code, side, exec_point, qty, limit_price]).fetchone()[0]


def cancel_order(owner_id, order_id):
    with transaction(user_id=owner_id) as conn:
        conn.execute('SELECT sim.fn_cancel_order(%s)', [order_id])


def advance_day(owner_id, account_id):
    with transaction(user_id=owner_id) as conn:
        return conn.execute('SELECT * FROM sim.fn_advance_day(%s)', [account_id]).fetchall()
