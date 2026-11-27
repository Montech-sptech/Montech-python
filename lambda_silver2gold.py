import os
import json
from io import BytesIO
from urllib.parse import unquote_plus

import boto3

from etl_silver2gold import (
    parse_silver_csv,
    series_for_day,
    diagnostico_from_series,
    atual_from_df,
    processos_from_list,
)

s3 = boto3.client('s3')


def _safe_json_dumps(obj):
    # convert numpy types via default=str would produce strings; instead ensure Python types
    return json.dumps(obj, ensure_ascii=False, allow_nan=False).encode('utf-8')


def identificar_destino(chave, prefixo_silver):
    partes = chave.split('/')
    # expected: PREFIXO_SILVER/empresa/aeroporto/token/data/filename
    if len(partes) < 6 or partes[0] != prefixo_silver:
        raise ValueError(f'Caminho fora do padrão: {chave}')
    _, empresa, aeroporto, token, data = partes[:5]
    return empresa, aeroporto, token, data


def _s3_get_json(bucket, key):
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
    except s3.exceptions.NoSuchKey:
        return None
    data = resp['Body'].read()
    return json.loads(data.decode('utf-8'))


def _s3_put_json(bucket, key, obj):
    body = _safe_json_dumps(obj)
    s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType='application/json; charset=utf-8')


def lambda_handler(event, context):
    bucket_env = os.environ['NOME_BUCKET']
    prefixo_silver = os.environ['PREFIXO_SILVER'].strip('/')
    prefixo_gold = os.environ['PREFIXO_GOLD'].strip('/')

    records = event.get('Records', [])
    results = []

    for r in records:
        if r.get('eventSource') != 'aws:s3' or not r.get('eventName', '').startswith('ObjectCreated:'):
            continue
        bucket = r['s3']['bucket']['name']
        key = unquote_plus(r['s3']['object']['key'])
        if bucket != bucket_env:
            raise ValueError('Bucket mismatch')

        # process only silver prefix
        try:
            empresa, aeroporto, token, data = identificar_destino(key, prefixo_silver)
        except ValueError:
            continue

        # download silver csv to memory
        resp = s3.get_object(Bucket=bucket, Key=key)
        content = resp['Body'].read()
        # write to temp file-like buffer for parse_silver_csv
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(delete=False, suffix='.csv') as tmp:
            tmp.write(content)
            tmp_path = tmp.name

        df = parse_silver_csv(tmp_path)

        # for every local date in df, update series file
        dates = sorted({d.isoformat() for d in df['TimeStamp'].dt.tz_convert('America/Sao_Paulo').dt.date})
        for date_local in dates:
            # slice df for that local date
            mask = df['TimeStamp'].dt.tz_convert('America/Sao_Paulo').dt.date.astype(str) == date_local
            df_day = df[mask]
            series = series_for_day(df_day, aeroporto=aeroporto, servidor=token, date_local=date_local)

            # merge with existing series file if exists
            series_key = f"{prefixo_gold}/series/{empresa}/{aeroporto}/{token}/{date_local}.json"
            existing = None
            try:
                resp = s3.get_object(Bucket=bucket, Key=series_key)
            except Exception:
                existing = None
            else:
                existing = json.loads(resp['Body'].read().decode('utf-8'))

            if existing is None:
                merged = series
            else:
                # merge arrays per resource
                merged = existing.copy()
                merged_series = merged.get('series', {})
                for res, arr in series['series'].items():
                    if not arr:
                        continue
                    exist_arr = merged_series.get(res, []) or []
                    # ensure length
                    if len(exist_arr) < len(arr):
                        exist_arr.extend([None] * (len(arr) - len(exist_arr)))
                    for i, v in enumerate(arr):
                        if v is not None:
                            exist_arr[i] = v
                    merged_series[res] = exist_arr
                merged['series'] = merged_series

            # write merged series
            _s3_put_json(bucket, series_key, merged)

            # compute diagnostico using merged series and reference = last timestamp UTC ms
            last_ts = df_day['TimeStamp'].max()
            referencia_ms = int(last_ts.tz_convert('UTC').timestamp() * 1000)
            diagnostico = diagnostico_from_series(merged, referencia_ms)
            diag_key = f"{prefixo_gold}/diagnostico/{empresa}/{aeroporto}/{token}.json"
            _s3_put_json(bucket, diag_key, diagnostico)

        # atual snapshot (use full df, ultimaColeta is last ts)
        atual = atual_from_df(df)
        atual_key = f"{prefixo_gold}/atual/{empresa}/{aeroporto}/{token}.json"
        _s3_put_json(bucket, atual_key, atual)

        # processes: try to read processes-silver JSON in same folder as components
        # derive processes key from silver key by replacing '-components-silver.csv' with '-processos-silver.json'
        proc_key_guess = key.replace('-components-silver.csv', '-processos-silver.json')
        try:
            resp = s3.get_object(Bucket=bucket, Key=proc_key_guess)
        except Exception:
            processes = None
        else:
            processes = json.loads(resp['Body'].read().decode('utf-8'))

        if processes:
            proc_obj = processos_from_list(processes)
            proc_key = f"{prefixo_gold}/processos/{empresa}/{aeroporto}/{token}.json"
            _s3_put_json(bucket, proc_key, proc_obj)

        results.append({'silver': key})

    return {'processed': len(results), 'details': results}
