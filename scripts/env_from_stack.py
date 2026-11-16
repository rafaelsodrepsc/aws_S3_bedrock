import argparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError

ENV_OUTPUTS = {
    "AWS_REGION": "Region",
    "DOCS_BUCKET": "DocsBucket",
    "KB_ID": "KnowledgeBaseId",
    "DATA_SOURCE_ID": "DataSourceId",
    "MODEL_ARNS": "ModelArns",
}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Imprime o .env a partir dos Outputs da stack.")
    parser.add_argument("stack_name", help="Nome da stack na região configurada no perfil AWS")
    args = parser.parse_args(argv)
    try:
        response = boto3.client("cloudformation").describe_stacks(StackName=args.stack_name)
    except (BotoCoreError, ClientError) as error:
        parser.exit(1, f"Não foi possível ler a stack: {error}\n")

    outputs = {
        output["OutputKey"]: output["OutputValue"]
        for output in response["Stacks"][0].get("Outputs", [])
    }
    missing = [key for key in ENV_OUTPUTS.values() if not outputs.get(key)]
    if missing:
        parser.exit(1, f"Outputs ausentes na stack: {', '.join(missing)}\n")

    lines = [f"{env}={outputs[key]}" for env, key in ENV_OUTPUTS.items()]
    print("\n".join([*lines, "UPLOAD_TTL_SECONDS=300", "UPLOADS_PER_MINUTE=60"]))


if __name__ == "__main__":
    main()
