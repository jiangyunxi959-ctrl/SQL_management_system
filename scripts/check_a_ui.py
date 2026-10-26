"""验证演示页实际用受限账号查询，并且四个研究日期显示正确数据。"""

from datetime import date
from pathlib import Path
from streamlit.testing.v1 import AppTest

ROOT = Path(__file__).resolve().parents[1]

if __name__ == "__main__":
    app = AppTest.from_file(str(ROOT / "app/ui/pages/a_bias.py")).run(timeout=30)
    assert not app.exception and not app.error
    examples = [
        (date(2025, 4, 27), None),
        (date(2025, 5, 1), 100000000),
        (date(2025, 8, 21), 80000000),
        (date(2025, 8, 30), 160000000),
    ]
    for day, expected in examples:
        app.date_input[0].set_value(day).run()
        assert not app.exception and not app.error
        if expected is None:
            assert len(app.info) == 1
        else:
            assert float(app.dataframe[0].value.iloc[0]["净利润（元）"]) == expected
    print("[PASS] Streamlit 受限账号连接与四个日期场景")
