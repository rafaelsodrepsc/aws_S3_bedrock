import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from eval.run import evaluate, main, score_results, summarize

QUESTION = {"pergunta": "Qual o prazo de trancamento?", "fonte_esperada": "regimento.pdf"}


def result(filename: str, score: float = 0.8) -> dict:
    return {
        "content": {"text": "Artigo do regimento."},
        "location": {"type": "S3", "s3Location": {"uri": f"s3://bucket/docs/{filename}"}},
        "score": score,
    }


def params(question: str = QUESTION["pergunta"], top_k: int = 5) -> dict:
    return {
        "knowledgeBaseId": "KB12345678",
        "retrievalQuery": {"text": question},
        "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": top_k}},
    }


@pytest.mark.parametrize("filenames, expected", [
    (["regimento.pdf", "regimento.pdf"], 1),
    (["outro.pdf", "calendario.pdf", "regimento.pdf"], 3),
    (["outro-regimento.pdf"], None),
    ([], None),
])
def test_score_results(filenames: list[str], expected: int | None) -> None:
    assert score_results([result(name) for name in filenames], "regimento.pdf") == expected


def test_score_results_ignores_missing_s3_location() -> None:
    assert score_results([{}, {"location": {"type": "WEB"}}], "regimento.pdf") is None


def test_summarize_positions() -> None:
    assert summarize([1, 3, None]) == pytest.approx({"hit@k": 2 / 3, "mrr": 4 / 9})
    assert summarize([]) == {"hit@k": 0.0, "mrr": 0.0}


def test_evaluate_retrieves_each_question(aws: dict) -> None:
    second = {"pergunta": "Qual a frequência mínima?", "fonte_esperada": "regimento.pdf"}
    aws["runtime_stub"].add_response(
        "retrieve", {"retrievalResults": [result("outro.pdf", 0.9), result("regimento.pdf")]},
        params(top_k=3),
    )
    aws["runtime_stub"].add_response(
        "retrieve", {"retrievalResults": []}, params(second["pergunta"], top_k=3),
    )

    rows = evaluate(aws["clients"].kb_runtime, "KB12345678", [QUESTION, second], 3)

    assert rows == [
        {"pergunta": QUESTION["pergunta"], "position": 2, "score": 0.9},
        {"pergunta": second["pergunta"], "position": None, "score": None},
    ]
    aws["runtime_stub"].assert_no_pending_responses()


@pytest.mark.parametrize("exhausted", [False, True])
def test_throttling_retries_three_times(aws: dict, exhausted: bool) -> None:
    for _ in range(3 + int(exhausted)):
        aws["runtime_stub"].add_client_error(
            "retrieve", service_error_code="ThrottlingException", expected_params=params(),
        )
    if not exhausted:
        aws["runtime_stub"].add_response(
            "retrieve", {"retrievalResults": [result("regimento.pdf")]}, params(),
        )
    waits = []

    if exhausted:
        with pytest.raises(ClientError, match="ThrottlingException"):
            evaluate(aws["clients"].kb_runtime, "KB12345678", [QUESTION], 5, sleep=waits.append)
    else:
        rows = evaluate(
            aws["clients"].kb_runtime, "KB12345678", [QUESTION], 5, sleep=waits.append,
        )
        assert rows[0]["position"] == 1

    assert waits == [1, 2, 4]
    aws["runtime_stub"].assert_no_pending_responses()


def test_other_aws_error_propagates(aws: dict) -> None:
    aws["runtime_stub"].add_client_error(
        "retrieve", service_error_code="AccessDeniedException", expected_params=params(),
    )
    waits = []
    with pytest.raises(ClientError, match="AccessDeniedException"):
        evaluate(aws["clients"].kb_runtime, "KB12345678", [QUESTION], 5, sleep=waits.append)
    assert waits == []


@pytest.mark.parametrize("minimum, expected_exit", [(None, 0), ("0.5", 1), ("0.0", 0)])
def test_main_reports_metrics_and_applies_minimum(
    aws: dict, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture, minimum: str | None, expected_exit: int,
) -> None:
    path = tmp_path / "perguntas.json"
    path.write_text(json.dumps([QUESTION]), encoding="utf-8")
    monkeypatch.setattr("eval.run.boto3.client", lambda *args, **kwargs: aws["clients"].kb_runtime)
    aws["runtime_stub"].add_response("retrieve", {"retrievalResults": []}, params())
    args = ["--kb-id", "KB12345678", "--region", "us-east-1", "--arquivo", str(path)]
    if minimum is not None:
        args += ["--minimo", minimum]

    assert main(args) == expected_exit
    output = capsys.readouterr().out
    assert QUESTION["pergunta"] in output
    assert "Posição" in output
    assert "Score" in output
    assert "hit@5: 0.00%" in output
    assert "MRR: 0.0000" in output
