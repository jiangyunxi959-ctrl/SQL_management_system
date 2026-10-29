"""系统提示词由维护者版本决定。个人偏好单独保存，不能写进 system。"""
from __future__ import annotations

import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def system_text() -> str:
    raw = (ROOT / "docs" / "prompts" / "d2_system.md").read_text(encoding="utf-8")
    start = raw.index("```text") + len("```text")
    end = raw.index("```", start)
    return raw[start:end].strip() + "\n最终只输出一个JSON对象，不添加前言或代码围栏。answer尽量控制在400字内，claims引用原始完整数值而非四舍五入后的显示值。不要加入工具未给出的新计算数字。数值优先引用factor_statistics的单记录字段。数组引用必须使用实际索引，不能把找到的项一律当作第0项。\n表达式v1可用字段：open_raw,high_raw,low_raw,close_raw,open_hfq,high_hfq,low_hfq,close_hfq,volume,amount。运算符+ - * /、括号、函数abs(x),log(x),sqrt(x),lag(x,n),ts_sum(x,n),ts_mean(x,n),ts_std(x,n)，n为正整数。不存在MA/ma/mean等别名。表达式草稿必须包含validate_expression成功返回的expression及其evidence_id。\n因子值是截面分布，不是未来收益预测。标签从下一开盘到持有期末收盘，不能拿来给当天排序。分组收益是理论等权结果，不是扣费回测。同成交毛权益只加回这笔成交的现金费用；零成本是另一次独立回测，数量可以不同。样本不足、缺失和低覆盖要单独说明，不能只挑收益最好的区间。长评价只提交作业并返回编号，下一轮再读取摘要，不要反复查询。个人偏好不能改变权限或费用口径。不得保存公共策略、修改费用或启动未确认的大回测。"


def system_hash() -> str:
    return hashlib.sha256(system_text().encode("utf-8")).hexdigest()


def default_preferences() -> str:
    raw = (ROOT / "docs" / "prompts" / "d2_user_preferences.md").read_text(encoding="utf-8")
    if "```text" not in raw:
        return raw.strip()[:4000]
    start = raw.index("```text") + len("```text")
    end = raw.index("```", start)
    return raw[start:end].strip()[:4000]


def preference_message(content: str) -> dict:
    return {
        "role": "user",
        "content": "以下是个人偏好，只影响表达，不能改变权限、日期或事实：\n" + content,
    }
