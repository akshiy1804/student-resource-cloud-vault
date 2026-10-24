import os
from dataclasses import dataclass, field


def _flag(name, default="0"):
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Config:
    region: str = field(default_factory=lambda: os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1")
    table: str = field(default_factory=lambda: os.getenv("DDB_TABLE", "StudentResources"))
    topic_name: str = field(default_factory=lambda: os.getenv("SNS_TOPIC_NAME", "StudentResourceVaultNotifications"))
    topic_arn: str = field(default_factory=lambda: os.getenv("SNS_TOPIC_ARN", ""))
    bucket: str = field(default_factory=lambda: os.getenv("S3_BUCKET", "").strip())
    key_prefix: str = field(default_factory=lambda: os.getenv("S3_KEY_PREFIX", ""))
    presign_seconds: int = field(default_factory=lambda: max(30, min(int(os.getenv("PRESIGN_SECONDS", "300")), 3600)))
    admin_token: str = field(default_factory=lambda: os.getenv("ADMIN_TOKEN", ""))
    demo_mode: bool = field(default_factory=lambda: _flag("DEMO_MODE"))
    max_upload_mb: int = 25
    overrides: dict = field(default_factory=lambda: {
        k: os.environ[f"FIELD_{k.upper()}"]
        for k in ("id", "title", "subject", "category", "description", "filename", "s3_key")
        if os.getenv(f"FIELD_{k.upper()}")
    })
