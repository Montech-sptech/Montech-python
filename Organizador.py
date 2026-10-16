# ============================================================
# Organizador de pastas por Empresa / Aeroporto  (camadas bronze, prata e ouro)
#
# Fluxo (executado UMA vez por token):
#   1. Consulta no MySQL: token do servidor -> aeroporto -> empresa
#   2. Garante <camada>/<empresa>/<aeroporto>/ (cria raiz e subpastas se não existirem)
#   3. Devolve o(s) caminho(s) para quem chamou (coletor ou ETL)
#
# Uso pelo coletor (Script_Extracao.py) - NADA muda:
#   from Organizador import organizar
#   pasta = organizar(token, conexao)              # bronze/<empresa>/<aeroporto>/
#
# Uso pela ETL (etl_1.py):
#   from Organizador import organizar_camadas
#   destino = organizar_camadas(token, conexao)
#   destino["bronze"], destino["prata"], destino["ouro"], destino["idAeroporto"]
#
# Uso manual:
#   python Organizador.py --token ABC                    (só bronze)
#   python Organizador.py --token ABC --camada todas
# ============================================================

import argparse
import os
import re

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# Raiz = pasta onde este script está salvo (independe de onde ele é executado).
# Para simular um bucket, basta apontar PASTA_DATALAKE para outra pasta (ex.: "bucket_simulacao").
PASTA_RAIZ = os.path.dirname(os.path.abspath(__file__))
PASTA_DATALAKE = PASTA_RAIZ

CAMADAS = ("bronze", "prata", "ouro")
PASTAS_CAMADAS = {camada: os.path.join(PASTA_DATALAKE, camada) for camada in CAMADAS}

# Mantido porque o Script_Extracao.py importa este nome
PASTA_BRONZE = PASTAS_CAMADAS["bronze"]

# ------------------------------------------------------------
# ATENÇÃO: ajuste os nomes de tabelas/colunas abaixo ao seu schema.
# Suposição: servidor(idServidor, token, fkAeroporto)
#            aeroporto(idAeroporto, nome, fkEmpresa)
#            empresa(idEmpresa, razaoSocial)
# ------------------------------------------------------------
SQL_DESTINO = """
    SELECT e.razaoSocial, a.nome, a.idAeroporto
    FROM servidor s
    JOIN aeroporto a ON a.idAeroporto = s.fkAeroporto
    JOIN empresa e   ON e.idEmpresa   = a.fkEmpresa
    WHERE s.token = %s
"""


def conectar_banco():
    """Conecta ao MySQL usando as credenciais do .env. Retorna None se falhar."""
    try:
        return mysql.connector.connect(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME"),
        )
    except mysql.connector.Error as erro:
        print(f"Erro ao conectar ao banco de dados: {erro}")
        return None


def nome_seguro(nome):
    """Remove caracteres inválidos para nome de pasta (principalmente no Windows)."""
    limpo = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(nome)).strip().rstrip(". ")
    return limpo or "sem_nome"


def buscar_destino(conexao, token):
    """Retorna (empresa, aeroporto, idAeroporto) do token, ou None se não existir/der erro."""
    cursor = None
    try:
        if not conexao.is_connected():
            conexao.reconnect(attempts=3, delay=2)
        cursor = conexao.cursor(buffered=True)
        cursor.execute(SQL_DESTINO, (token,))
        linha = cursor.fetchone()
    except mysql.connector.Error as erro:
        print(f"Erro ao consultar o token '{token}': {erro}")
        return None
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except mysql.connector.Error:
                pass

    return (linha[0], linha[1], linha[2]) if linha is not None else None


def resolver_destino(token, conexao=None):
    """
    Descobre (empresa, aeroporto, idAeroporto) do token.
    conexao: conexão MySQL já aberta. Se None, abre e fecha uma própria.
    """
    conexaoPropria = conexao is None
    if conexaoPropria:
        conexao = conectar_banco()
        if conexao is None:
            return None

    try:
        return buscar_destino(conexao, token)
    finally:
        if conexaoPropria:
            try:
                conexao.close()
            except mysql.connector.Error:
                pass


def montar_pasta(camada, empresa, aeroporto):
    """Cria (se preciso) <camada>/<empresa>/<aeroporto>/ e devolve o caminho."""
    raiz = PASTAS_CAMADAS[camada]
    if not os.path.isdir(raiz):
        os.makedirs(raiz)
        print(f"Pasta raiz criada: {raiz}")

    pasta = os.path.join(raiz, nome_seguro(empresa), nome_seguro(aeroporto))
    os.makedirs(pasta, exist_ok=True)
    return pasta


def organizar(token, conexao=None, camada="bronze"):
    """
    Garante <camada>/<empresa>/<aeroporto>/ para o token e devolve o caminho.
    Retorna None se o token não tiver aeroporto/empresa vinculados ou se o banco falhar
    (a pasta raiz da camada é criada de qualquer forma).

    Chamadas antigas (organizar(token, conexao)) continuam funcionando: camada padrão = bronze.
    """
    if camada not in PASTAS_CAMADAS:
        raise ValueError(f"Camada inválida: '{camada}'. Use uma de {CAMADAS}.")

    os.makedirs(PASTAS_CAMADAS[camada], exist_ok=True)

    destino = resolver_destino(token, conexao)
    if destino is None:
        print(f"Token '{token}' sem servidor/aeroporto/empresa vinculados.")
        return None

    empresa, aeroporto, _ = destino
    return montar_pasta(camada, empresa, aeroporto)


def organizar_camadas(token, conexao=None):
    """
    Faz UMA consulta ao banco e garante as pastas das 3 camadas.
    Retorna {"idAeroporto": ..., "bronze": ..., "prata": ..., "ouro": ...}
    ou None se o token não tiver aeroporto/empresa vinculados.
    """
    destino = resolver_destino(token, conexao)
    if destino is None:
        print(f"Token '{token}' sem servidor/aeroporto/empresa vinculados.")
        return None

    empresa, aeroporto, idAeroporto = destino
    resultado = {"idAeroporto": idAeroporto}
    for camada in CAMADAS:
        resultado[camada] = montar_pasta(camada, empresa, aeroporto)
    return resultado


def main():
    parser = argparse.ArgumentParser(description="Cria <camada>/<empresa>/<aeroporto>/ para um token.")
    parser.add_argument("--token", required=True, help="Token do servidor.")
    parser.add_argument(
        "--camada",
        default="bronze",
        choices=CAMADAS + ("todas",),
        help="Camada a criar (padrão: bronze).",
    )
    args = parser.parse_args()

    if args.camada == "todas":
        destino = organizar_camadas(args.token)
        if destino is None:
            raise SystemExit(1)
        for camada in CAMADAS:
            print(destino[camada])
    else:
        pasta = organizar(args.token, camada=args.camada)
        if pasta is None:
            raise SystemExit(1)
        print(pasta)


if __name__ == "__main__":
    main()