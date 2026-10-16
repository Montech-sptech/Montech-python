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

import pandas as pnd

from Organizador import conectar_banco, organizar_camadas, PASTA_BRONZE


# Nome gerado pelo Script_Extracao: <token>-AAAA-MM-DD_HHh.csv
# O regex pega tudo antes da data como token (não depende do tamanho do token).
PADRAO_ARQUIVO = re.compile(r"^(?P<token>.+)-\d{4}-\d{2}-\d{2}_\d{2}h$")

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


def main():

    conexao = conectar_banco()

    if conexao is None:
        print("Não foi possível conectar ao banco de dados.")
        raise SystemExit(1)

    print("Conectado ao banco de dados!")

    try:
        arquivos_por_token = AgruparCsvsPorToken(PASTA_BRONZE)
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

            # avisa se algum CSV está fora da pasta esperada (ex.: servidor trocou de aeroporto no banco)
            pasta_esperada = Path(destino["bronze"]).resolve()
            for arquivo in arquivos:
                if arquivo.resolve().parent != pasta_esperada:
                    print(f"Aviso: {arquivo} está fora de {pasta_esperada}")

            df = LerCsvs(arquivos)

            if df.empty:
                print("Sem dados para processar.")
                continue

            df = ValidarDados(df, destino["idAeroporto"])
            df = TratarDados(df)

            if df.empty:
                print("Nenhuma linha válida após o tratamento.")
                continue

            # PRATA: dados tratados
            caminho_prata = Path(destino["prata"]) / f"{token}.csv"
            df.to_csv(caminho_prata, sep=";", index=False, encoding="utf-8")
            print("Prata gravada em:", caminho_prata)

            # OURO: resumo por hora
            ouro = GerarOuro(df)

            if ouro.empty:
                print("Ouro não gerada (sem colunas para agregar).")
                continue

            caminho_ouro = Path(destino["ouro"]) / f"{token}.csv"
            ouro.to_csv(caminho_ouro, sep=";", index=False, encoding="utf-8")
            print("Ouro gravada em:", caminho_ouro)

    finally:
        conexao.close()


if __name__ == "__main__":
    main()