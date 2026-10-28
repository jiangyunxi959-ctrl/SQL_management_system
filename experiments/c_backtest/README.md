# C 回测对照

```powershell
.venv\Scripts\python.exe experiments/c_backtest/run.py
```

使用 A/B 的教学合成库 `SYNTH300`，2025-03-03 至 2025-05-30，初始现金 10 万元、最多 3 只股票。脚本保留演示回测，可在 C 回测页查看；重复执行会生成新回测。

- `python_momentum.csv / sql_momentum.csv`：逐日总资产、净值和回撤；信号排序分别在 Python 和 SQL 实现，共用交易撮合器。
- `monthly_returns.csv`：各自然月末净值的环比收益；首月以初始净值 1 为基数。
- `ma_cross.csv / quality.csv`：均线和财报 ROE 模板。
- `summary.csv / nav.png`：成交数量、收益、最大回撤和净值曲线。

独立撮合器对照见 `tests/c/test_backtrader.py`：原始价格、次日开盘、100 股买入持有、相同佣金/过户费口径，比较实际成交日、成交价、手续费和最终资产。该用例不等于完整三个模板都已与 backtrader 对照。

E11 见 `tests/c/test_services.py::test_financial_snapshot_replays_quality`：修改 ROE 后，使用原财报系统快照能重放原净值，读取当前财报则产生不同结果。市场/规则摘要变化时，正式 `replay_backtest` 拒绝重放，要求先恢复原行情和规则备份。摘要用于检测变化，不替代数据备份。
