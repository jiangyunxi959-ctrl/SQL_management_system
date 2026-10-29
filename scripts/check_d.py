"""D 的安装与检查入口。失败时返回非零，不把错误吞掉。"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.setup_d import install


def main() -> None:
    database = os.getenv("PGDATABASE", "")
    if not database.endswith("_sample"):
        raise SystemExit("为避免误装到需要保留的库，PGDATABASE 必须以 _sample 结尾")
    install()
    import pytest

    code = pytest.main(["tests/d", "-q"])
    if code != 0:
        raise SystemExit(code)
    print("[OK] D checks passed")


if __name__ == "__main__":
    main()
