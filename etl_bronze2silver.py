import os
import json
import pandas as pd
from datetime import datetime

# Bronze -> Silver ETL
# Esta implementação lê um CSV de components gerado pelo Script_Extracao
# (um arquivo por minuto contendo até 6 registros), limpa e trata dados
# ausentes por variável, e grava um arquivo no nível silver com os mesmos
# registros (resolução de 10s). Mantém os 6 registros por minuto (quando
# presentes) e adiciona colunas derivadas como taxas MB/s para contadores.


def detect_type(nome_coluna: str) -> str:
    n = nome_coluna.lower()
    if "process" in n or "processo" in n:
        return "process_list"
    if any(k in n for k in ("cpu", "uso", "ram", "disco", "temperatura", "temp")):
        # CPU, RAM, disco e temperatura tratados como gauges/percentuais
        if "temper" in n or "temp" in n:
            return "temperature"
        return "gauge"
    if any(k in n for k in ("bytes", "read", "write", "rede", "pacote", "swap", "disco_")):
        # counters/accumulators
        return "counter"
    if any(k in n for k in ("qtd", "count", "numero")):
        return "count"
    return "other"


def _to_numeric_list(series):
    vals = []
    for v in series.tolist():
        try:
            if pd.isna(v):
                vals.append(None)
            else:
                vals.append(float(v))
        except Exception:
            vals.append(None)
    return vals


def tratar_coluna(nome, series, expected_records=6):
    tipo = detect_type(nome)
    valores = _to_numeric_list(series)
    nao_nulos = [v for v in valores if v is not None]

    resultado = {"value": None, "imputed": False, "method": None}

    if tipo == "gauge" or tipo == "temperature" or tipo == "count":
        # usa mediana quando possível (robusto a outliers). Se não houver números,
        # mantém último valor não nulo (se houver) ou None.
        if nao_nulos:
            resultado["value"] = float(pd.Series(nao_nulos).median())
            resultado["imputed"] = False
            resultado["method"] = "median"
        else:
            resultado["value"] = None
            resultado["imputed"] = True
            resultado["method"] = "no_values"

    elif tipo == "counter":
        # contadores acumulativos: calcula delta = last_valid - first_valid
        first = next((v for v in valores if v is not None), None)
        last = next((v for v in reversed(valores) if v is not None), None)
        if first is not None and last is not None:
            if last >= first:
                resultado["value"] = float(last - first)
                resultado["imputed"] = False
                resultado["method"] = "delta_last_first"
            else:
                # possível reset do contador: não tenta adivinhar
                resultado["value"] = None
                resultado["imputed"] = True
                resultado["method"] = "counter_reset"
        else:
            resultado["value"] = None
            resultado["imputed"] = True
            resultado["method"] = "insufficient"

    else:
        # default: tenta média numérica, senão último não nulo
        if nao_nulos:
            resultado["value"] = float(pd.Series(nao_nulos).mean())
            resultado["imputed"] = False
            resultado["method"] = "mean"
        else:
            resultado["value"] = None
            resultado["imputed"] = True
            resultado["method"] = "no_values"

    return resultado


def bronze_to_silver(bronze_components_path: str, silver_root: str, expected_records: int = 6):
    """
    Lê um arquivo components do bronze (CSV) e gera um arquivo silver limpo.

    Retorna um dict com paths gerados: {'components': path, 'processes': path_or_None}
    """
    df = pd.read_csv(bronze_components_path, sep=';', dtype=str, encoding='utf-8')

    # Normaliza colunas
    cols = list(df.columns)
    # TimeStamp e IdAeroporto devem existir
    if 'TimeStamp' not in cols:
        raise ValueError('TimeStamp column missing')
    if 'IdAeroporto' not in cols:
        raise ValueError('IdAeroporto column missing')

    # Determina minute target a partir do primeiro timestamp
    primeiro_ts = df.loc[0, 'TimeStamp']
    minuto = datetime.strptime(primeiro_ts, "%Y-%m-%d %H:%M:%S").replace(second=0, microsecond=0)

    # Agora: mantemos todos os registros do bronze (resolução 10s) no silver,
    # aplicando imputação por coluna quando necessário e calculando taxas
    # MB/s para os contadores (diferença entre registros consecutivos).
    out = {}
    df_work = df.copy()
    # parse timestamps
    df_work['TimeStamp'] = pd.to_datetime(df_work['TimeStamp'], format="%Y-%m-%d %H:%M:%S")

    # Converte colunas numéricas onde aplicável
    numeric_cols = []
    for c in cols:
        if c in ('TimeStamp', 'IdAeroporto'):
            continue
        # tenta converter para float, mantendo NaN
        df_work[c] = pd.to_numeric(df_work[c], errors='coerce')
        numeric_cols.append(c)

    # Imputação para gauges/temperatura/count: preencher valores None com mediana da coluna
    imputed_flags = {}
    imputed_methods = {}
    for c in numeric_cols:
        tipo = detect_type(c)
        if tipo in ('gauge', 'temperature', 'count'):
            med = df_work[c].median(skipna=True)
            # se med is NaN, não imputamos
            if pd.notna(med):
                mask = df_work[c].isna()
                df_work.loc[mask, c] = med
                imputed_flags[c] = mask.astype(int).tolist()
                imputed_methods[c] = ['median' if m else '' for m in mask]
            else:
                imputed_flags[c] = [0]*len(df_work)
                imputed_methods[c] = ['' for _ in range(len(df_work))]
        else:
            # counters and others: do not impute raw counter values here
            imputed_flags[c] = [0]*len(df_work)
            imputed_methods[c] = ['' for _ in range(len(df_work))]

    # Calcula taxas MB/s para contadores: usa diferença para o valor válido anterior
    # Mapa de colunas de contador para nome de output MB/s
    counter_to_rate = {
        'Disco_Read_Bytes': 'Disco_Read_MB_s',
        'Disco_Write_Bytes': 'Disco_Write_MB_s',
        'Rede_Bytes_Enviados': 'Rede_Bytes_Enviados_MB_s',
        'Rede_Bytes_Recebidos': 'Rede_Bytes_Recebidos_MB_s',
        'Swap_In': 'Swap_In_MB_s',
        'Swap_Out': 'Swap_Out_MB_s',
    }

    # initialize rate columns
    for rate_col in counter_to_rate.values():
        df_work[rate_col] = pd.NA
        df_work[f"{rate_col}_imputed"] = 1

    # compute rates
    prev_vals = {}
    prev_times = {}
    for idx, row in df_work.iterrows():
        t = row['TimeStamp']
        for c, rate_col in counter_to_rate.items():
            val = row.get(c)
            if pd.isna(val):
                # cannot compute
                df_work.at[idx, rate_col] = pd.NA
                df_work.at[idx, f"{rate_col}_imputed"] = 1
                continue
            if c in prev_vals and prev_vals[c] is not None:
                delta = val - prev_vals[c]
                delta_t = (t - prev_times[c]).total_seconds() if prev_times[c] is not None else None
                if delta_t and delta_t > 0 and delta >= 0:
                    mb_s = (delta / delta_t) / (1024*1024)
                    df_work.at[idx, rate_col] = round(mb_s, 6)
                    df_work.at[idx, f"{rate_col}_imputed"] = 0
                else:
                    df_work.at[idx, rate_col] = pd.NA
                    df_work.at[idx, f"{rate_col}_imputed"] = 1
            else:
                # no previous -> cannot compute rate
                df_work.at[idx, rate_col] = pd.NA
                df_work.at[idx, f"{rate_col}_imputed"] = 1

            # update prev
            prev_vals[c] = val
            prev_times[c] = t

    # Adiciona colunas de imputação para as colunas originais
    for c in numeric_cols:
        df_work[f"{c}_imputed"] = imputed_flags.get(c, [0]*len(df_work))
        df_work[f"{c}_imputed_method"] = imputed_methods.get(c, ['']*len(df_work))

    # Acrescenta colunas de metadata por linha: original_records e partial_minute
    df_work['original_records'] = len(df_work)
    df_work['partial_minute'] = len(df_work) < expected_records

    # Grava o CSV silver com os mesmos registros (resolução 10s)
    norm = os.path.normpath(bronze_components_path)
    parts = norm.replace('\\', '/').split('/')
    if '01-bronze' in parts:
        idx = parts.index('01-bronze')
        relative = parts[idx+1:]
        silver_dir = os.path.join(silver_root, os.path.dirname(os.path.join(*relative)))
    else:
        silver_dir = os.path.join(silver_root)
    os.makedirs(silver_dir, exist_ok=True)

    sufixo_minuto = minuto.strftime("%Y-%m-%d-%H-%M")
    token = os.path.basename(os.path.dirname(os.path.dirname(bronze_components_path)))
    comp_name = f"{token}-{sufixo_minuto}-components-silver.csv"
    caminho_components_silver = os.path.join(silver_dir, comp_name)

    # formata TimeStamp de volta para string
    df_out = df_work.copy()
    df_out['TimeStamp'] = df_out['TimeStamp'].dt.strftime("%Y-%m-%d %H:%M:%S")
    df_out.to_csv(caminho_components_silver, sep=';', index=False, encoding='utf-8')
    out['components'] = caminho_components_silver

    # tenta ler processes file no mesmo dir com sufixo -processos.csv
    dir_bronze = os.path.dirname(bronze_components_path)
    proc_glob = os.path.join(dir_bronze, os.path.basename(bronze_components_path).replace('-components.csv', '-processos.csv'))
    if os.path.exists(proc_glob):
        dfp = pd.read_csv(proc_glob, sep=';', dtype=str, encoding='utf-8')
        # grava processes como JSON no silver
        proc_name = f"{token}-{sufixo_minuto}-processos-silver.json"
        caminho_processos_silver = os.path.join(silver_dir, proc_name)
        # converte para lista de dicts
        lista = dfp.fillna('').to_dict(orient='records')
        with open(caminho_processos_silver, 'w', encoding='utf-8') as f:
            json.dump(lista, f, ensure_ascii=False)
        out['processes'] = caminho_processos_silver
    else:
        out['processes'] = None

    return out
