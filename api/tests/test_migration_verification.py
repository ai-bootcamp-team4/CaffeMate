import hashlib
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, text

from app.migrations import (
    MigrationChecksumMismatchError,
    MigrationSetMismatchError,
    verify_migrations,
)


def _write_migration(directory: Path, name: str, sql: str) -> None:
    (directory / name).write_text(sql, encoding="utf-8")


def _seed_migration_ledger(engine: Engine, directory: Path) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE schema_migrations(version TEXT PRIMARY KEY, checksum TEXT NOT NULL)")
        )
        for path in sorted(directory.glob("*.sql")):
            connection.execute(
                text(
                    "INSERT INTO schema_migrations(version, checksum) "
                    "VALUES (:version, :checksum)"
                ),
                {
                    "version": path.name,
                    "checksum": hashlib.sha256(path.read_bytes()).hexdigest(),
                },
            )


def test_verify_migrations_returns_count_and_stable_set_digest(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0001_first.sql", "CREATE TABLE first_table(id INTEGER);")
    _write_migration(tmp_path, "0002_second.sql", "CREATE TABLE second_table(id INTEGER);")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _seed_migration_ledger(engine, tmp_path)

    result = verify_migrations(engine, tmp_path)

    expected_lines = []
    for path in sorted(tmp_path.glob("*.sql")):
        expected_lines.append(f"{path.name}:{hashlib.sha256(path.read_bytes()).hexdigest()}")
    assert result.count == 2
    assert result.set_digest == hashlib.sha256("\n".join(expected_lines).encode()).hexdigest()


def test_verify_migrations_rejects_missing_or_unexpected_version(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0001_first.sql", "CREATE TABLE first_table(id INTEGER);")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _seed_migration_ledger(engine, tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO schema_migrations(version, checksum) "
                "VALUES ('9999_unexpected.sql', 'unexpected')"
            )
        )

    with pytest.raises(MigrationSetMismatchError):
        verify_migrations(engine, tmp_path)


def test_verify_migrations_rejects_checksum_mismatch(tmp_path: Path) -> None:
    _write_migration(tmp_path, "0001_first.sql", "CREATE TABLE first_table(id INTEGER);")
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _seed_migration_ledger(engine, tmp_path)
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE schema_migrations SET checksum='tampered' WHERE version='0001_first.sql'")
        )

    with pytest.raises(MigrationChecksumMismatchError):
        verify_migrations(engine, tmp_path)
