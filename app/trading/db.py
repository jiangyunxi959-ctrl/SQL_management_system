"""C 的临时连接适配器。D 提供 app.db 后自动委托，不占用 D 的文件。"""
from contextlib import contextmanager
from datetime import date
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / '.env')


@contextmanager
def transaction(*, user_id=None, as_of: date | None = None, read_only=False, role='app'):
    try:
        from app.db import transaction as shared_transaction
    except ModuleNotFoundError as exc:
        if exc.name != 'app.db':
            raise
    else:
        with shared_transaction(user_id=user_id, as_of=as_of, read_only=read_only, role=role) as conn:
            yield conn
        return
    users = {'app': ('app_user', 'APP_USER_PASSWORD'), 'agent': ('agent_reader', 'AGENT_READER_PASSWORD'),
             'etl': ('etl_writer', 'ETL_WRITER_PASSWORD')}
    username, password_key = users[role]
    with psycopg.connect(host=os.getenv('PGHOST', '127.0.0.1'), port=os.getenv('PGPORT', '5432'),
                         dbname=os.getenv('PGDATABASE', 'zhishi'), user=username,
                         password=os.environ[password_key]) as conn:
        with conn.transaction():
            if read_only:
                conn.execute('SET TRANSACTION READ ONLY')
            if user_id is not None:
                conn.execute("SELECT set_config('app.user_id', %s, true)", [str(user_id)])
            if as_of is not None:
                conn.execute("SELECT set_config('app.as_of_date', %s, true)", [as_of.isoformat()])
            yield conn
