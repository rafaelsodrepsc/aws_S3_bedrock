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

Uploads em rajada geram vários eventos, e `ConflictException` é esperado quando
já existe uma ingestão rodando. Nesse caso o handler falha de propósito: o S3
invoca a Lambda de forma assíncrona, e a Lambda repete a invocação até 2 vezes,
com alguns minutos de espera. Como o sync é incremental, a nova tentativa pega o
arquivo que o job anterior não viu. Por isso aparecem erros nas métricas da
função durante uma rajada; eles não são problema. Se as tentativas acabarem e o
arquivo ainda faltar, rode uma sincronização manual pela página `/admin`.
