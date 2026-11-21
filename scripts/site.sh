#!/usr/bin/env bash
# Liga e desliga o backend do site na hora, sem apagar nada.
# Uso: AWS_PROFILE=minicurso scripts/site.sh off|on|status
# Desligado, a Lambda não roda (concorrência reservada 0) e o custo dela vai a zero. O site
# inteiro para; só o que estiver no cache do CloudFront ainda abre, por até 5 minutos.
set -euo pipefail

REGION=${AWS_REGION:-us-east-2}
FUNCTION=do-bucket-a-ia-web
lambda() { aws --region "$REGION" lambda "$@"; }

case "${1:-status}" in
  off) lambda put-function-concurrency --function-name "$FUNCTION" \
         --reserved-concurrent-executions 0 >/dev/null && echo desligado ;;
  on) lambda delete-function-concurrency --function-name "$FUNCTION" && echo ligado ;;
  status)
    reserved=$(lambda get-function-concurrency --function-name "$FUNCTION" \
      --query ReservedConcurrentExecutions --output text)
    [[ "$reserved" == "0" ]] && echo desligado || echo ligado ;;
  *) echo "uso: $0 off|on|status" >&2; exit 2 ;;
esac
