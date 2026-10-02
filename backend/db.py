from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row


def load_dotenv(path: str = ".env") -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw in file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def database_url() -> str:
    value = os.getenv("DATABASE_URL")
    if value:
        return value
    parts = {
        "host": os.getenv("PGHOST", "127.0.0.1"),
        "port": os.getenv("PGPORT", "5432"),
        "dbname": os.getenv("PGDATABASE", "ai_gateway_eval_test"),
        "user": os.getenv("PGUSER", "ai_gateway_eval_test"),
        "password": os.getenv("PGPASSWORD", ""),
    }
    return psycopg.conninfo.make_conninfo(**parts)


@contextmanager
def connection() -> Iterator[psycopg.Connection[Any]]:
    with psycopg.connect(database_url(), row_factory=dict_row, connect_timeout=5) as conn:
        yield conn


def ensure_schema() -> None:
    sql = Path(__file__).resolve().parent.parent.joinpath("schema.sql").read_text(encoding="utf-8")
    with connection() as conn:
        conn.execute(sql)
        conn.commit()
