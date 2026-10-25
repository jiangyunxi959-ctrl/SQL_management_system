# B 模块｜行情数据与指标计算

> **项目：** 知时——时点一致的量化投研 Agent 系统  
> **负责范围：** 成员 B（行情与计算层，兼项目环境与数据准备）  
> **阶段：** 2026-10-25 核心开发；2026-10-26 本地验收与交付  
> **协作分支：** `b/market-etl`

本模块负责将行情和复权数据从 BaoStock 下载到本地，通过 PostgreSQL 的 staging → ETL → 正式表链路完成校验与幂等入库，并基于后复权行情计算常用技术指标。同时提供数据库初始化、样本数据、自动化测试和数据质量巡检，供小组其他模块使用。

**模块边界：** 本 README 仅描述 B 已实现并验证的内容。证券主数据及正式交易日历属于 A；PIT、财务、回测、Agent 等模块不在本次交付范围内。`sql/dev/10_ref_stub.sql` 和 `etl/load/load_dev_ref.py` 仅供开发期联调，不能代替 A 的正式实现。

## 1. 已实现功能

| 子模块 | 实现内容 | 主要文件 |
| --- | --- | --- |
| 基础环境 | PostgreSQL 16、Schema、数据库角色、应用用户基础表 | `sql/00_init.sql`、`.env.example`、`requirements.txt` |
| 数据库重建 | 按 SQL 文件顺序重建开发数据库并写入合成样本 | `scripts/rebuild_db.py` |
| 数据下载 | BaoStock 证券列表、交易日历、不复权日线、复权因子 | `etl/download/download_baostock_sample.py` |
| 行情存储 | 日线表、年度 RANGE 分区、索引、业务约束 | `sql/20_mkt.sql` |
| ETL | staging 落地、数据校验、异常记录、批次统计、幂等合并 | `sql/20_mkt.sql`、`etl/load/` |
| 复权 | 因子有效区间、区间维护触发器、GiST 排他约束、后复权视图 | `mkt.adj_factor`、`mkt.v_bar_hfq` |
| 技术指标 | MA、EMA、收益率、波动率、成交额均值；物化视图 | `mkt.mv_indicator_daily` |
| 数据质量 | 缺失交易日行情、非交易日行情、缺失复权因子 | `etl.dq_*` 三个视图 |
| 验收 | SQL 自动化测试、SQL 与 pandas 独立计算对比 | `scripts/run_sql_tests.py`、`scripts/verify_real_indicators.py` |

数据处理链路：

```text
BaoStock API
   │
   ▼
data/raw/baostock/*.csv         （本地缓存，不上传 Git）
   │
   ▼
staging.*_raw                 （保留原始字段，便于校验和追溯）
   │
   ▼
etl.fn_merge_*                 （批次统计、拒绝记录、幂等 UPSERT）
   │
   ├──► mkt.daily_bar            （不复权日线、年度分区）
   └──► mkt.adj_factor           （累计后复权因子与生效区间）
                │
                ▼
         mkt.v_bar_hfq           （后复权行情）
                │
                ▼
         mkt.mv_indicator_daily  （技术指标物化视图）
```

## 2. 开发环境与目录

- PostgreSQL **16**（本地安装或 `docker compose`）
- Python **3.11**（本次验收版本）
- Windows PowerShell（已验证；其他系统可换用相应终端命令）
- Python 依赖：BaoStock、pandas、psycopg 3、python-dotenv，详见 `requirements.txt`

```text
.
├── .env.example                    # 配置模板，不含实际密码
├── requirements.txt
├── docker-compose.yml
├── sql/
│   ├── 00_init.sql                 # 基础对象
│   ├── 20_mkt.sql                  # 行情/复权/ETL/指标/DQ
│   ├── dev/10_ref_stub.sql         # DEV ONLY：A 的参考表占位实现
│   ├── seed/sample_data.sql        # 合成样本
│   └── tests/                      # SQL 验收用例
├── etl/
│   ├── download/download_baostock_sample.py
│   └── load/
│       ├── load_dev_ref.py         # DEV ONLY：仅用于真实数据测试库
│       ├── load_daily_bar.py
│       └── load_adjust_factor.py
├── scripts/
│   ├── rebuild_db.py
│   ├── run_sql_tests.py
│   └── verify_real_indicators.py
└── docs/
    ├── 字段对照.md
    └── worklog/B.md
```

`.env`、`.venv/`、`data/raw/` 和 `data/dumps/` 已配置为 Git 忽略项。真实数据 CSV 和数据库备份**不包含在代码仓库中**。

## 3. 快速复现：合成样本库 `zhishi`

在**仓库根目录**执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在本机的 `.env` 中配置 PostgreSQL 连接（尤其是 `PGADMIN_PASSWORD`），并确保 `PGDATABASE=zhishi`。不要提交 `.env`，也不要在聊天或截图中公开真实密码。

如果使用本地 PostgreSQL，先确保服务正在运行。若改用 Docker，可在准备好连接配置后运行 `docker compose up -d`；本地 PostgreSQL 已占用 5432 时，不要同时映射相同端口。

**重要：以下命令会删除并重建 `.env` / 环境变量 `PGDATABASE` 指向的数据库，数据库内旧数据将丢失。请仅对开发用 `zhishi` 执行，切勿指向 `zhishi_real` 或生产库。**

```powershell
# 如果之前临时设置过 PGDATABASE，先移除覆盖值
Remove-Item Env:PGDATABASE -ErrorAction SilentlyContinue
python scripts/rebuild_db.py --yes
python scripts/run_sql_tests.py
```

通过时最后应显示：

```text
[PASS] test_etl_idempotency.sql
[PASS] test_mkt_basic.sql
[PASS] test_mkt_indicator.sql
[PASS] EMA vs pandas (...)
[SUMMARY] daily_bar=10440, indicators=10440, DQ=(0, 0, 0)
[ALL PASS] B 10/25 market + indicator acceptance passed
```

合成样本包括 20 只证券、2024–2025 年共 **10,440** 条行情、**21** 条因子和 **10,440** 条指标记录。样本中包含模拟停牌、ST 标签与复权事件，主要用于可重复的逻辑与回归测试，**不代表真实市场数据**。

重建脚本在存在正式 `sql/10_ref.sql` 时优先使用它；否则使用 `sql/dev/10_ref_stub.sql`。将来接入 A 的正式参考数据模块后，需要重新进行集成验证。

## 4. 真实 BaoStock 数据验证（独立测试库 `zhishi_real`）

### 4.1 下载原始数据

```powershell
python etl/download/download_baostock_sample.py --start 2024-01-01 --end 2025-12-31 --stocks 20
```

输出到 `data/raw/baostock/`：`stock_basic.csv`、`trade_dates.csv`、`daily_bar.csv`、`adjust_factor.csv` 等。日线参数为 `frequency="d"`、`adjustflag="3"`（**不复权**）；复权因子另行获取。字段映射详见 [`docs/字段对照.md`](docs/字段对照.md)。

### 4.2 初始化独立数据库

**请勿把真实数据导入 `zhishi` 合成样本库。** 以下命令以 Windows PowerShell 和本地 PostgreSQL 16 为例；根据本机安装路径修改 `$psql`：

```powershell
$psql = "D:\postgreSQL16\bin\psql.exe"
$env:PGCLIENTENCODING = "UTF8"

# 以下 CREATE DATABASE 只需执行一次，已存在时跳过
& $psql -U postgres -h localhost -d postgres -c "CREATE DATABASE zhishi_real ENCODING 'UTF8';"

& $psql -U postgres -h localhost -d zhishi_real -v ON_ERROR_STOP=1 -1 -f ".\sql\00_init.sql"
& $psql -U postgres -h localhost -d zhishi_real -v ON_ERROR_STOP=1 -1 -f ".\sql\dev\10_ref_stub.sql"
& $psql -U postgres -h localhost -d zhishi_real -v ON_ERROR_STOP=1 -1 -f ".\sql\20_mkt.sql"
```

使用开发参考表时，先导入证券与交易日历：

```powershell
python etl/load/load_dev_ref.py
```

`load_dev_ref.py` 固定写入 `zhishi_real`，**仅供当前开发测试使用**。在正式系统中，应改用 A 负责的 `ref` 表与加载流程，而不是依赖此脚本。

### 4.3 导入行情和复权因子

两个正式 loader 会读取当前环境的 `PGDATABASE`，因此通过**只在当前命令块有效**的覆盖值将目标指向 `zhishi_real`：

```powershell
$env:PGDATABASE = "zhishi_real"
try {
    python etl/load/load_daily_bar.py
    python etl/load/load_adjust_factor.py
} finally {
    Remove-Item Env:PGDATABASE -ErrorAction SilentlyContinue
}
```

导入结束后，刷新指标物化视图：

```powershell
& $psql -U postgres -h localhost -d zhishi_real -v ON_ERROR_STOP=1 -c "REFRESH MATERIALIZED VIEW mkt.mv_indicator_daily;"
```

重复执行两个 loader 时，输入数据未变化的情况下 `rows_upsert` 应为 `0`，这是幂等性的重要验收点。

### 4.4 独立验证指标

```powershell
python scripts/verify_real_indicators.py
```

该脚本固定**只读连接** `zhishi_real`，对全部证券、全部日期使用 pandas 独立重算并比较九项结果：`close_hfq`、`ma20`、`ma60`、`ema12`、`ema26`、`ret_20d`、`ret_60d`、`vol_20d`、`amount_20d`。通过时输出 `[ALL PASS] Real market indicators agree with pandas on 9700 rows`。

## 5. 数据模型与指标口径

- **`mkt.daily_bar`**：不复权日线；以 `(stock_code, trade_date)` 为主键，按 `trade_date` 进行年度 RANGE 分区；提供时序及按日期检索所需索引。
- **`mkt.adj_factor`**：每只股票的累计后复权因子 `back_factor`，有效区间表示为 `[ex_date, 下一事件日)`；通过触发器衔接相邻区间，并使用 GiST 排他约束防止同一股票的区间重叠。
- **`mkt.v_bar_hfq`**：根据当日有效的累计因子对原始 OHLC 计算后复权价格。
- **`mkt.mv_indicator_daily`**：后复权日行情上的指标物化视图。导入或修改基础行情与因子后需要手动 `REFRESH`；它不会自动实时更新。

| 指标 | 计算口径 |
| --- | --- |
| `ma20` / `ma60` | 后复权收盘价最近 20 / 60 个交易日的算术平均 |
| `ema12` / `ema26` | 指数移动平均，`alpha = 2/(N+1)`，与 pandas `ewm(adjust=False)` 对齐 |
| `ret_20d` / `ret_60d` | 当日后复权收盘价 / 20 或 60 个交易日前收盘价 − 1 |
| `vol_20d` | 最近 20 个日对数收益的样本标准差 × `sqrt(252)` |
| `amount_20d` | 最近 20 个交易日原始成交额的平均值 |

MA、收益率和波动率的历史窗口不够时，对应值为 `NULL`，属于正常的指标预热行为。

质量巡检视图：

```sql
SELECT COUNT(*) FROM etl.dq_missing_bar;
SELECT COUNT(*) FROM etl.dq_bar_on_closed_day;
SELECT COUNT(*) FROM etl.dq_bar_without_factor;
```

## 6. 已完成的本地验收（2026-10-26）

| 验收项 | 结果 |
| --- | --- |
| 合成数据库 `zhishi` | 20 只证券、10,440 条日线、21 条因子、10,440 条指标 |
| 合成样本 SQL 自动化测试 | 3 个 SQL 文件全部通过；EMA 与 pandas 误差在容差内 |
| 真实数据 `zhishi_real` | 20 只证券；2024-01-02 至 2025-12-31 共 9,700 条日线、459 条因子 |
| 行情、因子重复导入 | 第二次导入实际 UPSERT 都为 0 |
| 后复权视图 / 指标物化视图 | 各 9,700 行 |
| 真实数据 pandas 对照 | 9 项指标全量比较，0 条不一致 |
| 数据质量巡检 | 缺失交易日行情 0、非交易日行情 0、无复权因子行情 0 |
| 因子有效区间 | 区间起点错误 0、衔接错误 0、空区间 0；20 个最终区间无上界 |
| GiST 排他约束 | 故意制造重叠区间被数据库拒绝，测试已回滚 |
| 年度分区 | 2024、2025 年各 5,220 条合成行情；观察到按年度条件分区裁剪 |
| 数据库备份 | 两套 `pg_dump -Fc` 归档已生成，`pg_restore -l` 可读取目录 |

注意：以上是**本地测试记录**，不是每次运行都会产生相同的真实数据结果；BaoStock 数据可能更新，网络或抽样条件也会影响下载结果。数据库备份尚未做完整恢复演练；分区裁剪检查不等同于完整的 E3 性能实验。

## 7. 协作接口与后续工作

**依赖 A：** `ref.security`、`ref.trading_calendar` 的正式建表和加载，以及后续与财务 / PIT 模块的联合验收。当前开发 stub 不能用于正式生产环境。

**提供给其他成员：**

- 原始行情：`mkt.daily_bar`
- 后复权行情：`mkt.v_bar_hfq`
- 技术指标：`mkt.mv_indicator_daily`
- 导入批次与拒绝记录：`etl.load_batch`、`etl.reject_row`
- 数据质量检查：`etl.dq_missing_bar`、`etl.dq_bar_on_closed_day`、`etl.dq_bar_without_factor`

**本次尚未完成：** E1–E4、E8 的完整性能实验与耗时报告、数据质量展示页面、正式 `ref` 集成、全 A 股扩展、备份的完整恢复演练。不能将这些内容列为已验收功能。

更多细节参见 [`docs/worklog/B.md`](docs/worklog/B.md) 和 [`docs/字段对照.md`](docs/字段对照.md)。
