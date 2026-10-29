"""结果原因。空值必须能解释，不能改成零。"""

REASON_BITS = (
    (1, "INSUFFICIENT_HISTORY", "历史交易日不足"),
    (2, "MISSING_DATA", "缺少行情"),
    (4, "SUSPENDED", "停牌"),
    (8, "ZERO_VOLUME", "成交量为零"),
    (16, "DIVISION_BY_ZERO", "除数为零"),
    (32, "DOMAIN_ERROR", "超出对数或平方根的定义域"),
    (64, "NON_FINITE_RESULT", "结果不是有限数"),
    (128, "CORPORATE_ACTION", "区间内发生除权，原始价格或股数不能跨事件比较"),
    (256, "ZERO_AMOUNT", "成交额为零"),
)


def decode_mask(mask: int) -> tuple[list[str], str | None]:
    codes = [code for bit, code, _label in REASON_BITS if mask & bit]
    if not codes:
        return [], None
    labels = [label for bit, _code, label in REASON_BITS if mask & bit]
    return codes, "；".join(labels)
