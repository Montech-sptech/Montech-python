import os
import pandas as pd

from etl_bronze2silver import bronze_to_silver
import etl_silver2gold as eg


def test_series_and_diagnostico_from_example(tmp_path):
    tmpdir = str(tmp_path)
    rows = [
        {"TimeStamp": "2023-01-01 14:03:00", "IdAeroporto": 1, "UsoCPU_Geral": 10},
        {"TimeStamp": "2023-01-01 14:03:10", "IdAeroporto": 1, "UsoCPU_Geral": 20},
        {"TimeStamp": "2023-01-01 14:03:20", "IdAeroporto": 1, "UsoCPU_Geral": 30},
        {"TimeStamp": "2023-01-01 14:03:30", "IdAeroporto": 1, "UsoCPU_Geral": 40},
        {"TimeStamp": "2023-01-01 14:03:40", "IdAeroporto": 1, "UsoCPU_Geral": 50},
        {"TimeStamp": "2023-01-01 14:03:50", "IdAeroporto": 1, "UsoCPU_Geral": 60},
    ]
    # create bronze file
    token = "TKN"
    dir_hour = os.path.join(tmpdir, token, "01-01-2023", "14")
    os.makedirs(dir_hour, exist_ok=True)
    fname = os.path.join(dir_hour, f"{token}-2023-01-01-14-03-components.csv")
    pd.DataFrame(rows).to_csv(fname, sep=';', index=False, encoding='utf-8')

    out = bronze_to_silver(fname, silver_root=os.path.join(tmpdir, '02-silver'))
    silver_path = out['components']
    assert os.path.exists(silver_path)

    df = eg.parse_silver_csv(silver_path)

    # generate series for day
    series = eg.series_for_day(df, aeroporto=1, servidor=token, date_local='2023-01-01')

    # position for 14:03:00 should be 5058
    pos = 5058
    cpu_series = series['series']['cpu']
    assert len(cpu_series) > pos
    assert cpu_series[pos] == 10.0

    # compute diagnostico and check 1min window stats
    # reference is last timestamp
    last_ts = df['TimeStamp'].max()
    referencia_ms = int(last_ts.tz_convert('UTC').value // 1_000_000)

    diag = eg.diagnostico_from_series(series, referencia_ms)
    # find 1min lines for cpu
    resol = diag['recursos']['cpu']['resolucoes'].get('1min')
    assert resol is not None
    linhas = resol['linhas']
    # expected window start (as in spec)
    expected_start = 1672592580000
    found = None
    for ln in linhas:
        if ln[0] == expected_start:
            found = ln
            break
    assert found is not None
    # check values approximately
    _, p10, med, p90, mx, cnt = found
    assert cnt == 6
    # numpy.percentile with linear interpolation yields 15.0 for p10 on this data
    assert abs(p10 - 15.0) < 1e-6
    assert abs(med - 35.0) < 1e-6
    # numpy.percentile yields 55.0 for p90 on this data
    assert abs(p90 - 55.0) < 1e-6
    assert abs(mx - 60.0) < 1e-6


def test_atual_and_processes_helpers():
    # craft a small df with timestamps
    rows = [
        {"TimeStamp": pd.Timestamp("2023-01-01 14:03:00").tz_localize('America/Sao_Paulo'), "UsoCPU_Geral": 10},
        {"TimeStamp": pd.Timestamp("2023-01-01 14:03:10").tz_localize('America/Sao_Paulo'), "UsoCPU_Geral": 20},
        {"TimeStamp": pd.Timestamp("2023-01-01 14:03:20").tz_localize('America/Sao_Paulo'), "UsoCPU_Geral": 30},
        {"TimeStamp": pd.Timestamp("2023-01-01 14:03:30").tz_localize('America/Sao_Paulo'), "UsoCPU_Geral": 40},
        {"TimeStamp": pd.Timestamp("2023-01-01 14:03:40").tz_localize('America/Sao_Paulo'), "UsoCPU_Geral": 50},
        {"TimeStamp": pd.Timestamp("2023-01-01 14:03:50").tz_localize('America/Sao_Paulo'), "UsoCPU_Geral": 60},
    ]
    df = pd.DataFrame(rows)
    atual = eg.atual_from_df(df)
    assert atual['janelaMs'] == 60000
    assert atual['leiturasNaJanela'] == 6
    assert abs(atual['valores']['cpu'] - 35.0) < 1e-6

    # processes helper
    procs = []
    for i in range(30):
        procs.append({'pid': i, 'nome': f'p{i}', 'cpu': float(i), 'mem': float(i/2), 'status': 'Rodando', 'usuario': 'u'})
    out = eg.processos_from_list(procs)
    assert len(out['processos']) == 20
    # top cpu should be first
    assert out['processos'][0]['cpu'] == 29.0
