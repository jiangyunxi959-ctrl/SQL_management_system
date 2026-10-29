from app.factors.errors import FactorError
from app.factors.validate import validate
import pytest


PUBLIC = [
    ("amount / volume", 1),
    ("close_raw / open_raw - 1", 1),
    ("open_hfq / lag(close_hfq,1) - 1", 2),
    ("high_raw / low_raw - 1", 1),
    ("ts_sum(amount,20) / ts_sum(volume,20)", 20),
    ("close_raw / (amount / volume) - 1", 1),
    ("volume / ts_mean(lag(volume,1),20)", 21),
    ("close_hfq / lag(close_hfq,20) - 1", 21),
    ("close_hfq / ts_mean(close_hfq,20) - 1", 20),
    ("ts_std(log(close_hfq / lag(close_hfq,1)),20) * sqrt(252)", 21),
]


def test_public_expressions_parse_with_documented_length():
    for source, lookback in PUBLIC:
        got = validate(source)
        assert got.lookback == lookback
        assert got.syntax_version == "v1"
        assert got.digest
        assert got.explanation


def test_priority_and_parentheses_keep_operation_order():
    divided = validate("close_raw/open_raw-1").normalized
    assert divided["type"] == "binary" and divided["op"] == "-"
    assert divided["left"]["op"] == "/"
    product = validate("-close_raw*volume").normalized
    assert product["op"] == "*" and product["left"]["type"] == "unary"
    left = validate("(close_raw-open_raw)-high_raw").digest
    right = validate("close_raw-(open_raw-high_raw)").digest
    assert left != right


def test_equivalent_spacing_has_one_digest_and_subtraction_does_not_commute():
    assert validate("close_raw + open_raw").digest == validate("close_raw+open_raw").digest
    assert validate("(close_raw+open_raw)").digest == validate("close_raw+open_raw").digest
    assert validate("close_raw-open_raw").digest != validate("open_raw-close_raw").digest
    assert validate("1.0").digest != validate("1").digest
    assert validate("1.0").normalized["text"] == "1.0"


def test_literal_rules():
    assert validate("close_raw*20").normalized["right"]["text"] == "20"
    with pytest.raises(FactorError, match="科学计数法") as exc:
        validate("close_raw*1e10")
    assert exc.value.category == "LEXICAL"
    with pytest.raises(FactorError, match="前导零"):
        validate("close_raw*01")
    with pytest.raises(FactorError, match="小数最多 12 位"):
        validate("close_raw*1.1234567890123")
    with pytest.raises(FactorError, match="10 的 12 次方"):
        validate("close_raw*10000000000000")
    assert validate("close_raw*1000000000000").normalized["right"]["text"] == "1000000000000"


def test_rejected_examples_have_positions():
    cases = [
        ("lag(close_hfq,-1)", "ARGUMENT", "未来取值"),
        ("ts_mean(close_raw,20.0)", "ARGUMENT", "整数字面量"),
        ("ts_mean(ts_mean(volume,5),20)", "TEMPORAL", "嵌套窗口"),
        ("close_raw/close_hfq", "UNIT", "价格口径"),
        ("close_raw+volume", "UNIT", "单位必须一致"),
        ("close_raw>10", "SYNTAX", "筛选条件"),
        ("system.vwap_d", "SYNTAX", "对象属性"),
        ("amount/volume; DELETE FROM sim.account", "SYNTAX", "数据库命令"),
    ]
    for source, category, text in cases:
        with pytest.raises(FactorError, match=text) as exc:
            validate(source)
        assert exc.value.category == category
        assert 0 <= exc.value.start < exc.value.end <= len(source)


def test_case_unicode_and_unknown_names():
    with pytest.raises(FactorError, match="小写") as exc:
        validate("Close_raw")
    assert exc.value.start == 0
    with pytest.raises(FactorError, match="小写"):
        validate("TS_MEAN(volume,20)")
    with pytest.raises(FactorError, match="ASCII") as exc:
        validate("ｃlose_raw")
    assert exc.value.category == "LEXICAL"
    with pytest.raises(FactorError, match="未知字段"):
        validate("turnover")
    with pytest.raises(FactorError, match="未知函数"):
        validate("ema(close_raw)")


def test_complexity_bounds():
    assert validate("ts_mean(volume,252)").lookback == 252
    assert validate("lag(volume,251)").lookback == 252
    with pytest.raises(FactorError, match="252 个交易日"):
        validate("ts_mean(lag(volume,1),252)")
    with pytest.raises(FactorError, match="252 个交易日"):
        validate("lag(ts_mean(volume,252),1)")
    with pytest.raises(FactorError, match="1 到 251"):
        validate("lag(volume,252)")
    with pytest.raises(FactorError, match="1 到 252"):
        validate("ts_mean(volume,253)")
    with pytest.raises(FactorError, match="至少为 2"):
        validate("ts_std(volume,1)")
    ok = "abs("*7 + "close_raw" + ")"*7
    assert validate(ok).depth == 8
    with pytest.raises(FactorError, match="深度不能超过 8"):
        validate("abs("*8 + "close_raw" + ")"*8)
    chain = "+".join(["close_raw"] * 8)
    assert validate(chain).depth == 8
    with pytest.raises(FactorError, match="深度不能超过 8"):
        validate("+".join(["close_raw"] * 9))

    def paired(count: int) -> str:
        parts = ["close_raw"] * count
        while len(parts) > 1:
            nxt = []
            for index in range(0, len(parts), 2):
                left = parts[index]
                if index + 1 == len(parts):
                    nxt.append(left)
                else:
                    nxt.append(f"({left}+{parts[index + 1]})")
            parts = nxt
        return parts[0]

    assert validate(paired(16)).node_count == 31
    with pytest.raises(FactorError, match="节点数不能超过 32"):
        validate(paired(17))
    with pytest.raises(FactorError, match="1024"):
        validate("close_raw+" * 600)


def test_comparisons_and_multiple_statements_do_not_parse():
    for source in ["close_raw>=10", "volume==0", "close_raw!=open_raw"]:
        with pytest.raises(FactorError, match="筛选条件"):
            validate(source)
