import pandas as pnd
import csv
import mysql.connector
from dotenv import load_dotenv
import os
import glob
from pathlib import Path



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
    colunas_numericas = [
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

    for coluna in colunas_numericas:
        if coluna in df.columns:
            df[coluna] = pnd.to_numeric(
                df[coluna],
                errors="coerce"
            )

    # remove linhas com valores ausentes ou inválidos
    # Inclui timestamps e valores number não conversiveis.
    df = df.dropna().copy()

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

    # calcula deltas dos contadores cumulativos, o primeiro registro recebe delta zero porque não
    # existe uma coleta anterior no arquivo para comparar
    contadores = {
        "Swap_In": "Delta_Swap_In",
        "Swap_Out": "Delta_Swap_Out",
        "Disco_Read_Bytes": "Delta_Read_Bytes",
        "Disco_Write_Bytes": "Delta_Write_Bytes",
        "Rede_Bytes_Enviados": "Delta_Rede_Bytes_Enviados",
        "Rede_Bytes_Recebidos": "Delta_Rede_Bytes_Recebidos"
    }

    for coluna_original, coluna_delta in contadores.items():
        if coluna_original in df.columns:

            df[coluna_delta] = (
                df[coluna_original].diff().fillna(0)
            )

            # Um delta negativo pode indicar reinício do contador.
            # Como não temos a leitura anterior ao reinício,
            # não é possível calcular o consumo real desse intervalo.
            df.loc[df[coluna_delta] < 0, coluna_delta] = 0

    return df


# carega o arquivo env
load_dotenv()


# conexao com bd pelo .env
conexao = mysql.connector.connect(
    host=os.getenv("DB_HOST"),
    user=os.getenv("DB_USER"),
    password=os.getenv("DB_PASSWORD"),
    database=os.getenv("DB_NAME"),
)

# verificando se a conexao foi feita
if conexao.is_connected():
    print("Conectado ao banco de dados!")
else:
    print("Não foi possível conectar ao banco de dados.")


# criando cursos
# cursor é um objeto python que permite executar comandos SQL através do python
cursor = conexao.cursor()


# consulta, guardando o select dentro da variavel sql

sql = """
SELECT
    e.idEmpresa,
    e.razaoSocial,
    a.idAeroporto,
    a.nome AS nomeAeroporto,
    a.codigoAeroporto,
    s.idServidor,
    s.nomeServidor,
    s.token
FROM servidor s
JOIN aeroporto a
    ON s.fkAeroporto = a.idAeroporto
JOIN empresa e
    ON a.fkEmpresa = e.idEmpresa
WHERE s.token = %s;
"""


# Caminhos das camadas do Data Lake
pasta_bronze = Path("bucket_simulacao") / "bronze"
pasta_prata = Path("bucket_simulacao") / "prata"
pasta_ouro = Path("bucket_simulacao") / "ouro"

# procurando todos os arquivos CSV dentro da pastabronze
# e tambem dentro das subpastas
arquivos_csv = pasta_bronze.rglob("*.csv")


# Processa cada arquivo encontrado, percorre os arquivos e executa o codigo para cada aquivo encontrado
for arquivo in arquivos_csv:

    # pega somente o nome do arquivo
    nome_arquivo = arquivo.name

    # Remove o .csv do final do nome
    nome_sem_csv = nome_arquivo.removesuffix(".csv")

    # pegando o token do servidor e removendo a data
    token = nome_sem_csv[:32]

    # consulta do nome do arquivo
    print("Arquivo:", nome_arquivo)
    # consulta do nome do token do servidor
    print("Token:", token)

    # ---- CONSULTA O BANCO PARA ESTE CSV ----

    # Usa o token que acabamos de extrair do nome do arquivo
    # faz o select da variavel sql utilizando como parametro o token
    cursor.execute(sql, (token,))

    # pega o resultado da consulta vindo do bd
    resultado = cursor.fetchone()

    # exibundo informacoes da consulta
    print("Resultado do banco:", resultado)

    # se encontrou o servidor ele define variaveis de acordo com a posicao delas que vem em uma lista
    if resultado:

        id_empresa = resultado[0]
        nome_empresa = resultado[1]

        id_aeroporto = resultado[2]
        nome_aeroporto = resultado[3]
        codigo_aeroporto = resultado[4]

        id_servidor = resultado[5]
        nome_servidor = resultado[6]
        token_servidor = resultado[7]


        # LÊ O CSV
        df = pnd.read_csv(arquivo, sep=";")

        # AQUI ENTRA A VALIDAÇÃO
        df = ValidarDados(df, id_aeroporto)

        # FAZ O TRATAMENTO DOS DADOS
        df = TratarDados(df)

        # MOSTRA AS PRIMEIRAS LINHAS DO DATAFRAME TRATADO
        print(df.head())



        # Monta o caminho do servidor em cada camada
        caminho_servidor_bronze = (
            pasta_bronze / nome_empresa / nome_aeroporto / nome_servidor
        )

        caminho_servidor_prata = (
            pasta_prata / nome_empresa / nome_aeroporto / nome_servidor
        )

        caminho_servidor_ouro = (
            pasta_ouro / nome_empresa / nome_aeroporto / nome_servidor
        )


       
        # VERIFICA O  BUCKETBRONZE
        

        if caminho_servidor_bronze.exists():
            print("Estrutura Bronze já existe.")
        else:
            print("Estrutura Bronze não existe.")
            caminho_servidor_bronze.mkdir(parents=True, exist_ok=True)
            print("Estrutura Bronze criada.")


        
        # VERIFICA O BUCKET PRATA
        

        if caminho_servidor_prata.exists():
            print("Estrutura Prata já existe.")
        else:
            print("Estrutura Prata não existe.")
            caminho_servidor_prata.mkdir(parents=True, exist_ok=True)
            print("Estrutura Prata criada.")


        
        # VERIFICA O BUCKET OURO
        

        if caminho_servidor_ouro.exists():
            print("Estrutura Ouro já existe.")
        else:
            print("Estrutura Ouro não existe.")
            caminho_servidor_ouro.mkdir(parents=True, exist_ok=True)
            print("Estrutura Ouro criada.")




        # Monta o caminho completo onde o CSV ficará
        caminho_destino = caminho_servidor_bronze / nome_arquivo
        print("Destino do CSV:", caminho_destino)

        # Verifica se o arquivo já está no local correto
        if arquivo != caminho_destino:
            arquivo.rename(caminho_destino)
            print("CSV movido para:", caminho_destino)
        else:
            print("CSV já está no local correto.")

        print("Empresa:", nome_empresa)
        print("Aeroporto:", nome_aeroporto)
        print("Código:", codigo_aeroporto)
        print("Servidor:", nome_servidor)
        print("Token:", token_servidor)

    else:
        print("Servidor não encontrado.")

    print("--------------------------------------------")
