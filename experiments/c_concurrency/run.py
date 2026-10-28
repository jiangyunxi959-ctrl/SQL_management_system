"""E5：50 连接竞争资金/持仓，区分正常拒单与错误；只删本次创建的表。"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import csv
import json
import sys
import threading
import time
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import psycopg
from psycopg import sql
from etl.temporal.common import connect

OUTPUT = Path(__file__).resolve().parent


def measure(mode, workers=50, scenario='stock_sell'):
    if scenario not in ('stock_sell', 'cash_freeze'):
        raise ValueError('Unknown E5 scenario')
    initial = per_order = 100 if scenario == 'stock_sell' else 10000
    table = sql.Identifier('sim', 'e5_' + uuid.uuid4().hex)
    with connect() as conn:
        conn.execute(sql.SQL('CREATE TABLE {} (id int PRIMARY KEY, available int NOT NULL)').format(table))
        conn.execute(sql.SQL('INSERT INTO {} VALUES (1,%s)').format(table), [initial])
    barrier = threading.Barrier(workers, timeout=60)
    lock = threading.Lock()
    ready_times = []

    def worker(_):
        retries = 0
        with connect() as conn:
            # 连接建立时间不算在 SQL 竞争耗时里。
            with lock:
                ready_times.append(time.perf_counter())
            barrier.wait()
            while True:
                try:
                    with conn.transaction():
                        if mode == 'serializable':
                            conn.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
                        if mode == 'atomic':
                            accepted = conn.execute(sql.SQL('UPDATE {} SET available=available-%s WHERE id=1 AND available>=%s RETURNING available').format(table), [per_order, per_order]).fetchone() is not None
                        else:
                            suffix = sql.SQL(' FOR UPDATE') if mode == 'for_update' else sql.SQL('')
                            available = conn.execute(sql.SQL('SELECT available FROM {} WHERE id=1').format(table) + suffix).fetchone()[0]
                            if mode == 'unsafe':
                                barrier.wait()  # 所有请求都读到初始可用值后才写入，稳定复现竞争。
                            accepted = available >= per_order
                            if accepted:
                                conn.execute(sql.SQL('UPDATE {} SET available=available-%s WHERE id=1').format(table), [per_order])
                    return int(accepted), retries, time.perf_counter()
                except psycopg.errors.SerializationFailure:
                    conn.rollback()
                    retries += 1
                    if retries > 100:
                        raise
                    time.sleep(min(retries * .001, .02))
    try:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(worker, range(workers)))
        with connect() as conn:
            available = conn.execute(sql.SQL('SELECT available FROM {} WHERE id=1').format(table)).fetchone()[0]
        elapsed = max(r[2] for r in results) - max(ready_times)
        accepted = sum(r[0] for r in results)
        invariant = available >= 0 and accepted <= 1 and available == initial-accepted*per_order
        if mode == 'unsafe':
            assert accepted == workers and not invariant
        else:
            assert accepted == 1 and invariant
        return dict(scenario=scenario,mode=mode,workers=workers,accepted=accepted,rejected=workers-accepted,
                    initial_available=initial,per_order=per_order,final_available=available,
                    reserved_total=accepted*per_order,serialization_retries=sum(r[1] for r in results),
                    invariant_ok=invariant,seconds=round(elapsed,6),requests_per_second=round(workers/elapsed,2))
    finally:
        with connect() as conn:
            conn.execute(sql.SQL('DROP TABLE {}').format(table))


if __name__ == '__main__':
    results = [measure(mode, scenario=scenario)
               for scenario in ('stock_sell','cash_freeze')
               for mode in ('unsafe','for_update','atomic','serializable')]
    with (OUTPUT/'results.csv').open('w',newline='',encoding='utf-8') as handle:
        writer = csv.DictWriter(handle,fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    (OUTPUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    lines = ['# E5 本机实测结果', '',
             '2026-10-28；每组 50 连接。stock_sell 单位为股，初始 100 股、每单卖 100 股；cash_freeze 单位为元，余额 10,000 元、每单预留 10,000 元。', '',
             '| 场景 | 写法 | 成功 | 正常拒绝 | 剩余可用资源 | 总预留资源 | 重试次数 | 请求/秒 |',
             '|---|---|---:|---:|---:|---:|---:|---:|']
    for row in results:
        fields = ('scenario','mode','accepted','rejected','final_available',
                  'reserved_total','serialization_retries','requests_per_second')
        lines.append('| ' + ' | '.join(str(row[k]) for k in fields) + ' |')
    lines += ['', '不加锁组在两种场景均接受 50 单：超卖 4,900 股；对 10,000 元资金冻结 500,000 元。三种保护写法在两类场景均仅接受 1 单，正确拒绝 49 单，无超卖或超额冻结。', '',
              '资金场景以固定预留金额隔离并发问题，未模拟费用。真实函数的金额、费用与冻结对账另由 C 的 50 并发集成测试验证。', '',
              '吞吐量为单次本机同一行竞争实验，不代表普遍性能排名；连接建立不计时，序列化重试计入耗时。']
    (OUTPUT/'results.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    for row in results:
        print(row)
    print('[PASS] E5: unsafe oversells/overfreezes; all protected variants preserve both invariants')
