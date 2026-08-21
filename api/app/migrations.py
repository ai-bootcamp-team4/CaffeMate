import hashlib
from pathlib import Path

from sqlalchemy import Engine, text


class MigrationChecksumMismatchError(RuntimeError):
    pass


def apply_migrations(engine: Engine, migration_directory: Path | None = None) -> None:
    directory = migration_directory or Path(__file__).resolve().parents[1] / "migrations"
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    checksum TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        )

    for migration_path in sorted(directory.glob("*.sql")):
        sql = migration_path.read_text(encoding="utf-8")
        checksum = hashlib.sha256(sql.encode()).hexdigest()
        with engine.begin() as connection:
            existing = connection.execute(
                text("SELECT checksum FROM schema_migrations WHERE version = :version"),
                {"version": migration_path.name},
            ).scalar_one_or_none()
            if existing is not None:
                if existing != checksum:
                    raise MigrationChecksumMismatchError(
                        f"Applied migration changed: {migration_path.name}"
                    )
                continue

            for statement in sql.split("-- statement-break"):
                if statement.strip():
                    connection.exec_driver_sql(statement)
            connection.execute(
                text(
                    "INSERT INTO schema_migrations(version, checksum) "
                    "VALUES (:version, :checksum)"
                ),
                {"version": migration_path.name, "checksum": checksum},
            )
