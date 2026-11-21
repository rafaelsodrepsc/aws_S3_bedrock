# Infraestrutura da demo

No console do CloudFormation, selecione a região do seu projeto, crie uma stack e escolha
**Upload a template file** com `infra/stack.yaml`. Preencha `AllowedOrigin`,
aceite a criação de recursos IAM e aguarde `CREATE_COMPLETE`.

- `AllowedOrigin`: origem exata da página de upload, como `http://localhost:8000`
  ou a URL HTTPS do túnel, sem caminho nem barra final; é obrigatório.
- `PrimaryModelId`: modelo principal, por padrão `amazon.nova-lite-v1:0`.
- `SecondaryModelId`: segundo modelo para comparar na página `/chat`. Vazio
  por padrão, e aí a demo usa só o principal.
- `InferenceProfilePrefix`: `none` por padrão, que chama o modelo direto na
  região da stack. `us`, `eu` ou `apac` usam inference profile, que distribui
  as chamadas entre regiões.

O padrão é chamar o modelo direto na região porque projetos que só aceitam uma
região podem bloquear o inference profile. Em `us-east-2`, o único modelo de
texto da Amazon que roda direto na região é o Nova Lite; para comparar com o
Nova Micro, informe `amazon.nova-micro-v1:0` em `SecondaryModelId` e `us` em
`InferenceProfilePrefix`, se o seu projeto permitir. Para ver o que roda direto
na sua região:

```sh
aws bedrock list-foundation-models --by-provider amazon \
  --query "modelSummaries[?contains(inferenceTypesSupported,'ON_DEMAND')].modelId"
```

A região também precisa ter S3 Vectors e Titan Text Embeddings V2. Os IDs dos
modelos não incluem o prefixo geográfico; `ModelArns` traz o principal primeiro.

Nos Outputs, associe `BackendPolicyArn` à identidade IAM usada pelo backend.
Com credenciais AWS configuradas e `AWS_DEFAULT_REGION` na região da stack,
execute na raiz do projeto:

```sh
uv run python scripts/env_from_stack.py nome-da-stack
```

Copie a saída para `.env` e acrescente `ADMIN_TOKEN`, gerado por você com pelo
menos 16 caracteres. O script precisa de `cloudformation:DescribeStacks` na
stack; essa permissão pertence à identidade de configuração, não à policy do
backend. O template cria os recursos AWS, e o backend roda separadamente, no notebook ou publicado pelo `scripts/deploy_web.sh`.

Para apagar, se você publicou o site, apague antes a stack `minicurso-web` e depois a
`minicurso-waf`, em `us-east-1`. Pare o backend, espere qualquer ingestão terminar e esvazie o
bucket indicado em `DocsBucket`, incluindo `pendentes/` e `docs/`, antes de
usar **Delete stack** no CloudFormation. A exclusão da fonte remove os vetores;
as dependências mantêm o índice e a role disponíveis até essa etapa acabar.

O custo esperado para uma demo pequena é baixo e varia com armazenamento,
requisições e tokens de embeddings e geração, conforme os preços de
[S3](https://aws.amazon.com/s3/pricing/) e [Bedrock](https://aws.amazon.com/bedrock/pricing/).

## Site: `web.yaml` e `waf.yaml`

Os dois sobem pelo `scripts/deploy_web.sh`, não pelo console. O `web.yaml` cria a
Lambda (Python 3.13, arm64), a URL dela fechada com `AWS_IAM`, o CloudFront com OAC
e as permissões para só esse CloudFront chamar a função. O `waf.yaml` cria o WAF com
a lista de IPs de má reputação da AWS e o limite de 10 mil pedidos por IP em 5
minutos. Ele fica em `us-east-1` porque o WAF de um CloudFront só pode ser criado lá.

O WAF custa uns US$ 7 por mês, mesmo sem tráfego. A Lambda e o CloudFront, numa
demo, ficam no nível gratuito.
