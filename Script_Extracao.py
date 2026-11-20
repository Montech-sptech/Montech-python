# ============================================================
# Sistema de Coleta de Métricas
# Fluxo geral:
#   1. Conecta ao banco MySQL
#   2. Faz login do usuário (email + senha)
#   3. Pede o token do servidor, busca no banco os componentes selecionados
#      para ele (tabela servidorcomponente) e atualiza o statusAtividade
#      para 1 se estiver como 0
#   4. O Organizador (executado UMA vez) descobre empresa/aeroporto do token,
#      cria bronze/<empresa>/<aeroporto>/ se não existir e devolve esse caminho
#   5. Em loop, coleta apenas as métricas dos componentes selecionados: o
#      nomeCodigo vindo do banco é procurado em COLETORES (neste arquivo),
#      que guarda o comando psutil de cada métrica. Grava uma linha no CSV,
#      dentro de <pasta do Organizador>/<token>/<DD-MM-AAAA>/.
#      As colunas do CSV são os nomeCodigo dos componentes selecionados.
#      Existe UM CSV por dia (<token>-AAAA-MM-DD.csv): se ele já existir,
#      as novas linhas são adicionadas ao final, sem sobrescrever nada.
#      A cada registro, também são gravados arquivos pequenos na pasta da hora
#      (<token>/<DD-MM-AAAA>/<HH>/) e enviados à Lambda (URL_LAMBDA), que grava
#      no S3. O CSV do dia fica apenas local.
#      Frequência: o sistema AMOSTRA as métricas a cada INTERVALO_AMOSTRA_SEGUNDOS
#      e GRAVA um registro a cada INTERVALO_REGISTRO_SEGUNDOS, com a
#      MÉDIA das amostras da janela.
#      Se o componente Qtd_Processos estiver selecionado, também é capturada a
#      lista de todos os processos (PID, Nome, CPU, RAM) e gravada em outro CSV
#      na mesma pasta (<token>-AAAA-MM-DD-processos.csv), com o mesmo
#      TimeStamp e IdAeroporto do CSV de componentes. O CPU e a RAM de cada
#      processo também são a média das amostras da janela.
#
# IMPORTANTE: este arquivo e o Organizador.py precisam ficar na MESMA pasta.
# ============================================================

import pandas as pnd                 # monta o DataFrame e exporta para CSV
from datetime import datetime        # data/hora das coletas
import os                            # variáveis de ambiente, pastas, clear do terminal
import shutil                        # largura do terminal (para a linha de status não quebrar)
import psutil                        # biblioteca de métricas (CPU, RAM, disco...)
import time                          # sleep e relógio monotônico
import subprocess                    # chama o PowerShell para ler a temperatura via WMI (Windows)
import mysql.connector               # conexão com o MySQL
import urllib.request                # envia o CSV por requisição HTTP para a Lambda
from urllib.parse import quote       # codifica o nome do arquivo na URL
from dotenv import load_dotenv       # lê o arquivo .env
from Organizador import (
    organizar,
    PASTA_BRONZE,
    resolver_destino,
)  # cria bronze/<empresa>/<aeroporto>/ e devolve o caminho

# Carrega as configurações do arquivo .env, como os dados do banco.
load_dotenv()

# URL da Lambda (Function URL) que recebe os CSVs e grava no S3.
# PREENCHER com a URL real, por exemplo:
# "https://xxxxxxxx.lambda-url.us-east-1.on.aws/"
URL_LAMBDA = ""

# Tempo máximo (segundos) de espera pela resposta da Lambda.
TIMEOUT_LAMBDA_SEGUNDOS = 30

# Pasta local inicial dos CSVs.
# Depois, o Organizador define a subpasta da empresa e do aeroporto.
PASTA_CSVS = PASTA_BRONZE

# ============================================================
# FREQUÊNCIA DE COLETA (altere aqui)
# INTERVALO_REGISTRO_SEGUNDOS: de quanto em quanto tempo uma linha é gravada
#   nos CSVs (valor = média das amostras da janela).
# INTERVALO_AMOSTRA_SEGUNDOS: de quanto em quanto tempo as métricas são lidas
#   para compor essa média.
# ============================================================
INTERVALO_REGISTRO_SEGUNDOS = 10
INTERVALO_AMOSTRA_SEGUNDOS = 5

# Quantas amostras entram em cada registro (registro / amostra). Mínimo de 1.
AMOSTRAS_POR_REGISTRO = max(1, INTERVALO_REGISTRO_SEGUNDOS // INTERVALO_AMOSTRA_SEGUNDOS)

# Disco monitorado: raiz do Windows ou do Linux/Mac.
DISCO_RAIZ = "C:\\" if os.name == "nt" else "/"

def lerTemperaturaWindowsWMI():
    # Alternativa simples (sem DLL): pergunta ao Windows via WMI/PowerShell.
    # Limitações: costuma exigir terminal como Administrador e nem toda máquina
    # expõe esse sensor (é a "zona térmica" ACPI, não o núcleo da CPU).
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
# MÉTRICAS DISPONÍVEIS
# Chave = nomeCodigo (mesmo valor da coluna nomeCodigo da tabela
# "componente" e nome da coluna no CSV).
# Valor = função que retorna a métrica (lambda, para só executar
# no momento da coleta).
# Só são executadas as métricas cujo nomeCodigo está selecionado para o
# servidor na tabela servidorcomponente.
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
    "Qtd_Processos":          lambda: len(psutil.pids())
}


# Quantidade de núcleos lógicos, usada para normalizar o uso de CPU por processo.
# O psutil devolve o uso de CPU de um processo em relação a UM núcleo (pode passar
# de 100% em máquinas multi-core). Dividindo pelo total de núcleos, o valor fica
# entre 0 e 100% da máquina inteira (igual ao Gerenciador de Tarefas).
QTD_NUCLEOS = psutil.cpu_count() or 1


def coletarProcessos():
    # Devolve um array (lista) com um dicionário por processo em execução:
    # {"PID": ..., "Nome": ..., "CPU": ..., "RAM": ...}
    # O process_iter mantém os objetos Process entre as chamadas, então o
    # cpu_percent mede o uso desde a última coleta. Na PRIMEIRA chamada o valor
    # de CPU vem 0 (não há medição anterior), por isso ela é feita uma vez
    # antes do loop principal para "aquecer" a leitura.
    processos = []

    for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = proc.info
            cpu = info["cpu_percent"]
            ram = info["memory_percent"]

            processos.append({
                "PID": info["pid"],
                "Nome": info["name"] or "",
                "CPU": round(cpu / QTD_NUCLEOS, 2) if cpu is not None else None,
                "RAM": round(ram, 2) if ram is not None else None,
            })
        except (psutil.NoSuchProcess, psutil.ZombieProcess):
            # O processo terminou durante a leitura: ignora.
            continue

    return processos


def calcularMedia(valores):
    # Média dos valores numéricos da janela (ignora vazios/None), com 2 casas.
    # Se não houver nenhum número (ex.: sensor indisponível), devolve o último
    # valor não nulo (caso seja texto) ou None.
    numeros = [
        v for v in valores
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    ]
    if numeros:
        return round(sum(numeros) / len(numeros), 2)

    restantes = [v for v in valores if v is not None]
    return restantes[-1] if restantes else None


def calcularMediaProcessos(amostrasProcessos):
    # Recebe uma lista de amostras (cada amostra é o array devolvido por
    # coletarProcessos) e devolve um único array com a média de CPU e RAM de
    # cada processo (identificado por PID + Nome). A média considera as
    # amostras em que o processo estava presente.
    acumulado = {}

    for amostra in amostrasProcessos:
        for proc in amostra:
            chave = (proc["PID"], proc["Nome"])
            item = acumulado.setdefault(chave, {"CPU": [], "RAM": []})
            item["CPU"].append(proc["CPU"])
            item["RAM"].append(proc["RAM"])

    return [
        {
            "PID": pid,
            "Nome": nome,
            "CPU": calcularMedia(item["CPU"]),
            "RAM": calcularMedia(item["RAM"]),
        }
        for (pid, nome), item in sorted(acumulado.items(), key=lambda x: x[0][0])
    ]


def enviar_csv_s3(caminho_csv):
    nome_arquivo = os.path.basename(caminho_csv)

    # Remove espaços/quebras de linha que sobram ao colar a URL.
    url_lambda = URL_LAMBDA.strip()

    if not url_lambda:
        print(f"\nFalha ao enviar {nome_arquivo}: URL_LAMBDA não foi configurada.")
        return False

    # O caminho vem do Organizador (pasta exata do arquivo). Ele vira a chave
    # no S3, sempre começando por "01-bronze/" (acrescenta se não vier; se o
    # Organizador devolver apenas "bronze", ele é trocado por "01-bronze").
    caminho = os.path.normpath(caminho_csv).replace("\\", "/")
    partes = [p for p in caminho.split("/") if p not in ("", ".")]
    if "01-bronze" in partes:
        partes = partes[partes.index("01-bronze"):]
    elif "bronze" in partes:
        partes = ["01-bronze"] + partes[partes.index("bronze") + 1:]
    else:
        partes = ["01-bronze"] + partes
    chave_s3 = "/".join(partes)

    try:
        with open(caminho_csv, "rb") as arquivo:
            conteudo = arquivo.read()

        # A Lambda lê o nome/caminho do arquivo no query param "filename".
        requisicao = urllib.request.Request(
            f"{url_lambda}?filename={quote(chave_s3, safe='/')}",
            data=conteudo,
            method="POST",
            headers={"Content-Type": "text/csv"},
        )
        with urllib.request.urlopen(requisicao, timeout=TIMEOUT_LAMBDA_SEGUNDOS) as resposta:
            if resposta.status != 200:
                raise RuntimeError(f"resposta HTTP {resposta.status}")
    except Exception as erro:
        # Mantém o arquivo local se o envio falhar.
        print(f"\nFalha ao enviar {nome_arquivo}: {erro}")
        return False

    print(f"\nCSV enviado: {chave_s3}")
    return True


def conectarBanco():
    while True:
        try:
            conexao = mysql.connector.connect(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_NAME"),
                ssl_disabled=True,
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
# e carrega a lista de componentes selecionados (nomeCodigo).
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

        # Busca os componentes selecionados para esse servidor
        # (só o nomeCodigo: o comando psutil fica em COLETORES)
        cursorComponente = None
        try:
            if not conexao.is_connected():
                conexao.reconnect(attempts=3, delay=2)

            cursorComponente = conexao.cursor(buffered=True)
            cursorComponente.execute(
                "SELECT c.nomeCodigo FROM componente c "
                "JOIN servidorcomponente sc ON sc.fkComponente = c.idComponente "
                "JOIN servidor s ON s.idServidor = sc.fkServidor "
                "WHERE s.idServidor = %s",
                (servidor[0],),
            )
            componentes = [linha[0] for linha in cursorComponente.fetchall()]
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

    _, idAeroporto, _ = destinoServidor

    os.system("cls" if os.name == "nt" else "clear")
    print("""\n========================================\n
                Iniciando Escrita
                \n========================================\n""")
    print(f"Gravando em: {PASTA_CSVS}\n")
    if not componentes:
        print(
            "ATENÇÃO: nenhum componente selecionado para este servidor no banco "
            "(tabela servidorcomponente).\nOs CSVs terão apenas TimeStamp e "
            "IdAeroporto até que componentes sejam selecionados.\n"
        )
    else:
        print(f"Componentes selecionados ({len(componentes)}): {', '.join(componentes)}")
        desconhecidos = [c for c in componentes if c not in COLETORES]
        if desconhecidos:
            print(f"ATENÇÃO: sem coletor em COLETORES para: {', '.join(desconhecidos)}")
        print()

    execucoes = 0
    estadoCSV = {"dia": None, "caminho": None}
    arquivosPendentes = set()
    bufferMetricas = []            # amostras da janela atual
    bufferProcessos = []           # arrays de processos da janela atual
    avisoSemComponentes = {"emitido": False}
    codigosDesconhecidos = set()   # nomeCodigo do banco que não existe em COLETORES
    inicioMonitoramento = time.monotonic()

    ultimoSegundoExibido = {"valor": None}

    def atualizarStatus():
        # Só redesenha a linha quando o contador de segundos muda (1x por segundo),
        # em vez de a cada passo do loop de espera.
        segundosAtivos = int(time.monotonic() - inicioMonitoramento)
        if segundosAtivos == ultimoSegundoExibido["valor"]:
            return
        ultimoSegundoExibido["valor"] = segundosAtivos

        horas, resto = divmod(segundosAtivos, 3600)
        minutos, segundos = divmod(resto, 60)
        caminhoAtual = (
            os.path.basename(estadoCSV["caminho"])
            if estadoCSV["caminho"] is not None
            else "aguardando primeiro registro"
        )
        indicador = "|/-\\"[segundosAtivos % 4]
        texto = (
            f"{indicador} Monitoramento ativo | registros: {execucoes} | "
            f"amostras: {len(bufferMetricas)}/{AMOSTRAS_POR_REGISTRO} | "
            f"tempo: {horas:02}:{minutos:02}:{segundos:02} | CSV: {caminhoAtual}"
        )

        # Se a linha for maior que a largura do terminal, ela quebra em várias
        # linhas e o "\r" não consegue reescrevê-la (efeito de linhas repetidas).
        # Por isso a linha é cortada para caber e completada com espaços.
        largura = shutil.get_terminal_size((120, 20)).columns - 1
        texto = texto[:largura].ljust(largura)
        print(f"\r{texto}", end="", flush=True)

    def aguardarAte(fimEspera):
        # Espera até o instante fimEspera (time.monotonic), atualizando o status
        while time.monotonic() < fimEspera:
            atualizarStatus()
            time.sleep(0.2)

    def buscarComponentesAtualizados():
        cursorAtualizacao = None
        try:
            if not conexao.is_connected():
                conexao.reconnect(attempts=3, delay=2)

            cursorAtualizacao = conexao.cursor(buffered=True)
            cursorAtualizacao.execute(
                "SELECT c.nomeCodigo FROM componente c "
                "JOIN servidorcomponente sc ON sc.fkComponente = c.idComponente "
                "JOIN servidor s ON s.idServidor = sc.fkServidor "
                "WHERE s.idServidor = %s",
                (servidor[0],),
            )
            return [linha[0] for linha in cursorAtualizacao.fetchall()]
        except mysql.connector.Error as erro:
            print(f"\nNão foi possível atualizar as métricas: {erro}")
            return None
        finally:
            if cursorAtualizacao is not None:
                try:
                    cursorAtualizacao.close()
                except mysql.connector.Error:
                    pass

    def tirarAmostra():
        # Lê agora as métricas dos componentes selecionados (e, se Qtd_Processos
        # estiver selecionado, a lista de processos) e guarda nos buffers.
        global componentes
        amostra = {}

        # Sem componentes selecionados não há o que medir. Em vez de gravar linhas
        # só com TimeStamp/IdAeroporto, relê o banco a cada amostra (assim, quando
        # os componentes forem selecionados, a coleta começa sozinha).
        if not componentes:
            atualizados = buscarComponentesAtualizados()
            if not atualizados:
                if not avisoSemComponentes["emitido"]:
                    avisoSemComponentes["emitido"] = True
                    print(
                        "\nAviso: nenhum componente selecionado para este servidor "
                        "(tabela servidorcomponente). Nada será gravado até que "
                        "existam componentes; o banco é consultado a cada amostra."
                    )
                return
            componentes = atualizados
            avisoSemComponentes["emitido"] = False
            print(f"\nComponentes selecionados: {', '.join(componentes)}")
            if "Qtd_Processos" in componentes:
                coletarProcessos()   # aquece a leitura de CPU por processo

        for nomeCodigo in componentes:
            coletor = COLETORES.get(nomeCodigo)

            if coletor is None:
                # nomeCodigo cadastrado no banco que não existe em COLETORES
                # (provável diferença de grafia). Avisa só uma vez e ignora.
                if nomeCodigo not in codigosDesconhecidos:
                    codigosDesconhecidos.add(nomeCodigo)
                    print(
                        f"\nAviso: '{nomeCodigo}' está no banco mas não existe "
                        "em COLETORES. Componente ignorado."
                    )
                continue

            try:
                valor = coletor()
                if isinstance(valor, (list, tuple, dict)):
                    valor = str(valor)
            except Exception as erro:
                # Ex.: sensor de temperatura indisponível na máquina.
                print(f"\nErro ao coletar '{nomeCodigo}': {erro}")
                valor = None
            amostra[nomeCodigo] = valor

        bufferMetricas.append(amostra)

        if "Qtd_Processos" in componentes:
            bufferProcessos.append(coletarProcessos())

    def gravarRegistro():
        # Calcula a média das amostras da janela e grava uma linha em cada CSV.
        global componentes

        agora = datetime.now()
        diaAtual = agora.strftime("%Y-%m-%d")

        # Só troca de arquivo quando o dia muda (ou na primeira coleta)
        novoArquivo = (
            estadoCSV["caminho"] is None
            or estadoCSV["dia"] != diaAtual
        )
        if novoArquivo:
            # Reenvia arquivos cujo envio falhou antes.
            for caminhoPendente in list(arquivosPendentes):
                enviado = enviar_csv_s3(caminhoPendente)

                if enviado:
                    arquivosPendentes.discard(caminhoPendente)

            # A cada novo dia, relê no banco quais componentes estão selecionados.
            if estadoCSV["caminho"] is not None:
                componentesAtualizados = buscarComponentesAtualizados()
                if componentesAtualizados is not None:
                    componentes = componentesAtualizados

            estadoCSV["dia"] = diaAtual
            # Um CSV por dia. Se já existir (ex.: script reiniciado no mesmo dia),
            # o bloco de gravação abaixo apenas adiciona as linhas no final.
            nomeArquivo = f"{tokenServidor}-{diaAtual}.csv"

            # Pasta com o token do servidor como nome (não duplica se o
            # Organizador já tiver devolvido o caminho terminando no token).
            if os.path.basename(os.path.normpath(PASTA_CSVS)) == tokenServidor:
                pastaToken = PASTA_CSVS
            else:
                pastaToken = os.path.join(PASTA_CSVS, tokenServidor)

            # Dentro dela, uma pasta por dia no formato DD-MM-AAAA.
            pastaDia = os.path.join(pastaToken, agora.strftime("%d-%m-%Y"))
            estadoCSV["caminho"] = os.path.join(pastaDia, nomeArquivo)

        # Mesmo TimeStamp para o CSV de componentes e o de processos
        timestamp = agora.strftime("%Y-%m-%d %H:%M:%S")

        resultados = {
            "TimeStamp": [timestamp],
            "IdAeroporto": [idAeroporto],
        }

        # Cada coluna recebe a MÉDIA das amostras da janela.
        for nomeCodigo in componentes:
            if nomeCodigo not in COLETORES:
                continue   # já avisado em tirarAmostra
            resultados[nomeCodigo] = [
                calcularMedia([amostra.get(nomeCodigo) for amostra in bufferMetricas])
            ]

        # Média de CPU/RAM de cada processo (só se Qtd_Processos foi selecionado).
        processos = (
            calcularMediaProcessos(bufferProcessos)
            if "Qtd_Processos" in componentes
            else None
        )

        # Janela fechada: limpa os buffers para a próxima.
        bufferMetricas.clear()
        bufferProcessos.clear()

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

        # Grava os processos em outro CSV, na mesma pasta do CSV do dia.
        # O nome começa com <token>-AAAA-MM-DD, igual ao CSV de componentes.
        if processos:
            df_Processos = pnd.DataFrame(processos)
            df_Processos.insert(0, "IdAeroporto", idAeroporto)
            df_Processos.insert(0, "TimeStamp", timestamp)

            caminhoProcessos = os.path.join(
                os.path.dirname(caminhoCSV),
                f"{tokenServidor}-{estadoCSV['dia']}-processos.csv",
            )
            df_Processos.to_csv(
                caminhoProcessos,
                sep=';',
                mode='a',
                index=False,
                header=not os.path.exists(caminhoProcessos),
                encoding='utf-8'
            )

        # Além do CSV do dia (histórico local), cada registro também é gravado
        # em arquivos pequenos dentro da pasta da hora, que fica dentro da
        # pasta do dia: <token>/<DD-MM-AAAA>/<HH>/. Só esses arquivos são
        # enviados à Lambda (cada um uma única vez, sem reenviar o do dia).
        pastaHora = os.path.join(os.path.dirname(caminhoCSV), agora.strftime("%H"))
        os.makedirs(pastaHora, exist_ok=True)
        sufixoRegistro = agora.strftime("%Y-%m-%d-%H-%M-%S")

        arquivosDoRegistro = []

        caminhoRegistro = os.path.join(pastaHora, f"{tokenServidor}-{sufixoRegistro}.csv")
        df_Novo.to_csv(caminhoRegistro, sep=';', index=False, header=True, encoding='utf-8')
        arquivosDoRegistro.append(caminhoRegistro)

        if processos:
            caminhoRegistroProcessos = os.path.join(
                pastaHora, f"{tokenServidor}-{sufixoRegistro}-processos.csv"
            )
            df_Processos.to_csv(
                caminhoRegistroProcessos, sep=';', index=False, header=True, encoding='utf-8'
            )
            arquivosDoRegistro.append(caminhoRegistroProcessos)

        # Envia os arquivos do registro. Se falhar, ficam pendentes.
        todosEnviados = True
        for caminhoArquivo in arquivosDoRegistro:
            enviado = enviar_csv_s3(caminhoArquivo)
            if not enviado:
                arquivosPendentes.add(caminhoArquivo)
                todosEnviados = False

        # Se a Lambda respondeu, tenta alguns pendentes antigos (poucos por vez,
        # para não atrasar a coleta). Se ela está fora do ar, não insiste.
        if todosEnviados:
            for caminhoPendente in list(arquivosPendentes)[:5]:
                if not os.path.isfile(caminhoPendente):
                    arquivosPendentes.discard(caminhoPendente)
                    continue
                if enviar_csv_s3(caminhoPendente):
                    arquivosPendentes.discard(caminhoPendente)

        global execucoes
        execucoes += 1


    if __name__ == "__main__":
        try:
            # Aquece a leitura de CPU por processo (a 1ª chamada sempre retorna 0)
            if "Qtd_Processos" in componentes:
                coletarProcessos()

            proximaAmostra = time.monotonic() + INTERVALO_AMOSTRA_SEGUNDOS

            while True:
                aguardarAte(proximaAmostra)
                tirarAmostra()
                proximaAmostra += INTERVALO_AMOSTRA_SEGUNDOS
                # Se a leitura demorou mais que o intervalo, não acumula atraso.
                proximaAmostra = max(proximaAmostra, time.monotonic())

                if len(bufferMetricas) >= AMOSTRAS_POR_REGISTRO:
                    gravarRegistro()
        except KeyboardInterrupt:
            for caminhoPendente in list(arquivosPendentes):
                enviado = enviar_csv_s3(caminhoPendente)

                if enviado:
                    arquivosPendentes.discard(caminhoPendente)

            if arquivosPendentes:
                print(
                    f"\nAtenção: {len(arquivosPendentes)} arquivo(s) "
                    "não enviado(s). Os CSVs continuam salvos localmente."
                )

            print(
                f"\nMonitoramento encerrado. Registros salvos: {execucoes}.",
                flush=True,
            )