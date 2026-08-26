"""The migration runner (PLAN §4.1: "plain versioned SQL + a tiny runner" chosen over
Alembic autogenerate, which is a liability under time pressure). Applies every
`infra/sql/V*.sql` file not yet recorded in `schema_migrations`, in lexical filename
order, each inside its own transaction.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import asyncpg

from libs.common.config import Settings, get_settings

logger = logging.getLogger("migrate")

_CREATE_TRACKER = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def sql_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "infra" / "sql"


async def run_migrations(settings: Settings | None = None, *, directory: Path | None = None) -> list[str]:
    settings = settings or get_settings()
    directory = directory or sql_dir()
    applied: list[str] = []

    conn = await asyncpg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        user=settings.postgres_user,
        password=settings.postgres_password,
        database=settings.postgres_db,
    )
    try:
        await conn.execute(_CREATE_TRACKER)
        already = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}

        files = sorted(directory.glob("V*.sql"))
        if not files:
            logger.warning("no migration files found in %s", directory)

        for f in files:
            version = f.stem
            if version in already:
                continue
            sql = f.read_text(encoding="utf-8")
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations(version) VALUES ($1)", version
                )
            applied.append(version)
            logger.info("applied migration %s", version)
    finally:
        await conn.close()
    return applied


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    applied = asyncio.run(run_migrations())
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("No pending migrations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
