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
2. Gere o `.env`. Se você entra com `aws login`, informe o perfil e a região:

   ```sh
   export AWS_PROFILE=seu-perfil AWS_REGION=sua-regiao
   uv run python scripts/env_from_stack.py nome-da-stack > .env
   echo "ADMIN_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
   ```

3. Publique o site:

   ```sh
   AWS_PROFILE=seu-perfil scripts/deploy_web.sh
   ```

   O script sobe o WAF na stack `minicurso-waf`, em `us-east-1`, porque o WAF de um CloudFront só pode ficar lá. Depois sobe a Lambda com o CloudFront na stack `minicurso-web` e troca o `AllowedOrigin` da stack principal pelo endereço do CloudFront. No fim, imprime o endereço do site. O `ADMIN_TOKEN` vem do `.env`, e os nomes das stacks mudam com `MAIN_STACK`, `WEB_STACK` e `WAF_STACK`.

`scripts/site.sh off` desliga a Lambda na hora e `scripts/site.sh on` liga de novo. Serve para cortar abuso sem apagar nada.

### Rodar no notebook

Serve para desenvolver. Associe a policy do output `BackendPolicyArn` à identidade que vai rodar o backend e suba o servidor:

```sh
set -a && source .env && set +a
export AWS_PROFILE=seu-perfil
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Para o upload funcionar, o `AllowedOrigin` da stack precisa ser a origem da página, como `http://localhost:8000` ou a URL HTTPS de um túnel. Depois de um `deploy_web.sh` ele aponta para o CloudFront, então troque de volta antes de testar o upload local. `/admin` e `/chat` não dependem do CORS.

Num túnel, use um endereço fixo. O Quick Tunnel do Cloudflare muda de endereço a cada execução e quebra o QR code e o CORS. O túnel conecta localmente, então o backend não precisa escutar na rede. Isso também importa para o `CLIENT_IP_HEADER`: o header só é confiável se ninguém alcançar a porta 8000 sem passar pelo Cloudflare.

## Páginas

- `/` recebe o PDF (até 5 MB). É o endereço do QR code. Com `?ttl=10` dá para mostrar a autorização expirando.
- `/admin` lista os pendentes, abre cada PDF numa aba ("Ver") para você conferir, aprova ou rejeita, e dispara a sincronização. Só dá para aprovar depois de abrir o arquivo. A lista só aparece depois de clicar em "Mostrar lista", para você conferir antes de projetar.
- `/chat` faz perguntas com fonte, mostra só a busca com os scores e, se a stack tiver um segundo modelo, troca entre os dois.

`/admin` e `/chat` têm uma barra lateral com os documentos que estão na base, pelo nome original do arquivo. O "×" exclui o documento de `docs/` e dispara a sincronização para a base esquecer dele.

As duas últimas pedem o `ADMIN_TOKEN`, que fica guardado só na aba do navegador.

O status da sincronização mostra há quanto tempo ela roda. Se você trocar de página ou recarregar, `/admin` e `/chat` retomam o acompanhamento, e o `/chat` avisa enquanto a base está sendo atualizada: uma pergunta feita nesse meio-tempo pode não achar o documento novo.

## O que mais tem aqui

- `eval/`: mede se a busca acha o arquivo certo (acerto em k e MRR). Copie `eval/perguntas.example.json`, escreva suas perguntas e rode `uv run python -m eval.run --kb-id SEU_KB --region SUA_REGIAO --arquivo eval/perguntas.json`. A `fonte_esperada` é o nome do arquivo dentro de `docs/`; os PDFs aprovados pela demo ficam com o nome do UUID gerado no upload.
- `lambda/auto_sync/`: Lambda que sincroniza a Knowledge Base quando um arquivo entra ou sai de `docs/`. É o passo seguinte da demo, que sincroniza na mão.

## Decisões que valem saber

- O upload usa POST assinado, e não PUT, porque a policy do POST aceita condições de tipo, tamanho e chave que o S3 confere antes de gravar.
- A chave do arquivo é gerada no servidor, em `pendentes/`. A Knowledge Base só lê `docs/`, então nada entra na base sem aprovação.
- O S3 só confere o `Content-Type` declarado. Por isso, ao abrir um arquivo para conferir, o backend lê os primeiros bytes e recusa o que não começa com `%PDF-`.
- A autorização de upload continua valendo por alguns minutos, então quem enviou poderia trocar o arquivo depois que você conferiu. Por isso "Ver" guarda uma cópia em `revisao/`, onde a autorização não grava, e a aprovação publica essa cópia.
- Há um teto de autorizações por minuto para todo mundo. No site, o limite por IP fica no WAF, em 10 mil pedidos a cada 5 minutos, porque a plateia costuma sair pelo mesmo IP do Wi-Fi do evento e atrás do CloudFront o IP que chega à Lambda é o dele. No túnel, `CLIENT_IP_HEADER=cf-connecting-ip` liga um limite por pessoa no próprio app.
- A URL da Lambda usa `AWS_IAM`, e só o CloudFront assina pedidos para ela (OAC). Um acesso direto volta 403 sem rodar a função, então não ocupa concorrência nem passa por fora do WAF. Com OAC, o CloudFront exige o SHA-256 do corpo nos POST; o `static/signed-fetch.js` calcula e envia o header `x-amz-content-sha256`.
- O CloudFront guarda as páginas e os estáticos por 5 minutos e nunca guarda `/admin/*`, onde estão os GET que pedem token. O deploy invalida o cache.
- O lifecycle do bucket apaga `pendentes/` e `revisao/` depois de um dia.
- O `/ask` usa um prompt em português, traduzido do padrão do Bedrock. Com o padrão em inglês, o modelo respondia em inglês quando não achava a resposta.

## Apagar tudo

Apague a stack `minicurso-web` e depois a `minicurso-waf`, em `us-east-1`. O WAF cobra uns US$ 7 por mês mesmo sem tráfego. Depois esvazie o bucket do output `DocsBucket` e apague a stack principal. Confira o Cost Explorer no dia seguinte.
