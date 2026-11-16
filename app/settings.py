import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    region: str
    bucket: str
    kb_id: str
    data_source_id: str
    # O primeiro é o padrão; os outros podem ser escolhidos no /ask (comparação de modelos).
    model_arns: tuple[str, ...]
    admin_token: str
    upload_ttl_seconds: int = 300
    max_upload_bytes: int = 5 * 1024 * 1024
    # Limite global, não por IP: atrás do túnel todo mundo chega com o mesmo IP.
    uploads_per_minute: int = 60

    @classmethod
    def from_env(cls) -> "Settings":
        models = tuple(m.strip() for m in os.environ["MODEL_ARNS"].split(",") if m.strip())
        if not models:
            raise RuntimeError("MODEL_ARNS precisa ter pelo menos um ARN")
        token = os.environ["ADMIN_TOKEN"]
        if len(token) < 16:
            raise RuntimeError("ADMIN_TOKEN precisa ter pelo menos 16 caracteres")
        return cls(
            region=os.environ["AWS_REGION"],
            bucket=os.environ["DOCS_BUCKET"],
            kb_id=os.environ["KB_ID"],
            data_source_id=os.environ["DATA_SOURCE_ID"],
            model_arns=models,
            admin_token=token,
            upload_ttl_seconds=int(os.getenv("UPLOAD_TTL_SECONDS", "300")),
            uploads_per_minute=int(os.getenv("UPLOADS_PER_MINUTE", "60")),
        )
