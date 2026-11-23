import os
import pandas as pd

from etl_bronze2silver import bronze_to_silver
from etl_silver2gold import silver_to_gold_components


def test_gold_average_matches_silver(tmp_path):
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

    gold = silver_to_gold_components(silver_path)
    # find mean_UsoCPU_Geral in calc_averages
    means = {c['nome']: c['valor'] for c in gold['calc_averages']}
    expected = sum([10,20,30,40,50,60]) / 6.0
    assert abs(means.get('mean_UsoCPU_Geral', 0) - expected) < 1e-6
    # custom calculation cpu_mean should match the same expected value
    customs = {c['nome']: c['valor'] for c in gold['custom_calculations']}
    assert abs(customs.get('cpu_mean', 0) - expected) < 1e-6
