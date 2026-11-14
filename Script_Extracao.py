# ============================================================
# Sistema de Coleta de Métricas
# Fluxo geral:
#   1. Conecta ao banco MySQL
#   2. Faz login do usuário (email + senha)
#   3. Pede o token do servidor, valida no banco
#      e atualiza o statusAtividade para 1 se estiver como 0
#   4. O Organizador (executado UMA vez) descobre empresa/aeroporto do token,
#      cria bronze/<empresa>/<aeroporto>/ se não existir e devolve esse caminho
#   5. Em loop, coleta TODAS as métricas definidas em COLETORES (neste arquivo,
#      independente do que está cadastrado no banco) e grava uma linha no CSV,
#      direto dentro da pasta devolvida pelo Organizador.
#      As colunas do CSV são os mesmos nomeCodigo da tabela "componente".
#      Quem decide quais colunas serão usadas é a ETL, com base no banco.
#      Existe UM CSV por dia (<token>-AAAA-MM-DD.csv): se ele já existir,
#      as novas linhas são adicionadas ao final, sem sobrescrever nada.
#      A cada novo registro, o CSV do dia é enviado ao S3.
#
# IMPORTANTE: este arquivo e o Organizador.py precisam ficar na MESMA pasta.
# ============================================================

import pandas as pnd                 # monta o DataFrame e exporta para CSV
from datetime import datetime        # data/hora das coletas
import os                            # variáveis de ambiente, pastas, clear do terminal
import boto3                         # permitir que desenvolvedores escrevam scripts e aplicativos em Python para interagir diretamente com os serviços da AWS
import psutil                        # biblioteca de métricas (CPU, RAM, disco...)
import time                          # sleep e relógio monotônico
import subprocess                    # chama o PowerShell para ler a temperatura via WMI (Windows)
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
NOME_BUCKET = "montech-datalake-4333"

# Região da AWS onde o bucket foi criado.
REGIAO_AWS = "us-east-1"

# Cria o cliente que será usado para enviar arquivos ao S3.
# As credenciais são buscadas na configuração do ambiente.
cliente_s3 = boto3.client("s3", region_name=REGIAO_AWS)

# Pasta local inicial dos CSVs.
# Depois, o Organizador define a subpasta da empresa e do aeroporto.
PASTA_CSVS = PASTA_BRONZE

# Disco monitorado: raiz do Windows ou do Linux/Mac.
DISCO_RAIZ = "C:\\" if os.name == "nt" else "/"

def lerTemperaturaWindowsWMI():
    comando = (
        "Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
        "| ForEach-Object { $_.CurrentTemperature }"
    )
    try:
        saida = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", comando],
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
    except Exception:
        return None

    temperaturas = []
    for linha in saida.split():
        try:
            celsius = float(linha) / 10 - 273.15   # vem em décimos de Kelvin
        except ValueError:
            continue
        if 5 < celsius < 150:                      # descarta valores falsos/fixos (ex.: 2732 = 0 °C)
            temperaturas.append(celsius)

    return round(max(temperaturas), 1) if temperaturas else None


def lerTemperaturaCPU():
    # Windows: lê via WMI (sem bibliotecas extras).
    if os.name == "nt":
        return lerTemperaturaWindowsWMI()

    # Linux/Mac: usa o psutil e devolve um número (°C), igual ao do Windows.
    sensores = psutil.sensors_temperatures()
    if not sensores:
        return None

    # Prefere os sensores de CPU mais comuns; senão pega o primeiro disponível.
    for chave in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "cpu-thermal"):
        if chave in sensores and sensores[chave]:
            leituras = sensores[chave]
            break
    else:
        leituras = next((l for l in sensores.values() if l), None)
        if leituras is None:
            return None

    # Prefere a leitura geral do processador (Package/Tctl) quando existir.
    for leitura in leituras:
        if any(t in (leitura.label or "") for t in ("Package", "Tctl", "Tdie")):
            return leitura.current
    return leituras[0].current


# ============================================================
# MÉTRICAS COLETADAS
# Chave = nomeCodigo (mesmo valor da coluna nomeCodigo da tabela
# "componente" e nome da coluna no CSV).
# Valor = função que retorna a métrica (lambda, para só executar
# no momento da coleta).
# Para adicionar uma métrica nova: crie a linha aqui E cadastre o
# mesmo nomeCodigo na tabela "componente".
# ============================================================
COLETORES = {
    "UsoCPU_Geral":           lambda: psutil.cpu_percent(interval=1),
    "UsoRAM":                 lambda: psutil.virtual_memory().percent,
    "Swap_In":                lambda: psutil.swap_memory().sin,
    "Swap_Out":               lambda: psutil.swap_memory().sout,
    "UsoDisco":               lambda: psutil.disk_usage(DISCO_RAIZ).percent,
    "Disco_Read_Bytes":       lambda: psutil.disk_io_counters().read_bytes,
    "Disco_Write_Bytes":      lambda: psutil.disk_io_counters().write_bytes,
    "Rede_Bytes_Enviados":    lambda: psutil.net_io_counters().bytes_sent,
    "Rede_Bytes_Recebidos":   lambda: psutil.net_io_counters().bytes_recv,
    "Rede_Pacotes_Enviados":  lambda: psutil.net_io_counters().packets_sent,
    "Rede_Pacotes_Recebidos": lambda: psutil.net_io_counters().packets_recv,
    "Temperatura_CPU":        lerTemperaturaCPU,
    "Qtd_Processos":          lambda: len(psutil.pids()),

    # Adicionais para predição (apenas os usados no script R)
    "RAM_Livre":              lambda: psutil.virtual_memory().free,
    "CPU_Ctx_Switches":       lambda: psutil.cpu_stats().ctx_switches,
    "CPU_Interrupts":         lambda: psutil.cpu_stats().interrupts,
    "CPU_Syscalls":           lambda: psutil.cpu_stats().syscalls,
    "Disco_Write_Count":      lambda: psutil.disk_io_counters().write_count,
}


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
# ETAPA 2: TOKEN DO SERVIDOR
# Pede o token, descobre o idServidor e atualiza o status se for 0.
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

        # Os componentes NÃO são mais buscados aqui: o script coleta tudo
        # o que está em COLETORES. A seleção por servidor é feita na ETL.
        break
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
    estadoCSV = {"dia": None, "caminho": None}
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

    def coletarMetricas():
        agora = datetime.now()
        diaAtual = agora.strftime("%Y-%m-%d")

        # Só troca de arquivo quando o dia muda (ou na primeira coleta)
        novoArquivo = (
            estadoCSV["caminho"] is None
            or estadoCSV["dia"] != diaAtual
        )
        if novoArquivo:
            # Reenvia arquivos cujo envio falhou antes (o do dia anterior já foi
            # enviado a cada registro, então só entra aqui se houve falha).
            for caminhoPendente in list(arquivosPendentes):
                enviado = enviar_csv_s3(
                    caminhoPendente,
                    idEmpresa,
                    idAeroporto,
                    tokenServidor,
                )

                if enviado:
                    arquivosPendentes.discard(caminhoPendente)

            estadoCSV["dia"] = diaAtual
            # Um CSV por dia. Se já existir (ex.: script reiniciado no mesmo dia),
            # o bloco de gravação abaixo apenas adiciona as linhas no final.
            nomeArquivo = f"{tokenServidor}-{diaAtual}.csv"
            estadoCSV["caminho"] = os.path.join(PASTA_CSVS, nomeArquivo)

        resultados = {
            "TimeStamp": [agora.strftime("%Y-%m-%d %H:%M:%S")],
            "IdAeroporto": [idAeroporto],
        }

        for nomeCodigo, coletor in COLETORES.items():
            try:
                valor = coletor()
                if isinstance(valor, (list, tuple, dict)):
                    valor = str(valor)
            except Exception as erro:
                # Ex.: sensors_temperatures() não existe no Windows.
                # A coluna continua no CSV, só que vazia.
                print(f"\nErro ao coletar '{nomeCodigo}': {erro}")
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

        # Envia o CSV do dia ao S3 a cada novo registro (o arquivo inteiro,
        # substituindo a versão anterior na mesma chave). Se falhar, fica
        # pendente e é reenviado no próximo registro.
        enviado = enviar_csv_s3(
            caminhoCSV,
            idEmpresa,
            idAeroporto,
            tokenServidor,
        )
        if enviado:
            arquivosPendentes.discard(caminhoCSV)
        else:
            arquivosPendentes.add(caminhoCSV)

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