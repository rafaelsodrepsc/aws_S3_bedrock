"""Entrada da Lambda: o mesmo app do notebook, atrás do CloudFront."""

from typing import Any

from mangum import Mangum

from app.main import create_app

# Atrás do CloudFront o IP que chega aqui é o do CloudFront, não o da pessoa, e a plateia
# costuma sair pelo mesmo IP da rede do evento. O limite por IP fica no WAF (infra/waf.yaml).
_asgi: Mangum | None = None


def handler(event: dict, context: Any) -> dict:
    global _asgi
    if _asgi is None:
        _asgi = Mangum(create_app(), lifespan="off")
    return _asgi(event, context)
