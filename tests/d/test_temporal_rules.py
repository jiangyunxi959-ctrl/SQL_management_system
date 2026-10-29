import pytest

from app.factors.errors import FactorError
from app.factors.validate import validate


def test_lag_and_window_paths_need_the_same_twenty_one_inputs():
    lagged_mean = validate("lag(ts_mean(volume,20),1)")
    mean_of_lag = validate("ts_mean(lag(volume,1),20)")
    assert lagged_mean.lookback == 21
    assert mean_of_lag.lookback == 21
    assert lagged_mean.fields == ("volume",)


def test_sibling_windows_are_separate_paths():
    got = validate("ts_sum(amount,20)/ts_sum(volume,20)")
    assert got.lookback == 20
    assert got.ca_sensitive is True
    with pytest.raises(FactorError, match="嵌套窗口"):
        validate("ts_mean(ts_mean(volume,5),20)")
    with pytest.raises(FactorError, match="嵌套滞后"):
        validate("lag(lag(volume,1),1)")


def test_cross_day_raw_comparison_is_marked_and_same_day_ratio_is_not():
    assert validate("close_raw/lag(close_raw,1)").ca_sensitive is True
    assert validate("close_raw/open_raw").ca_sensitive is False
    assert validate("volume/ts_mean(lag(volume,1),20)").ca_sensitive is True
    assert validate("close_hfq/lag(close_hfq,20)-1").ca_sensitive is False
    assert validate("ts_std(log(close_hfq/lag(close_hfq,1)),20)*sqrt(252)").ca_sensitive is False
    assert validate("lag(close_raw,1)").ca_sensitive is False


def test_window_argument_cannot_be_an_expression():
    with pytest.raises(FactorError, match="不能是表达式") as exc:
        validate("ts_mean(volume,10+10)")
    assert exc.value.category == "ARGUMENT"
