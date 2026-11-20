# Montech-backend

Este repositório tem como fim a organização do backend do projeto, onde utilizaremos Java e Python.

---

## Visão geral da extração

O `Script_Extracao.py` monitora um servidor 24/7. O fluxo é:

1. Conecta ao banco MySQL.
2. Faz o login do usuário (email e senha).
3. Pede o **token do servidor** e busca no banco os componentes selecionados para ele.
4. Em loop, amostra as métricas (CPU, RAM, disco, rede, temperatura, processos...) e grava um registro com a **média das amostras da janela**.
5. Grava os CSVs localmente e envia os arquivos de cada registro, por requisição HTTP, para a **Lambda**, que salva no S3.

> O script **não** acessa o S3 diretamente. Quem grava no bucket é a Lambda, então não são necessárias credenciais da AWS na máquina que roda a extração.

---

## Pré-requisitos

- Python 3.9 ou superior
- Banco MySQL acessível, com as tabelas `usuario`, `servidor`, `componente` e `servidorcomponente`
- Arquivo `.env` com as credenciais do banco (veja abaixo)
- URL da função Lambda configurada no script (veja abaixo)
- Os arquivos `Script_Extracao.py` e `Organizador.py` **na mesma pasta**
- Windows: terminal executado como **Administrador** (necessário para ler a temperatura da CPU)

---

## Configuração das bibliotecas

Faça o download das bibliotecas utilizando o pip.

Comando para baixar todas de uma vez:

```bash
pip install -r requirements.txt
```

Comando para baixar uma biblioteca específica:

```bash
pip install nome_da_biblioteca
```

Bibliotecas usadas pelo `Script_Extracao.py`:

| Biblioteca | Para que serve |
|---|---|
| `pandas` | Monta e grava os CSVs |
| `psutil` | Coleta as métricas do servidor |
| `mysql-connector-python` | Conexão com o MySQL |
| `python-dotenv` | Lê o arquivo `.env` |

O envio à Lambda usa apenas a biblioteca padrão do Python (`urllib`), sem instalar nada extra.

---

## Configuração do MySQL

Crie um arquivo `.env` na raiz do projeto, copie e cole o template abaixo e preencha com as suas credenciais:

```env
DB_HOST=localhost
DB_USER=UserBanco
DB_PASSWORD=SenhaUser
DB_NAME=NomeDataBase
```

---

## Configuração da Lambda (envio ao S3)

No topo do `Script_Extracao.py`, preencha a constante com a **Function URL** da Lambda:

```python
URL_LAMBDA = "https://<id-da-funcao>.lambda-url.us-east-1.on.aws/"
```

- Substitua `<id-da-funcao>` pela URL real da sua função (aba **Configuração → URL da função** no console da AWS).
- A URL fica entre aspas, **sem espaços** no início ou no fim. O script remove espaços sobrando, mas vale conferir.
- Enquanto `URL_LAMBDA` estiver vazia, os envios falham com um aviso e os CSVs continuam salvos localmente.

### Como a Lambda recebe os arquivos

- Requisição **POST** na Function URL, com o corpo em `text/csv`.
- O caminho do arquivo no S3 vai no query param `filename`, por exemplo:
  `?filename=01-bronze/<empresa>/<aeroporto>/<token>/<DD-MM-AAAA>/<HH>/<token>-AAAA-MM-DD-HH-MM-SS.csv`
- A Lambda grava o corpo no bucket com essa chave e responde `200`.

### Requisitos da função na AWS

- Role da Lambda com a permissão `s3:PutObject` no bucket do projeto.
- Function URL criada e com o tipo de autenticação definido (recomendado: não deixar aberta ao público sem proteção).
- Limite de **6 MB** por requisição. Os arquivos enviados são pequenos (um registro por arquivo), então não chegam perto disso.
- Recomendado: validar o `filename` na Lambda (aceitar só chaves começando com `01-bronze/` e sem `..`).

---

## Organização dos arquivos

O `Organizador.py` devolve a pasta do servidor. A partir dela, o script cria:

```
<pasta do Organizador>/<token>/<DD-MM-AAAA>/
├── <token>-AAAA-MM-DD.csv                              CSV do dia (somente local)
├── <token>-AAAA-MM-DD-processos.csv                    processos do dia (somente local)
└── <HH>/
    ├── <token>-AAAA-MM-DD-HH-MM-SS.csv                 um registro de componentes
    └── <token>-AAAA-MM-DD-HH-MM-SS-processos.csv       um registro de processos
```

- O CSV do dia é o histórico local, com todas as linhas, e **não** é enviado.
- Os arquivos da pasta da hora são pequenos, enviados **uma única vez** cada.
- No S3, a chave começa sempre com `01-bronze/` e segue o mesmo caminho acima.
- Se um envio falhar, o arquivo fica pendente e é reenviado nos próximos registros (e ao encerrar com `Ctrl+C`).

---

## Frequência de coleta

Ajustada no topo do `Script_Extracao.py`:

| Constante | O que controla |
|---|---|
| `INTERVALO_AMOSTRA_SEGUNDOS` | De quanto em quanto tempo as métricas são lidas |
| `INTERVALO_REGISTRO_SEGUNDOS` | De quanto em quanto tempo um registro é gravado (média das amostras da janela) |
| `TIMEOUT_LAMBDA_SEGUNDOS` | Tempo máximo de espera pela resposta da Lambda |

---

## Inicialização

Para iniciar no Windows (execute o cmd como **ADM** para funcionar a captura):

```bash
python Script_Extracao.py
python Script_tratamento.py
```

Para iniciar no Linux:

```bash
python3 Script_Extracao.py
python3 Script_tratamento.py
```

Ao iniciar, o script pede:

1. **Email** e **senha** do usuário cadastrado no banco.
2. **Token do servidor** que será monitorado.

Para encerrar, use `Ctrl+C`. O script tenta reenviar os arquivos pendentes antes de sair.

> Credenciais da AWS: a extração não precisa delas, pois o envio é feito pela Lambda. Se o `Script_tratamento.py` acessar o S3 diretamente, configure as credenciais da AWS no usuário (ADM no Windows) que executa esse script.

---

## Problemas comuns

| Mensagem ou sintoma | Causa provável |
|---|---|
| `URL can't contain control characters` | Espaço ou quebra de linha na `URL_LAMBDA` |
| `URL_LAMBDA não foi configurada` | A constante `URL_LAMBDA` está vazia |
| `Falha ao enviar ...` | Lambda fora do ar, URL incorreta ou sem rede. O arquivo continua local e é reenviado depois |
| Resposta HTTP 403 | Function URL com autenticação ou permissão que bloqueia a requisição |
| Temperatura da CPU vazia | No Windows, terminal sem permissão de Administrador, ou a máquina não expõe o sensor |
| `Erro ao conectar ao banco de dados` | Credenciais do `.env` incorretas ou MySQL inacessível |
| `Servidor sem empresa/aeroporto definido` | O token não tem empresa e aeroporto vinculados no banco |