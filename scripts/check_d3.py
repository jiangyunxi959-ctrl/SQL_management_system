"""安装 D3 并运行课程检查。数据库名必须以 _sample 结尾。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.setup_d3 import install


def main() -> None:
    database = os.getenv("PGDATABASE", "")
    if not database.endswith("_sample"):
        raise SystemExit("PGDATABASE 必须以 _sample 结尾")
    install()
    import pytest

    code = pytest.main([
        "tests/d/test_d3_metrics.py", "tests/d/test_d3_access.py", "tests/d/test_d3_eval.py",
        "tests/d/test_d3_binding.py", "tests/d/test_d3_third.py", "tests/d/test_d3_fourth.py", "-q",
    ])
    if code != 0:
        raise SystemExit(code)
    print("[OK] D3 checks passed")


if __name__ == "__main__":
    main()
