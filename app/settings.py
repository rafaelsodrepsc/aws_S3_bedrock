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
    # Teto somado de todos os clientes.
    uploads_per_minute: int = 120
    # Limite por cliente, só ativo com client_ip_header: atrás do túnel o IP da conexão é o
    # mesmo para todos. Configure só se o proxy sobrescreve o header (ex.: cf-connecting-ip).
    uploads_per_minute_per_client: int = 10
    client_ip_header: str | None = None

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
            uploads_per_minute=int(os.getenv("UPLOADS_PER_MINUTE", "120")),
            uploads_per_minute_per_client=int(os.getenv("UPLOADS_PER_MINUTE_PER_CLIENT", "10")),
            client_ip_header=os.getenv("CLIENT_IP_HEADER") or None,
        )
