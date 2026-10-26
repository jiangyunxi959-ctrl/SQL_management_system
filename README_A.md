# A 模块：时态数据层与 PIT 查询

这个项目叫“知时”：用户选一个过去的日期，查询当时的股票、行情、财报，再做选股和模拟研究。B 提供价格和指标；A 管历史资料与可见时间；C 管交易和回测；D 把工具接给 AI 和界面。

例如，某公司 4 月公告利润 1 亿元，8 月改成 8000 万。研究 5 月时，系统应返回 1 亿元。A 负责把这条规则落实到数据库，其他同学不用每写一次查询都重新判断公告时间。

本分支基于 B 的 `b/market-etl`（`769aa88`），新增 A 模块并保留 B 原文件。实现由三个核心 SQL 文件和独立 Python 脚本组成，数据库查询使用显式 SQL。

## 1. 交付内容

| 文件 | 功能 | 使用模块 |
|---|---|---|
| `sql/10_ref.sql` | 股票名单、名称/ST/行业/指数成员的历史，以及费率规则 | B、C、D |
| `sql/30_fin.sql` | 财报各次披露、本地修改前的旧版本、原始导入记录 | B、D |
| `sql/40_pit.sql` | 统一按研究日期查询，隐藏未来信息 | C、D |
| `etl/temporal/` | 接 B 的 CSV，整理历史区间，导入财报 | B |
| `sql/seed/pit_fixtures.sql` | 为时间边界准备的简单合成案例 | 全组 |
| `sql/seed/reference_rules.sql` | 有来源的历史费率、涨跌幅配置 | C |
| `sql/tests/test_ref_* / test_fin_* / test_pit_*` | 区间冲突、未来泄漏、系统快照、读写权限验证 | 全组 |
| `experiments/a_bias/` | E6/E7 曲线与选股差异，E11 财务快照复现 | 实验验证 |
| `app/ui/pages/a_bias.py` | 能切换日期的演示页 | A 单独演示，D 接导航 |

P0 代码、偏差实验、演示页已提供。P1 已提供系统版本、三组真实报告期更正案例、E11 的财务输入与固定价格复现；C 的完整回测引擎接入仍由 C 完成。P2 匿名化不在本次实现范围。

## 2. 运行与验证

环境：Python 3.11+、PostgreSQL 16；数据库可沿用 B 的 Docker。以下命令都在仓库根目录执行。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements-a.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

如果已有 `.env`，保留原文件。填入 PostgreSQL 管理密码及 `AGENT_READER_PASSWORD`；演示页使用受限角色登录。建一个独立的样本库，例如把 `PGDATABASE` 设为 `zhishi_a_sample`。

下面第一条会删除并重建 `.env` 指向的数据库，因此只对准备好的样本库执行：

```powershell
python scripts/rebuild_db.py --yes
python scripts/setup_a_sample.py
python scripts/run_sql_tests.py
python scripts/check_a_python.py
python scripts/check_a_integration.py
python experiments/a_bias/run.py
python experiments/a_bias/e11_replay.py
python scripts/check_a_ui.py
python -m streamlit run app/ui/pages/a_bias.py
```

B 的重建器会自动发现 A 的三个正式 SQL 文件。第二条补充 A 的样例和规则，可重复执行。测试结束仍是原样本数据；CSV 集成测试只删除它自己创建的临时数据库。

**已有 B 的数据库时**：使用 `python scripts/apply_a.py` 接入 A，原行情保留。合成库再补 `setup_a_sample.py`；真实库直接按第 4 节补数据。

`run_sql_tests.py` 最后一行仍沿用 B 原来的提示文字；前面逐条列出的 `[PASS] test_fin_* / test_pit_* / test_ref_*` 就是 A 的结果。

演示页可试四个日期：

| 研究日期 | sh.600001 应显示 |
|---|---|
| 2025-04-27 | 还没有财报 |
| 2025-05-01 | 一季报利润 1 亿元 |
| 2025-08-21 | 一季报更正为 8000 万元 |
| 2025-08-30，周六 | 半年报利润 1.6 亿元；行情仍取上周五 |

这些数字和 `SYNTH300` 都是教学合成数据。真实更正案例另见 [案例说明](docs/A_真实更正案例.md)。

## 3. 时态建模约定

**区间**：`[2025-03-03,2025-03-17)` 表示 3 月 3 日开始是 ST，3 月 17 日已经不是。数据库排他约束会拒绝同一股票在同一天出现两种状态。

**两种时间**：`announce_date / available_date` 表示披露及可用时间；`sys_period` 表示数据在本地系统中的存续时间。公司发布财报更正时新增一条披露；修正本地录入错误时更新对应记录，并自动保存更新前的版本。

**PIT 查询**：`pit` 是 Point In Time，即“截至某个时间”。先在事务里设研究日期，再调用 `pit` 工具。研究日期按自然日日终解释，所以当天财报适合用于下一交易日的决策；不要拿日终信息模拟当天开盘已经成交。

```sql
BEGIN;
SET LOCAL app.as_of_date = '2025-05-01';
SELECT * FROM pit.fn_fundamentals('sh.600001', 4);
SELECT * FROM pit.fn_index_members('SYNTH300', '2025-05-01');
SELECT * FROM pit.fn_screen(
  'SYNTH300', '[{"field":"roe","op":">=","value":0.09}]', 'ret_20d', 10
);
COMMIT;
```

5 月还在指数里、7 月才退出的股票，5 月应该看得到，但不能看到“7 月退出”这个未来终点。PIT 会把区间显示为截至研究日次日。

## 4. 与 B 的真实数据衔接

真实行情和合成样本使用不同数据库。B 目前的真实下载方式从今天仍上市的股票中选样本，研究全市场幸存者偏差时，还需要补齐退市股票与历史成员，不能把这 20 只当成完整历史股票池。

在独立库完成 `python scripts/rebuild_db.py --yes --no-seed` 后：

```powershell
python etl/download/download_baostock_sample.py
python etl/temporal/load_reference.py
python etl/load/load_daily_bar.py
python etl/load/load_adjust_factor.py
python etl/temporal/load_reference.py --status-only
```

日历、代码、上市日期直接接 B 的 `data/raw/baostock`；当前名称从实际观察日起记录。ST 状态只消费 B 已接受的原始行，有交易日缺口就报错，避免把未知状态连成一大段。行情指标按 B README 的命令刷新。

财报有两条路径：

- 已核对历史公告：参考 `etl/temporal/restatement_examples.csv`，逐次填写公告日期和来源，运行 `python etl/temporal/load_financials.py 文件.csv`。
- 供应商当前快照：`python etl/temporal/download_financials.py --year 2024 --quarter 4`，再导入输出 CSV。此类值从采集日才可用，不能冒充原公告版本。BaoStock 的主营收入、合并净利润、TTM EPS 口径在 `notes` 中保留。

同一披露键的已核对公告与供应商快照不能互相覆盖；再次采集时若可用日不同，导入器会要求核对版本，原 CSV 应继续保留。这个基础实现不自动合并多供应商数据。

指数/行业历史：`load_memberships.py 文件.csv --kind index` 或 `--kind industry`。CSV 列为 `stock_code,member_code,member_name,start_date,end_date,source_url`。结束日不包含在区间内；只有公告证明持续有效时才留空。

若拿到的是完整逐日成分股快照，先用 `convert_member_snapshots.py 输入.csv 输出.csv` 转换。输入列及空名单约定在文件开头。只合并连续自然日；每周采样的中间未知日期不作推断。

## 5. C、D 接入说明

详见 [接口与边界](docs/A_接口与验收.md)。原分工约定中的函数名称和参数保留。新增字段提供数据来源和口径，D 展示财报时可一起展示。

- C 使用 `ref.fn_fee / ref.fn_limit_pct`，先加载 `reference_rules.sql`。规则的日期覆盖写在 SQL 中，缺规则直接报错。普通新股上市初期、退市整理、交易限制及撮合由 C 处理。
- D 用真实 `agent_reader` 连接；在每次事务里使用 `SET LOCAL` 或参数化 `set_config(..., true)` 设置日期。该角色没有 `ref/fin/mkt` 原表访问权。
- 会话变量由 D 的可信程序设置。数据库自定义变量本身可被连接持有者更改；Agent 工具网关只允许这五个工具，不提供任意 SQL、`SET` 或 `set_config` 给模型。
- E11：保存财报导入完成后的快照时刻，再设置 `app.data_snapshot_at` 重放。ETL 与快照采集串行；行情、规则和策略参数还需 C 另行固定。

## 6. 相关文档

开发与验收记录见 [A 工作日志](docs/worklog/A.md)，实验结果见 [E6/E7](experiments/a_bias/results.md)。
