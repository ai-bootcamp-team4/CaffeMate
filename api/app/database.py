from dataclasses import dataclass
from typing import Protocol, cast

from google.cloud.sql.connector import Connector, IPTypes
from sqlalchemy import Engine, create_engine

from app.settings import RuntimeSettings


class Closeable(Protocol):
    def close(self) -> None: ...


@dataclass
class DatabaseHandle:
    engine: Engine
    connector: Closeable | None = None

    def close(self) -> None:
        self.engine.dispose()
        if self.connector is not None:
            self.connector.close()


def create_database_handle(settings: RuntimeSettings) -> DatabaseHandle | None:
    if settings.database_url:
        return DatabaseHandle(
            engine=create_engine(settings.database_url, pool_pre_ping=True),
        )
    if not settings.has_cloud_sql_configuration:
        return None

    connector = Connector(refresh_strategy="LAZY")
    ip_type = {
        "PUBLIC": IPTypes.PUBLIC,
        "PRIVATE": IPTypes.PRIVATE,
        "PSC": IPTypes.PSC,
    }.get(settings.cloud_sql_ip_type.upper())
    if ip_type is None:
        connector.close()
        raise ValueError("CLOUD_SQL_IP_TYPE must be PUBLIC, PRIVATE, or PSC")

    instance = cast(str, settings.cloud_sql_instance)
    user = cast(str, settings.database_user)
    password = cast(str, settings.database_password)
    database = cast(str, settings.database_name)

    def connect() -> object:
        return connector.connect(
            instance,
            "pg8000",
            user=user,
            password=password,
            db=database,
            ip_type=ip_type,
        )

    engine = create_engine(
        "postgresql+pg8000://",
        creator=connect,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,
        pool_recycle=1800,
    )
    return DatabaseHandle(engine=engine, connector=connector)
