from tests.conftest import MODEL_A, MODEL_B

DOC = "s3://bucket-de-teste/docs/regimento.pdf"


def rag_response(text="O prazo é de 30 dias.", refs=None):
    refs = refs if refs is not None else [DOC, DOC]
    return {
        "sessionId": "sessao-1",
        "output": {"text": text},
        "citations": [{
            "generatedResponsePart": {"textResponsePart": {"text": text}},
            "retrievedReferences": [
                {"content": {"text": f"trecho {i}"},
                 "location": {"type": "S3", "s3Location": {"uri": uri}}}
                for i, uri in enumerate(refs)
            ],
        }],
    }


def expected_rag_params(model):
    return {
        "input": {"text": "Qual o prazo de trancamento?"},
        "retrieveAndGenerateConfiguration": {
            "type": "KNOWLEDGE_BASE",
            "knowledgeBaseConfiguration": {"knowledgeBaseId": "KB12345678", "modelArn": model},
        },
    }


def test_ask_returns_answer_with_unique_sources(client, aws, admin):
    aws["runtime_stub"].add_response(
        "retrieve_and_generate", rag_response(), expected_rag_params(MODEL_A)
    )

    resp = client.post("/ask", json={"question": "Qual o prazo de trancamento?"}, headers=admin)

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "O prazo é de 30 dias."
    assert body["sources"] == [{"uri": DOC, "excerpt": "trecho 0"}]
    assert body["model"] == MODEL_A


def test_ask_with_second_model(client, aws, admin):
    aws["runtime_stub"].add_response(
        "retrieve_and_generate", rag_response(), expected_rag_params(MODEL_B)
    )

    resp = client.post(
        "/ask", json={"question": "Qual o prazo de trancamento?", "model": MODEL_B}, headers=admin
    )

    assert resp.status_code == 200
    assert resp.json()["model"] == MODEL_B


def test_ask_refuses_model_outside_allowlist(client, admin):
    resp = client.post(
        "/ask",
        json={"question": "Qual o prazo?", "model": "arn:aws:bedrock:::modelo-caro"},
        headers=admin,
    )

    assert resp.status_code == 422


def test_ask_without_citations_returns_empty_sources(client, aws, admin):
    aws["runtime_stub"].add_response(
        "retrieve_and_generate",
        {"sessionId": "sessao-1", "output": {"text": "Não encontrei."}, "citations": []},
        expected_rag_params(MODEL_A),
    )

    resp = client.post("/ask", json={"question": "Qual o prazo de trancamento?"}, headers=admin)

    assert resp.json()["sources"] == []


def test_ask_throttled_returns_429(client, aws, admin):
    aws["runtime_stub"].add_client_error(
        "retrieve_and_generate", service_error_code="ThrottlingException"
    )

    resp = client.post("/ask", json={"question": "Qual o prazo de trancamento?"}, headers=admin)

    assert resp.status_code == 429


def test_ask_validates_question_length(client, admin):
    assert client.post("/ask", json={"question": "oi"}, headers=admin).status_code == 422
    assert client.post("/ask", json={"question": "x" * 501}, headers=admin).status_code == 422


def test_search_returns_chunks_with_scores(client, aws, admin):
    aws["runtime_stub"].add_response(
        "retrieve",
        {"retrievalResults": [
            {"content": {"text": "Art. 10. O trancamento..."}, "score": 0.82,
             "location": {"type": "S3", "s3Location": {"uri": DOC}}},
        ]},
        {
            "knowledgeBaseId": "KB12345678",
            "retrievalQuery": {"text": "trancamento"},
            "retrievalConfiguration": {"vectorSearchConfiguration": {"numberOfResults": 3}},
        },
    )

    resp = client.post("/search", json={"question": "trancamento", "top_k": 3}, headers=admin)

    assert resp.status_code == 200
    assert resp.json()["results"] == [
        {"score": 0.82, "uri": DOC, "text": "Art. 10. O trancamento..."}
    ]


def test_search_limits_top_k(client, admin):
    resp = client.post("/search", json={"question": "trancamento", "top_k": 50}, headers=admin)

    assert resp.status_code == 422
