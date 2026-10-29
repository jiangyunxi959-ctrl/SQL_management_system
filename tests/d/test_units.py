import pytest

from app.factors.errors import FactorError
from app.factors.validate import validate


def test_units_of_public_building_blocks():
    assert validate("amount / volume").unit_label == "元/股（未调整价格）"
    assert validate("amount / volume").price_basis == "RAW"
    assert validate("close_raw / open_raw - 1").unit_label == "无单位"
    assert validate("open_hfq / lag(close_hfq,1) - 1").price_basis == "NONE"
    assert validate("volume").unit_label == "股"
    assert validate("amount").unit_label == "元"
    assert validate("close_hfq").price_basis == "HFQ"


def test_cancelled_units_from_different_prices_can_be_combined():
    mixed = validate("close_raw / open_raw + close_hfq / open_hfq")
    assert mixed.unit_label == "无单位"
    assert mixed.ca_sensitive is False
    averaged = validate("ts_mean(close_raw / open_raw,20)")
    assert averaged.unit_label == "无单位"
    assert averaged.ca_sensitive is False


def test_raw_and_adjusted_residuals_are_rejected():
    for source in ["close_raw/close_hfq", "amount/(close_hfq*volume)"]:
        with pytest.raises(FactorError, match="价格口径") as exc:
            validate(source)
        assert exc.value.category == "UNIT"
    with pytest.raises(FactorError, match="单位必须一致") as exc:
        validate("close_raw+close_hfq")
    assert exc.value.category == "UNIT"


def test_zero_does_not_bypass_addition_and_dimensionless_functions():
    with pytest.raises(FactorError, match="单位必须一致"):
        validate("close_raw+0")
    with pytest.raises(FactorError, match="单位必须一致"):
        validate("0-volume")
    kept = validate("close_raw*0")
    assert kept.price_basis == "RAW"
    with pytest.raises(FactorError, match="无单位"):
        validate("log(close_raw)")
    with pytest.raises(FactorError, match="无单位"):
        validate("sqrt(volume)")
    assert validate("log(close_hfq/lag(close_hfq,1))").unit_label == "无单位"
    assert validate("sqrt(252)").unit_label == "无单位"
