from datetime import UTC, datetime
from unittest.mock import patch

import boto3
import pytest
from botocore.stub import Stubber

from scripts.env_from_stack import main

OUTPUTS = {
    "DocsBucket": "bucket-da-demo",
    "KnowledgeBaseId": "KB12345678",
    "DataSourceId": "DS12345678",
    "BackendPolicyArn": "arn:aws:iam::123456789012:policy/backend",
    "ModelArns": (
        "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.amazon.nova-micro-v1:0,"
        "arn:aws:bedrock:us-east-1:123456789012:inference-profile/us.amazon.nova-lite-v1:0"
    ),
    "Region": "us-east-1",
}


def stack_response(outputs: dict[str, str]) -> dict:
    return {
        "Stacks": [{
            "StackName": "demo",
            "CreationTime": datetime(2026, 1, 1, tzinfo=UTC),
            "StackStatus": "CREATE_COMPLETE",
            "Outputs": [
                {"OutputKey": key, "OutputValue": value} for key, value in outputs.items()
            ],
        }],
    }


def test_prints_env_from_stack_outputs(capsys) -> None:
    client = boto3.client("cloudformation")
    with Stubber(client) as stub, patch("scripts.env_from_stack.boto3.client", return_value=client):
        stub.add_response("describe_stacks", stack_response(OUTPUTS), {"StackName": "demo"})
        main(["demo"])
        stub.assert_no_pending_responses()

    captured = capsys.readouterr()
    assert captured.out == (
        "AWS_REGION=us-east-1\n"
        "DOCS_BUCKET=bucket-da-demo\n"
        "KB_ID=KB12345678\n"
        "DATA_SOURCE_ID=DS12345678\n"
        f"MODEL_ARNS={OUTPUTS['ModelArns']}\n"
        "UPLOAD_TTL_SECONDS=300\n"
        "UPLOADS_PER_MINUTE=120\n"
    )
    assert captured.err == ""


@pytest.mark.parametrize("missing", ["DocsBucket", "ModelArns"])
def test_missing_output_does_not_print_partial_env(missing: str, capsys) -> None:
    client = boto3.client("cloudformation")
    outputs = {key: value for key, value in OUTPUTS.items() if key != missing}
    with Stubber(client) as stub, patch("scripts.env_from_stack.boto3.client", return_value=client):
        stub.add_response("describe_stacks", stack_response(outputs), {"StackName": "demo"})
        with pytest.raises(SystemExit) as error:
            main(["demo"])
        stub.assert_no_pending_responses()

    captured = capsys.readouterr()
    assert error.value.code != 0
    assert captured.out == ""
    assert missing in captured.err


def test_aws_error_is_reported_on_stderr(capsys) -> None:
    client = boto3.client("cloudformation")
    with Stubber(client) as stub, patch("scripts.env_from_stack.boto3.client", return_value=client):
        stub.add_client_error(
            "describe_stacks", service_error_code="ValidationError",
            service_message="Stack with id demo does not exist",
            expected_params={"StackName": "demo"},
        )
        with pytest.raises(SystemExit) as error:
            main(["demo"])
        stub.assert_no_pending_responses()

    captured = capsys.readouterr()
    assert error.value.code != 0
    assert captured.out == ""
    assert "ValidationError" in captured.err


def test_stack_name_is_required(capsys) -> None:
    with pytest.raises(SystemExit) as error:
        main([])

    assert error.value.code == 2
    assert capsys.readouterr().out == ""
