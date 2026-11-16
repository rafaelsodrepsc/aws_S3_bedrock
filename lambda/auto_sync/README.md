# Sincronização pelo S3

No console do Lambda, crie uma função com runtime Python 3.12 na mesma região
do bucket e da Knowledge Base. Cole o código de `handler.py` em um arquivo com
esse nome, configure o handler como `handler.handler` e clique em Deploy.
Em Configuration > Environment variables, defina `KB_ID` e `DATA_SOURCE_ID`.
A fonte de dados da KB deve apontar para o prefixo `docs/` do bucket.

Use uma role de execução com `AWSLambdaBasicExecutionRole` para os logs.
Adicione esta permissão para iniciar a ingestão, substituindo região, conta e ID:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": "bedrock:StartIngestionJob",
    "Resource": "arn:aws:bedrock:us-east-1:123456789012:knowledge-base/KB12345678"
  }]
}
```

A permissão usa o [ARN da Knowledge Base](https://docs.aws.amazon.com/bedrock/latest/userguide/kb-direct-ingestion-prereq.html).
A role da Lambda não lê os documentos; a role da KB mantém o acesso ao S3.

No bucket, abra Properties > Event notifications > Create event notification.
Escolha todos os eventos de criação (`s3:ObjectCreated:*`) e remoção
(`s3:ObjectRemoved:*`), prefixo `docs/` e a função Lambda como destino.
Salve a [notificação](https://docs.aws.amazon.com/AmazonS3/latest/userguide/enable-event-notifications.html)
e confira a permissão da função para o S3 invocá-la.

Uploads em rajada geram vários eventos; `ConflictException` é esperado quando
já existe uma ingestão rodando. O handler registra isso e retorna sem falhar.
O sync é incremental: o próximo job inclui as alterações pendentes.
Este handler não agenda esse próximo job; se não houver outro evento depois,
inicie uma sincronização manual para recolher arquivos que ficaram de fora.
