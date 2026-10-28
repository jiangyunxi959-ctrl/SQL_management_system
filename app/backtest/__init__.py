"""三个 Python 策略：独立排序信号，共用数据库交易与会计规则。"""
from datetime import date

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.trading.db import transaction


def select_codes(rows, template: str, top_n: int) -> list[str]:
    if template not in ('momentum', 'ma_cross', 'quality'):
        raise ValueError('未知策略模板')
    field = 'roe' if template == 'quality' else 'ret_20d'
    eligible = [r for r in rows if r[field] is not None]
    if template == 'ma_cross':
        eligible = [r for r in eligible if r['ma20'] is not None and r['ma60'] is not None and r['ma20'] > r['ma60']]
    return [r['stock_code'] for r in sorted(eligible, key=lambda r: (-r[field], r['stock_code']))[:top_n]]


def run_backtest(owner_id: int, template: str, params: dict,
                 universe: str, start: date, end: date) -> int:
    """原分工接口；整个运行在同一事务中，失败不留下半成品账户或净值。"""
    if template not in ('momentum', 'ma_cross', 'quality'):
        raise ValueError('未知策略模板')
    with transaction(user_id=owner_id) as conn:
        run_id = conn.execute('SELECT research.fn_begin(%s,%s,%s,%s,%s,%s,%s)',
                              [owner_id, template, Jsonb(params), universe, start, end, 'PYTHON']).fetchone()[0]
        while True:
            signal_date = conn.execute('SELECT research.fn_prepare_day(%s)', [run_id]).fetchone()[0]
            if signal_date >= end:
                break
            with conn.cursor(row_factory=dict_row) as cur:
                cur.execute('SELECT * FROM research.fn_candidates(%s)', [universe])
                codes = select_codes(cur.fetchall(), template, int(params.get('top_n', 5)))
            conn.execute('SELECT research.fn_step(%s,%s::varchar[])', [run_id, codes])
        conn.execute('SELECT research.fn_finish(%s)', [run_id])
    return run_id


def replay_backtest(owner_id: int, run_id: int) -> int:
    """重用原参数和财报快照；行情/规则摘要改变时数据库明确拒绝。"""
    with transaction(user_id=owner_id, read_only=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute('SELECT r.*,s.template FROM research.backtest_run r JOIN research.strategy s USING(strategy_id) WHERE run_id=%s', [run_id])
            original = cur.fetchone()
    if original is None:
        raise ValueError('回测不存在或无权访问')
    params = dict(original['params'], replay_of=run_id)
    return run_backtest(owner_id, original['template'], params, original['universe'], original['start_date'], original['end_date'])
