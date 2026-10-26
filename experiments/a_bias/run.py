"""E6/E7 的最小对照实验：只改变信息集，价格、持有期、权重完全相同。

依赖 B+A 合成样本。2025-05-01 日终选股，下一交易日收盘买入，6 月底卖出。
各股初始等权，持有期间不再平衡；不计手续费。这里不实现 C 的撮合引擎。
"""

import csv
import sys
from datetime import date
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from etl.temporal.common import connect

HERE = Path(__file__).resolve().parent
SIGNAL = date(2025, 5, 1)
ENTRY = date(2025, 5, 2)
END = date(2025, 6, 30)


def select_stocks(cur):
    cur.execute("SELECT set_config('app.as_of_date',%s,true)", (SIGNAL.isoformat(),))
    cur.execute("SELECT stock_code FROM pit.fn_index_members('SYNTH300',%s)", (SIGNAL,))
    historical = [r[0] for r in cur.fetchall()]
    cur.execute(
        "SELECT stock_code FROM ref.index_member WHERE index_code='SYNTH300' AND valid @> date '2025-12-31' ORDER BY stock_code"
    )
    current = [r[0] for r in cur.fetchall()]
    # E6：固定 5 月的成员，仅改变财务版本选择。真实策略只能用第一条查询。
    cur.execute(
        """SELECT stock_code FROM pit.fin_report WHERE report_period='2025-03-31'
                   AND roe>=0.09 AND stock_code=ANY(%s) ORDER BY stock_code""",
        (historical,),
    )
    known = [r[0] for r in cur.fetchall()]
    cur.execute(
        """WITH latest AS (
        SELECT DISTINCT ON(stock_code) stock_code,roe FROM fin.report
        WHERE report_period='2025-03-31' ORDER BY stock_code,available_date DESC,announce_date DESC
    ) SELECT stock_code FROM latest WHERE roe>=0.09 AND stock_code=ANY(%s) ORDER BY stock_code""",
        (historical,),
    )
    leaked = [r[0] for r in cur.fetchall()]
    return {"E6": (known, leaked), "E7": (historical, current)}


def nav(prices, codes):
    if not codes:
        raise ValueError("实验股票池为空")
    selected = prices[codes]
    if selected.isna().any().any() or (selected <= 0).any().any():
        raise ValueError("价格缺失或非正，停止实验，不填 0 或静默删股票")
    return selected.div(selected.iloc[0]).mean(axis=1)


def main():
    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM etl.load_batch WHERE source='SAMPLE')")
        if not cur.fetchone()[0]:
            raise ValueError("此实验只针对明确标注的 B+A 合成样本")
        selections = select_stocks(cur)
        cur.execute(
            """SELECT trade_date,stock_code,close FROM mkt.v_bar_hfq
                       WHERE trade_date BETWEEN %s AND %s ORDER BY trade_date,stock_code""",
            (ENTRY, END),
        )
        prices = pd.DataFrame(cur.fetchall(), columns=["date", "code", "close"])
    prices["close"] = prices["close"].astype(float)
    prices = prices.pivot(index="date", columns="code", values="close")
    if prices.index[0] != ENTRY or prices.index[-1] != END:
        raise ValueError("样本持有期不完整")
    conclusions = []
    for experiment, (correct, wrong) in selections.items():
        if correct == wrong:
            raise AssertionError("实验未改变选股，请先装载 A 的夹具")
        curve = pd.DataFrame(
            {"pit": nav(prices, correct), "biased": nav(prices, wrong)}
        )
        curve.to_csv(HERE / f"{experiment}_nav.csv", float_format="%.10f")
        with (HERE / f"{experiment}_selection.csv").open(
            "w", encoding="utf-8", newline=""
        ) as file:
            writer = csv.writer(file)
            writer.writerow(["stock_code", "pit_selected", "biased_selected"])
            writer.writerows(
                (code, code in correct, code in wrong)
                for code in sorted(set(correct) | set(wrong))
            )
        fig, ax = plt.subplots(figsize=(9, 4.8))
        ax.plot(
            curve.index, curve["pit"], label="PIT: known at signal date", linewidth=2
        )
        ax.plot(
            curve.index,
            curve["biased"],
            label="Biased: future information",
            linestyle="--",
            linewidth=2,
        )
        ax.set(
            title=f"{experiment} | synthetic teaching data", ylabel="NAV (entry = 1)"
        )
        ax.grid(alpha=0.2)
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        fig.savefig(HERE / f"{experiment}_nav.png", dpi=150)
        plt.close(fig)
        correct_return = curve["pit"].iloc[-1] - 1
        wrong_return = curve["biased"].iloc[-1] - 1
        description = (
            "提前使用 8 月财报更正，改变了 5 月的 ROE 筛选"
            if experiment == "E6"
            else "把年底成员倒填到 5 月，提前加入后来纳入者，并删掉当时仍在的成员"
        )
        conclusions.append(
            f"{experiment}：{description}。PIT 选中 {len(correct)} 只，错误方式选中 {len(wrong)} 只；"
            f"期间收益分别为 {correct_return:.4%} 和 {wrong_return:.4%}，相差 {(wrong_return-correct_return)*100:.4f} 个百分点。"
        )
    output = (
        "\n\n".join(conclusions)
        + "\n\n本例衡量信息泄漏如何改变结果；偏差方向由样本决定。数据为合成数据，结果不代表真实策略收益。\n"
    )
    (HERE / "results.md").write_text(output, encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
