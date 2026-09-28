import pandas as pnd
from datetime import datetime
import os
import psutil
import socket
import time
import uuid
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

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


conexao = conectarBanco()

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

while True:
    email = input("Digite o email: ").strip()
    senha = input("Digite a senha: ").strip()

    if not email or not senha:
        print("Email e senha não podem ficar vazios ou conter apenas espaços.")
        continue

    cursor = None

    try:
        if not conexao.is_connected():
            conexao.reconnect(attempts=3, delay=2)

        cursor = conexao.cursor(buffered=True)
        cursor.execute(
            "SELECT idUsuario, fkEmpresa FROM usuario WHERE email = %s AND senha = %s",
            (email, senha),
        )
        usuario = cursor.fetchone()
    except mysql.connector.Error as erro:
        print(f"Erro no banco de dados durante o login: {erro}")
        conexao = conectarBanco()
        continue
    finally:
        if cursor is not None:
            try:
                cursor.close()
            except mysql.connector.Error:
                pass

    if usuario is not None:
        print("Usuário autenticado com sucesso!")
        break

    print("Falha na autenticação. Verifique o email e a senha.")
    os.system("cls" if os.name == "nt" else "clear")


if conexao.is_connected():
    print("""\n========================================\n
                Iniciando Escrita
                \n========================================\n""")

    execucoes = 0

    def coletarMetricas():
        # 1. Identificação do Servidor
        idUnico = uuid.getnode() 
        hostnameAtual = socket.gethostname()
        timestampAtual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fkEmpresa = usuario[1]  # Obter o ID da empresa do usuário autenticado

        # MÉTRICAS SDV (CPU + RAM)

        usoCpuGeral = psutil.cpu_percent(interval=1)
        usoCpuCores = str(psutil.cpu_percent(percpu=True)) 
        
        memoriaVirtual = psutil.virtual_memory()
        usoRam = memoriaVirtual.percent
        
        memoriaSwap = psutil.swap_memory()
        swapIn = memoriaSwap.sin
        swapOut = memoriaSwap.sout

        # MÉTRICAS SPA E AIS (RAM + Disco)

        caminhoDisco = 'C:\\' if os.name == 'nt' else '/'
        usoDisco = psutil.disk_usage(caminhoDisco).percent
        ioDisco = psutil.disk_io_counters()
        discoRead = ioDisco.read_bytes if ioDisco else 0
        discoWrite = ioDisco.write_bytes if ioDisco else 0

        # Dataframe
        resultados = {
            "TimeStamp": [timestampAtual],
            "IdUnico": [idUnico],
            "Hostname": [hostnameAtual],
            "IdEmpresa": [fkEmpresa],
            "UsoCPU_Geral": [usoCpuGeral],
            "UsoCPU_Por_Core": [usoCpuCores],
            "UsoRAM": [usoRam],
            "Swap_In": [swapIn],
            "Swap_Out": [swapOut],
            "UsoDisco": [usoDisco],
            "Disco_Read_Bytes": [discoRead],
            "Disco_Write_Bytes": [discoWrite]
        }


        df_Novo = pnd.DataFrame(resultados)
        caminhoCSV = "./csvs/dados.csv"
        
        # Cria o diretório caso não exista
        os.makedirs(os.path.dirname(caminhoCSV), exist_ok=True)
        arquivoExiste = os.path.exists(caminhoCSV)

        # Exportação
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

        print(f"[{timestampAtual}] Coleta salva | CPU: {usoCpuGeral}% | RAM: {usoRam}% | Disco: {usoDisco}% | Swap Ativo: {swapIn > 0}")

    if __name__ == "__main__":
        while (execucoes < 100):
            time.sleep(4) 
            coletarMetricas()
            
        print("""\n========================================\n
                Encerrando Escrita
                \n========================================\n""")