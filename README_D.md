# D 模块：因子研究、AI 分析与统一入口

交付日期：2026-10-29。分支：`d/factor-research-agent`；基线：C 分支 `07f0de6`，已包含 A/B。D、D2、D3 是三个实现阶段。本分支用于数据库课程大作业，不连接真实证券账户。

**当前事实先看 [交付与原方案差异](docs/D_当前交付与方案差异.md)**、[本次分支检查](docs/D_分支交付检查_20261029.md) 和 [交接](README_D_HANDOFF.md)。下文“初始 D 阶段记录”保留当时的 10 项因子和本机环境，不能据此判断现在只有 10 项，也不能要求其他机器取得作者的 `.audit` 目录。

## 当前功能

50 项公共因子、本人私有表达式与固定版本、数据库计算和分布统计、1/5/20 日预测关系评价、私有单因子策略、个人费用版本、净权益/同成交毛权益/另跑零成本对照、自适应图表，以及 DeepSeek + LangGraph 的受控工具分析。统一登录入口传递用户和研究日，数据库限制私有数据可见范围并记录成功、失败和拒绝。

## 在新机器运行

需要 Python 3.11+、PostgreSQL 16 和 `btree_gist`，在仓库根目录执行。先保存自己的改动；若本地已有该分支，直接切换，不重置。

```powershell
git fetch origin
git switch --track origin/d/factor-research-agent
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-d.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
```

填写自己的数据库连接和密码，管理连接与应用连接应指向同一实例。已有完整 ABC 库，按顺序非破坏安装：

```powershell
.venv\Scripts\python.exe -m app.setup_d
.venv\Scripts\python.exe -m app.setup_ai
.venv\Scripts\python.exe -m app.setup_d3
```

若要从零建教学库，先明确指定可删除的新库，再执行以下命令。**重建会删除目标库**，不能用于真实数据或要保留的工作库；第二步补历史规则、股票池与财报教学案例。

```powershell
$env:PGDATABASE='zhishi_d_sample'
.venv\Scripts\python.exe scripts/rebuild_db.py --yes
.venv\Scripts\python.exe scripts/setup_a_sample.py
```

将自己选择的密码放在不提交的 `.tmp/password.txt`，再创建用户并启动统一入口：

```powershell
.venv\Scripts\python.exe scripts/create_d_user.py --username student --display-name 学员 --password-file .tmp/password.txt
.venv\Scripts\python.exe -m streamlit run app/ui/main.py --server.address 127.0.0.1 --server.port 8510
```

打开 [统一入口](http://127.0.0.1:8510)。端口可自行更换，不同端口不表示功能版本不同，应检查启动文件、分支和数据库。旧的 A/C 独立页面不属于正式登录入口。

AI 在服务端配置 `DEEPSEEK_API_KEY`、`DEEPSEEK_MODEL`、`DEEPSEEK_BASE_URL`，参照 `.env.example`；没有密钥仍能用因子和回测，AI 会显示未连接。型号应使用自己账号可用的名称，本机曾验证 `deepseek-flash`，不承诺所有账号长期可用。模型可能接收问题及工具资料，演示使用教学数据。

## 当前边界

- 50 项是日线改编或项目补充；不支持任意 SQL/Python、公式递归或财报列。规则见 [表达式规范](docs/D_因子表达式规范.md) 和 [完整目录](docs/D2_因子扩充目录.md)。
- 单因子回测使用独立模拟计算，未接 C 的原资金冻结和现金流水；C 三个原策略仍走原费用路径。
- maker/taker 是用户指定的费用假设，日线无法识别真实成交类型；净权益包含未卖出持仓估值，滑点计入价格，不能重复扣除。
- 输入表写入会使评价/回测重算，但未逐行冻结全部输入，不承诺完整重放；缺可靠计算截止日的旧评价要求重算。
- 不支持的除权/退市边界会停止并报告；尚无完整分红送转账务或断电自动恢复队列。
- 外部财报仅为采集日可见的供应商快照，不具有完整历史公告证据。性能对比尚未完成。

## 初始 D 阶段记录（历史）

以下记录按原日期保留。本机地址、原始 10 项批次和当时未完成状态，均以以上当前说明及链接为准。

这一部分让登录用户查看和运行 10 个公共公式，保存自己的私有公式，并由数据库按研究日计算。计算结果、私有公式和操作记录按用户分开。它接上原来的历史查询、行情和模拟交易，不是实盘系统。

## 环境

需要本机 PostgreSQL 16 和项目虚拟环境。连接信息写在不提交的 `.env`。当前样本库是 `zhishi_abc_fixed_sample`，监听 `127.0.0.1:55436`。如果数据库没启动，用原来的数据目录启动，不要重建这个库：

```powershell
.\.audit\runtime\pgsql\bin\pg_ctl.exe -D .\.audit\pgdata -l .\.audit\postgres.log -o "-p 55436 -h 127.0.0.1" -w start
```

安装 D 的依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-d.txt
```

## 安装到已有库

下面的命令不会删除行情、账户或回测。数据库名必须以 `_sample` 结尾。

```powershell
.\.venv\Scripts\python.exe -c "from app.setup_d import install; install()"
```

重复执行不会把 10 个公共公式再插入一遍。如果同一个代码的第 1 版内容不同，安装会停下，不会悄悄覆盖。

全新库仍用原来的重建命令，且只能指向专门准备的新库：

```powershell
.\.venv\Scripts\python.exe scripts/rebuild_db.py --yes
```

## 创建登录用户

把密码单独放在一个不提交的文件里：

```powershell
.\.venv\Scripts\python.exe scripts/create_d_user.py --username student --display-name 学员 --password-file .\secret.txt
```

命令只打印用户编号，不打印密码。

## 打开页面

```powershell
.\.venv\Scripts\python.exe -m streamlit run app/ui/main.py --server.address 127.0.0.1 --server.port 8510
```

浏览器访问 `http://127.0.0.1:8510`。用用户名和密码登录。页面上的研究日期会固定显示。可以查看历史资料、公共和私有公式、计算结果、三个原策略、模拟交易和操作记录。

从公式结果带到交易页的股票只是预填。订单要你自己提交，并继续走原来的资金和持仓检查。

原来的三个独立演示页还在，页面上写明它们需要手填用户编号，不属于正式入口。

## 检查

```powershell
.\.venv\Scripts\python.exe scripts/check_d.py
.\.venv\Scripts\python.exe scripts/run_sql_tests.py
.\.venv\Scripts\python.exe scripts/check_a_python.py
.\.venv\Scripts\python.exe scripts/check_a_integration.py
.\.venv\Scripts\python.exe scripts/check_a_ui.py
.\.venv\Scripts\python.exe -m pytest tests/c tests/d -q
```

## 因子统计

公共和个人公式运行后，结果页显示有效/缺失数量、均值、中位数、最小最大值、样本标准差、分位数、正负零数量及缺失原因。统计由数据库对本人已保存运行计算，页面筛选不会改变这份整次运行统计。

这些数字描述分布，不代表预测能力。[因子统计与 AI 说明](docs/D_因子统计与AI说明.md)记录首次增加统计时的状态；现在真实AI的验收以 [D2实现验收](docs/D2_实现验收.md) 为准。

## D2 增量

统一入口增加“AI分析”。它使用 LangGraph 的 `StateGraph.compile()`，模型按工具返回决定下一步。DeepSeek 使用 `https://api.deepseek.com` 和可配置的 `deepseek-flash`，并关闭 thinking。没有 `DEEPSEEK_API_KEY` 时，页面只显示未连接，不会把固定演示说成真实模型。

公共公式从 10 项扩到 50 项。原来的 10 项代码、表达式和摘要不变。详细验收见 [docs/D2_实现验收.md](docs/D2_实现验收.md)，课堂步骤见 [docs/D2_课堂演示.md](docs/D2_课堂演示.md)。

```powershell
.\.venv\Scripts\python.exe scripts/check_d2.py
```

## D3 增量

增加因子研究、因子策略和个人成本设置。评价、策略、费用和回测按所有者和父对象隔离。原有动量、均线、财报三个策略仍在“原策略”页。

安装不扫描 `00` 到 `90` 前缀：

```powershell
.\.venv\Scripts\python.exe -m app.setup_d3
.\.venv\Scripts\python.exe scripts\check_d3.py
```

从零重建时，`scripts/rebuild_db.py` 会在 D2 之后调用它。运行课程页面不需要新增 Python 包。验收时用过的浏览器驱动没有写进 `requirements-d.txt`。

详细结果见 [docs/D3_实现验收.md](docs/D3_实现验收.md)，课堂步骤见 [docs/D3_课堂演示.md](docs/D3_课堂演示.md)。

## 当前限制

- 公式是日线改编或项目补充，不是供应商指标的完整复现。
- 早期 D3 小样本真实回答曾被比例检查拒绝，失败记录保留；后来已修正并做真实页面复测，见最新前端实测。换机器需要自己的密钥，不承诺所有问题均成功。
- 三个原策略保持原样。单因子策略在单独页面，确认后才回测。
- 外部财报样例是供应商快照，可用日是 2026-10-29 的采集日。没有完整公告证据时，不能当作历史财报策略。
- 样本是 20 只证券。一次计算最多 100 只，评价默认不超过 126 个信号日。
- 因子计算单次语句超时 5 秒。评价作业上限 180 秒。
