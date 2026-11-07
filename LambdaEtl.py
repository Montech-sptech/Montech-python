import json
import urllib.parse
import csv
import boto3

# Inicializa o cliente do S3
s3_client = boto3.client('s3')

def lambda_handler(event, context):
    try:
        # 1. Captura o bucket e o nome do arquivo CSV do gatilho
        bucket_name = event['Records'][0]['s3']['bucket']['name']
        file_key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'], encoding='utf-8')
        
        print(f"--- Nova ETL Iniciada (CSV) ---")
        print(f"Lendo o arquivo: {file_key} do bucket: {bucket_name}")
        
        # 2. Busca o arquivo CSV dentro do S3
        response = s3_client.get_object(Bucket=bucket_name, Key=file_key)
        
        # 3. Lê o corpo do arquivo e decodifica as linhas de texto
        csv_lines = response['Body'].read().decode('utf-8').splitlines()
        
        # 4. Converte as linhas do CSV em um formato de dicionário estruturado
        # Nota: Se o seu CSV usar ponto-e-vírgula (;) mude o delimiter abaixo para delimiter=';'
        leitor_csv = csv.DictReader(csv_lines, delimiter=',')
        
        print("Conteúdo do arquivo CSV processado linha por linha:")
        for linha in leitor_csv:
            print(dict(linha))
        
        return {
            'statusCode': 200,
            'body': json.dumps('ETL executada com sucesso e CSV processado!')
        }
        
    except Exception as e:
        print(f"Erro no processamento da ETL do CSV: {str(e)}")
        raise e