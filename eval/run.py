import argparse
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError


def score_results(results: list[dict], expected: str) -> int | None:
    for position, result in enumerate(results, start=1):
        uri = result.get("location", {}).get("s3Location", {}).get("uri", "")
        if uri.endswith(f"/{expected}"):
            return position
    return None


def summarize(positions: list[int | None]) -> dict:
    if not positions:
        return {"hit@k": 0.0, "mrr": 0.0}
    return {
        "hit@k": sum(position is not None for position in positions) / len(positions),
        "mrr": sum(1 / position for position in positions if position is not None) / len(positions),
    }


def retrieve(
    client: Any, kb_id: str, question: str, top_k: int,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    # Até três novas tentativas, além da chamada inicial.
    for attempt in range(4):
        try:
            response = client.retrieve(
                knowledgeBaseId=kb_id,
                retrievalQuery={"text": question},
                retrievalConfiguration={"vectorSearchConfiguration": {"numberOfResults": top_k}},
            )
            return response.get("retrievalResults", [])
        except ClientError as err:
            if err.response["Error"]["Code"] != "ThrottlingException" or attempt == 3:
                raise
            sleep(2 ** attempt)
    raise RuntimeError("tentativas de busca esgotadas")


def evaluate(
    client: Any, kb_id: str, questions: list[dict], top_k: int,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    rows = []
    for question in questions:
        results = retrieve(client, kb_id, question["pergunta"], top_k, sleep)
        rows.append({
            "pergunta": question["pergunta"],
            "position": score_results(results, question["fonte_esperada"]),
            "score": results[0].get("score") if results else None,
        })
    return rows


def load_questions(path: Path) -> list[dict]:
    questions = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(questions, list) or any(
        not isinstance(question, dict)
        or any(
            not isinstance(question.get(field), str) or not question[field].strip()
            for field in ("pergunta", "fonte_esperada")
        )
        or "/" in question["fonte_esperada"]
        for question in questions
    ):
        raise ValueError("use uma lista com pergunta e fonte_esperada (nome do arquivo)")
    return questions


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Mede hit@k e MRR da busca da Knowledge Base.")
    parser.add_argument("--kb-id", required=True)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--arquivo", type=Path, required=True)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--minimo", type=float, default=0.0, help="taxa mínima de acerto, entre 0 e 1",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.top_k <= 100:
        parser.error("--top-k deve estar entre 1 e 100")
    if not 0 <= args.minimo <= 1:
        parser.error("--minimo deve estar entre 0 e 1")
    try:
        questions = load_questions(args.arquivo)
    except (OSError, ValueError) as err:
        parser.error(str(err))

    # O retry fica aqui, sem multiplicar as tentativas pelo retry do SDK.
    client = boto3.client(
        "bedrock-agent-runtime", region_name=args.region,
        config=Config(retries={"total_max_attempts": 1}),
    )
    rows = evaluate(client, args.kb_id, questions, args.top_k)
    print("Posição | Score do primeiro resultado | Pergunta")
    for row in rows:
        position = str(row["position"]) if row["position"] is not None else "-"
        score = f'{row["score"]:.4f}' if row["score"] is not None else "-"
        print(f'{position:>7} | {score:>27} | {row["pergunta"]}')
    metrics = summarize([row["position"] for row in rows])
    print(f'Perguntas: {len(rows)} | hit@{args.top_k}: {metrics["hit@k"]:.2%}')
    print(f'MRR: {metrics["mrr"]:.4f}')
    return int(metrics["hit@k"] < args.minimo)


if __name__ == "__main__":
    raise SystemExit(main())
