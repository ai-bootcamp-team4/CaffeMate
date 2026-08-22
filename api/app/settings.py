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
    control_api_audience: str | None
    control_api_url: str | None
    worker_service_account_email: str | None
    worker_id: str | None
    pubsub_subscription: str | None
    workflow_stage_topic_resource: str | None
    agent_runtime_project_id: str | None
    agent_runtime_resource_id: str | None
    agent_runtime_user_hmac_secret: str | None
    mcp_base_url: str | None
    mcp_audience: str | None
    mcp_scope_hmac_secret: str | None
    document_bucket: str | None
    cors_allowed_origins: tuple[str, ...] = ()

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
            control_api_audience=os.getenv("CONTROL_API_AUDIENCE"),
            control_api_url=os.getenv("CONTROL_API_URL"),
            worker_service_account_email=os.getenv("WORKER_SERVICE_ACCOUNT_EMAIL"),
            worker_id=os.getenv("WORKER_ID") or os.getenv("HOSTNAME"),
            pubsub_subscription=os.getenv("PUBSUB_SUBSCRIPTION"),
            workflow_stage_topic_resource=os.getenv("WORKFLOW_STAGE_TOPIC_RESOURCE"),
            agent_runtime_project_id=os.getenv("AGENT_RUNTIME_PROJECT_ID")
            or os.getenv("GOOGLE_CLOUD_PROJECT"),
            agent_runtime_resource_id=os.getenv("AGENT_RUNTIME_RESOURCE_ID"),
            agent_runtime_user_hmac_secret=os.getenv("AGENT_RUNTIME_USER_HMAC_SECRET"),
            mcp_base_url=os.getenv("MCP_BASE_URL"),
            mcp_audience=os.getenv("MCP_AUDIENCE"),
            mcp_scope_hmac_secret=os.getenv("MCP_SCOPE_HMAC_SECRET"),
            document_bucket=os.getenv("DOCUMENT_BUCKET"),
            cors_allowed_origins=tuple(
                origin.strip()
                for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(";")
                if origin.strip()
            ),
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

    @property
    def has_agent_runtime_configuration(self) -> bool:
        return all(
            (
                self.agent_runtime_project_id,
                self.agent_runtime_resource_id,
                self.agent_runtime_user_hmac_secret,
            )
        )

    @property
    def has_mcp_configuration(self) -> bool:
        return all((self.mcp_base_url, self.mcp_audience, self.mcp_scope_hmac_secret))
