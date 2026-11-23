import os
import json
import pandas as pd
import tempfile

from etl_bronze2silver import bronze_to_silver


def make_bronze_components(tmpdir, token="TKN", minute="2023-01-01-14-03", rows=None):
    # rows: list of dicts for columns beyond TimeStamp/IdAeroporto
    dir_hour = os.path.join(tmpdir, token, "01-01-2023", "14")
    os.makedirs(dir_hour, exist_ok=True)
    fname = os.path.join(dir_hour, f"{token}-2023-01-01-14-03-components.csv")
    if rows is None:
        rows = []
    df = pd.DataFrame(rows)
    df.to_csv(fname, sep=';', index=False, encoding='utf-8')
    return fname


def test_bronze_to_silver_basic(tmp_path):
    tmpdir = str(tmp_path)
    rows = [
        {"TimeStamp": "2023-01-01 14:03:00", "IdAeroporto": 1, "UsoCPU_Geral": 10, "Rede_Bytes_Enviados": 1000},
        {"TimeStamp": "2023-01-01 14:03:10", "IdAeroporto": 1, "UsoCPU_Geral": 20, "Rede_Bytes_Enviados": 1500},
        {"TimeStamp": "2023-01-01 14:03:20", "IdAeroporto": 1, "UsoCPU_Geral": 30, "Rede_Bytes_Enviados": 2000},
    ]
    path = make_bronze_components(tmpdir, rows=rows)
    out = bronze_to_silver(path, silver_root=os.path.join(tmpdir, '02-silver'), expected_records=6)
    assert os.path.exists(out['components'])
    df = pd.read_csv(out['components'], sep=';')
    # three rows preserved
    assert len(df) == 3
    # original UsoCPU_Geral values preserved
    assert list(df['UsoCPU_Geral']) == [10.0, 20.0, 30.0]
    # rates MB/s computed for rows 1 and 2 (row 0 has no previous -> NaN)
    # compute expected approx: delta 500 bytes over 10s -> 50 B/s -> 50/1048576 MB/s ~ 4.768e-05 -> rounded ~ 0.00005
    assert round(float(df.loc[1, 'Rede_Bytes_Enviados_MB_s']), 5) == 0.00005


def test_partial_minute_flag(tmp_path):
    tmpdir = str(tmp_path)
    rows = [
        {"TimeStamp": "2023-01-01 14:04:00", "IdAeroporto": 2, "UsoCPU_Geral": 5},
    ]
    path = make_bronze_components(tmpdir, rows=rows)
    out = bronze_to_silver(path, silver_root=os.path.join(tmpdir, '02-silver'), expected_records=6)
    df = pd.read_csv(out['components'], sep=';')
    assert df.loc[0, 'partial_minute'] in (True, 'True', 'TRUE', 1, '1')


def test_no_values_imputation(tmp_path):
    tmpdir = str(tmp_path)
    rows = [
        {"TimeStamp": "2023-01-01 14:05:00", "IdAeroporto": 3, "UsoCPU_Geral": None},
    ]
    path = make_bronze_components(tmpdir, rows=rows)
    out = bronze_to_silver(path, silver_root=os.path.join(tmpdir, '02-silver'))
    df = pd.read_csv(out['components'], sep=';')
    # When no numeric values, UsoCPU_Geral should be empty / NaN
    assert pd.isna(df.loc[0, 'UsoCPU_Geral'])


def test_processes_json_includes_status(tmp_path):
    tmpdir = str(tmp_path)
    token = "TKN"
    dir_hour = os.path.join(tmpdir, token, "01-01-2023", "14")
    os.makedirs(dir_hour, exist_ok=True)
    # create a processes CSV in bronze dir
    proc_fname = os.path.join(dir_hour, f"{token}-2023-01-01-14-03-processos.csv")
    dfp = pd.DataFrame([
        {"TimeStamp": "2023-01-01 14:03:50", "IdAeroporto": 1, "PID": 1234, "Nome": "procA", "CPU": 0.5, "RAM": 1.2, "Status": "running"},
        {"TimeStamp": "2023-01-01 14:03:50", "IdAeroporto": 1, "PID": 2345, "Nome": "procB", "CPU": 1.0, "RAM": 2.3, "Status": "sleeping"},
    ])
    dfp.to_csv(proc_fname, sep=';', index=False, encoding='utf-8')

    # create a minimal components bronze file so bronze_to_silver will process dir
    comp_fname = os.path.join(dir_hour, f"{token}-2023-01-01-14-03-components.csv")
    pd.DataFrame([{"TimeStamp": "2023-01-01 14:03:00", "IdAeroporto": 1, "UsoCPU_Geral": 10}]).to_csv(comp_fname, sep=';', index=False)

    out = bronze_to_silver(comp_fname, silver_root=os.path.join(tmpdir, '02-silver'))
    assert out['processes'] is not None
    with open(out['processes'], 'r', encoding='utf-8') as f:
        lista = json.load(f)
    assert all('Status' in p for p in lista)
