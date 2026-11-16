import json
import logging
import secrets
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.settings import Settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PENDING_PREFIX = "pendentes/"
DOCS_PREFIX = "docs/"
UUID_PATTERN = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
PENDING_KEY_PATTERN = rf"^pendentes/{UUID_PATTERN}\.pdf$"
PDF_MAGIC = b"%PDF-"

# Erros da AWS que têm um status HTTP mais útil que 502 para quem chama.
STATUS_BY_AWS_CODE = {
    "ThrottlingException": 429,
    "ConflictException": 409,
    "NoSuchKey": 404,
    "ResourceNotFoundException": 404,
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"level": record.levelname, "event": record.getMessage()}
        payload.update(getattr(record, "ctx", {}))
        return json.dumps(payload, ensure_ascii=False)


log = logging.getLogger("minicurso")
if not log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(JsonFormatter())
    log.addHandler(_handler)
    log.setLevel(logging.INFO)


@dataclass(frozen=True)
class Clients:
    s3: Any
    kb_admin: Any
    kb_runtime: Any

    @classmethod
    def from_settings(cls, settings: Settings) -> "Clients":
        # Região e SigV4 explícitos: sem isso o POST assinado pode cair num endpoint que
        # responde 307, e o navegador não segue redirect em requisição com CORS.
        s3_config = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
        return cls(
            s3=boto3.client("s3", region_name=settings.region, config=s3_config),
            kb_admin=boto3.client("bedrock-agent", region_name=settings.region),
            kb_runtime=boto3.client("bedrock-agent-runtime", region_name=settings.region),
        )


class RateLimiter:
    """Janela deslizante de 60 segundos, global para o processo."""

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._per_minute = per_minute
        self._clock = clock
        self._hits: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = self._clock()
        with self._lock:
            while self._hits and now - self._hits[0] >= 60:
                self._hits.popleft()
            if len(self._hits) >= self._per_minute:
                return False
            self._hits.append(now)
            return True


class PendingKey(BaseModel):
    key: str = Field(pattern=PENDING_KEY_PATTERN)


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    model: str | None = None


class SearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    top_k: int = Field(default=5, ge=1, le=10)


def aws_failure(op: str, err: Exception) -> HTTPException:
    code = err.response["Error"]["Code"] if isinstance(err, ClientError) else type(err).__name__
    log.error("aws_call_failed", extra={"ctx": {"op": op, "aws_code": code}})
    status = STATUS_BY_AWS_CODE.get(code, 502)
    return HTTPException(status_code=status, detail=f"{op} falhou: {code}")


def s3_uri(ref: dict) -> str | None:
    return ref.get("location", {}).get("s3Location", {}).get("uri")


def create_app(settings: Settings | None = None, clients: Clients | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    clients = clients or Clients.from_settings(settings)
    s3, kb_admin, kb_runtime = clients.s3, clients.kb_admin, clients.kb_runtime
    upload_limiter = RateLimiter(settings.uploads_per_minute)

    app = FastAPI(title="Do bucket à IA")

    def require_admin(token: str | None) -> None:
        if token is None or not secrets.compare_digest(token, settings.admin_token):
            raise HTTPException(status_code=401, detail="não autorizado")

    @app.get("/")
    def upload_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "upload.html")

    @app.get("/chat")
    def chat_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "chat.html")

    @app.get("/admin")
    def admin_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin.html")

    @app.post("/upload-url")
    def create_upload_url(ttl: int = settings.upload_ttl_seconds) -> dict:
        if not upload_limiter.allow():
            raise HTTPException(status_code=429, detail="muitos envios agora, tente em um minuto")
        # ttl menor permite a demo da autorização expirada; nunca passa do padrão.
        ttl = max(5, min(ttl, settings.upload_ttl_seconds))
        # A chave é gerada aqui: o cliente nunca escolhe nome nem prefixo.
        key = f"{PENDING_PREFIX}{uuid.uuid4()}.pdf"
        try:
            post = s3.generate_presigned_post(
                Bucket=settings.bucket,
                Key=key,
                Fields={"Content-Type": "application/pdf"},
                Conditions=[
                    {"Content-Type": "application/pdf"},
                    ["content-length-range", 1, settings.max_upload_bytes],
                ],
                ExpiresIn=ttl,
            )
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("generate_presigned_post", err) from err
        log.info("upload_url_issued", extra={"ctx": {"key": key, "ttl": ttl}})
        return post

    @app.get("/admin/pending")
    def list_pending(x_admin_token: str | None = Header(default=None)) -> list[dict]:
        require_admin(x_admin_token)
        try:
            resp = s3.list_objects_v2(Bucket=settings.bucket, Prefix=PENDING_PREFIX)
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("list_objects_v2", err) from err
        return [
            {"key": o["Key"], "size": o["Size"], "last_modified": o["LastModified"].isoformat()}
            for o in resp.get("Contents", [])
        ]

    @app.post("/admin/approve")
    def approve(req: PendingKey, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        target = DOCS_PREFIX + req.key.removeprefix(PENDING_PREFIX)
        try:
            # O S3 só conferiu o Content-Type declarado. Aqui olhamos o conteúdo de verdade.
            head = s3.get_object(Bucket=settings.bucket, Key=req.key, Range="bytes=0-4")
            if head["Body"].read() != PDF_MAGIC:
                raise HTTPException(status_code=422, detail="o arquivo não é um PDF")
            s3.copy_object(
                Bucket=settings.bucket,
                Key=target,
                CopySource={"Bucket": settings.bucket, "Key": req.key},
            )
            s3.delete_object(Bucket=settings.bucket, Key=req.key)
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("approve", err) from err
        log.info("document_approved", extra={"ctx": {"source": req.key, "target": target}})
        return {"key": target}

    @app.post("/admin/reject")
    def reject(req: PendingKey, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        try:
            s3.delete_object(Bucket=settings.bucket, Key=req.key)
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("reject", err) from err
        log.info("document_rejected", extra={"ctx": {"key": req.key}})
        return {"key": req.key}

    @app.post("/admin/sync")
    def start_sync(x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        try:
            job = kb_admin.start_ingestion_job(
                knowledgeBaseId=settings.kb_id, dataSourceId=settings.data_source_id
            )["ingestionJob"]
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("start_ingestion_job", err) from err
        log.info("ingestion_started", extra={"ctx": {"job_id": job["ingestionJobId"]}})
        return {"job_id": job["ingestionJobId"], "status": job["status"]}

    @app.get("/admin/sync/{job_id}")
    def sync_status(job_id: str, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        try:
            job = kb_admin.get_ingestion_job(
                knowledgeBaseId=settings.kb_id,
                dataSourceId=settings.data_source_id,
                ingestionJobId=job_id,
            )["ingestionJob"]
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("get_ingestion_job", err) from err
        return {
            "status": job["status"],
            "statistics": job.get("statistics", {}),
            "failure_reasons": job.get("failureReasons", []),
        }

    @app.get("/admin/models")
    def list_models(x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        return {"models": list(settings.model_arns)}

    @app.post("/ask")
    def ask(req: AskRequest, x_admin_token: str | None = Header(default=None)) -> dict:
        # Só o apresentador pergunta: cada pergunta custa tokens.
        require_admin(x_admin_token)
        model = req.model or settings.model_arns[0]
        if model not in settings.model_arns:
            raise HTTPException(status_code=422, detail="modelo fora da lista permitida")
        started = time.perf_counter()
        try:
            resp = kb_runtime.retrieve_and_generate(
                input={"text": req.question},
                retrieveAndGenerateConfiguration={
                    "type": "KNOWLEDGE_BASE",
                    "knowledgeBaseConfiguration": {
                        "knowledgeBaseId": settings.kb_id,
                        "modelArn": model,
                    },
                },
            )
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("retrieve_and_generate", err) from err

        sources: list[dict] = []
        seen: set[str] = set()
        for citation in resp.get("citations", []):
            for ref in citation.get("retrievedReferences", []):
                uri = s3_uri(ref)
                if uri and uri not in seen:
                    seen.add(uri)
                    sources.append({"uri": uri, "excerpt": ref.get("content", {}).get("text", "")})

        latency_ms = round((time.perf_counter() - started) * 1000)
        log.info("question_answered", extra={"ctx": {
            "latency_ms": latency_ms, "sources": len(sources), "model": model,
        }})
        return {
            "answer": resp["output"]["text"],
            "sources": sources,
            "model": model,
            "latency_ms": latency_ms,
        }

    @app.post("/search")
    def search(req: SearchRequest, x_admin_token: str | None = Header(default=None)) -> dict:
        # Só a etapa de busca, sem geração: mostra o que o modelo receberia.
        require_admin(x_admin_token)
        try:
            resp = kb_runtime.retrieve(
                knowledgeBaseId=settings.kb_id,
                retrievalQuery={"text": req.question},
                retrievalConfiguration={
                    "vectorSearchConfiguration": {"numberOfResults": req.top_k}
                },
            )
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("retrieve", err) from err
        return {
            "results": [
                {
                    "score": r.get("score"),
                    "uri": s3_uri(r),
                    "text": r.get("content", {}).get("text", ""),
                }
                for r in resp.get("retrievalResults", [])
            ]
        }

    return app
