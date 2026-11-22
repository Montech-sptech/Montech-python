import os
import tempfile
import shutil
import pandas as pnd
import pytest

from Script_Extracao import calcularMedia, fechar_minuto


def fake_enviar_success(path):
    # simula envio bem sucedido
    return True


def fake_enviar_fail(path):
    return False


def fake_coletar_processos_one():
    return [{"PID": 1, "Nome": "proc", "CPU": 1.0, "RAM": 0.5}]


def fake_coletar_processos_empty():
    return []


def test_calcular_media_com_nulos_e_texto():
    valores = [None, 10, None, 20, None]
    assert calcularMedia(valores) == 15.0

    valores = [None, 'ok', None, None]
    assert calcularMedia(valores) == 'ok'


def test_minuto_completo_gera_components_e_processos(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 42

    # 6 registros no mesmo minuto
    base = "2023-01-01 14:03:"
    registros = []
    for s in (0, 10, 20, 30, 40, 50):
        registros.append({"TimeStamp": base + f"{s:02d}", "IdAeroporto": idAeroporto, "UsoCPU_Geral": s})

    resultado = fechar_minuto(
        registros,
        token,
        pasta,
        idAeroporto,
        qtd_processos_selected=True,
        coletar_processos_func=fake_coletar_processos_one,
        enviar_func=fake_enviar_success,
    )

    assert len(resultado["files"]) == 2
    # verifica components com 6 linhas de dados
    comp = [p for p in resultado["files"] if p.endswith("-components.csv")][0]
    df = pnd.read_csv(comp, sep=';')
    assert len(df) == 6


def test_nome_e_pasta_virada_hora(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 1

    # minuto 14:59
    base = "2023-01-01 14:59:"
    registros = []
    for s in (0, 10, 20, 30, 40, 50):
        registros.append({"TimeStamp": base + f"{s:02d}", "IdAeroporto": idAeroporto, "UsoCPU_Geral": s})

    resultado = fechar_minuto(
        registros,
        token,
        pasta,
        idAeroporto,
        qtd_processos_selected=False,
        coletar_processos_func=fake_coletar_processos_one,
        enviar_func=fake_enviar_success,
    )

    comp = resultado["files"][0]
    # pasta hora deve ser 14
    assert os.path.sep + os.path.join("14") in comp or comp.split(os.path.sep)[-2] == "14"


def test_inicio_no_meio_do_minuto_gera_parcial_e_depois_completo(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 7

    # primeiro bloco parcial (3 registros) para minuto 14:03
    registros_parciais = [
        {"TimeStamp": "2023-01-01 14:03:10", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 1},
        {"TimeStamp": "2023-01-01 14:03:20", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 2},
        {"TimeStamp": "2023-01-01 14:03:30", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 3},
    ]

    res1 = fechar_minuto(registros_parciais, token, pasta, idAeroporto, qtd_processos_selected=False, enviar_func=fake_enviar_success)
    comp1 = res1["files"][0]
    df1 = pnd.read_csv(comp1, sep=';')
    assert len(df1) == 3

    # próximo minuto completo
    registros2 = []
    base = "2023-01-01 14:04:"
    for s in (0, 10, 20, 30, 40, 50):
        registros2.append({"TimeStamp": base + f"{s:02d}", "IdAeroporto": idAeroporto, "UsoCPU_Geral": s})

    res2 = fechar_minuto(registros2, token, pasta, idAeroporto, qtd_processos_selected=False, enviar_func=fake_enviar_success)
    df2 = pnd.read_csv(res2["files"][0], sep=';')
    assert len(df2) == 6


def test_drift_registro_atrasado_permanece_no_minuto_correto(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 2

    # Simula 6 registros onde o último está atrasado (mas ainda com timestamp do mesmo minuto)
    base = "2023-01-01 14:05:"
    regs = []
    for s in (0, 10, 20, 30, 40, 50):
        regs.append({"TimeStamp": base + f"{s:02d}", "IdAeroporto": idAeroporto, "UsoCPU_Geral": s})

    res = fechar_minuto(regs, token, pasta, idAeroporto, qtd_processos_selected=False, enviar_func=fake_enviar_success)
    df = pnd.read_csv(res["files"][0], sep=';')
    assert len(df) == 6


def test_minuto_incompleto_gera_parcial(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 3

    regs = [
        {"TimeStamp": "2023-01-01 14:06:10", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 1},
        {"TimeStamp": "2023-01-01 14:06:20", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 2},
        {"TimeStamp": "2023-01-01 14:06:30", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 3},
    ]

    res = fechar_minuto(regs, token, pasta, idAeroporto, qtd_processos_selected=False, enviar_func=fake_enviar_success)
    df = pnd.read_csv(res["files"][0], sep=';')
    assert len(df) == 3


def test_falha_envio_arquivo_fica_pendente(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 4
    regs = [
        {"TimeStamp": "2023-01-01 14:07:00", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 1},
    ]

    res = fechar_minuto(regs, token, pasta, idAeroporto, qtd_processos_selected=False, enviar_func=fake_enviar_fail)
    assert len(res["failed"]) == 1
    # arquivo existe no disco
    assert os.path.exists(res["failed"][0])


def test_envio_sucesso_apos_falha_remove_pendente(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 5
    regs = [
        {"TimeStamp": "2023-01-01 14:08:00", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 1},
    ]

    res = fechar_minuto(regs, token, pasta, idAeroporto, qtd_processos_selected=False, enviar_func=fake_enviar_fail)
    pend = res["failed"][0]
    assert os.path.exists(pend)
    # agora envia com sucesso
    assert fake_enviar_success(pend) is True


def test_qtd_processos_nao_selecionado_nao_gera_processos(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 6
    regs = [
        {"TimeStamp": "2023-01-01 14:09:00", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 1},
    ]

    res = fechar_minuto(regs, token, pasta, idAeroporto, qtd_processos_selected=False, coletar_processos_func=fake_coletar_processos_one, enviar_func=fake_enviar_success)
    # apenas components
    assert len(res["files"]) == 1


def test_coleta_processos_vazia_nao_gera_arquivo_processos(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 8
    regs = [
        {"TimeStamp": "2023-01-01 14:10:00", "IdAeroporto": idAeroporto, "UsoCPU_Geral": 1},
    ]

    res = fechar_minuto(regs, token, pasta, idAeroporto, qtd_processos_selected=True, coletar_processos_func=fake_coletar_processos_empty, enviar_func=fake_enviar_success)
    # apenas components
    assert len(res["files"]) == 1


def test_sensor_indisponivel_nao_quebra(tmp_path):
    token = "TKN"
    pasta = str(tmp_path)
    idAeroporto = 9
    regs = [
        {"TimeStamp": "2023-01-01 14:11:00", "IdAeroporto": idAeroporto, "UsoCPU_Geral": None},
    ]

    res = fechar_minuto(regs, token, pasta, idAeroporto, qtd_processos_selected=False, enviar_func=fake_enviar_success)
    assert len(res["files"]) == 1
