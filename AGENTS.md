AGENTS
======

Resumo rápido
------------
- Repositório: MonTech-python
- ETL atual: existe uma Lambda (lambda_function.py) que lê CSVs da área bronze (01-bronze/...), reconstrói a silver por dia, calcula deltas para contadores e gera um JSON "ouro" (gold) com agregações por hora. Há um esqueleto auxiliar em etl_silver2gold.py usado por testes.
- Identificador do servidor: extraído do token presente no caminho S3 (o terceiro segmento após o prefixo bronze). Em identificar_destino() o parâmetro `token` é retornado — esse token é o identificador do servidor (ex.: AIS-CGH-01, SPA-GRU-01).

O que já está implementado (reutilizável)
--------------------------------------
- Identificação de destino e validação de caminho S3 (identificar_destino).
- Leitura de CSV do S3 (ler_csv) e paginação/listagem (listar_arquivos).
- Tratamento básico dos dados (tratar_dados): conversão para numérico, remoção de linhas inválidas, ordenação, cálculo de deltas para contadores, detecção de outliers, verificação de IdAeroporto coerente com o caminho.
- Mecanismo incremental por dia em processar_servidor: lê somente os dias afetados, concatena base + parte nova, evita duplicação, e recalcula deltas considerando última leitura do dia anterior.
- Funções de segurança: verificar_tempo para evitar timeout e checagem de prefixos permitidos antes de gravar.
- Escrita atual de um artefato gold (salvar_resultados) que grava um JSON por dia com agregações por hora (manteremos para compatibilidade quando possível).

O que precisa mudar / ser acrescentado
------------------------------------
Objetivo: adaptar a Lambda silver2gold para produzir os artefatos gold usados pelas duas dashboards e pela aplicação Java, conforme a especificação do produto. As mudanças principais são:

1) Novos arquivos gold por aeroporto/servidor
   - gold/diagnostico/{aeroporto}/{servidor}.json
     - Janelas em múltiplas resoluções (1min, 5min, 15min, 30min, 45min).
     - Cada janela: inicio (epoch ms UTC), p10, mediana, p90, maximo, quantidade.
     - Resoluções calculadas diretamente a partir das leituras de 10 s (série bruta).
   - gold/series/{aeroporto}/{servidor}/AAAA-MM-DD.json
     - Série bruta por recurso com passoMs = 10000 e início = meia-noite America/Sao_Paulo em epoch ms.
     - Arrays com até 8640 posições, null quando falta coleta; idempotente por posição.
     - Manter 8 dias e apagar mais antigos.
   - gold/atual/{aeroporto}/{servidor}.json
     - Snapshot sobrescrito a cada arquivo da silver; contém geradoEm, ultimaColeta, janelaMs (60000), leiturasNaJanela e valores = média do último minuto por recurso (rede: média das taxas MB/s).
   - gold/processos/{aeroporto}/{servidor}.json
     - Retrato mais recente (capturadoEm e lista de até 20 processos de maior CPU).

2) Compatibilidade
   - Não remover nem quebrar o JSON já gerado por salvar_resultados (o "ouro" horário). Se possível, manter a gravação atual e acrescentar os novos arquivos.

3) Timezone
   - Interpretar TimeStamp dos CSVs como America/Sao_Paulo (UTC-3) ao converter para epoch ms. Todos os timestamps nos arquivos gold devem ser epoch ms UTC.

4) Séries e janelas
   - Guardar série bruta de 10 s por recurso (cpu, temperatura, ram, disco, swap, rede). Se alguma coluna não existir na silver, a série deve existir com valores null e você deve avisar (log/PR) quais colunas faltam.
   - Calcular percentis com interpolação linear (mesmo resultado que numpy.percentile default).
   - Janelas alinhadas por floor(t / passo) * passo com t em epoch ms; fim exclusivo; janelas abertas incluem leituras presentes.

5) Limitações e responsabilidades (restrições do LabRole)
   - A Lambda só pode ler a silver do S3 e gravar no gold no S3. Não pode acessar o banco de dados, Jira, Slack, SNS ou qualquer sistema externo.
   - A Lambda NÃO deve classificar leituras por limites, detectar episódios, gerar alertas ou persistir estado de detecção. Esses comportamentos ficam para o Node (backend) e a aplicação Java.

6) Validações exigidas antes de gravar cada arquivo
   - JSON serializa sem NaN (usar allow_nan=False na serialização).
   - Linhas das janelas ordenadas por inicio crescente e cada inicio múltiplo do passoMs.
   - Para cada janela: p10 <= mediana <= p90 <= maximo e quantidade >= 1.
   - Todas as posições do arquivo de série estejam entre 0 e 8639 e arrays não excedam 8640 elementos.
   - Os quatro tipos de arquivo trazem o mesmo aeroporto e servidor.
   - Nenhum valor numérico é escrito como texto (usar tipos numéricos).

7) Concorrência e timeout
   - Configurar concorrência reservada da Lambda para 1 (para evitar sobrescritas concorrentes do arquivo diário). Timeout recomendado ~60s (ajustar conforme volume).

Campos/colunas: verificação de compatibilidade
------------------------------------------------
- A Lambda já espera várias colunas (COLUNAS_NUMERICAS) incluindo UsoCPU_Geral, UsoRAM, UsoDisco, Rede_Bytes_Enviados, etc. Entretanto a especificação da silver menciona colunas adicionais e variações (p.ex. UsoCPU_Geral_imputed, Rede_Bytes_Enviados_MB_s, Rede_Bytes_Enviados_MB_s_imputed, original_records, partial_minute, e colunas de temperatura e métricas por recurso).
- A ação obrigatória: mapear quais colunas a silver efetivamente produz. Se houver diferença de nomes (por ex. Temperatura_CPU vs Temperatura), adicionar lógica de mapeamento (alias) ou gerar séries com null e avisar.

Plano de implementação (alto nível)
----------------------------------
1. Auditar os nomes das colunas emitidas pela silver (usar exemplos/CSV). Mapear para os recursos esperados: cpu, temperatura, ram, disco, swap, rede (MB/s).
2. Implementar geração do arquivo series/AAAA-MM-DD.json por aeroporto/servidor:
   - Calcular início do dia em America/Sao_Paulo e converter leituras para posições i = (ts_local_ms - inicio_ms) // 10000.
   - Atualizar somente posições afetadas (idempotente) e escrever com allow_nan=False.
   - Apagar arquivos com mais de 8 dias.
3. Implementar função que, dada a série bruta, calcula janelas para cada resolução solicitada e produz o bloco de diagnostico com colunas compactas.
4. Implementar escrita de atual/{aeroporto}/{servidor}.json com média do último minuto e ultimaColeta.
5. Implementar gravação de processos (processos/{aeroporto}/{servidor}.json) limitada a top 20 por CPU; aceitar que alguns campos podem estar ausentes e preencher null.
6. Manter salvar_resultados atual (compatibilidade) — só acrescentar gravações adicionais.
7. Adicionar validações unitárias e testes de integração local (ver lista abaixo).

Testes que você deve ver / incluir
---------------------------------
Unidades e integração para a nova lógica:

- Teste de parsing de TimeStamp: uma timestamp textual sem fuso deve ser interpretada como America/Sao_Paulo e convertida para epoch ms UTC corretamente.
- Teste de séries: montar uma pequena série de 10 s e verificar que as posições na array correspondem ao índice esperado (p.ex. leitura em 14:03:00 -> posição correta) e que ausências são null.
- Teste de idempotência: reprocessar o mesmo CSV não altera posições já gravadas para o mesmo instante.
- Teste de janelas/resoluções: dado uma série conhecida, calcule manualmente percentis e verifique p10, mediana, p90, maximo e quantidade para várias resoluções (1min, 5min, 15min, 30min, 45min). Verificar ordenação e múltiplos do passo.
- Teste de atual: média do último minuto (últimas 6 leituras de 10 s) por recurso; rede deve usar campo MB/s (não média do contador).
- Teste de processos: garantir truncamento a 20 processos, campos numéricos como números, status string, ausência de campos permitida (preencher null).
- Teste de validações: produza janelas com quantidade 0 e verifique que a rotina lida corretamente (janelas sem leituras são descartadas do arquivo diretorio de janelas guardadas) e que as relações p10 <= mediana <= p90 <= maximo são verificadas.
- Teste de serialização JSON: assert allow_nan=False, sem NaN e todos os números como números.
- Teste de limpeza de arquivos antigos: garantir remoção após 8 dias.

Notas e riscos
--------------
- O LabRole não permite criar IAM roles — portanto não é possível expandir permissões. A Lambda precisa ter permissão de leitura/escrita ao bucket e os prefixos configurados via variáveis de ambiente.
- Performance: gerar percentis para grandes séries em memória (por servidor por dia) é razoável (8640 pontos) — usar numpy/pandas é suficiente. Se o número de servidores/dias por execução crescer, será necessário particionar.
- Concorrência: o requisito de concorrência reservada = 1 simplifica consistência, mas aumenta latência de processamento paralelo. Alternativa futura: usar locking via S3 object tags ou renomeação atômica (mais complexa e não permitida no lab).

Próximo passo
-------------
- Se você concordar com o plano, eu implemento as alterações na Lambda: gerar os quatro artefatos gold (diagnostico, series, atual, processos), mantendo a gravação atual do salvar_resultados, ajustando timezone e validações, e adicionando testes unitários para as rotinas críticas.

Referências rápidas de arquivos existentes
----------------------------------------
- lambda_function.py: comportamento atual do ETL incremental e funções utilitárias.
- etl_silver2gold.py: esqueleto de conversão de components -> calc_averages e compute_cpu_mean usado pelos testes existentes.
- tests/test_etl_silver2gold.py: teste atual que confirma cálculo de médias em silver_to_gold_components.
