import logging
import os
from typing import Any
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)
kb_client = boto3.client("bedrock-agent")


def handler(event: dict, context: Any, client: Any = None) -> dict:
    relevant = any(
        record.get("eventSource") == "aws:s3"
        and record.get("eventName", "").startswith(("ObjectCreated:", "ObjectRemoved:"))
        and unquote_plus(record["s3"]["object"]["key"]).startswith("docs/")
        for record in event.get("Records", [])
    )
    if not relevant:
        return {"started": False, "reason": "evento ignorado"}

    client = client if client is not None else kb_client
    try:
        client.start_ingestion_job(
            knowledgeBaseId=os.environ["KB_ID"],
            dataSourceId=os.environ["DATA_SOURCE_ID"],
        )
    except ClientError as err:
        if err.response["Error"]["Code"] != "ConflictException":
            raise
        # O sync é incremental: o próximo job pega os arquivos que chegaram agora.
        log.info("job em andamento")
        return {"started": False, "reason": "job em andamento"}
    return {"started": True}
