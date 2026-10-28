"""在已有 A/B 数据库上事务化安装 C；不删库、不导入合成数据。"""
from pathlib import Path

from etl.temporal.common import connect

ROOT = Path(__file__).resolve().parents[2]


def main():
    with connect() as conn:
        required = ('core.app_user', 'ref.security', 'ref.trading_calendar',
                    'ref.fee_rule', 'ref.price_limit_rule', 'mkt.daily_bar',
                    'mkt.adj_factor', 'mkt.mv_indicator_daily', 'fin.report',
                    'fin.report_history', 'pit.fin_latest', 'pit.index_member')
        missing = [name for name in required
                   if conn.execute('SELECT to_regclass(%s)', [name]).fetchone()[0] is None]
        if missing:
            raise RuntimeError('请先完成 A/B 建表：' + ', '.join(missing))
        for name in ('50_sim.sql', '60_research.sql'):
            conn.execute((ROOT / 'sql' / name).read_text(encoding='utf-8'))
            print(f'[SQL] sql/{name}')
    print('[PASS] C installed in one transaction; existing market data retained')


if __name__ == '__main__':
    main()
