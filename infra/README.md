# Infraestrutura da demo

No console do CloudFormation, selecione `us-east-1`, crie uma stack e escolha
**Upload a template file** com `infra/stack.yaml`. Preencha `AllowedOrigin`,
aceite a criação de recursos IAM e aguarde `CREATE_COMPLETE`.

- `AllowedOrigin`: origem exata da página de upload, como `http://localhost:8000`
  ou a URL HTTPS do túnel, sem caminho nem barra final; é obrigatório.
- `PrimaryModelId`: modelo principal, por padrão `amazon.nova-micro-v1:0`.
- `SecondaryModelId`: segundo modelo, por padrão `amazon.nova-lite-v1:0`.
- `InferenceProfilePrefix`: geografia dos inference profiles (`us`, `eu` ou
  `apac`), por padrão `us`; a região da stack e os modelos precisam oferecer
  esses profiles, além de suportar S3 Vectors e Titan Text Embeddings V2.

Os IDs dos modelos não incluem o prefixo geográfico. A stack monta os ARNs
com a conta e a região atuais; `ModelArns` traz o principal primeiro.
Consulte os [profiles disponíveis por região](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-profiles-support.html)
antes de mudar os padrões.

Nos Outputs, associe `BackendPolicyArn` à identidade IAM usada pelo backend.
Com credenciais AWS configuradas e `AWS_DEFAULT_REGION` na região da stack,
execute na raiz do projeto:

```sh
uv run python scripts/env_from_stack.py nome-da-stack
```

Copie a saída para `.env` e acrescente `ADMIN_TOKEN`, gerado por você com pelo
menos 16 caracteres. O script precisa de `cloudformation:DescribeStacks` na
stack; essa permissão pertence à identidade de configuração, não à policy do
backend. O template cria os recursos AWS, e o backend roda separadamente.

Para apagar, pare o backend, espere qualquer ingestão terminar e esvazie o
bucket indicado em `DocsBucket`, incluindo `pendentes/` e `docs/`, antes de
usar **Delete stack** no CloudFormation. A exclusão da fonte remove os vetores;
as dependências mantêm o índice e a role disponíveis até essa etapa acabar.

O custo esperado para uma demo pequena é baixo e varia com armazenamento,
requisições e tokens de embeddings e geração, conforme os preços de
[S3](https://aws.amazon.com/s3/pricing/) e [Bedrock](https://aws.amazon.com/bedrock/pricing/).
