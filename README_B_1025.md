# B — 10 月 25 日交付：环境、数据、行情与指标

这份目录只覆盖 **B 今天应该完成的范围**：环境、样本/原始数据、ETL、行情/复权/指标，以及可重复验收。PIT、双时态、模拟交易、Agent 不在今天范围内。

## 0. 今天做到什么才算完成

运行以下两条命令后全部 PASS：

```powershell
python scripts/rebuild_db.py --yes
python scripts/run_sql_tests.py
```

最后应看到：

```text
[ALL PASS] B 10/25 market + indicator acceptance passed
```

这才算“完成”，不是只看 SQL 能保存。

## 1. 环境

推荐：Python 3.11 + PostgreSQL 16。Windows PowerShell：

```powershell
cd zhishi_B_1025
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

数据库二选一：

**A. 已安装 PostgreSQL 16**：把 `.env` 的管理员密码改成安装时设置的 postgres 密码。

**B. 有 Docker Desktop**：

```powershell
docker compose up -d
```

默认 `.env.example` 使用本地 `postgres/postgres`；只用于开发。

## 2. 一键重建样本库

```powershell
python scripts/rebuild_db.py --yes
```

执行顺序：

1. 删除并重建 `zhishi` 数据库；
2. `sql/00_init.sql`；
3. 如果存在正式 `sql/10_ref.sql` 就执行它，否则执行 **仅开发用** `sql/dev/10_ref_stub.sql`；
4. `sql/20_mkt.sql`；
5. 后续若出现 `30_...90_*.sql` 会按前缀执行；
6. `sql/seed/sample_data.sql`；
7. 刷新指标物化视图。

开发 stub 只提供 `ref.security` 和 `ref.trading_calendar` 的接口形状，用来解除 B 今天的外键阻塞；A 合并正式 `10_ref.sql` 后自动不再使用 stub。

## 3. 样本数据内容

`sample_data.sql` 生成确定性演示数据：

- 20 只股票；
- 2024-01-02 ～ 2025-12-31 的工作日行情（超过 1 万行）；
- `sh.600001` 在 2025-05-15 停牌；
- `sz.000001` 在 2025-03-03 ～ 2025-03-14 的 staging `isST=1`，留给 A 做连续区间转换；
- `sh.600001` 在 2025-06-16 发生一次演示复权事件，后复权因子从 1 变为 1.08；
- 数据明确是 **synthetic/demo**，不伪装成真实行情。

## 4. 今天已经实现的数据库对象

核心接口与分工文档保持一致：

- `mkt.daily_bar(stock_code, trade_date, open, high, low, close, pre_close, volume, amount, is_suspended)`；
- 按年 RANGE 分区（2014–2026）；
- `(trade_date, stock_code) INCLUDE (close, volume)` 截面索引，主键同时提供 `(stock_code, trade_date)` 时序索引；
- `mkt.adj_factor` + `trg_adj_factor_valid`；
- `mkt.v_bar_hfq`；
- `mkt.ema(numeric,numeric)`；
- `mkt.mv_indicator_daily`：`ma20/ma60/ema12/ema26/ret_20d/ret_60d/vol_20d/amount_20d`；
- `etl.load_batch`、`etl.reject_row`、`staging.daily_bar_raw`、`staging.adjust_factor_raw`；
- `etl.fn_merge_daily_bar()` 和 `etl.fn_merge_adj_factor()` 幂等导入；
- 三个数据质量巡检视图。

## 5. 自动验收覆盖什么

```powershell
python scripts/run_sql_tests.py
```

会检查：

- 20 只证券、超过 1 万行行情；
- 指标行数和行情行数一致；
- 停牌样例存在；
- 缺失行情 / 非交易日行情 / 无复权因子三个 DQ 视图均为空；
- MA20/MA60/20 日收益的 warm-up 边界正确；
- 复权事件前因子为 1、事件日起为 1.08；
- 后复权公式正确；
- 同一批业务数据重新导入时 `rows_upsert=0`；
- SQL EMA 与 pandas `ewm(adjust=False)` 最大误差 < `1e-6`。

## 6. 拉一份真实 BaoStock 数据验证字段

这一步需要能联网：

```powershell
python etl/download/download_baostock_sample.py --start 2024-01-01 --end 2025-12-31 --stocks 20
```

输出到 `data/raw/baostock/`：

- `stock_basic.csv`
- `trade_dates.csv`
- `daily_bar.csv`
- `adjust_factor.csv`
- `selected_codes.txt`

字段对应见 `docs/字段对照.md`。

正式导入真实行情前，A 要先把这些证券写入正式 `ref.security`。之后：

```powershell
python etl/load/load_daily_bar.py
python etl/load/load_adjust_factor.py
```

最后刷新：

```sql
REFRESH MATERIALIZED VIEW mkt.mv_indicator_daily;
```

## 7. 10 月 25 日提交/截图建议

至少留 5 张证据：

1. `python scripts/rebuild_db.py --yes` 成功；
2. `python scripts/run_sql_tests.py` 全 PASS；
3. `SELECT tableoid::regclass, count(*) FROM mkt.daily_bar GROUP BY 1 ORDER BY 1;` 展示年度分区；
4. 查询 `sh.600001` 2025-06-13 与 2025-06-16 的 `mkt.v_bar_hfq`，展示因子由 1 → 1.08；
5. 查询一只股票最近 10 行 `mkt.mv_indicator_daily`，展示 MA/EMA/收益/波动率。

到这里，B 的 10 月 25 日任务才算闭环。
