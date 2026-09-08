import pandas as pnd
from datetime import datetime
import os
import psutil
import socket
import time

print("""\n========================================\n
            Iniciando Escrita
            \n========================================\n""")

execucoes = 0

def coletarMetricas():
    # 1. Identificação do Servidor 
    hostnameAtual = socket.gethostname()
    timestampAtual = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

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
        "Hostname": [hostnameAtual],
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