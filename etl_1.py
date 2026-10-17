# ============================================================
# ETL: bronze -> prata -> ouro
#
# Fluxo:
#   1. Varre TODOS os CSVs dentro de bronze/ (incluindo subpastas)
#   2. Agrupa os arquivos pelo token (o token vem do nome do arquivo)
#   3. Pede ao Organizador as pastas do token (empresa/aeroporto vêm do banco)
#   4. Junta os CSVs do token, valida e trata  -> grava em prata/<empresa>/<aeroporto>/
#   5. Agrega por hora                          -> grava em ouro/<empresa>/<aeroporto>/
#
# O bronze NUNCA é alterado (nada é movido ou apagado): ele é a fonte original.
# IMPORTANTE: este arquivo e o Organizador.py precisam ficar na MESMA pasta.
# ============================================================

import re
from pathlib import Path

import boto3
from io import BytesIO

import pandas as pnd

from Organizador import (
    conectar_banco,
    organizar_camadas,
    PASTA_BRONZE,
    resolver_destino,
    montar_chave_s3,
)


# Bucket e região configurados para o projeto.
NOME_BUCKET = "montech-datalake-9526"
REGIAO_AWS = "us-east-1"

# Cliente usado para ler e gravar arquivos no S3.
cliente_s3 = boto3.client("s3", region_name=REGIAO_AWS)

# Nome gerado pelo Script_Extracao: <token>-AAAA-MM-DD_HHh.csv
# O regex pega tudo antes da data como token (não depende do tamanho do token).
PADRAO_ARQUIVO = re.compile(
    r"^(?P<token>.+)-\d{4}-\d{2}-\d{2}_\d{2}(?:h|-\d{2}-\d{2})$"
)

COLUNAS_NUMERICAS = [
    "IdAeroporto",
    "UsoCPU_Geral",
    "UsoRAM",
    "Swap_In",
    "Swap_Out",
    "UsoDisco",
    "Disco_Read_Bytes",
    "Disco_Write_Bytes",
    "Rede_Bytes_Enviados",
    "Rede_Bytes_Recebidos",
    "Qtd_Processos"
]

# contador cumulativo -> coluna de delta
CONTADORES = {
    "Swap_In": "Delta_Swap_In",
    "Swap_Out": "Delta_Swap_Out",
    "Disco_Read_Bytes": "Delta_Read_Bytes",
    "Disco_Write_Bytes": "Delta_Write_Bytes",
    "Rede_Bytes_Enviados": "Delta_Rede_Bytes_Enviados",
    "Rede_Bytes_Recebidos": "Delta_Rede_Bytes_Recebidos"
}

def AgruparCsvsS3PorToken():
    # Agrupa as chaves dos CSVs pelo token do servidor.
    arquivos_por_token = {}

    # A paginação permite listar todos os objetos, mesmo acima de 1.000.
    paginador = cliente_s3.get_paginator("list_objects_v2")

    for pagina in paginador.paginate(
        Bucket=NOME_BUCKET,
        Prefix="01-bronze/",
    ):
        for objeto in pagina.get("Contents", []):
            chave = objeto["Key"]

            # Ignora marcadores de pasta e arquivos que não sejam CSV.
            if not chave.endswith(".csv"):
                continue

            # Verifica o nome usando o padrão já definido na ETL.
            nome_sem_extensao = Path(chave).stem
            correspondencia = PADRAO_ARQUIVO.match(nome_sem_extensao)

            if correspondencia is None:
                print(f"Ignorado (nome fora do padrão): {chave}")
                continue

            token = correspondencia["token"]
            arquivos_por_token.setdefault(token, []).append(chave)

    return arquivos_por_token


def AgruparCsvsPorToken(pasta_bronze):

    # procura todos os CSVs em bronze e nas subpastas, e agrupa por token
    arquivos_por_token = {}

    for arquivo in sorted(Path(pasta_bronze).rglob("*.csv")):
        correspondencia = PADRAO_ARQUIVO.match(arquivo.stem)

        if correspondencia is None:
            print(f"Ignorado (nome fora do padrão): {arquivo}")
            continue

        token = correspondencia["token"]
        arquivos_por_token.setdefault(token, []).append(arquivo)

    return arquivos_por_token


def LerCsvs(arquivos):

    # le todos os CSVs do token e junta em um único DataFrame.
    # Se o cabeçalho mudou entre arquivos (componente novo), as colunas ausentes ficam NaN.
    partes = []

    for arquivo in arquivos:
        try:
            # on_bad_lines="skip" ignora linha cortada (arquivo da hora atual ainda sendo escrito)
            partes.append(pnd.read_csv(arquivo, sep=";", on_bad_lines="skip"))
        except pnd.errors.EmptyDataError:
            print(f"Arquivo vazio ignorado: {arquivo}")

    if not partes:
        return pnd.DataFrame()

    return pnd.concat(partes, ignore_index=True)


def LerCsvsS3(chaves):
    # Guarda os dados de cada CSV para juntá-los ao final.
    partes = []

    for chave in sorted(chaves):
        resposta = cliente_s3.get_object(
            Bucket=NOME_BUCKET,
            Key=chave,
        )

        # Lê o objeto e fecha a conexão de leitura, mesmo se ocorrer erro.
        corpo = resposta["Body"]
        try:
            conteudo = corpo.read()
        finally:
            corpo.close()

        try:
            # Lê o CSV em memória, usando o separador da captura.
            df = pnd.read_csv(BytesIO(conteudo), sep=";")
        except pnd.errors.EmptyDataError:
            print(f"Arquivo vazio ignorado: {chave}")
            continue

        partes.append(df)

    if not partes:
        return pnd.DataFrame()

    # Une os arquivos, preservando colunas que variam entre eles.
    return pnd.concat(partes, ignore_index=True)


def ValidarDados(df, id_aeroporto):

    # Verifica quantos valores nulos existem em cada coluna
    print("\n--- VALIDAÇÃO DOS DADOS ORIGINAIS ---")

    print("\nValores nulos por coluna:")
    print(df.isnull().sum())

    # Verifica linhas completamente duplicadas
    print("\nLinhas duplicadas:", df.duplicated().sum())

    # Verifica timestamps que não podem ser convertidos
    timestamps = pnd.to_datetime(df["TimeStamp"], errors="coerce")
    print("Timestamps inválidos:", timestamps.isnull().sum())

    # Verifica se o aeroporto do CSV corresponde ao banco
    if "IdAeroporto" in df.columns:
        aeroportos_invalidos = (df["IdAeroporto"] != id_aeroporto).sum()
        print("Linhas com aeroporto divergente:", aeroportos_invalidos)

    return df


def TratarDados(df):

    # convertendo o timestamp para data e hora
    # valores inválidos passam a ser nan
    df["TimeStamp"] = pnd.to_datetime(
        df["TimeStamp"],
        errors="coerce"
    )

    # convertendo as métricas numéricas.
    # tipo string que não representam números passam a ser NaN.
    for coluna in COLUNAS_NUMERICAS:
        if coluna in df.columns:
            df[coluna] = pnd.to_numeric(
                df[coluna],
                errors="coerce"
            )

    # remove linhas com valores ausentes ou inválidos nas colunas conhecidas.
    # (antes era dropna() geral: uma coluna extra de componente novo apagava a linha inteira)
    obrigatorias = ["TimeStamp"] + [c for c in COLUNAS_NUMERICAS if c in df.columns]
    df = df.dropna(subset=obrigatorias).copy()

    #remove linhas completamente iguais
    df = df.drop_duplicates().copy()

    # Remove valores impossíveis das metricas de porcentagem
    # CPU, RAM e disco devem ficar entre 0 e100.
    for coluna in ["UsoCPU_Geral", "UsoRAM", "UsoDisco"]:
        if coluna in df.columns:
            df = df[
                df[coluna].between(0, 100)
            ]

    # contadores e quantidade de processos não podem ser numeros negativos
    colunas_nao_negativas = [
        "IdAeroporto",
        "Swap_In",
        "Swap_Out",
        "Disco_Read_Bytes",
        "Disco_Write_Bytes",
        "Rede_Bytes_Enviados",
        "Rede_Bytes_Recebidos",
        "Qtd_Processos"
    ]

    for coluna in colunas_nao_negativas:
        if coluna in df.columns:
            df = df[df[coluna] >= 0]

    #ordenando registros de forma cronologica
    df = df.sort_values(
        by="TimeStamp"
    ).reset_index(drop=True)

    # Identifica possíveis outliers pelo método IQR.
    # não exclui esses valores automaticamente
    colunas_outliers = [
        "UsoCPU_Geral",
        "UsoRAM",
        "UsoDisco",
        "Qtd_Processos"
    ]

    for coluna in colunas_outliers:
        if coluna in df.columns and not df.empty:

            q1 = df[coluna].quantile(0.25)
            q3 = df[coluna].quantile(0.75)

            intervalo = q3 - q1

            limite_inferior = q1 - (1.5 * intervalo)
            limite_superior = q3 + (1.5 * intervalo)

            # Cria uma coluna indicando possíveis outliers.
            df[f"Outlier_{coluna}"] = (
                (df[coluna] < limite_inferior) |
                (df[coluna] > limite_superior)
            )

            quantidade = df[f"Outlier_{coluna}"].sum()

            print(
                f"Possíveis outliers em {coluna}: "
                f"{quantidade}"
            )

    # calcula deltas dos contadores cumulativos.
    # Agora o cálculo é feito sobre TODOS os arquivos do token juntos, então a troca de
    # CSV (a cada 2h) não gera um delta zero falso. Só a primeira coleta de todas recebe zero.
    for coluna_original, coluna_delta in CONTADORES.items():
        if coluna_original in df.columns:

            df[coluna_delta] = (
                df[coluna_original].diff().fillna(0)
            )

            # Um delta negativo pode indicar reinício do contador.
            # Como não temos a leitura anterior ao reinício,
            # não é possível calcular o consumo real desse intervalo.
            df.loc[df[coluna_delta] < 0, coluna_delta] = 0

    return df


def GerarOuro(df):

    # Camada ouro: um resumo por hora, pronto para dashboard/consulta.
    # médias e máximos das métricas de uso, soma dos deltas dos contadores.
    agregacoes = {}

    for coluna in ["UsoCPU_Geral", "UsoRAM", "UsoDisco", "Qtd_Processos"]:
        if coluna in df.columns:
            agregacoes[coluna] = ["mean", "max"]

    for coluna_delta in CONTADORES.values():
        if coluna_delta in df.columns:
            agregacoes[coluna_delta] = ["sum"]

    if not agregacoes:
        return pnd.DataFrame()

    df = df.copy()
    # "60min" equivale a 1 hora e funciona em qualquer versão do pandas
    df["Hora"] = df["TimeStamp"].dt.floor("60min")

    agrupado = df.groupby("Hora")
    ouro = agrupado.agg(agregacoes)

    # colunas viram nomes simples: UsoCPU_Geral_mean, Delta_Swap_In_sum...
    ouro.columns = [f"{coluna}_{funcao}" for coluna, funcao in ouro.columns]
    ouro["Qtd_Coletas"] = agrupado.size()

    return ouro.round(2).reset_index()


def SalvarCsvS3(df, camada, id_empresa, id_aeroporto, token, data):
    # Define o nome do CSV consolidado do dia.
    nome_arquivo = f"{token}-{data}.csv"

    # Monta o destino na camada solicitada.
    chave = montar_chave_s3(
        camada,
        id_empresa,
        id_aeroporto,
        token,
        data,
        nome_arquivo,
    )

    # Converte os dados para CSV.
    conteudo = df.to_csv(sep=";", index=False).encode("utf-8")

    # Envia o CSV para o S3.
    cliente_s3.put_object(
        Bucket=NOME_BUCKET,
        Key=chave,
        Body=conteudo,
        ContentType="text/csv; charset=utf-8",
    )

    print(f"CSV gravado em: s3://{NOME_BUCKET}/{chave}")


def SalvarJsonS3(df, id_empresa, id_aeroporto, token, data):
    # Define o nome do JSON consolidado do dia.
    nome_arquivo = f"{token}-{data}.json"

    # Monta o destino na camada 03-gold.
    chave = montar_chave_s3(
        "ouro",
        id_empresa,
        id_aeroporto,
        token,
        data,
        nome_arquivo,
    )

    # Converte cada linha em um objeto JSON, com datas em formato ISO.
    conteudo = df.to_json(
        orient="records",
        date_format="iso",
        force_ascii=False,
        indent=2,
    ).encode("utf-8")

    # Envia o JSON para o S3.
    cliente_s3.put_object(
        Bucket=NOME_BUCKET,
        Key=chave,
        Body=conteudo,
        ContentType="application/json; charset=utf-8",
    )

    print(f"JSON gravado em: s3://{NOME_BUCKET}/{chave}")

def main():

    conexao = conectar_banco()

    if conexao is None:
        print("Não foi possível conectar ao banco de dados.")
        raise SystemExit(1)

    print("Conectado ao banco de dados!")

    try:
        # Lista os CSVs de bronze no S3 e agrupa por servidor.
        arquivos_por_token = AgruparCsvsS3PorToken()
        print(f"{len(arquivos_por_token)} servidor(es) encontrado(s) no bronze.")

        for token, arquivos in arquivos_por_token.items():

            print("\n============================================")
            print("Token:", token)
            print("Arquivos:", len(arquivos))

            # O Organizador consulta o banco, cria as pastas das 3 camadas e devolve as rotas
            destino = organizar_camadas(token, conexao)

            if destino is None:
                print("Servidor não encontrado. Token ignorado.")
                continue
            
                        # Busca os IDs usados para montar os destinos no S3.
            dadosDestino = resolver_destino(token, conexao)

            if dadosDestino is None:
                print("Não foi possível resolver o destino. Token ignorado.")
                continue

            idEmpresa, idAeroporto, _ = dadosDestino

            # Lê e reúne os CSVs deste servidor diretamente do S3.
            df = LerCsvsS3(arquivos)

            if df.empty:
                print("Sem dados para processar.")
                continue

            df = ValidarDados(df, destino["idAeroporto"])
            df = TratarDados(df)

            if df.empty:
                print("Nenhuma linha válida após o tratamento.")
                continue

            # Separa os dados tratados por dia.
            # Os deltas já foram calculados antes dessa separação,
            # preservando a continuidade entre arquivos e dias.
            for dia, dadosDia in df.groupby(df["TimeStamp"].dt.date):
                data = dia.isoformat()

                # SILVER: salva todas as coletas válidas do dia em CSV.
                SalvarCsvS3(
                    dadosDia,
                    "prata",
                    idEmpresa,
                    idAeroporto,
                    token,
                    data,
                )

                # GOLD: calcula o resumo por hora para este dia.
                ouro = GerarOuro(dadosDia)

                if ouro.empty:
                    print(f"Sem dados para gerar gold em {data}.")
                    continue

                # Salva o resumo diário em JSON na camada 03-gold.
                SalvarJsonS3(
                    ouro,
                    idEmpresa,
                    idAeroporto,
                    token,
                    data,
                )

    finally:
        conexao.close()


if __name__ == "__main__":
    main()