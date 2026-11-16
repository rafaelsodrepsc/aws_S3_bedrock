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
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.settings import Settings

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PENDING_PREFIX = "pendentes/"
DOCS_PREFIX = "docs/"
# Cópia que o moderador abre. A autorização de upload só grava em pendentes/, então
# quem enviou não consegue trocar o arquivo depois que ele foi conferido.
REVIEW_PREFIX = "revisao/"
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
    """Janela deslizante de 60 segundos por chave (um cliente, ou "" para o total)."""

    MAX_KEYS = 10_000

    def __init__(self, per_minute: int, clock: Callable[[], float] = time.monotonic) -> None:
        self._per_minute = per_minute
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._last_prune = float("-inf")
        self._lock = threading.Lock()

    def allow(self, key: str = "") -> bool:
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                # Teto de clientes distintos: limpa os parados no máximo uma vez por segundo e,
                # se continuar cheio, recusa cliente novo em vez de crescer sem limite.
                if len(self._hits) >= self.MAX_KEYS and now - self._last_prune >= 1:
                    self._last_prune = now
                    self._hits = {k: h for k, h in self._hits.items() if h and now - h[-1] < 60}
                if len(self._hits) >= self.MAX_KEYS:
                    return False
                hits = self._hits[key] = deque()
            while hits and now - hits[0] >= 60:
                hits.popleft()
            if len(hits) >= self._per_minute:
                return False
            hits.append(now)
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
    client_limiter = RateLimiter(settings.uploads_per_minute_per_client)

    def client_id(request: Request, header: str) -> str:
        # O header só é confiável quando o proxy o sobrescreve (o Cloudflare faz isso com
        # cf-connecting-ip). Sem ele, atrás de túnel todo mundo teria o IP do túnel.
        forwarded = request.headers.get(header, "")
        return forwarded.split(",")[0].strip() or "sem-header"

    app = FastAPI(title="Do bucket à IA")

    def require_admin(token: str | None) -> None:
        if token is None or not secrets.compare_digest(
            token.encode(), settings.admin_token.encode()
        ):
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
    def create_upload_url(request: Request, ttl: int = settings.upload_ttl_seconds) -> dict:
        # Por cliente primeiro, para um script em loop não gastar a cota da plateia inteira.
        header = settings.client_ip_header
        if header and not client_limiter.allow(client_id(request, header)):
            raise HTTPException(status_code=429, detail="muitos envios agora, tente em um minuto")
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
            pages = s3.get_paginator("list_objects_v2").paginate(
                Bucket=settings.bucket, Prefix=PENDING_PREFIX
            )
            return [
                {
                    "key": o["Key"],
                    "size": o["Size"],
                    "last_modified": o["LastModified"].isoformat(),
                }
                for page in pages
                for o in page.get("Contents", [])
            ]
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("list_objects_v2", err) from err

    def review_key(pending_key: str) -> str:
        return REVIEW_PREFIX + pending_key.removeprefix(PENDING_PREFIX)

    def is_pdf(key: str) -> bool:
        head = s3.get_object(Bucket=settings.bucket, Key=key, Range="bytes=0-4")
        return head["Body"].read() == PDF_MAGIC

    def discard(key: str) -> None:
        try:
            s3.delete_object(Bucket=settings.bucket, Key=key)
        except (ClientError, BotoCoreError):
            log.error("snapshot_discard_failed", extra={"ctx": {"key": key}})

    @app.post("/admin/review")
    def review(req: PendingKey, x_admin_token: str | None = Header(default=None)) -> dict:
        # Tira uma cópia que quem enviou não alcança e devolve um link curto para ela.
        # A aprovação publica exatamente essa cópia, a que o moderador abriu.
        require_admin(x_admin_token)
        snapshot = review_key(req.key)
        try:
            s3.copy_object(
                Bucket=settings.bucket,
                Key=snapshot,
                CopySource={"Bucket": settings.bucket, "Key": req.key},
            )
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("review", err) from err
        try:
            # O S3 só conferiu o Content-Type declarado. Aqui olhamos o conteúdo de verdade.
            if not is_pdf(snapshot):
                raise HTTPException(status_code=422, detail="o arquivo não é um PDF")
            url = s3.generate_presigned_url(
                "get_object",
                Params={
                    "Bucket": settings.bucket,
                    "Key": snapshot,
                    "ResponseContentType": "application/pdf",
                    "ResponseContentDisposition": "inline",
                },
                ExpiresIn=60,
            )
        except (ClientError, BotoCoreError, HTTPException) as err:
            # Cópia que o moderador não chegou a ver não pode ficar para a aprovação publicar.
            discard(snapshot)
            if isinstance(err, HTTPException):
                raise
            raise aws_failure("review", err) from err
        log.info("document_reviewed", extra={"ctx": {"source": req.key, "snapshot": snapshot}})
        return {"url": url}

    @app.post("/admin/approve")
    def approve(req: PendingKey, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        snapshot = review_key(req.key)
        target = DOCS_PREFIX + req.key.removeprefix(PENDING_PREFIX)
        try:
            # Existência pela listagem do prefixo: a policy do backend não dá ListBucket fora
            # de pendentes/ e revisao/, e sem isso o S3 responde 403 para chave inexistente.
            listed = s3.list_objects_v2(Bucket=settings.bucket, Prefix=snapshot, MaxKeys=1)
            if not any(o["Key"] == snapshot for o in listed.get("Contents", [])):
                raise HTTPException(
                    status_code=409, detail="abra o arquivo em Ver antes de aprovar"
                )
            # Confere de novo: se a conferência do Ver falhou no meio, nada sem %PDF- passa.
            if not is_pdf(snapshot):
                raise HTTPException(status_code=422, detail="o arquivo não é um PDF")
            s3.copy_object(
                Bucket=settings.bucket,
                Key=target,
                CopySource={"Bucket": settings.bucket, "Key": snapshot},
            )
        except ClientError as err:
            if err.response["Error"]["Code"] in ("NoSuchKey", "404"):
                raise HTTPException(
                    status_code=409, detail="abra o arquivo em Ver antes de aprovar"
                ) from err
            raise aws_failure("approve", err) from err
        except BotoCoreError as err:
            raise aws_failure("approve", err) from err
        try:
            s3.delete_object(Bucket=settings.bucket, Key=snapshot)
            s3.delete_object(Bucket=settings.bucket, Key=req.key)
        except (ClientError, BotoCoreError) as err:
            raise aws_failure("approve_cleanup", err) from err
        log.info("document_approved", extra={"ctx": {"source": snapshot, "target": target}})
        return {"key": target}

    @app.post("/admin/reject")
    def reject(req: PendingKey, x_admin_token: str | None = Header(default=None)) -> dict:
        require_admin(x_admin_token)
        try:
            s3.delete_object(Bucket=settings.bucket, Key=review_key(req.key))
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
