# Montech-backend
Este repositório tem como fim a organização do bakend do projeto, onde utilizaremos Java e Python.

# CONFIGURAÇÃO DAS BIBLIOTECAS
Faça o download das bibliotecas utilizando o pip.

Comando para baixar todas de uma vez:
pip install -r requirements.txt

Comando para baixar uma biblioteca específica:
pip install nome_da_biblioteca

# CONFIGURAÇÃO DO MYSQL
Crie um arquivo `.env` na raiz do projeto, copie e cole o template abaixo e preencha com as suas credenciais:

DB_HOST=localhost
DB_USER=UserBanco
DB_PASSWORD=SenhaUser
DB_NAME=NomeDataBase

# INICIALIZAÇÃO
Para iniciar no Windows (Execute o cmd como adm para funcionar a captura):
python Script_Extracao.py
python Script_tratamento.py

(lembre-se de colocar as credenciais da aws no user adm)

Para iniciar no Linux:
python3 Script_Extracao.py
python3 Script_tratamento.py
