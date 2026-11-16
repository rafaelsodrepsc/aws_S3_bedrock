# Do bucket à IA

Código da demo do minicurso "Do bucket à IA com Amazon S3 e Amazon Bedrock", apresentado por Rafael Sodré e João Aguiar.

A plateia envia PDFs pelo celular direto para um bucket S3. O apresentador aprova os arquivos, sincroniza uma Bedrock Knowledge Base e faz perguntas que voltam com a fonte. O backend nunca recebe o arquivo: ele só assina a autorização de upload e chama o Bedrock.

## Rodar os testes

Não precisa de conta AWS. O S3 é simulado com moto e o Bedrock com `botocore.stub.Stubber`.

```sh
uv sync
uv run pytest -q
```

## Subir a demo na sua conta

Antes de tudo, crie um alerta no AWS Budgets. A demo custa pouco, mas recurso esquecido ligado é o susto mais comum.

1. Suba `infra/stack.yaml` pelo console do CloudFormation, na região do seu projeto. O passo a passo e os parâmetros estão em `infra/README.md`.
2. Associe a policy do output `BackendPolicyArn` ao usuário ou role que vai rodar o backend.
3. Gere o `.env`:

   ```sh
   uv run python scripts/env_from_stack.py nome-da-stack > .env
   echo "ADMIN_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(24))')" >> .env
   ```

4. Rode o backend e exponha com um túnel HTTPS. A origem do túnel precisa ser a mesma do parâmetro `AllowedOrigin` da stack, senão o CORS recusa o upload.

   ```sh
   set -a && source .env && set +a
   uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
   ```

   O túnel conecta localmente, então o backend não precisa escutar na rede. Isso também importa para o `CLIENT_IP_HEADER`: o header só é confiável se ninguém alcançar a porta 8000 sem passar pelo Cloudflare.

Use um túnel com endereço fixo. O Quick Tunnel do Cloudflare muda de endereço a cada execução e quebra o QR code e o CORS.

## Páginas

- `/` recebe o PDF (até 5 MB). É o endereço do QR code. Com `?ttl=10` dá para mostrar a autorização expirando.
- `/admin` lista os pendentes, abre cada PDF numa aba ("Ver") para você conferir, aprova ou rejeita, e dispara a sincronização. Só dá para aprovar depois de abrir o arquivo. A lista só aparece depois de clicar em "Mostrar lista", para você conferir antes de projetar.
- `/chat` faz perguntas com fonte, mostra só a busca com os scores e, se a stack tiver um segundo modelo, troca entre os dois.

As duas últimas pedem o `ADMIN_TOKEN`, que fica guardado só na aba do navegador.

## O que mais tem aqui

- `eval/`: mede se a busca acha o arquivo certo (acerto em k e MRR). Copie `eval/perguntas.example.json`, escreva suas perguntas e rode `uv run python -m eval.run --kb-id SEU_KB --region SUA_REGIAO --arquivo eval/perguntas.json`. A `fonte_esperada` é o nome do arquivo dentro de `docs/`; os PDFs aprovados pela demo ficam com o nome do UUID gerado no upload.
- `lambda/auto_sync/`: Lambda que sincroniza a Knowledge Base quando um arquivo entra ou sai de `docs/`. É o passo seguinte da demo, que sincroniza na mão.

## Decisões que valem saber

- O upload usa POST assinado, e não PUT, porque a policy do POST aceita condições de tipo, tamanho e chave que o S3 confere antes de gravar.
- A chave do arquivo é gerada no servidor, em `pendentes/`. A Knowledge Base só lê `docs/`, então nada entra na base sem aprovação.
- O S3 só confere o `Content-Type` declarado. Por isso, ao abrir um arquivo para conferir, o backend lê os primeiros bytes e recusa o que não começa com `%PDF-`.
- A autorização de upload continua valendo por alguns minutos, então quem enviou poderia trocar o arquivo depois que você conferiu. Por isso "Ver" guarda uma cópia em `revisao/`, onde a autorização não grava, e a aprovação publica essa cópia.
- Há um teto de autorizações por minuto para todo mundo e, com `CLIENT_IP_HEADER=cf-connecting-ip` no Cloudflare Tunnel, um limite por pessoa. Sem esse header o limite por pessoa fica desligado, porque atrás do túnel todo mundo chega com o mesmo IP.
- O lifecycle do bucket apaga `pendentes/` depois de um dia.

## Apagar tudo

Pare o backend, esvazie o bucket do output `DocsBucket` e apague a stack no CloudFormation. Confira o Cost Explorer no dia seguinte.
