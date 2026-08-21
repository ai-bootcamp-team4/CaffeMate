import os
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeSettings:
    firebase_project_id: str | None
    database_url: str | None
    cloud_sql_instance: str | None
    database_user: str | None
    database_password: str | None
    database_name: str | None
    cloud_sql_ip_type: str
    policy_snapshot_id: str | None

    @classmethod
    def from_environment(cls) -> "RuntimeSettings":
        return cls(
            firebase_project_id=os.getenv("FIREBASE_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT"),
            database_url=os.getenv("DATABASE_URL"),
            cloud_sql_instance=os.getenv("INSTANCE_CONNECTION_NAME"),
            database_user=os.getenv("DB_USER"),
            database_password=os.getenv("DB_PASS"),
            database_name=os.getenv("DB_NAME"),
            cloud_sql_ip_type=os.getenv("CLOUD_SQL_IP_TYPE", "PRIVATE"),
            policy_snapshot_id=os.getenv("CAFFEMATE_POLICY_SNAPSHOT_ID"),
        )

    @property
    def has_cloud_sql_configuration(self) -> bool:
        return all(
            (
                self.cloud_sql_instance,
                self.database_user,
                self.database_password,
                self.database_name,
            )
        )
