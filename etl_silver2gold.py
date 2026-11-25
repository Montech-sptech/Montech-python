"""
Skeleton do ETL silver2gold.
Objetivo: receber arquivos do silver, converter para JSON e preparar arrays
e pontos de extensão para cálculos adicionais (a serem definidos pelo
responsável). Aqui fornecemos o esqueleto e funções de conversão para uso
posterior pela Lambda ou testes.
"""
import json
from datetime import datetime, timezone
import pandas as pd
import math


def silver_to_gold_components(silver_components_path: str) -> dict:
    """
    Converte um components-silver CSV para um JSON-ready dict. Não implementa
    fórmulas específicas — fornece pontos de extensão:
    - to_compute_averages: lista de funções que recebem o DataFrame e retornam pares nome->valor
    - to_compute_histories: lista de funções que podem agregar dados históricos

    Retorna um dicionário com chaves: 'record' (os dados limpos), 'calc_averages', 'calc_histories'
    """
    df = pd.read_csv(silver_components_path, sep=';', encoding='utf-8')

    # record: todos os registros tratados (mantemos como array)
    records = df.to_dict(orient='records')

    # calc_averages: calcula médias simples para colunas numéricas detectadas
    calc_averages = []
    for col in df.columns:
        if col in ('TimeStamp', 'IdAeroporto', 'original_records', 'partial_minute'):
            continue
        # ignora columns de imputação (sufixo _imputed/_imputed_method) e MB/s derived markers
        if col.endswith('_imputed') or col.endswith('_imputed_method') or col.endswith('_MB_s'):
            continue
        # tenta converter para numérico
        try:
            series = pd.to_numeric(df[col], errors='coerce')
        except Exception:
            continue
        if series.notna().any():
            avg = series.mean()
            calc_averages.append({'nome': f'mean_{col}', 'valor': float(avg)})

    # calc_histories: esqueleto vazio (pontos de extensão)
    calc_histories = []

    # custom calculations: funções específicas que o responsável poderá
    # implementar/alterar. Exemplo: média do UsoCPU_Geral em uma função nomeada.
    custom_calculations = []
    try:
        cpu_calc = compute_cpu_mean(df)
        if cpu_calc is not None:
            custom_calculations.append(cpu_calc)
    except Exception:
        # não falhar o ETL por causa de cálculo custom
        pass

    return {
        'records': records,
        'calc_averages': calc_averages,
        'calc_histories': calc_histories,
        'custom_calculations': custom_calculations,
    }


def compute_cpu_mean(df: pd.DataFrame) -> dict:
    """
    Cálculo exemplo (ponto de extensão) — média simples do UsoCPU_Geral.
    A responsável pode substituir essa função pela lógica real que desejar.

    Retorna dict {'nome': 'cpu_mean', 'valor': float}
    """
    if 'UsoCPU_Geral' not in df.columns:
        return None
    series = pd.to_numeric(df['UsoCPU_Geral'], errors='coerce')
    if not series.notna().any():
        return {'nome': 'cpu_mean', 'valor': None}
    return {'nome': 'cpu_mean', 'valor': float(series.mean())}


def silver_to_gold_processes(silver_processes_path: str) -> dict:
    # abre o JSON de processos e devolve como lista
    with open(silver_processes_path, 'r', encoding='utf-8') as f:
        lista = json.load(f)
    return {'processes': lista}


# Helpers to build the new gold artefacts (series, diagnostico, atual, processos)


def _to_epoch_ms_utc(ts: pd.Timestamp) -> int:
    # ts is timezone-aware or naive in local Sao_Paulo; ensure UTC epoch ms
    if ts.tzinfo is None:
        # assume America/Sao_Paulo local naive
        import pytz
        tz = pytz.timezone('America/Sao_Paulo')
        ts = tz.localize(ts)
    ts_utc = ts.tz_convert(timezone.utc)
    return int(ts_utc.value // 1_000_000)


def parse_silver_csv(path: str) -> pd.DataFrame:
    """Lê um CSV silver gerado pelo bronze_to_silver e devolve DataFrame com
    TimeStamp timezone-localized para America/Sao_Paulo (tz-aware).
    """
    df = pd.read_csv(path, sep=';', encoding='utf-8')
    if 'TimeStamp' not in df.columns:
        raise ValueError('TimeStamp missing')
    # parse as naive then localize to America/Sao_Paulo
    df['TimeStamp'] = pd.to_datetime(df['TimeStamp'], format="%Y-%m-%d %H:%M:%S", errors='coerce')
    import pytz
    tz = pytz.timezone('America/Sao_Paulo')
    df['TimeStamp'] = df['TimeStamp'].dt.tz_localize(tz)
    return df


RESOURCE_COLUMN_CANDIDATES = {
    'cpu': ['UsoCPU_Geral'],
    'temperatura': ['Temperatura_CPU', 'Temperatura', 'TemperaturaCPU', 'Temperatura_Cpu'],
    'ram': ['UsoRAM'],
    'disco': ['UsoDisco'],
    'swap': ['Swap_In', 'Swap_Out', 'Swap'],
    'rede': ['Rede_Bytes_Enviados_MB_s', 'Rede_Bytes_Recebidos_MB_s', 'Rede_Bytes_Enviados'],
}


def _choose_column(cols, candidates):
    for c in candidates:
        if c in cols:
            return c
    return None


def series_for_day(df: pd.DataFrame, aeroporto: int, servidor: str, date_local: str) -> dict:
    """Gera a estrutura de series para um dia (date_local no formato 'YYYY-MM-DD').
    A saída segue o formato descrito no AGENTS.md.
    """
    # ensure TimeStamp is tz-aware (America/Sao_Paulo)
    if df['TimeStamp'].dt.tz is None:
        df = df.copy()
        df['TimeStamp'] = df['TimeStamp'].dt.tz_localize('America/Sao_Paulo')

    # day start in local tz
    import pytz
    tz = pytz.timezone('America/Sao_Paulo')
    day_local = pd.Timestamp(date_local, tz=tz)
    day_start_local = day_local.replace(hour=0, minute=0, second=0, microsecond=0)
    # convert to UTC epoch ms
    inicio_ms = int(day_start_local.tz_convert(timezone.utc).value // 1_000_000)

    passo = 10000

    # build empty series dict for resources
    series_out = {}
    cols = set(df.columns)
    # compute index for each row
    df = df.copy()
    # timestamps in UTC epoch ms
    # use astype to get int64 representation safely
    df['ts_utc_ms'] = (df['TimeStamp'].dt.tz_convert(timezone.utc).astype('int64') // 1_000_000)
    df['pos'] = ((df['ts_utc_ms'] - inicio_ms) // passo).astype('Int64')

    max_pos = -1
    for res, candidates in RESOURCE_COLUMN_CANDIDATES.items():
        col = _choose_column(cols, candidates)
        if col is None:
            # produce empty series (will be filled with None)
            series_out[res] = []
            continue

        arr = {}
        for _, row in df.dropna(subset=['pos']).iterrows():
            pos = int(row['pos'])
            if pos < 0 or pos > 8639:
                continue
            val = row.get(col)
            if pd.isna(val):
                continue
            # store as python float/int
            if isinstance(val, (int, float)) and not (isinstance(val, float) and math.isnan(val)):
                arr[pos] = float(val)
            else:
                try:
                    arr[pos] = float(val)
                except Exception:
                    continue
            if pos > max_pos:
                max_pos = pos

        if max_pos >= 0:
            out_list = [None] * (max_pos + 1)
            for p, v in arr.items():
                out_list[p] = v
            series_out[res] = out_list
        else:
            series_out[res] = []

    result = {
        'aeroporto': int(aeroporto),
        'servidor': servidor,
        'data': date_local,
        'inicio': inicio_ms,
        'passoMs': passo,
        'geradoEm': int(pd.Timestamp.utcnow().timestamp() * 1000),
        'series': series_out,
    }

    return result


def _compute_window_stats(values):
    # values: list of floats (non-empty)
    import numpy as np
    arr = np.array(values, dtype=float)
    # try numpy.percentile with method compatibility
    try:
        # numpy >= 1.22 uses method=
        p10, med, p90 = np.percentile(arr, [10, 50, 90], method='linear')
    except TypeError:
        # older numpy uses interpolation=
        p10, med, p90 = np.percentile(arr, [10, 50, 90], interpolation='linear')
    mx = float(np.max(arr))
    cnt = int(arr.size)
    return float(p10), float(med), float(p90), mx, cnt


RESOLUTION_CONFIG = {
    '1min': (60_000, 60),
    '5min': (5 * 60_000, 6 * 12),  # 72 windows for 6 hours
    '15min': (15 * 60_000, 24 * 4),
    '30min': (30 * 60_000, 3 * 24 * 2),
    '45min': (45 * 60_000, int(7 * 24 * 60 / 45)),
}


def diagnostico_from_series(series_dict: dict, referencia_ms: int) -> dict:
    """Gera o objeto diagnostico (conteúdo completo por recurso) a partir da série
    e do instante de referência (epoch ms UTC).
    """
    recursos_out = {}
    inicio = int(series_dict['inicio'])
    passo = int(series_dict['passoMs'])

    for res, arr in series_dict['series'].items():
        resolucoes = {}
        for name, (passoMs, count_windows) in RESOLUTION_CONFIG.items():
            linhas = []
            # last window aligned
            last_aligned = (referencia_ms // passoMs) * passoMs
            # compute first window start
            first_start = last_aligned - (count_windows - 1) * passoMs
            for i in range(count_windows):
                start = first_start + i * passoMs
                # determine indices in series array
                idx0 = int((start - inicio) // passo) if (start - inicio) // passo >= 0 else None
                idx1 = int(((start + passoMs - 1) - inicio) // passo) if ((start + passoMs - 1) - inicio) // passo >= 0 else None
                values = []
                if idx0 is not None and arr:
                    # iterate indices from idx0..idx1
                    max_idx = len(arr) - 1
                    low = max(0, idx0)
                    high = min(max_idx, idx1) if idx1 is not None else max_idx
                    for p in range(low, high + 1):
                        v = arr[p]
                        if v is not None:
                            values.append(v)

                if values:
                    p10, med, p90, mx, cnt = _compute_window_stats(values)
                    linhas.append([start, p10, med, p90, mx, cnt])
                # windows without values are omitted

            if linhas:
                resolucoes[name] = {
                    'passoMs': passoMs,
                    'colunas': ['inicio', 'p10', 'mediana', 'p90', 'maximo', 'quantidade'],
                    'linhas': linhas,
                }
        recursos_out[res] = {'resolucoes': resolucoes}

    out = {
        'geradoEm': int(pd.Timestamp.utcnow().timestamp() * 1000),
        'referencia': int(referencia_ms),
        'aeroporto': int(series_dict['aeroporto']),
        'servidor': series_dict['servidor'],
        'recursos': recursos_out,
    }

    return out


def atual_from_df(df: pd.DataFrame) -> dict:
    """Gera o snapshot atual a partir do DataFrame silver do minuto.
    Retorna dicionário com geradoEm, ultimaColeta, janelaMs=60000, leiturasNaJanela e valores por recurso.
    """
    # ensure tz-aware
    if df['TimeStamp'].dt.tz is None:
        df = df.copy()
        df['TimeStamp'] = df['TimeStamp'].dt.tz_localize('America/Sao_Paulo')

    last_ts = df['TimeStamp'].max()
    # use timestamp() to get seconds since epoch for a single Timestamp
    last_ms = int(last_ts.tz_convert(timezone.utc).timestamp() * 1000)
    window_start = last_ts - pd.Timedelta(milliseconds=60000) + pd.Timedelta(seconds=0)
    window_df = df[df['TimeStamp'] > window_start]

    valores = {}
    for res, candidates in RESOURCE_COLUMN_CANDIDATES.items():
        col = _choose_column(set(df.columns), candidates)
        if col is None:
            valores[res] = None
            continue
        ser = pd.to_numeric(window_df[col], errors='coerce')
        if ser.dropna().empty:
            valores[res] = None
        else:
            # for rede we expect already MB/s columns
            valores[res] = float(ser.mean())

    out = {
        'geradoEm': int(pd.Timestamp.utcnow().timestamp() * 1000),
        'ultimaColeta': last_ms,
        'janelaMs': 60000,
        'leiturasNaJanela': int(len(window_df)),
        'valores': valores,
    }

    return out


def processos_from_list(processes: list) -> dict:
    """Dado uma lista de processos (lista de dicts), retorna o objeto a ser salvo
    em gold/processos com capturadoEm e até 20 processos por CPU desc.
    """
    if not processes:
        return {'capturadoEm': int(pd.Timestamp.utcnow().timestamp() * 1000), 'processos': []}

    # try to extract timestamp per record, else use now
    times = []
    for p in processes:
        ts = p.get('TimeStamp') or p.get('capturadoEm')
        if ts:
            try:
                t = pd.to_datetime(ts)
                times.append(t)
            except Exception:
                times.append(None)
        else:
            times.append(None)

    # choose latest record time
    valid_times = [t for t in times if t is not None]
    if valid_times:
        captured = valid_times[-1]
        if captured.tzinfo is None:
            import pytz
            captured = captured.tz_localize('America/Sao_Paulo')
        captured_ms = int(captured.tz_convert(timezone.utc).timestamp() * 1000)
    else:
        captured_ms = int(pd.Timestamp.utcnow().timestamp() * 1000)

    # normalize fields and sort by cpu descending
    norm = []
    for p in processes:
        entry = {}
        entry['pid'] = int(p.get('pid')) if p.get('pid') not in (None, '') else None
        entry['nome'] = p.get('nome')
        entry['usuario'] = p.get('usuario') if 'usuario' in p else None
        entry['cpu'] = float(p.get('cpu')) if p.get('cpu') not in (None, '') else None
        entry['mem'] = float(p.get('mem')) if p.get('mem') not in (None, '') else None
        entry['redeKBs'] = float(p.get('redeKBs')) if p.get('redeKBs') not in (None, '') else None
        entry['swapMB'] = float(p.get('swapMB')) if p.get('swapMB') not in (None, '') else None
        entry['status'] = p.get('status') if 'status' in p else None
        norm.append(entry)

    norm_sorted = sorted(norm, key=lambda x: (-(x['cpu'] or 0)))[:20]

    return {'capturadoEm': captured_ms, 'processos': norm_sorted}
