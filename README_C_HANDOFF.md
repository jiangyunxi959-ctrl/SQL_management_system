# C 模块交接：交易、回测与并发实验

交付日期：2026-10-28。交付分支：`c/trading-backtest`。基线为 `b/market-etl` 的 `1a137eb`，其中已合入 A；本次新增 C 的文件，不修改 A/B 的已有文件。

C 的代码、样本验收与实验结果在此分支交付。正式事务助手和登录由 D 接入；全系统端到端联调、依赖方审核及合并主干属于后续集成工作，不能仅凭 C 的测试通过视为整组已经完成。

## 1. 分工核对与交付内容

P0：账户/持仓/订单/成交/流水与对账、订单状态机、开户/下单/撤单/每日撮合、E5 四种并发写法、数据库内动量回测与最大回撤、三个 Python 模板均已提供，并有对应验收。

P1：SQL/Python 日净值和月收益对照、backtrader 买入持有独立对照、回测与模拟交易页，以及与 A 财报系统版本配合的 E11 重放均已提供。backtrader 对照覆盖一个买入持有案例，不宣称三个模板都完成了独立引擎全量验证。

主要文件：

- `sql/50_sim.sql`：交易表、状态机、锁、函数、约束、只追加记录、RLS 和对账。
- `sql/60_research.sql`：回测表、SQL 动量基准、每日执行、回撤和输入摘要。
- `app/trading/`：Python 交易服务、事务适配器及已有库安装入口。
- `app/backtest/__init__.py`：`momentum / ma_cross / quality` 三个模板和重放服务。
- `app/ui/pages/c_trading.py / c_backtest.py`：可独立运行的 Streamlit 页面。
- `sql/tests/test_sim_* / test_research_*`、`tests/c/`：SQL、真实账号、并发、回测、快照及页面测试。
- `experiments/c_concurrency/`：E5 脚本、CSV/JSON 吞吐量与正确性结果。
- `experiments/c_backtest/`：SQL/Python 净值、月收益、汇总和曲线。
- `README_C.md`：开发逻辑、字段口径、运行方法和完整边界。

个人报告/PPT 素材可从实验结果和 `docs/worklog/C.md` 整理。按此前安排，本次不生成 PPT，也不把整组报告统稿记为已完成。

## 2. 获取代码与准备环境

在自己的仓库中先保存本地改动，然后获取 C 分支。下面以新建一个本地验收分支为例；若同名分支已存在，直接切换到它，不要重置已有工作。

```powershell
git fetch origin
git switch -c c-review --track origin/c/trading-backtest
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-c.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

环境要求：Python 3.11+、PostgreSQL 16，以及 `btree_gist` 扩展。Docker 可选，普通本地 PostgreSQL 同样可用。不要提交 `.env`、虚拟环境、原始缓存或数据库备份。

自行填写 `.env` 的管理员连接和四个开发角色密码。`PGADMIN_HOST/PORT` 与 `PGHOST/PORT` 必须指向同一实例；`PGDATABASE` 是目标数据库。交易与页面使用 `app_user`，管理员账号仅供初始化和验收。不要使用 C 电脑上的密码，也不要把本机临时目录当作共享部署依赖。

## 3. 两种接入方式

### 从零建立专用样本库

将 `.env` 的 `PGDATABASE` 配置为专用名称，例如 `zhishi_c_sample`。下面第一条会删除并重建目标数据库，只能对专门准备的样本库执行，不能指向原有真实数据或其他成员的工作库。

```powershell
.venv\Scripts\python.exe scripts/rebuild_db.py --yes
.venv\Scripts\python.exe scripts/setup_a_sample.py
.venv\Scripts\python.exe scripts/run_sql_tests.py
.venv\Scripts\python.exe -m pytest tests/c -q
.venv\Scripts\python.exe experiments/c_concurrency/run.py
.venv\Scripts\python.exe experiments/c_backtest/run.py
```

B 重建器按顺序发现 `50_sim.sql / 60_research.sql`。`setup_a_sample.py` 补 A 的费率、历史成员、财报等教学案例。行情为合成数据，2024–2025 年共 20 只证券、10,440 条行情。

`pytest` 仅允许数据库名以 `_sample` 结尾；测试创建临时用户并清理这些用户的 C 数据。E11 会短暂修改合成财报和一条行情以验证重放，随后恢复当前值，因此验收时不要让 ETL、其他测试或演示同时写这个样本库。

### 在已有 A/B 数据库上安装 C

保留已有 `.env`，确认目标数据库已完成 A/B 建表。运行：

```powershell
.venv\Scripts\python.exe -m app.trading.setup
```

该入口检查 A/B 依赖，在一个事务中执行 C 的两个 SQL；不删库、不加载合成行情。可在当前相同版本上重复执行。已有真实库不要运行 `setup_a_sample.py` 或 C 的合成样本测试。

A 的历史费率、证券状态、交易日历、指数成员，以及 B 的原始行情和已刷新的指标必须准备齐全。缺数据应先由对应模块补充，不要用合成案例混入真实库来消除报错。

## 4. D 的调用接口与接入顺序

先接事务助手和可信用户身份，再接交易/回测服务，最后挂导航和 Agent 工具。

```python
from datetime import date
from app.trading import create_account, place_order, cancel_order, advance_day
from app.backtest import run_backtest, replay_backtest

account_id = create_account(owner_id, '练习账户', date(2025, 5, 13), 100000)
order_id = place_order(owner_id, account_id, 'sh.600003', 'B', 'OPEN', 100)
results = advance_day(owner_id, account_id)
# results: [(order_id, status, reason), ...]

run_id = run_backtest(owner_id, 'momentum', {'top_n': 3, 'initial_cash': 100000},
                      'SYNTH300', date(2025, 3, 3), date(2025, 5, 30))
replayed_id = replay_backtest(owner_id, run_id)
```

`cancel_order(owner_id, order_id)` 仅能撤销自己的 PENDING 订单。SQL 四个交易函数与原分工签名保持一致；新增的回测辅助函数和字段由 C SQL/COMMENT 定义。

当前缺少 D 的 `app/db.py`。C 的适配器会尝试导入 `app.db.transaction`：存在就委托，不存在才用 C 的临时连接实现。D 助手须支持原约定的 `user_id/as_of/read_only/role` 参数，并返回 psycopg Connection，支持 `execute()` 和 `cursor()`。用户与日期使用事务内 `SET LOCAL` 或 `set_config(..., true)`，不得污染后续连接。

`owner_id` 必须来自可信登录。演示页的用户 ID 输入框仅用于样本演示；D 接入正式身份后应替换。保留 C 的 RLS 和函数归属校验，普通应用账号不应直接写表，Agent 角色不应直接拥有交易写函数或任意 SQL 能力。

页面可独立运行，D 再统一挂入导航：

```powershell
.venv\Scripts\python.exe -m streamlit run app/ui/pages/c_trading.py
.venv\Scripts\python.exe -m streamlit run app/ui/pages/c_backtest.py
```

## 5. 必须保持的交易与回测口径

- 研究日期为日终；当天信号只能在下一交易日 OPEN/CLOSE 执行，不允许用当日完整数据模拟当日开盘成交。
- 资金余额包含冻结部分。下单先冻结；成交后释放冻结、实际记账；撤单或拒单释放冻结。所有账户写入先锁账户。
- T+1 按执行日落实：同日买单不能给同日卖单提供持仓；今天日终提交、明天执行的卖单可以卖今天已经买入的股票。
- 限价只在指定开盘或收盘点判断；不扫描日内路径，不模拟部分成交和排队。涨停不买、跌停不卖。
- 指标使用后复权行情，真实成交和持仓估值使用原始价格；选股使用 A 的历史 PIT 成员和财报。
- 回测保留仍入选持仓、对新入选股票补仓，不预支未成交卖单收入；现金和整手约束会使组合不完全等权。
- E11 恢复财报版本；行情、规则等输入摘要必须匹配。摘要变化时先恢复原备份，摘要本身不是备份。

## 6. 验收证据与集成后复核

交付前使用新的独立样本库从零重建，复核全部 SQL 测试、C Python/页面测试和 E5；具体结果记在 `docs/C_交付验收.md`。已有的逐日曲线、月收益和 E5 数据随分支提交，不包含数据库文件。

D 接入后应重新跑 C 的测试，重点确认：正式事务助手正确传递用户、不同用户数据隔离、账户/订单调用仍正常、三种策略和两个页面均能工作，再做全系统的问答—选股—回测—下单—审计链路。

当前已知边界：未提供现金分红/送转或退市结算明细时，持仓跨相关事件会明确停止；上市前五个交易日不撮合；历史规则仅覆盖现有样本区间；不支持滑点、逐笔行情、融券或成交量参与率模型。当前交付按这些明确的日频教学口径验收，不宣称实现完整实盘交易系统。

## 7. 分支与审核

只在 `c/trading-backtest` 交付 C 的代码、测试、实验和文档，不覆盖 A/B 的分支，不直接推送 `main`，不强制推送。当前分支已含 A/B 的基线，D 可据此接入。

合并前按原协作约定请至少一名其他成员审核；涉及接口时由依赖方审核。C 的开发和样本验收完成，与最终通过集成并合并主干是两个交付节点。完整报告和 PPT 后续按小组安排整理。
