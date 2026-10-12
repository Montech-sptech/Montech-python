# ============================================================
# Organizador de pastas por Empresa / Aeroporto  (camada "bronze")
#
# Fluxo (executado UMA vez por token, chamado pelo Script_Extracao.py):
#   1. Garante que a pasta raiz "bronze" existe (cria se não existir)
#   2. Consulta no MySQL: token do servidor -> aeroporto -> empresa
#   3. Cria bronze/<empresa>/<aeroporto>/ se não existir
#   4. Devolve o caminho dessa pasta para o coletor gravar os CSVs nela
#
# Uso pelo coletor:
#   from Organizador import organizar
#   pasta = organizar(token, conexao)
#
# Uso manual (só cria as pastas e mostra o caminho):
#   python Organizador.py --token ABC
# ============================================================

import argparse
import os
import re

import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# Raiz = pasta onde este script está salvo (independe de onde ele é executado)
PASTA_RAIZ = os.path.dirname(os.path.abspath(__file__))
# Pasta raiz de todos os dados: bronze/<empresa>/<aeroporto>/
PASTA_BRONZE = os.path.join(PASTA_RAIZ, "bronze")

# ------------------------------------------------------------
# ATENÇÃO: ajuste os nomes de tabelas/colunas abaixo ao seu schema.
# Suposição: servidor(idServidor, token, fkAeroporto)
#            aeroporto(idAeroporto, nome, fkEmpresa)
#            empresa(idEmpresa, razaoSocial)
# ------------------------------------------------------------
SQL_DESTINO = """
    SELECT e.razaoSocial, a.nome
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
    """Retorna (empresa, aeroporto) do token, ou None se não existir/der erro."""
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

    return (linha[0], linha[1]) if linha is not None else None


def organizar(token, conexao=None):
    """
    Garante a estrutura bronze/<empresa>/<aeroporto>/ para o token e devolve
    o caminho da pasta. Retorna None se o token não tiver aeroporto/empresa
    vinculados ou se o banco falhar (a pasta bronze é criada de qualquer forma).

    conexao: conexão MySQL já aberta (a do coletor). Se None, abre e fecha uma própria.
    """
    # 1) Pasta raiz "bronze": cria se não existir
    if not os.path.isdir(PASTA_BRONZE):
        os.makedirs(PASTA_BRONZE)
        print(f"Pasta raiz criada: {PASTA_BRONZE}")

    # 2) Descobre empresa/aeroporto do token
    conexaoPropria = conexao is None
    if conexaoPropria:
        conexao = conectar_banco()
        if conexao is None:
            return None

    try:
        destino = buscar_destino(conexao, token)
    finally:
        if conexaoPropria:
            try:
                conexao.close()
            except mysql.connector.Error:
                pass

    if destino is None:
        print(f"Token '{token}' sem servidor/aeroporto/empresa vinculados.")
        return None

    # 3) Cria bronze/<empresa>/<aeroporto>/ se necessário
    empresa, aeroporto = destino
    pastaDestino = os.path.join(PASTA_BRONZE, nome_seguro(empresa), nome_seguro(aeroporto))
    os.makedirs(pastaDestino, exist_ok=True)

    # 4) Devolve o caminho para o coletor
    return pastaDestino


def main():
    parser = argparse.ArgumentParser(description="Cria bronze/<empresa>/<aeroporto>/ para um token.")
    parser.add_argument("--token", required=True, help="Token do servidor.")
    args = parser.parse_args()

    pasta = organizar(args.token)
    if pasta is None:
        raise SystemExit(1)
    print(pasta)


if __name__ == "__main__":
    main()