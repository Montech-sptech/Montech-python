# ============================================================
# Sistema de Coleta de Métricas (atualizado)
# Comportamento atual:
# - Coleta amostras a cada INTERVALO_AMOSTRA_SEGUNDOS (padrão 5s).
# - Calcula registros a cada INTERVALO_REGISTRO_SEGUNDOS (padrão 10s), com a
#   média das amostras da janela (AMOSTRAS_POR_REGISTRO = 2).
# - Os registros são alinhados ao relógio: os timestamps dos registros ficam
#   em segundos múltiplos de 10 (por exemplo :00, :10, :20 ...). O script
#   espera no início até alinhar a amostragem para que os registros caiam
#   nesses pontos do relógio.
# - Os arquivos são gerados por MINUTO de relógio (ex.: 14:03:00-14:03:59) e
#   contêm até 6 registros (6 x 10s). Cada minuto gera dois arquivos quando
#   aplicável: components (com os registros do minuto) e processos (captura
#   única feita no fechamento do minuto). O nome dos arquivos identifica o
#   minuto: <token>-AAAA-MM-DD-HH-MM-components.csv e -processos.csv.
# - Se o script for iniciado no meio do minuto, o primeiro arquivo pode ser
#   parcial (menos de 6 registros); ele é gravado com o minuto correto.
# - Em caso de encerramento (KeyboardInterrupt), se houver registros no buffer
#   do minuto, um arquivo parcial é gravado e enviado (ou marcado como pendente).
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
URL_LAMBDA = "https://cc5s6yslccspfwzuegguan6u4i0jcrza.lambda-url.us-east-1.on.aws/"

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
    except (Exception, KeyboardInterrupt):
        # inclui KeyboardInterrupt porque communicate pode levantar durante wait
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

    try:
        for proc in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent", "status"]):
            try:
                info = proc.info
                cpu = info["cpu_percent"]
                ram = info["memory_percent"]

                processos.append({
                    "PID": info["pid"],
                    "Nome": info["name"] or "",
                    "CPU": round(cpu / QTD_NUCLEOS, 2) if cpu is not None else None,
                    "RAM": round(ram, 2) if ram is not None else None,
                    # status vem de psutil.status e descreve estado do processo
                    "Status": (info.get("status") or "").upper(),
                })
            except (psutil.NoSuchProcess, psutil.ZombieProcess, PermissionError):
                # O processo terminou durante a leitura ou acesso negado: ignora.
                continue
            except KeyboardInterrupt:
                return processos
    except KeyboardInterrupt:
        return processos

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


def fechar_minuto(
    registros,
    tokenServidor,
    pasta_base,
    idAeroporto,
    qtd_processos_selected=False,
    coletar_processos_func=None,
    enviar_func=None,
    datetime_parse_fmt="%Y-%m-%d %H:%M:%S",
):
    """
    Fecha um minuto: grava arquivo components (usando os registros fornecidos)
    e, se solicitado, captura e grava processos. Tenta enviar via enviar_func.

    Parâmetros:
    - registros: lista de dicionários contendo pelo menos 'TimeStamp' e 'IdAeroporto'
    - tokenServidor, pasta_base, idAeroporto: para construir paths
    - qtd_processos_selected: se True, chama coletar_processos_func e grava processes
    - coletar_processos_func: função para coletar processos (default: coletarProcessos)
    - enviar_func: função para envio (default: enviar_csv_s3)

    Retorna: dict { 'files': [paths], 'failed': [paths que falharam] }
    """
    if not registros:
        return {"files": [], "failed": []}

    if coletar_processos_func is None:
        coletar_processos_func = coletarProcessos
    if enviar_func is None:
        enviar_func = enviar_csv_s3

    # Determina o minuto-alvo a partir do primeiro registro (arredonda para minuto)
    try:
        primeiro_ts = datetime.strptime(registros[0]["TimeStamp"], datetime_parse_fmt)
    except Exception:
        # Se o TimeStamp não estiver no formato, usa agora
        primeiro_ts = datetime.now()

    minuto = primeiro_ts.replace(second=0, microsecond=0)

    # Monta caminhos: pasta_base / token / DD-MM-AAAA / HH /
    if os.path.basename(os.path.normpath(pasta_base)) == tokenServidor:
        pastaToken = pasta_base
    else:
        pastaToken = os.path.join(pasta_base, tokenServidor)

    pastaDia = os.path.join(pastaToken, minuto.strftime("%d-%m-%Y"))
    pastaHora = os.path.join(pastaDia, minuto.strftime("%H"))
    os.makedirs(pastaHora, exist_ok=True)

    sufixo_minuto = minuto.strftime("%Y-%m-%d-%H-%M")
    caminho_components = os.path.join(pastaHora, f"{tokenServidor}-{sufixo_minuto}-components.csv")

    # Garante que o DataFrame contenha TimeStamp e IdAeroporto
    df_components = pnd.DataFrame(registros)
    # Se IdAeroporto não estiver presente, insere
    if "IdAeroporto" not in df_components.columns:
        df_components.insert(0, "IdAeroporto", idAeroporto)

    # Salva components
    df_components.to_csv(caminho_components, sep=';', index=False, header=True, encoding='utf-8')

    files = [caminho_components]
    failed = []

    # Processos: captura somente se selecionado
    caminho_processos = None
    if qtd_processos_selected:
        try:
            processos = coletar_processos_func()
        except Exception:
            processos = None

        if processos:
            df_processos = pnd.DataFrame(processos)
            df_processos.insert(0, "IdAeroporto", idAeroporto)
            # TimeStamp do arquivo de processos deve ser o mesmo minuto (00s)
            df_processos.insert(0, "TimeStamp", minuto.strftime("%Y-%m-%d %H:%M:%S"))
            caminho_processos = os.path.join(pastaHora, f"{tokenServidor}-{sufixo_minuto}-processos.csv")
            df_processos.to_csv(caminho_processos, sep=';', index=False, header=True, encoding='utf-8')
            files.append(caminho_processos)

    # Tenta enviar os arquivos gerados
    for caminho in files:
        try:
            enviado = enviar_func(caminho)
        except Exception:
            enviado = False
        if not enviado:
            failed.append(caminho)

    return {"files": files, "failed": failed}


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


def main():
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
        arquivosPendentes = set()
        bufferMetricas = []            # amostras da janela atual (para cada registro de 10s)
        minute_buffer = []             # acumula os registros (6 x 10s) que formam o CSV do minuto
        avisoSemComponentes = {"emitido": False}
        codigosDesconhecidos = set()   # nomeCodigo do banco que não existe em COLETORES
        inicioMonitoramento = time.monotonic()
        last_caminho = None            # último arquivo gerado (para exibir status)

        ultimoSegundoExibido = {"valor": None}
        # flag de parada para shutdown gracioso (setada em KeyboardInterrupt)
        stop_event = {"stop": False}

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
                os.path.basename(last_caminho)
                if last_caminho is not None
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
            nonlocal componentes
            # se a parada foi solicitada, retorna imediatamente
            if stop_event.get("stop"):
                return
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

            # Não capturamos processos a cada amostra. A captura de processos
            # ocorre apenas uma vez no final do minuto (implementado em gravarRegistro()).

        def gravarRegistro():
            # Calcula a média das amostras da janela e grava uma linha em cada CSV.
            nonlocal componentes, last_caminho, execucoes
            # alinhamento do timestamp para múltiplos de 10 segundos
            agora = datetime.now()
            agora = agora.replace(second=(agora.second // 10) * 10, microsecond=0)
            timestamp = agora.strftime("%Y-%m-%d %H:%M:%S")

            # Monta o registro (média das amostras da janela)
            registro = {"TimeStamp": timestamp, "IdAeroporto": idAeroporto}
            for nomeCodigo in componentes:
                if nomeCodigo not in COLETORES:
                    continue
                registro[nomeCodigo] = calcularMedia([amostra.get(nomeCodigo) for amostra in bufferMetricas])

            # Limpa buffer de amostras (próxima janela começa)
            bufferMetricas.clear()

            # Acumula no buffer do minuto. O fechamento do minuto é baseado no
            # relógio (cada arquivo representa o minuto do TimeStamp do registro).
            minute_buffer.append(registro)

            # Determina minuto do registro atual
            registro_minuto = datetime.strptime(registro["TimeStamp"], "%Y-%m-%d %H:%M:%S").replace(second=0, microsecond=0)

            # Se há um minuto anterior distinto no buffer, fechar esse minuto
            if len(minute_buffer) >= 2:
                primeiro_ts = datetime.strptime(minute_buffer[0]["TimeStamp"], "%Y-%m-%d %H:%M:%S").replace(second=0, microsecond=0)
            else:
                primeiro_ts = registro_minuto

            arquivosDoRegistro = []

            # Caso 1: registramos 6 (esperados) para o minuto atual -> fechar
            if registro_minuto == primeiro_ts and len(minute_buffer) >= (60 // INTERVALO_REGISTRO_SEGUNDOS):
                resultado = fechar_minuto(
                    minute_buffer[:],
                    tokenServidor,
                    PASTA_CSVS,
                    idAeroporto,
                    qtd_processos_selected=("Qtd_Processos" in componentes),
                )
                arquivosDoRegistro.extend(resultado.get("files", []))
                for f in resultado.get("failed", []):
                    arquivosPendentes.add(f)
                last_caminho = arquivosDoRegistro[0] if arquivosDoRegistro else last_caminho
                minute_buffer.clear()
                execucoes += 1

            # Caso 2: o registro pertence a outro minuto (avanço do relógio) -> fechar
            elif registro_minuto != primeiro_ts:
                # fecha o minuto acumulado (pode ser parcial)
                resultado = fechar_minuto(
                    [r for r in minute_buffer[:-1]],
                    tokenServidor,
                    PASTA_CSVS,
                    idAeroporto,
                    qtd_processos_selected=("Qtd_Processos" in componentes),
                )
                arquivosDoRegistro.extend(resultado.get("files", []))
                for f in resultado.get("failed", []):
                    arquivosPendentes.add(f)
                # preserva apenas o registro do novo minuto no buffer
                minute_buffer[:] = [minute_buffer[-1]]
                last_caminho = arquivosDoRegistro[0] if arquivosDoRegistro else last_caminho

                execucoes += 1

        # Loop principal de coleta
        try:
            # Aquece a leitura de CPU por processo (a 1ª chamada sempre retorna 0)
            if "Qtd_Processos" in componentes:
                coletarProcessos()

            # Alinha o início para que os registros caiam em segundos múltiplos de 10
            # (amostras a cada 5s: 5,15,25... -> registros em 10,20,30...)
            now = datetime.now()
            sec = now.second + now.microsecond / 1_000_000
            # alvo: próximo instante cujo segundo % 10 == 5
            offset_to_next = ((5 - (int(now.second) % 10)) % 10) - now.microsecond / 1_000_000
            if offset_to_next <= 0:
                offset_to_next += 10
            time.sleep(offset_to_next)

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
            # sinaliza parada para que funções longas retornem rapidamente
            try:
                stop_event["stop"] = True
            except Exception:
                pass
            # Ao encerrar, grava qualquer minute_buffer parcial usando fechar_minuto
            if minute_buffer:
                resultado = fechar_minuto(
                    minute_buffer[:],
                    tokenServidor,
                    PASTA_CSVS,
                    idAeroporto,
                    qtd_processos_selected=("Qtd_Processos" in componentes),
                )
                for f in resultado.get("failed", []):
                    arquivosPendentes.add(f)

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



if __name__ == "__main__":
    main()
