# ============================================================
# Sistema de Coleta de Métricas
# Fluxo geral:
#   1. Conecta ao banco MySQL
#   2. Faz login do usuário (email + senha)
#   3. Pede o token do servidor e busca os componentes dele no banco
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
import psutil                        # biblioteca de métricas (CPU, RAM, disco...). Usada pelo eval()!
import socket                        # pega o hostname da máquina. Também usada pelo eval()
import time                          # sleep e relógio monotônico
import mysql.connector               # conexão com o MySQL
from dotenv import load_dotenv       # lê o arquivo .env
from Organizador import organizar, PASTA_BRONZE  # cria bronze/<empresa>/<aeroporto>/ e devolve o caminho

# Carrega as credenciais do banco (DB_HOST, DB_USER, DB_PASSWORD, DB_NAME) do .env
load_dotenv()

# Pasta onde os CSVs são gravados. Começa na raiz "bronze" (fallback) e é redefinida
# na Etapa 3 com o caminho bronze/<empresa>/<aeroporto>/ devolvido pelo Organizador.
PASTA_CSVS = PASTA_BRONZE


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
    # Os inputs ficam dentro de um try para tratar o Ctrl+C.
    # Sem isso, apertar Ctrl+C no login estourava um traceback (KeyboardInterrupt).
    try:
        email = input("Digite o email: ").strip()
        senha = input("Digite a senha: ").strip()
    except KeyboardInterrupt:
        # Encerra o programa de forma limpa quando o usuário cancela com Ctrl+C
        print("\nLogin cancelado. Encerrando o programa.")
        try:
            # Fecha a conexão com o banco antes de sair, se ainda estiver aberta
            if conexao.is_connected():
                conexao.close()
        except mysql.connector.Error:
            pass
        # SystemExit(0) = saída "normal" (código 0), sem mensagem de erro
        raise SystemExit(0)

    # Impede campos vazios (ou só espaços)
    if not email or not senha:
        print("Email e senha não podem ficar vazios ou conter apenas espaços.")
        continue

    cursorAutenticacao = None  # começa como None para o finally saber se foi criado

    try:
        # Se a conexão caiu, tenta reconectar (3 tentativas, 2s de intervalo)
        if not conexao.is_connected():
            conexao.reconnect(attempts=3, delay=2)

        # buffered=True traz o resultado todo para a memória, evitando erro de "unread result"
        cursorAutenticacao = conexao.cursor(buffered=True)
        # Os %s são parâmetros: o driver escapa os valores e evita SQL Injection
        cursorAutenticacao.execute(
            "SELECT idUsuario, fkAeroporto FROM usuario WHERE email = %s AND senha = %s",
            (email, senha),
        )
        # Retorna uma tupla (idUsuario, fkAeroporto) ou None se não achou ninguém
        usuario = cursorAutenticacao.fetchone()
    except mysql.connector.Error as erro:
        # Erro de banco: reconecta e volta pro começo do loop
        print(f"\nErro no banco de dados durante o login: {erro}")
        conexao = conectarBanco()
        continue
    finally:
        # Sempre fecha o cursor, dando certo ou não
        if cursorAutenticacao is not None:
            try:
                cursorAutenticacao.close()
            except mysql.connector.Error:
                pass

    # Achou o usuário: sai do loop de login
    if usuario is not None:
        print("\nUsuário autenticado com sucesso!")
        break

    print("\nFalha na autenticação. Verifique o email e a senha.")
    os.system("cls" if os.name == "nt" else "clear")

# ============================================================
# ETAPA 2: TOKEN DO SERVIDOR + COMPONENTES
# Pede o token, descobre o idServidor e carrega a lista de
# componentes (nomeCodigo, codigo) que serão monitorados.
# ============================================================
while True:

    # Mesmo tratamento de Ctrl+C da etapa de login, agora no input do token
    try:
        tokenServidor = input("\nDigite o Token do servidor que deseja monitorar: ").strip()
    except KeyboardInterrupt:
        # Cancela com mensagem limpa, fecha a conexão e sai
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
    deveReconectar = False  # flag: só reconecta depois do finally, para não misturar com o fechamento do cursor

    try:
        if not conexao.is_connected():
            conexao.reconnect(attempts=3, delay=2)

        cursorToken = conexao.cursor(buffered=True)
        # (tokenServidor,) com vírgula = tupla de 1 elemento (obrigatório para o driver)
        cursorToken.execute(
            "SELECT idServidor FROM servidor WHERE token = %s",
            (tokenServidor,),
        )
        # Tupla (idServidor,) ou None se o token não existir
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

    # Reconecta fora do try/finally e recomeça o loop
    if deveReconectar:
        conexao = conectarBanco()
        continue

    # Token encontrado: busca os componentes desse servidor
    if servidor is not None:
        print("\nServidor identificado com sucesso!")
        cursorComponente = None
        try:
            if not conexao.is_connected():
                conexao.reconnect(attempts=3, delay=2)

            cursorComponente = conexao.cursor(buffered=True)
            # Junta componente -> servidorComponente -> servidor para pegar só os componentes
            # vinculados a esse servidor.
            # nomeCodigo = nome da coluna no CSV | codigo = expressão Python que gera o valor
            # ALTERADO: (servidor) NÃO era uma tupla, só um parêntese. Agora usa (servidor[0],),
            # que é a forma correta, e s.idServidor para evitar ambiguidade na coluna.
            cursorComponente.execute(
                "SELECT c.nomeCodigo, c.codigo FROM componente c JOIN servidorComponente sc ON sc.fkComponente = c.idComponente JOIN servidor s ON s.idServidor = sc.fkServidor WHERE s.idServidor = %s",
                (servidor[0],),
            )
            # Lista de tuplas: [(nomeCodigo, codigo), (nomeCodigo, codigo), ...]
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
        # fetchall() devolve lista vazia (não None) quando não há linhas,
        # então esse if quase sempre é verdadeiro
        if componentes is not None:
            print("\nComponentes recolhidos com sucesso!")
            break  # sai do loop do token e segue para a coleta

        # (só chega aqui se componentes for None)
        os.system("cls" if os.name == "nt" else "clear")
        print("Falha verificação dos componentes. Retorno nulo.")
    else:
        # ALTERADO: antes, token inexistente voltava ao loop sem nenhuma mensagem
        print("\nToken não encontrado. Verifique e tente novamente.")


# ============================================================
# ETAPA 3: COLETA DE MÉTRICAS
# ============================================================
if conexao.is_connected():
    # O Organizador roda UMA única vez: cria bronze/<empresa>/<aeroporto>/ (e a raiz
    # "bronze", se não existir) e devolve o caminho. Se o token não tiver
    # aeroporto/empresa vinculados, grava direto em bronze/.
    PASTA_CSVS = organizar(tokenServidor, conexao) or PASTA_BRONZE

    os.system("cls" if os.name == "nt" else "clear")
    print("""\n========================================\n
                Iniciando Escrita
                \n========================================\n""")
    print(f"Gravando em: {PASTA_CSVS}\n")

    execucoes = 0                          # contador de coletas feitas
    intervaloCSVSegundos = 2 * 60 * 60     # a cada 2 horas começa um CSV novo
    # Guarda o estado do arquivo atual: quando começou (relógio monotônico) e o caminho.
    # Usa dicionário para as funções internas conseguirem alterar sem precisar de "global".
    estadoCSV = {"inicio": time.monotonic(), "caminho": None}
    inicioMonitoramento = time.monotonic() # usado para calcular o tempo total ativo

    def atualizarStatus(indiceAnimacao):
        # Tempo ativo em segundos, convertido para horas:minutos:segundos
        segundosAtivos = int(time.monotonic() - inicioMonitoramento)
        horas, resto = divmod(segundosAtivos, 3600)
        minutos, segundos = divmod(resto, 60)
        # Mostra só o nome do arquivo CSV (sem a pasta), ou aviso se ainda não existe
        caminhoAtual = (
            os.path.basename(estadoCSV["caminho"])
            if estadoCSV["caminho"] is not None
            else "aguardando primeira coleta"
        )
        # Spinner: alterna entre | / - \ conforme o índice
        indicador = "|/-\\"[indiceAnimacao % 4]
        # \r volta o cursor ao início da linha, então ela é sobrescrita (sem pular linha)
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

    # Função que relê do banco a lista de componentes do servidor.
    # Serve para pegar mudanças feitas no banco (componente adicionado/removido/alterado)
    # sem precisar reiniciar o programa.
    # Retorna a lista [(nomeCodigo, codigo), ...] ou None se der erro (aí mantém a lista antiga).
    def buscarComponentesAtualizados():
        cursorAtualizacao = None
        try:
            # Se a conexão caiu, tenta reconectar antes de consultar
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
            # Não derruba a coleta: avisa e devolve None
            print(f"\nNão foi possível atualizar as métricas: {erro}")
            return None
        finally:
            if cursorAtualizacao is not None:
                try:
                    cursorAtualizacao.close()
                except mysql.connector.Error:
                    pass

    def coletarMetricas():
        # Esta função também reatribui `componentes`,
        # então precisa declarar como global (senão criaria uma variável local)
        global componentes

        # Decide primeiro se vai começar um arquivo novo, para atualizar
        # os componentes ANTES de coletar.
        # novoArquivo = True na primeira coleta OU quando passaram 2h desde o início do CSV atual
        agoraMonotonico = time.monotonic()
        novoArquivo = (
            estadoCSV["caminho"] is None
            or agoraMonotonico - estadoCSV["inicio"] >= intervaloCSVSegundos
        )
        if novoArquivo:
            # A cada rotação (menos na primeira coleta, quando a lista acabou de
            # ser carregada na Etapa 2) relê os componentes do banco.
            # Assim as colunas só mudam na troca de arquivo, e um mesmo CSV
            # mantém sempre o mesmo cabeçalho do início ao fim.
            if estadoCSV["caminho"] is not None:
                componentesAtualizados = buscarComponentesAtualizados()
                # Só troca a lista se a consulta deu certo; se deu erro (None), mantém a anterior
                if componentesAtualizados is not None:
                    componentes = componentesAtualizados

            estadoCSV["inicio"] = agoraMonotonico
            # Nome: token-AAAA-MM-DD_HHh.csv (sem ":" para funcionar no Windows)
            nomeArquivo = f"{tokenServidor}-{datetime.now().strftime('%Y-%m-%d_%H')}h.csv"
            # O CSV é criado direto na pasta bronze/<empresa>/<aeroporto>/
            estadoCSV["caminho"] = os.path.join(PASTA_CSVS, nomeArquivo)

        # Colunas fixas (sempre existem). Cada valor é uma lista de 1 item
        # porque o DataFrame espera colunas com listas (uma linha = 1 elemento)
        resultados = {
            "TimeStamp": [datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
            "IdAeroporto": [usuario[1]],  # fkAeroporto do usuário logado
        }

        # Colunas dinâmicas: uma para cada componente vindo do banco
        for nomeCodigo, codigo in componentes:
            try:
                # eval executa a string do banco como código Python
                # (ex: "psutil.cpu_percent(interval=1)") e devolve o resultado
                valor = eval(codigo)
                # Listas/tuplas/dicts (ex: CPU por core) viram texto para caber numa célula
                if isinstance(valor, (list, tuple, dict)):
                    valor = str(valor)
            except Exception as erro:
                # Se um componente falhar, só aquela coluna fica vazia (None)
                print(f"Erro ao executar '{codigo}': {erro}")
                valor = None
            # nomeCodigo vira o nome da coluna no CSV
            resultados[nomeCodigo] = [valor]

        # Transforma o dicionário em um DataFrame de 1 linha
        df_Novo = pnd.DataFrame(resultados)

        # Só lê o caminho já definido no bloco de rotação acima
        caminhoCSV = estadoCSV["caminho"]

        # Cria o diretório caso não exista
        os.makedirs(os.path.dirname(caminhoCSV), exist_ok=True)
        # Se o arquivo já existe, não escreve o cabeçalho de novo
        arquivoExiste = os.path.exists(caminhoCSV)

        # Exportação: mode='a' adiciona a linha no final do arquivo (append)
        df_Novo.to_csv(
            caminhoCSV,
            sep=';',
            mode='a',
            index=False,
            header=not arquivoExiste,
            encoding='utf-8'
        )

        # "global" porque execucoes é uma variável do módulo e aqui ela é reatribuída
        global execucoes
        execucoes += 1


    if __name__ == "__main__":
        try:
            # Loop infinito: espera 3s (mostrando o status) e coleta
            while True:
                aguardarProximaColeta()
                coletarMetricas()
        except KeyboardInterrupt:
            # Ctrl+C encerra com mensagem limpa. \r\033[K volta ao início da linha e apaga o resto
            print(
                f"\r\033[KMonitoramento encerrado. Coletas salvas: {execucoes}.",
                flush=True,
            )