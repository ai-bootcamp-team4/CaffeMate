import argparse
import json

from app.database import create_database_handle
from app.migrations import apply_migrations, verify_migrations
from app.settings import RuntimeSettings


def main() -> None:
    parser = argparse.ArgumentParser(prog="caffemate-api")
    parser.add_argument("command", choices=["migrate", "verify-migrations"])
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
    elif arguments.command == "verify-migrations":
        handle = create_database_handle(RuntimeSettings.from_environment())
        if handle is None:
            parser.error(
                "DATABASE_URL or complete Cloud SQL INSTANCE_CONNECTION_NAME/DB_* settings required"
            )
        try:
            verification = verify_migrations(handle.engine)
            print(
                json.dumps(
                    {
                        "status": "verified",
                        "migration_count": verification.count,
                        "migration_set_digest": verification.set_digest,
                    },
                    sort_keys=True,
                )
            )
        finally:
            handle.close()
