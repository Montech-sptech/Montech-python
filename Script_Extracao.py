# ============================================================
# Sistema de Coleta de Métricas
# Fluxo geral:
#   1. Conecta ao banco MySQL
#   2. Faz login do usuário (email + senha)
#   3. Pede o token do servidor, busca os componentes dele no banco
#      e atualiza o statusAtividade para 1 se estiver como 0
#   4. O Organizador (executado UMA vez) descobre empresa/aeroporto do token,
#      cria bronze/<empresa>/<aeroporto>/ se não existir e devolve esse caminho
#   5. Em loop, executa o "codigo" de cada componente e grava uma linha no CSV,
#      direto dentro da pasta devolvida pelo Organizador
#
# IMPORTANTE: este arquivo e o Organizador.py precisam ficar na MESMA pasta.
# ============================================================

import pandas as pnd                 # monta o DataFrame e exporta para CSV
from datetime import datetime        # data/hora das coletas
import os                            # variáveis de ambiente, pastas, clear do terminal
import boto3                         # permitir que desenvolvedores escrevam scripts e aplicativos em Python para interagir diretamente com os serviços da AWS
import psutil                        # biblioteca de métricas (CPU, RAM, disco...). Usada pelo eval()!
import socket                        # pega o hostname da máquina. Também usada pelo eval()
import time                          # sleep e relógio monotônico
import mysql.connector               # conexão com o MySQL
from dotenv import load_dotenv       # lê o arquivo .env
from Organizador import (
    organizar,
    PASTA_BRONZE,
    resolver_destino,
    montar_chave_s3,
)  # cria bronze/<empresa>/<aeroporto>/ e devolve o caminho

# Carrega as configurações do arquivo .env, como os dados do banco.
load_dotenv()

# Bucket criado pelo script de configuração da equipe.
NOME_BUCKET = "montech-datalake-9526"

# Região da AWS onde o bucket foi criado.
REGIAO_AWS = "us-east-1"

# Cria o cliente que será usado para enviar arquivos ao S3.
# As credenciais são buscadas na configuração do ambiente.
cliente_s3 = boto3.client("s3", region_name=REGIAO_AWS)

# Pasta local inicial dos CSVs.
# Depois, o Organizador define a subpasta da empresa e do aeroporto.
PASTA_CSVS = PASTA_BRONZE


def enviar_csv_s3(caminho_csv, id_empresa, id_aeroporto, token):
    # Usa a data do nome do CSV para definir a pasta.
    # Assim, um arquivo iniciado ontem continua na pasta de ontem.
    nome_arquivo = os.path.basename(caminho_csv)
    data_arquivo = nome_arquivo[len(token) + 1:][:10]

    # Monta o destino dentro do bucket.
    chave_s3 = montar_chave_s3(
        "bronze",
        id_empresa,
        id_aeroporto,
        token,
        data_arquivo,
        nome_arquivo,
    )

    try:
        # Envia o CSV local para o caminho definido no S3.
        cliente_s3.upload_file(caminho_csv, NOME_BUCKET, chave_s3)
    except Exception as erro:
        # Mantém o arquivo local se o envio falhar.
        print(f"\nFalha ao enviar {nome_arquivo}: {erro}")
        return False

    print(f"\nCSV enviado para s3://{NOME_BUCKET}/{chave_s3}")
    return True


def conectarBanco():
    while True:
        try:
            conexao = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_NAME"),
            )
            print("Conexão bem-sucedida ao banco de dados MySQL!")
            return conexao
        except mysql.connector.Error as erro:
            print(f"Erro ao conectar ao banco de dados: {erro}")
            input("Pressione Enter para tentar conectar novamente...")


# Conexão principal, reutilizada em todo o script
conexao = conectarBanco()

# Limpa o terminal ("cls" no Windows, "clear" no Linux/Mac) e mostra o banner
os.system("cls" if os.name == "nt" else "clear")
print(r"""
-------------------------------------------

 __  __  ___  _   _ _____ _____ ____ _   _ 
|  \/  |/ _ \| \ | |_   _| ____/ ___| | | |
| |\/| | | | |  \| | | | |  _|| |   | |_| |
| |  | | |_| | |\  | | | | |__| |___|  _  |
|_|  |_|\___/|_| \_| |_| |_____\____|_| |_|

-------------------------------------------
""")
print("Bem-vindo ao Sistema de Coleta de Métricas dos seus Componentes!\n")
print("Para acessar o sistema, por favor, faça login com seu email e senha.\n")

# ============================================================
# ETAPA 1: LOGIN
# Repete até o usuário acertar email e senha.
# ============================================================
while True:
    try:
        email = input("Digite o email: ").strip()
        senha = input("Digite a senha: ").strip()
    except KeyboardInterrupt:
        print("\nLogin cancelado. Encerrando o programa.")
        try:
            if conexao.is_connected():
                conexao.close()
        except mysql.connector.Error:
            pass
        raise SystemExit(0)

    if not email or not senha:
        print("Email e senha não podem ficar vazios ou conter apenas espaços.")
        continue

    cursorAutenticacao = None

    try:
        if not conexao.is_connected():
            conexao.reconnect(attempts=3, delay=2)

        cursorAutenticacao = conexao.cursor(buffered=True)
        cursorAutenticacao.execute(
            "SELECT idUsuario, fkAeroporto FROM usuario WHERE email = %s AND senha = %s",
            (email, senha),
        )
        usuario = cursorAutenticacao.fetchone()
    except mysql.connector.Error as erro:
        print(f"\nErro no banco de dados durante o login: {erro}")
        conexao = conectarBanco()
        continue
    finally:
        if cursorAutenticacao is not None:
            try:
                cursorAutenticacao.close()
            except mysql.connector.Error:
                pass

    if usuario is not None:
        print("\nUsuário autenticado com sucesso!")
        break

    print("\nFalha na autenticação. Verifique o email e a senha.")
    os.system("cls" if os.name == "nt" else "clear")

# ============================================================
# ETAPA 2: TOKEN DO SERVIDOR + COMPONENTES
# Pede o token, descobre o idServidor, atualiza o status se for 0
# e carrega a lista de componentes.
# ============================================================
while True:
    try:
        tokenServidor = input("\nDigite o Token do servidor que deseja monitorar: ").strip()
    except KeyboardInterrupt:
        print("\nEntrada do token cancelada. Encerrando o programa.")
        try:
            if conexao.is_connected():
                conexao.close()
        except mysql.connector.Error:
            pass
        raise SystemExit(0)

    if not tokenServidor:
        print("\nO Token não pode ficar vazio.")
        continue

    cursorToken = None
    deveReconectar = False

    try:
        if not conexao.is_connected():
            conexao.reconnect(attempts=3, delay=2)

        cursorToken = conexao.cursor(buffered=True)
        cursorToken.execute(
            "SELECT idServidor FROM servidor WHERE token = %s",
            (tokenServidor,),
        )
        servidor = cursorToken.fetchone()
    except mysql.connector.Error as erro:
        print(f"\nErro no banco de dados durante a consulta do Token: {erro}")
        deveReconectar = True
    finally:
        if cursorToken is not None:
            try:
                cursorToken.close()
            except mysql.connector.Error:
                pass

    if deveReconectar:
        conexao = conectarBanco()
        continue

    if servidor is not None:
        print("\nServidor identificado com sucesso!")

        # ========================================================
        # ALTERAÇÃO: Atualiza o statusAtividade para 1 se estiver como 0
        # ========================================================
        cursorUpdate = None
        try:
            if not conexao.is_connected():
                conexao.reconnect(attempts=3, delay=2)

            cursorUpdate = conexao.cursor()
            cursorUpdate.execute(
                "UPDATE servidor SET statusAtividade = 1 WHERE idServidor = %s AND statusAtividade = 0",
                (servidor[0],)
            )
            conexao.commit()
            if cursorUpdate.rowcount > 0:
                print("Status de atividade do servidor atualizado para Ativo (1).")
        except mysql.connector.Error as erro:
            print(f"\nAviso: Não foi possível atualizar o status do servidor: {erro}")
        finally:
            if cursorUpdate is not None:
                try:
                    cursorUpdate.close()
                except mysql.connector.Error:
                    pass

        # Busca os componentes desse servidor
        cursorComponente = None
        try:
            if not conexao.is_connected():
                conexao.reconnect(attempts=3, delay=2)

            cursorComponente = conexao.cursor(buffered=True)
            cursorComponente.execute(
                "SELECT c.nomeCodigo, c.codigo FROM componente c JOIN servidorcomponente sc ON sc.fkComponente = c.idComponente JOIN servidor s ON s.idServidor = sc.fkServidor WHERE s.idServidor = %s",
                (servidor[0],),
            )
            componentes = cursorComponente.fetchall()
        except mysql.connector.Error as erro:
            print(f"\nErro no banco de dados ao buscar os componentes: {erro}")
            conexao = conectarBanco()
            continue
        finally:
            if cursorComponente is not None:
                try:
                    cursorComponente.close()
                except mysql.connector.Error:
                    pass

        if componentes is not None:
            print("\nComponentes recolhidos com sucesso!")
            break

        os.system("cls" if os.name == "nt" else "clear")
        print("Falha verificação dos componentes. Retorno nulo.")
    else:
        print("\nToken não encontrado. Verifique e tente novamente.")


# ============================================================
# ETAPA 3: COLETA DE MÉTRICAS
# ============================================================
if conexao.is_connected():
    PASTA_CSVS = organizar(tokenServidor, conexao) or PASTA_BRONZE
    
    destinoServidor = resolver_destino(tokenServidor, conexao)

    if destinoServidor is None:
        print("Servidor sem empresa/aeroporto definido. Coleta cancelada.")
        conexao.close()
        raise SystemExit(1)

    idEmpresa, idAeroporto, _ = destinoServidor

    os.system("cls" if os.name == "nt" else "clear")
    print("""\n========================================\n
                Iniciando Escrita
                \n========================================\n""")
    print(f"Gravando em: {PASTA_CSVS}\n")

    execucoes = 0
    intervaloCSVSegundos = 15 * 60
    estadoCSV = {"inicio": time.monotonic(), "caminho": None}
    arquivosPendentes = set()
    inicioMonitoramento = time.monotonic()

    def atualizarStatus(indiceAnimacao):
        segundosAtivos = int(time.monotonic() - inicioMonitoramento)
        horas, resto = divmod(segundosAtivos, 3600)
        minutos, segundos = divmod(resto, 60)
        caminhoAtual = (
            os.path.basename(estadoCSV["caminho"])
            if estadoCSV["caminho"] is not None
            else "aguardando primeira coleta"
        )
        indicador = "|/-\\"[indiceAnimacao % 4]
        print(
            f"\r{indicador} Monitoramento ativo | coletas: {execucoes} | "
            f"tempo: {horas:02}:{minutos:02}:{segundos:02} | CSV: {caminhoAtual}",
            end="",
            flush=True,
        )

    def aguardarProximaColeta():
        fimEspera = time.monotonic() + 3
        indiceAnimacao = 0
        while time.monotonic() < fimEspera:
            atualizarStatus(indiceAnimacao)
            indiceAnimacao += 1
            time.sleep(0.2)

    def buscarComponentesAtualizados():
        cursorAtualizacao = None
        try:
            if not conexao.is_connected():
                conexao.reconnect(attempts=3, delay=2)

            cursorAtualizacao = conexao.cursor(buffered=True)
            cursorAtualizacao.execute(
                "SELECT c.nomeCodigo, c.codigo FROM componente c "
                "JOIN servidorcomponente sc ON sc.fkComponente = c.idComponente "
                "JOIN servidor s ON s.idServidor = sc.fkServidor "
                "WHERE s.idServidor = %s",
                (servidor[0],),
            )
            return cursorAtualizacao.fetchall()
        except mysql.connector.Error as erro:
            print(f"\nNão foi possível atualizar as métricas: {erro}")
            return None
        finally:
            if cursorAtualizacao is not None:
                try:
                    cursorAtualizacao.close()
                except mysql.connector.Error:
                    pass

    def coletarMetricas():
        global componentes

        agoraMonotonico = time.monotonic()
        novoArquivo = (
            estadoCSV["caminho"] is None
            or agoraMonotonico - estadoCSV["inicio"] >= intervaloCSVSegundos
        )
        if novoArquivo:
            if estadoCSV["caminho"] is not None:
                arquivosPendentes.add(estadoCSV["caminho"])

            for caminhoPendente in list(arquivosPendentes):
                enviado = enviar_csv_s3(
                    caminhoPendente,
                    idEmpresa,
                    idAeroporto,
                    tokenServidor,
                )

                if enviado:
                    arquivosPendentes.discard(caminhoPendente)

            if estadoCSV["caminho"] is not None:
                componentesAtualizados = buscarComponentesAtualizados()
                if componentesAtualizados is not None:
                    componentes = componentesAtualizados

            estadoCSV["inicio"] = agoraMonotonico
            nomeArquivo = f"{tokenServidor}-{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.csv"
            estadoCSV["caminho"] = os.path.join(PASTA_CSVS, nomeArquivo)

        resultados = {
            "TimeStamp": [datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            "IdAeroporto": [idAeroporto],
        }

        for nomeCodigo, codigo in componentes:
            try:
                valor = eval(codigo)
                if isinstance(valor, (list, tuple, dict)):
                    valor = str(valor)
            except Exception as erro:
                print(f"Erro ao executar '{codigo}': {erro}")
                valor = None
            resultados[nomeCodigo] = [valor]

        df_Novo = pnd.DataFrame(resultados)
        caminhoCSV = estadoCSV["caminho"]

        os.makedirs(os.path.dirname(caminhoCSV), exist_ok=True)
        arquivoExiste = os.path.exists(caminhoCSV)

        df_Novo.to_csv(
            caminhoCSV,
            sep=';',
            mode='a',
            index=False,
            header=not arquivoExiste,
            encoding='utf-8'
        )

        global execucoes
        execucoes += 1


    if __name__ == "__main__":
        try:
            while True:
                aguardarProximaColeta()
                coletarMetricas()
        except KeyboardInterrupt:
            caminhoFinal = estadoCSV["caminho"]

            if caminhoFinal is not None and os.path.isfile(caminhoFinal):
                arquivosPendentes.add(caminhoFinal)

            for caminhoPendente in list(arquivosPendentes):
                enviado = enviar_csv_s3(
                    caminhoPendente,
                    idEmpresa,
                    idAeroporto,
                    tokenServidor,
                )

                if enviado:
                    arquivosPendentes.discard(caminhoPendente)

            if arquivosPendentes:
                print(
                    f"\nAtenção: {len(arquivosPendentes)} arquivo(s) "
                    "não enviado(s). Os CSVs continuam salvos localmente."
                )

            print(
                f"\nMonitoramento encerrado. Coletas salvas: {execucoes}.",
                flush=True,
            )
