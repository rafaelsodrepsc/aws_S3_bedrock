#!/usr/bin/env bash
# Sobe o WAF (us-east-1), empacota o backend, sobe a Lambda com o CloudFront e aponta o CORS
# do bucket para o endereço do CloudFront.
# Uso: AWS_PROFILE=minicurso scripts/deploy_web.sh
# O ADMIN_TOKEN vem do ambiente ou do .env, o mesmo das páginas /admin e /chat no notebook.
set -euo pipefail
cd "$(dirname "$0")/.."

REGION=${AWS_REGION:-us-east-2}
MAIN_STACK=${MAIN_STACK:-minicurso}
WEB_STACK=${WEB_STACK:-minicurso-web}
WAF_STACK=${WAF_STACK:-minicurso-waf}

cfn() { aws --region "$REGION" cloudformation "$@"; }
output() {
  cfn describe-stacks --stack-name "$1" --output text \
    --query "Stacks[0].Outputs[?OutputKey=='$2'].OutputValue"
}

if [[ -z "${ADMIN_TOKEN:-}" ]]; then
  ADMIN_TOKEN=$(grep '^ADMIN_TOKEN=' .env | cut -d= -f2-)
fi

BUCKET=$(output "$MAIN_STACK" DocsBucket)

# O WAF de um CloudFront só pode ser criado em us-east-1.
aws --region us-east-1 cloudformation deploy --stack-name "$WAF_STACK" \
  --template-file infra/waf.yaml --no-fail-on-empty-changeset
WEB_ACL=$(aws --region us-east-1 cloudformation describe-stacks --stack-name "$WAF_STACK" \
  --output text --query "Stacks[0].Outputs[?OutputKey=='WebAclArn'].OutputValue")

build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
# As versões travadas no uv.lock, só com o que roda na Lambda (sem uvicorn e sem awscrt).
uv export --frozen --no-dev --no-hashes --no-emit-project -o "$build/constraints.txt" >/dev/null
uv pip install --quiet --target "$build/pkg" -c "$build/constraints.txt" \
  --python-platform aarch64-manylinux2014 --python-version 3.13 --only-binary :all: \
  fastapi mangum boto3
cp -r app static "$build/pkg/"
find "$build/pkg" -name __pycache__ -prune -exec rm -rf {} +
(cd "$build/pkg" && zip -qr ../web.zip .)

key="deploy/web-$(sha256sum "$build/web.zip" | cut -c1-12).zip"
aws --region "$REGION" s3 cp --only-show-errors "$build/web.zip" "s3://$BUCKET/$key"

cfn deploy --stack-name "$WEB_STACK" --template-file infra/web.yaml \
  --capabilities CAPABILITY_IAM --no-fail-on-empty-changeset \
  --parameter-overrides \
    "DocsBucket=$BUCKET" \
    "KnowledgeBaseId=$(output "$MAIN_STACK" KnowledgeBaseId)" \
    "DataSourceId=$(output "$MAIN_STACK" DataSourceId)" \
    "ModelArns=$(output "$MAIN_STACK" ModelArns)" \
    "BackendPolicyArn=$(output "$MAIN_STACK" BackendPolicyArn)" \
    "AdminToken=$ADMIN_TOKEN" \
    "CodeKey=$key" \
    "WebAclArn=$WEB_ACL"

ORIGIN=$(output "$WEB_STACK" Origin)
# As páginas ficam 5 minutos no cache: sem isso a versão nova demora a aparecer.
aws cloudfront create-invalidation --distribution-id "$(output "$WEB_STACK" DistributionId)" \
  --paths '/*' >/dev/null

# O bucket só aceita upload vindo da origem cadastrada no CORS.
if ! result=$(cfn update-stack --stack-name "$MAIN_STACK" \
    --template-body file://infra/stack.yaml --capabilities CAPABILITY_IAM \
    --parameters "ParameterKey=AllowedOrigin,ParameterValue=$ORIGIN" \
      ParameterKey=PrimaryModelId,UsePreviousValue=true \
      ParameterKey=SecondaryModelId,UsePreviousValue=true \
      ParameterKey=InferenceProfilePrefix,UsePreviousValue=true 2>&1); then
  grep -q 'No updates are to be performed' <<<"$result" || { echo "$result" >&2; exit 1; }
else
  cfn wait stack-update-complete --stack-name "$MAIN_STACK"
fi

echo "No ar em $ORIGIN"
