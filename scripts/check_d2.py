"""安装 D2 并运行 D 与 AI 检查。数据库名必须以 _sample 结尾。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.setup_ai import install
from app.setup_d import install as install_d


def main() -> None:
    database = os.getenv("PGDATABASE", "")
    if not database.endswith("_sample"):
        raise SystemExit("PGDATABASE 必须以 _sample 结尾")
    install_d()
    count = install()
    if count != 50:
        raise SystemExit(f"公共公式应为 50 项，实际 {count}")
    import pytest

    code = pytest.main(["tests/d", "-q"])
    if code != 0:
        raise SystemExit(code)
    print("[OK] D2 checks passed")


if __name__ == "__main__":
    main()
