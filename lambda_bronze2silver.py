import os
import json
import tempfile
from io import BytesIO
from urllib.parse import unquote_plus

import boto3

from etl_bronze2silver import bronze_to_silver

s3 = boto3.client('s3')


def _upload_file(local_path, bucket, key):
    with open(local_path, 'rb') as f:
        s3.put_object(Bucket=bucket, Key=key, Body=f.read(), ContentType='application/octet-stream')


def identificar_destino(chave):
    partes = chave.split('/')
    if len(partes) < 6 or partes[0] != os.environ.get('PREFIXO_BRONZE', '01-bronze'):
        raise ValueError(f'Caminho fora do padrão: {chave}')
    _, empresa, aeroporto, token, data = partes[:5]
    return empresa, aeroporto, token


def lambda_handler(event, context):
    bucket_env = os.environ['NOME_BUCKET']
    prefixo_silver = os.environ['PREFIXO_SILVER'].strip('/')

    records = event.get('Records', [])
    results = []

    for r in records:
        if r.get('eventSource') != 'aws:s3' or not r.get('eventName', '').startswith('ObjectCreated:'):
            continue
        bucket = r['s3']['bucket']['name']
        key = unquote_plus(r['s3']['object']['key'])
        if bucket != bucket_env:
            raise ValueError('Bucket mismatch')

        empresa, aeroporto, token = identificar_destino(key)

        # download bronze object to /tmp
        _, fname = os.path.split(key)
        local_br = os.path.join(tempfile.gettempdir(), fname)
        obj = s3.get_object(Bucket=bucket, Key=key)
        with open(local_br, 'wb') as f:
            f.write(obj['Body'].read())

        # run bronze_to_silver, writing into tmp dir
        silver_root = os.path.join(tempfile.gettempdir(), '02-silver')
        os.makedirs(silver_root, exist_ok=True)
        out = bronze_to_silver(local_br, silver_root=silver_root)

        # upload produced silver files to S3 under PREFIXO_SILVER
        rel_root = silver_root
        uploaded = []
        for k, path in out.items():
            if path is None:
                continue
            rel = os.path.relpath(path, rel_root).replace('\\', '/')
            dest_key = f"{prefixo_silver}/{rel}"
            _upload_file(path, bucket, dest_key)
            uploaded.append(dest_key)

        results.append({'bronze': key, 'uploaded': uploaded})

    return {'processed': len(results), 'details': results}
