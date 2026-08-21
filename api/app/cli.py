import argparse

from app.database import create_database_handle
from app.migrations import apply_migrations
from app.settings import RuntimeSettings


def main() -> None:
    parser = argparse.ArgumentParser(prog="caffemate-api")
    parser.add_argument("command", choices=["migrate"])
    arguments = parser.parse_args()

    if arguments.command == "migrate":
        handle = create_database_handle(RuntimeSettings.from_environment())
        if handle is None:
            parser.error(
                "DATABASE_URL or complete Cloud SQL INSTANCE_CONNECTION_NAME/DB_* settings required"
            )
        try:
            apply_migrations(handle.engine)
        finally:
            handle.close()
