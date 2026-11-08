import os
from datetime import date, timedelta
from botocore.exceptions import ClientError
from io import BytesIO
from urllib.parse import unquote_plus

import boto3
import pandas as pnd


s3 = boto3.client("s3")

# Tolerância inicial para coletas esperadas a cada 10 segundos.
INTERVALO_MAXIMO_DELTA_SEGUNDOS = 30

COLUNAS_NUMERICAS = [
    "IdAeroporto",
    "UsoCPU_Geral",
    "UsoRAM",
    "Swap_In",
    "Swap_Out",
    "UsoDisco",
    "Disco_Read_Bytes",
    "Disco_Write_Bytes",
    "Rede_Bytes_Enviados",
    "Rede_Bytes_Recebidos",
    "Qtd_Processos",
]

CONTADORES = {
    "Swap_In": "Delta_Swap_In",
    "Swap_Out": "Delta_Swap_Out",
    "Disco_Read_Bytes": "Delta_Read_Bytes",
    "Disco_Write_Bytes": "Delta_Write_Bytes",
    "Rede_Bytes_Enviados": "Delta_Rede_Bytes_Enviados",
    "Rede_Bytes_Recebidos": "Delta_Rede_Bytes_Recebidos",
}

METRICAS_RESUMO = [
    "UsoCPU_Geral",
    "UsoRAM",
    "UsoDisco",
    "Qtd_Processos",
]


def verificar_tempo(context):
    # Interrompe antes do timeout para registrar um erro compreensível.
    if context.get_remaining_time_in_millis() < 15000:
        raise RuntimeError(
            "Tempo restante insuficiente. "
            "Precisamos reduzir o volume processado por execução."
        )


def identificar_destino(chave):
    # Estrutura esperada:
    # 01-bronze/empresa/aeroporto/token/data/arquivo.csv
    partes = chave.split("/")

    if len(partes) != 6 or partes[0] != "01-bronze":
        raise ValueError(f"Caminho fora do padrão: {chave}")

    _, empresa, aeroporto, token, data, arquivo = partes

    if not empresa.isdigit() or not aeroporto.isdigit():
        raise ValueError(
            "Empresa e aeroporto devem ter IDs numéricos."
        )

    if not token or not arquivo.endswith(".csv"):
        raise ValueError("Token ou arquivo inválido.")

    if date.fromisoformat(data).isoformat() != data:
        raise ValueError("Data fora do formato AAAA-MM-DD.")

    return empresa, aeroporto, token


def listar_arquivos(bucket, empresa, aeroporto, token):
    # Busca somente o histórico deste servidor e deste vínculo.
    prefixo = f"01-bronze/{empresa}/{aeroporto}/{token}/"
    arquivos = []

    paginador = s3.get_paginator("list_objects_v2")

    for pagina in paginador.paginate(
        Bucket=bucket,
        Prefix=prefixo,
    ):
        for objeto in pagina.get("Contents", []):
            chave = objeto["Key"]

            if not chave.endswith(".csv"):
                continue

            identificar_destino(chave)
            arquivos.append((chave, objeto["Size"]))

    # Limites iniciais da prova de conceito.
    # Se excederem, a execução falha antes de gravar resultados.
    if len(arquivos) > 500:
        raise ValueError(
            "Histórico acima de 500 arquivos. "
            "Precisamos dividir o processamento em períodos."
        )

    if sum(tamanho for _, tamanho in arquivos) > 50 * 1024 * 1024:
        raise ValueError(
            "Histórico acima de 50 MiB. "
            "Precisamos dividir o processamento em períodos."
        )

    return sorted(chave for chave, _ in arquivos)


def ler_csv(bucket, chave):
    resposta = s3.get_object(
        Bucket=bucket,
        Key=chave,
    )

    corpo = resposta["Body"]

    try:
        conteudo = corpo.read()
    finally:
        corpo.close()

    return pnd.read_csv(BytesIO(conteudo), sep=";")


def tratar_dados(dados, id_aeroporto):
    dados = dados.copy()

    obrigatorias = {"TimeStamp", "IdAeroporto"}
    ausentes = obrigatorias - set(dados.columns)

    if ausentes:
        raise ValueError(
            f"Colunas obrigatórias ausentes: {sorted(ausentes)}"
        )

    dados["TimeStamp"] = pnd.to_datetime(
        dados["TimeStamp"],
        errors="coerce",
    )

    presentes = [
        coluna
        for coluna in COLUNAS_NUMERICAS
        if coluna in dados.columns
    ]

    for coluna in presentes:
        dados[coluna] = pnd.to_numeric(
            dados[coluna],
            errors="coerce",
        )

    dados[presentes] = dados[presentes].replace(
        [float("inf"), float("-inf")],
        float("nan"),
    )

    # Confere o aeroporto antes de descartar linhas inválidas.
    aeroportos = dados["IdAeroporto"].dropna()

    if not aeroportos.eq(int(id_aeroporto)).all():
        raise ValueError(
            "Aeroporto do conteúdo diverge do caminho no S3."
        )

    # Exige valores válidos nas métricas conhecidas presentes.
    dados = dados.dropna(
        subset=["TimeStamp"] + presentes,
    ).copy()

    dados = dados.drop_duplicates().copy()

    # Percentuais precisam estar entre 0 e 100.
    percentuais = [
        "UsoCPU_Geral",
        "UsoRAM",
        "UsoDisco",
    ]

    for coluna in percentuais:
        if coluna in dados.columns:
            dados = dados[
                dados[coluna].between(0, 100)
            ].copy()

    # Contadores e quantidades não podem ser negativos.
    for coluna in presentes:
        if coluna not in percentuais:
            dados = dados[
                dados[coluna] >= 0
            ].copy()

    dados = dados.sort_values(
        "TimeStamp",
        kind="stable",
    ).reset_index(drop=True)

    # Sinaliza possíveis outliers sem removê-los.
    for coluna in METRICAS_RESUMO:
        if coluna not in dados.columns or dados.empty:
            continue

        q1 = dados[coluna].quantile(0.25)
        q3 = dados[coluna].quantile(0.75)
        intervalo = q3 - q1

        dados[f"Outlier_{coluna}"] = (
            (dados[coluna] < q1 - 1.5 * intervalo)
            | (dados[coluna] > q3 + 1.5 * intervalo)
        )

    # Mede o tempo entre cada coleta e a anterior.
    intervalo_segundos = (
        dados["TimeStamp"].diff().dt.total_seconds()
    )

    # Aceita deltas somente entre leituras próximas.
    # A troca de CSV ou de dia não interrompe o cálculo,
    # desde que o intervalo esteja dentro da tolerância.
    intervalo_valido = intervalo_segundos.between(
        0,
        INTERVALO_MAXIMO_DELTA_SEGUNDOS,
        inclusive="right",
    )

    for original, delta in CONTADORES.items():
        if original in dados.columns:
            diferencas = dados[original].diff()

            # Primeira leitura, lacunas e reduções do contador
            # recebem zero por falta de um delta confiável.
            # Esse zero não significa consumo efetivamente nulo.
            dados[delta] = diferencas.where(
                intervalo_valido & diferencas.ge(0),
                0,
            ).fillna(0)

    return dados


def gerar_ouro(dados):
    agregacoes = {}

    for coluna in METRICAS_RESUMO:
        if coluna in dados.columns:
            agregacoes[coluna] = ["mean", "max"]

    for delta in CONTADORES.values():
        if delta in dados.columns:
            agregacoes[delta] = ["sum"]

    if not agregacoes:
        raise ValueError(
            "Não existem métricas para gerar gold."
        )

    dados = dados.copy()
    dados["Hora"] = dados["TimeStamp"].dt.floor("60min")

    grupos = dados.groupby("Hora")
    ouro = grupos.agg(agregacoes)

    ouro.columns = [
        f"{coluna}_{funcao}"
        for coluna, funcao in ouro.columns
    ]

    ouro["Qtd_Coletas"] = grupos.size()

    return ouro.round(2).reset_index()


def salvar_resultados(
    bucket,
    dados,
    empresa,
    aeroporto,
    token,
    context,
):
    prefixo_silver = os.environ["PREFIXO_SILVER"].strip("/")
    prefixo_gold = os.environ["PREFIXO_GOLD"].strip("/")

    # Nesta fase, só permite gravar na área de testes.
    # Permite apenas os destinos de teste ou os definitivos.
    destinos_permitidos = {
        ("testes-lambda/02-silver", "testes-lambda/03-gold"),
        ("02-silver", "03-gold"),
    }

    if (prefixo_silver, prefixo_gold) not in destinos_permitidos:
        raise ValueError(
            "Combinação de prefixos de saída não autorizada."
        )

    if prefixo_silver == prefixo_gold:
        raise ValueError(
            "Os prefixos de silver e gold devem ser diferentes."
        )

    resultados = []

    # Os deltas já foram calculados antes da separação por dia.
    for dia, dados_dia in dados.groupby(
        dados["TimeStamp"].dt.date
    ):
        verificar_tempo(context)

        data = dia.isoformat()
        pasta = f"{empresa}/{aeroporto}/{token}/{data}"
        nome = f"{token}-{data}"

        chave_silver = f"{prefixo_silver}/{pasta}/{nome}.csv"
        chave_gold = f"{prefixo_gold}/{pasta}/{nome}.json"

        ouro = gerar_ouro(dados_dia)

        csv = dados_dia.to_csv(
            sep=";",
            index=False,
            date_format="%Y-%m-%d %H:%M:%S",
        ).encode("utf-8")

        json_ouro = ouro.to_json(
            orient="records",
            date_format="iso",
            force_ascii=False,
            indent=2,
        ).encode("utf-8")

        s3.put_object(
            Bucket=bucket,
            Key=chave_silver,
            Body=csv,
            ContentType="text/csv; charset=utf-8",
        )

        s3.put_object(
            Bucket=bucket,
            Key=chave_gold,
            Body=json_ouro,
            ContentType="application/json; charset=utf-8",
        )

        resultado_dia = {
            "data": data,
            "coletas": len(dados_dia),
            "horas_resumidas": len(ouro),
            "silver": chave_silver,
            "gold": chave_gold,
        }

        print(resultado_dia)
        resultados.append(resultado_dia)

    return resultados


def processar_servidor(bucket, chave, context):
    # A migração exige uma Silver inicial completa.
    # Depois disso, cada evento incorpora o CSV recebido.
    empresa, aeroporto, token = identificar_destino(chave)

    prefixo_silver = os.environ["PREFIXO_SILVER"].strip("/")
    prefixo_gold = os.environ["PREFIXO_GOLD"].strip("/")

    # Aceita os destinos de teste ou os definitivos.
    destinos_permitidos = {
        ("testes-lambda/02-silver", "testes-lambda/03-gold"),
        ("02-silver", "03-gold"),
    }

    if (prefixo_silver, prefixo_gold) not in destinos_permitidos:
        raise ValueError(
            "Combinação de prefixos de saída não autorizada."
        )

    colunas_originais = ["TimeStamp"] + COLUNAS_NUMERICAS
    cache = {}

    def preparar_originais(tabela):
        ausentes = set(colunas_originais) - set(tabela.columns)

        if ausentes:
            raise ValueError(
                f"Colunas ausentes: {sorted(ausentes)}"
            )

        # Não reutiliza deltas ou flags antigos.
        tabela = tabela[colunas_originais].copy()
        tabela = tratar_dados(tabela, aeroporto)

        return tabela[colunas_originais].copy()

    def chave_silver(dia):
        data = dia.isoformat()

        return (
            f"{prefixo_silver}/{empresa}/{aeroporto}/{token}/"
            f"{data}/{token}-{data}.csv"
        )

    def carregar_dia(dia):
        # Cada dia é carregado no máximo uma vez nesta execução.
        if dia not in cache:
            verificar_tempo(context)

            try:
                tabela = ler_csv(bucket, chave_silver(dia))
            except ClientError as erro:
                codigo = erro.response["Error"]["Code"]

                # Um dia novo ainda não possui Silver.
                # Outros erros, como falta de permissão, são propagados.
                if codigo not in ("NoSuchKey", "404"):
                    raise

                cache[dia] = None
            else:
                tabela = preparar_originais(tabela)

                if not tabela["TimeStamp"].dt.date.eq(dia).all():
                    raise ValueError(
                        "Silver contém registros de outro dia."
                    )

                cache[dia] = tabela

        return cache[dia]

    verificar_tempo(context)

    # Lê apenas o CSV Bronze indicado pelo evento.
    recebidos = ler_csv(bucket, chave)
    novos = preparar_originais(recebidos)

    if novos.empty:
        raise ValueError(
            "Nenhuma linha válida no CSV recebido."
        )

    dias_novos = set(novos["TimeStamp"].dt.date)

    # Reúne os dados novos com os já consolidados.
    for dia in sorted(dias_novos):
        base = carregar_dia(dia)

        parte = novos[
            novos["TimeStamp"].dt.date.eq(dia)
        ]

        if base is None:
            uniao = parte.copy()
        else:
            uniao = pnd.concat(
                [base, parte],
                ignore_index=True,
            )

        # Repetir o mesmo evento não duplica as coletas.
        uniao = uniao.drop_duplicates().sort_values("TimeStamp")

        # Não escolhe silenciosamente entre valores diferentes
        # registrados para o mesmo servidor no mesmo instante.
        if uniao["TimeStamp"].duplicated().any():
            raise ValueError(
                "Coletas conflitantes no mesmo timestamp."
            )

        cache[dia] = uniao.reset_index(drop=True)

    # Uma coleta atrasada no final de um dia pode alterar
    # o delta da primeira coleta do dia seguinte.
    afetados = set(dias_novos)

    for dia in dias_novos:
        seguinte = dia + timedelta(days=1)
        base_seguinte = carregar_dia(seguinte)

        if (
            base_seguinte is not None
            and not base_seguinte.empty
        ):
            afetados.add(seguinte)

    preparados = []

    for dia in sorted(afetados):
        atual = carregar_dia(dia)
        anterior = carregar_dia(dia - timedelta(days=1))

        contexto = atual

        # Usa a última leitura do dia anterior como contexto.
        # A regra dos 30 segundos continua sendo aplicada.
        if anterior is not None and not anterior.empty:
            contexto = pnd.concat(
                [anterior.tail(1), atual],
                ignore_index=True,
            )

        tratado = tratar_dados(contexto, aeroporto)

        # O registro anterior serve apenas ao cálculo do delta.
        tratado = tratado[
            tratado["TimeStamp"].dt.date.eq(dia)
        ].copy()

        # Os outliers passam a usar a distribuição do próprio dia.
        # O registro de contexto não participa dos quartis.
        for coluna in METRICAS_RESUMO:
            q1 = tratado[coluna].quantile(0.25)
            q3 = tratado[coluna].quantile(0.75)
            intervalo = q3 - q1

            tratado[f"Outlier_{coluna}"] = (
                (tratado[coluna] < q1 - 1.5 * intervalo)
                | (tratado[coluna] > q3 + 1.5 * intervalo)
            )

        preparados.append(tratado)

    # Reconstrói Silver e Gold somente dos dias afetados.
    # Os nomes permanecem os mesmos em uma repetição.
    resultados = salvar_resultados(
        bucket,
        pnd.concat(preparados, ignore_index=True),
        empresa,
        aeroporto,
        token,
        context,
    )

    resultado = {
        "bucket": bucket,
        "token": token,
        "modo": "incremental_por_dia",
        "arquivos_bronze_lidos": 1,
        "linhas_recebidas": len(recebidos),
        "linhas_validas_no_csv": len(novos),
        "dias_processados": len(resultados),
        "resultados_por_dia": resultados,
    }

    print(resultado)
    return resultado

def lambda_handler(event, context):
    bucket_configurado = os.environ["NOME_BUCKET"]

    if not isinstance(event, dict):
        raise ValueError(
            "O evento deve ser um objeto JSON."
        )

    # Evento especial enviado pelo S3 ao validar notificações.
    if (
        event.get("Service") == "Amazon S3"
        and event.get("Event") == "s3:TestEvent"
    ):
        if event.get("Bucket") != bucket_configurado:
            raise ValueError(
                "Bucket do evento de teste não autorizado."
            )

        return {
            "mensagem": "Evento de teste do S3 recebido."
        }

    # Mantém compatibilidade com o teste manual.
    if "Records" not in event:
        if event.get("bucket") != bucket_configurado:
            raise ValueError(
                "O bucket do evento deve corresponder "
                "ao NOME_BUCKET."
            )

        chave = event.get("chave")

        if not isinstance(chave, str):
            raise ValueError(
                "Informe a chave do CSV no evento de teste."
            )

        return processar_servidor(
            bucket_configurado,
            chave,
            context,
        )

    # Eventos reais do S3 chegam em uma lista de registros.
    registros = event["Records"]

    if not isinstance(registros, list) or not registros:
        raise ValueError(
            "Records deve ser uma lista não vazia."
        )

    servidores = {}
    ignorados = 0

    # Valida todos os registros antes de processar.
    for registro in registros:
        if not isinstance(registro, dict):
            raise ValueError(
                "Registro do evento inválido."
            )

        if registro.get("eventSource") != "aws:s3":
            raise ValueError(
                "A origem do evento deve ser aws:s3."
            )

        tipo_evento = registro.get("eventName", "")

        if not isinstance(tipo_evento, str):
            raise ValueError(
                "Tipo de evento inválido."
            )

        if not tipo_evento.startswith("ObjectCreated:"):
            ignorados += 1
            continue

        try:
            bucket = registro["s3"]["bucket"]["name"]
            chave_codificada = registro["s3"]["object"]["key"]
        except (KeyError, TypeError):
            raise ValueError(
                "Evento S3 sem nome do bucket "
                "ou chave do objeto."
            ) from None

        if bucket != bucket_configurado:
            raise ValueError(
                "O bucket do evento deve corresponder "
                "ao NOME_BUCKET."
            )

        if not isinstance(chave_codificada, str):
            raise ValueError(
                "A chave do objeto deve ser texto."
            )

        # Decodifica o caminho enviado pelo S3.
        chave = unquote_plus(chave_codificada)

        # Processa somente CSVs da bronze.
        if (
            not chave.startswith("01-bronze/")
            or not chave.endswith(".csv")
        ):
            ignorados += 1
            continue

        empresa, aeroporto, token = identificar_destino(chave)
                # Cada arquivo do evento precisa ser incorporado.
        # Só elimina registros repetidos do mesmo caminho.
        destino = (empresa, aeroporto, token, chave)

        # Reconstrói cada servidor uma única vez por invocação,
        # mesmo que existam vários registros dele no evento.
        servidores.setdefault(destino, chave)

    resultados = []

    for chave in servidores.values():
        verificar_tempo(context)

        resultados.append(
            processar_servidor(
                bucket_configurado,
                chave,
                context,
            )
        )

    return {
        "arquivos_processados": len(resultados),
        "registros_ignorados": ignorados,
        "resultados": resultados,
    }