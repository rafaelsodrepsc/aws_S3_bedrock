# Do bucket à IA

Código da demo do minicurso "Do bucket à IA com Amazon S3 e Amazon Bedrock", de Rafael Sodré e João Aguiar, na UEPB, em novembro de 2026.

A turma envia PDFs pelo celular direto para um bucket S3. Os arquivos aprovados são sincronizados com uma Knowledge Base do Bedrock, que responde perguntas sobre eles e indica a fonte. O arquivo não passa pelo backend, que só assina a autorização do envio.

## Testes

O S3 e o Bedrock são simulados, então não precisa de conta na AWS.

```sh
uv sync
uv run pytest -q
```

## Subindo na sua conta

Crie um alerta no AWS Budgets antes. O WAF custa cerca de US$ 7 por mês mesmo sem tráfego.

Primeiro suba `infra/stack.yaml` pelo console do CloudFormation, na região do seu projeto. Os parâmetros estão explicados em `infra/README.md`.

Depois gere o `.env` (aqui usando `aws login`):

```sh
export AWS_PROFILE=seu-perfil AWS_REGION=sua-regiao
uv run python scripts/env_from_stack.py nome-da-stack > .env
echo "ADMIN_TOKEN=$(python -c 'import secrets; print(secrets.token_urlsafe(32))')" >> .env
```

E publique o site:

```sh
AWS_PROFILE=seu-perfil scripts/deploy_web.sh
```

Isso cria a stack `minicurso-waf` (em `us-east-1`, porque o WAF do CloudFront só funciona lá) e a `minicurso-web`, com a Lambda e o CloudFront. Também troca o `AllowedOrigin` da stack principal pelo endereço do site, que aparece no final. Se quiser outros nomes de stack, use `MAIN_STACK`, `WEB_STACK` e `WAF_STACK`.

Se alguém abusar do site, `scripts/site.sh off` desliga a Lambda na hora e `scripts/site.sh on` liga de novo.

## Rodando no notebook

A identidade que roda o backend precisa da policy que sai no output `BackendPolicyArn`.

```sh
set -a && source .env && set +a
export AWS_PROFILE=seu-perfil
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Para testar o upload, o `AllowedOrigin` da stack precisa ser o endereço que você abre no navegador, como `http://localhost:8000`. Depois de um `deploy_web.sh` ele aponta para o CloudFront, então troque de volta. O `/admin` e o `/chat` funcionam sem isso.

Se usar um túnel, escolha um com endereço fixo. O Quick Tunnel do Cloudflare muda a cada execução e quebra o QR code e o CORS. Com o túnel nomeado, `CLIENT_IP_HEADER=cf-connecting-ip` limita os envios por pessoa, desde que ninguém alcance a porta 8000 por fora dele.

## Telas

`/` é a página do QR code, onde se envia o PDF de até 5 MB. Com `/?ttl=10` a autorização vence em 10 segundos, para demonstrar o erro de expiração.

`/admin` é a tela de moderação e de sincronização. A lista de pendentes começa escondida, para não aparecer no telão, e um arquivo só pode ser aprovado depois de aberto em "Ver".

`/chat` responde com a fonte. O "Só a busca" mostra os trechos e os scores, sem o modelo escrever nada. Se a stack tiver um segundo modelo, dá para trocar entre os dois.

O `/admin` e o `/chat` pedem o `ADMIN_TOKEN`, que fica só na aba do navegador. Na lateral aparecem os documentos aprovados, e o "×" exclui um deles e já sincroniza. Enquanto a sincronização roda, uma pergunta pode não achar o documento novo, e o `/chat` avisa.

## Decisões

O upload usa POST assinado porque ele aceita condições que o S3 confere antes de gravar: a chave, o tipo e o tamanho. O PUT assinado não limita o tamanho. A chave é gerada no servidor e sempre cai em `pendentes/`. Como a Knowledge Base só lê `docs/`, nada entra sem aprovação.

O S3 só olha o `Content-Type` declarado, então o "Ver" lê o começo do arquivo e recusa o que não começa com `%PDF-`. Ele também guarda uma cópia em `revisao/`, e é ela que a aprovação publica. A autorização de envio ainda vale por alguns minutos, e sem a cópia o arquivo poderia ser trocado depois da conferência. O que sobra em `pendentes/` e `revisao/` é apagado depois de um dia.

O limite por IP fica no WAF e é alto (10 mil pedidos em 5 minutos), porque a turma inteira costuma sair pelo mesmo IP do Wi-Fi. No app fica um teto de autorizações por minuto, contado em cada instância da Lambda.

A URL da Lambda só aceita pedidos assinados pelo CloudFront, então não há acesso por fora do WAF. Por isso os POST precisam levar o SHA-256 do corpo, calculado em `static/signed-fetch.js`. O CloudFront guarda as páginas por 5 minutos e nunca guarda `/admin/*`.

O prompt do `/ask` é uma tradução do padrão do Bedrock. Com o original, o modelo respondia em inglês quando não achava a resposta.

## Outras pastas

`eval/` mede se a busca acha o arquivo certo. Copie `eval/perguntas.example.json`, escreva suas perguntas e rode:

```sh
uv run python -m eval.run --kb-id SEU_KB --region SUA_REGIAO --arquivo eval/perguntas.json
```

Em `fonte_esperada` vai o nome do arquivo em `docs/`, que nos PDFs aprovados pela demo é o UUID gerado no upload.

`lambda/auto_sync/` sincroniza sozinha quando um arquivo entra ou sai de `docs/`. Na aula a sincronização é manual.

## Apagando tudo

Apague a stack `minicurso-web` e depois a `minicurso-waf`, em `us-east-1`. Esvazie o bucket do output `DocsBucket`, apague a stack principal e confira o Cost Explorer no dia seguinte.
