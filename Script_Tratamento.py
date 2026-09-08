import pandas as pnd
from datetime import timedelta
import os

caminhoCSV = "./csvs/dados.csv"

print("""\n========================================\n
        Iniciando Leitura e Análise
            \n========================================\n""")

if not os.path.exists(caminhoCSV):
    print("Arquivo CSV não encontrado. Execute o script de coleta primeiro.")
    exit()

DataFrame = pnd.read_csv(caminhoCSV, sep=";")

def TratarDados(df):
    # 1. Tratamento de Tempo
    df['TimeStamp'] = pnd.to_datetime(df['TimeStamp'])
    
    # Ordena cronologicamente
    df = df.sort_values(by='TimeStamp').reset_index(drop=True)

    # 2. Tratamento de CONTADORES
    # diff() calcula a diferença entre a linha atual e a anterior e fillna(0) limpa a 1ª linha que é nula.
    df['Delta_Swap_In'] = df['Swap_In'].diff().fillna(0)
    df['Delta_Swap_Out'] = df['Swap_Out'].diff().fillna(0)
    
    df['Delta_Read_Bytes'] = df['Disco_Read_Bytes'].diff().fillna(0)
    df['Delta_Write_Bytes'] = df['Disco_Write_Bytes'].diff().fillna(0)
    
    return df

def CalcularDados(df):
    # Usamos o último registro do CSV como referência 
    # para a leitura funcionar mesmo em dados passados
    ultimaColeta = df['TimeStamp'].max()

    print(f"--- Análise Referencia: {ultimaColeta} ---\n")

    # GAUGE: CPU % (Janela de 5 min)

    filtroCpu5m = df['TimeStamp'] >= (ultimaColeta - timedelta(minutes=5))
    df5m = df[filtroCpu5m]
    
    if not df5m.empty:
        mediaCpu5m = df5m['UsoCPU_Geral'].mean()
        picoCpu = df5m['UsoCPU_Geral'].max()
        tempoPico = df5m['TimeStamp'][df5m['UsoCPU_Geral'] == picoCpu].iloc[0]
        
        print(f"[CPU] Média Móvel (5 min): {mediaCpu5m:.1f}%") # Gatilho pra alerta
        print(f"[CPU] Pico Isolado: {picoCpu:.1f}% em {tempoPico.strftime('%H:%M:%S')} ") # Não gera alerta e strftime é pra transformar de timestamp pra H,M,S
    else:
        print("[CPU] Sem medidas nos últimos 5 minutos.")

    # GAUGE: RAM % (Janela 1 hora)
    
    filtroRam1h = df['TimeStamp'] >= (ultimaColeta - timedelta(hours=1))
    df1h = df[filtroRam1h]
    
    if not df1h.empty:
        mediaRam1h = df1h['UsoRAM'].mean()
        ramAtual = df1h['UsoRAM'].iloc[-1]
        print(f"\n[RAM] Uso Atual: {ramAtual:.1f}% | Média da última 1h: {mediaRam1h:.1f}% inclinação para vazamento")

    # CONTADOR: SWAP (Delta)

    if not df5m.empty:
        # Qualquer delta > 0 é um evento binário de thrashing 
        teveSwapOut = False
        teveSwapIn = False

        for valor in df5m['Delta_Swap_In']:
            if valor > 0:
                teveSwapIn = True
                break
        
        for valor in df5m['Delta_Swap_Out']:
            if valor > 0:
                teveSwapOut = True
                break
        estadoSwap = "ALERTA (Thrashing detectado!)" if (teveSwapIn or teveSwapOut) else "Estável"
        print(f"\n[SWAP] Status (5 min): {estadoSwap}")

    # GAUGE + CONTADOR: DISCO
    usoDiscoAtual = df['UsoDisco'].iloc[-1]
    print(f"\nDISCO (Armazenamento % Usado): {usoDiscoAtual:.1f}% Leitura direta")

    if not df5m.empty:
        # Converte bytes para Megabytes para leitura amigável
        mediaReadMb = df5m['Delta_Read_Bytes'].mean() / (1024 * 1024)
        mediaWriteMb = df5m['Delta_Write_Bytes'].mean() / (1024 * 1024)
        print(f"DISCO (I/O Média Leitura (5 min)): {mediaReadMb:.2f} MB por ciclo")
        print(f"[DISCO (I/O Média Escrita (5 min)): {mediaWriteMb:.2f} MB por ciclo")

    print("""\n========================================\n
            Encerrando Leitura
                \n========================================\n""")

# Execução principal
DataFrame = TratarDados(DataFrame)
CalcularDados(DataFrame)