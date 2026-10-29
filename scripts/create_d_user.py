"""创建可以登录的用户。密码从文件读取，不打印密码或会话凭证。"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.auth import create_user


def main() -> None:
    parser = argparse.ArgumentParser(description="创建知时登录用户")
    parser.add_argument("--username", required=True)
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--password-file", required=True, type=Path)
    parser.add_argument("--role", default="LEARNER", choices=["LEARNER", "ADMIN", "AUDITOR"])
    args = parser.parse_args()
    password = args.password_file.read_text(encoding="utf-8").strip()
    user_id = create_user(args.username, args.display_name, password, args.role)
    print(f"[OK] created user {args.username} id={user_id}")


if __name__ == "__main__":
    main()
