import base64
import json
import os
from urllib.parse import unquote

import boto3

s3 = boto3.client("s3")

PREFIXO_BRONZE_PADRAO = "01-bronze"


def _resposta(status, mensagem):
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({"mensagem": mensagem}),
    }


def lambda_handler(event, context):
    # Aceita NOME_BUCKET (padrão do projeto) ou BUCKET_NAME (compatibilidade).
    bucket = os.environ.get("NOME_BUCKET") or os.environ.get("BUCKET_NAME")
    if not bucket:
        return _resposta(500, "Variável NOME_BUCKET não configurada.")

    prefixo = os.environ.get("PREFIXO_BRONZE", PREFIXO_BRONZE_PADRAO).strip("/")

    metodo = event.get("requestContext", {}).get("http", {}).get("method")
    if metodo != "POST":
        return _resposta(405, "Use POST.")

    params = event.get("queryStringParameters") or {}
    chave = unquote(params.get("filename", "")).strip()

    if (
        not chave.startswith(prefixo + "/")
        or ".." in chave
        or not chave.endswith(".csv")
    ):
        return _resposta(400, "filename inválido.")

    corpo = event.get("body") or ""
    if event.get("isBase64Encoded"):
        conteudo = base64.b64decode(corpo)
    else:
        conteudo = corpo.encode("utf-8")

    if not conteudo:
        return _resposta(400, "Corpo vazio.")

    try:
        s3.put_object(
            Bucket=bucket,
            Key=chave,
            Body=conteudo,
            ContentType="text/csv",
        )
    except Exception as erro:
        print(f"Erro ao gravar no S3: {erro}")
        return _resposta(500, "Falha ao gravar no S3.")

    print(f"CSV gravado: s3://{bucket}/{chave} ({len(conteudo)} bytes)")
    return _resposta(200, f"Gravado: {chave}")
