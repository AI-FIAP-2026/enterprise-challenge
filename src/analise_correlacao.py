import pandas as pd
import oracledb as cx_Oracle # Biblioteca atualizada
import numpy as np

# Configurações de acesso
USER = "rm568906".lower()
PASSWORD = "fiap26".lower()
DSN = "oracle.fiap.com.br:1521/orcl"

def analisar_correlacao():
    print("Iniciando conexão com o banco de dados...")
    
    try:
        # 1. Conexão com o Banco Oracle (Modo Thin - não exige Instant Client)
        conn = cx_Oracle.connect(user=USER, password=PASSWORD, dsn=DSN)
        
        # 2. Query SQL para consolidar Clima e Eventos
        # Utilizamos LEFT JOIN para garantir que dias sem incidentes sejam contabilizados
        # Agrupamos por ID_FAZENDA e DATA para evitar distorção (várias medições no mesmo dia)
        query = """
        SELECT 
            c.ID_FAZENDA,
            TRUNC(c.DATA_HORA) as DATA_REGISTRO,
            AVG(c.TEMPERATURA) as MEDIA_TEMP,
            AVG(c.UMIDADE) as MEDIA_UMIDADE,
            AVG(c.VELOCIDADE_VENTO) as MEDIA_VENTO,
            COUNT(e.PROTOCOLO) as QTD_INCIDENTES
        FROM CS_CLIMA c
        LEFT JOIN CS_CLIMA_EVENTOS e 
            ON c.ID_FAZENDA = e.ID_FAZENDA 
            AND TRUNC(c.DATA_HORA) = TRUNC(e.REGISTRO)
        GROUP BY c.ID_FAZENDA, TRUNC(c.DATA_HORA)
        """
        
        # 3. Carregar dados em um DataFrame
        df = pd.read_sql(query, conn)
        conn.close()
        print("Dados carregados com sucesso!")
        
        if df.empty:
            print("Nenhum dado encontrado para análise.")
            return

        # 4. Cálculo da Matriz de Correlação
        # Selecionamos as colunas de interesse
        colunas = ['MEDIA_TEMP', 'MEDIA_UMIDADE', 'MEDIA_VENTO', 'QTD_INCIDENTES']
        matriz_correlacao = df[colunas].corr()
        
        print("\n--- Matriz de Correlação (Pearson) ---")
        print(matriz_correlacao)
        
        # 5. Salvar resultado para visualização
        matriz_correlacao.to_csv("resultado_correlacao.csv")
        print("\nResultado salvo em 'resultado_correlacao.csv'")
        
    except Exception as e:
        print(f"Erro ao processar: {e}")

if __name__ == "__main__":
    analisar_correlacao()