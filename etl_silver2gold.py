"""
Skeleton do ETL silver2gold.
Objetivo: receber arquivos do silver, converter para JSON e preparar arrays
e pontos de extensão para cálculos adicionais (a serem definidos pelo
responsável). Aqui fornecemos o esqueleto e funções de conversão para uso
posterior pela Lambda ou testes.
"""
import json
import pandas as pd


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
