# database connection helpers

from __future__ import annotations

from sqlalchemy import Engine, create_engine, text


def make_engine(url: str) -> Engine:
    if url.startswith("mssql"):
        # fast_executemany speeds up the bulk inserts done by the demo seeder.
        return create_engine(url, pool_pre_ping=True, fast_executemany=True)
    return create_engine(url, pool_pre_ping=True)


def check_connection(engine: Engine, label: str) -> None:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001
        raise ConnectionError(f"Cannot connect to the {label} database: {exc}") from exc
