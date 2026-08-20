import pandas as pd
import oracledb
import os
from oracle_config import USER, PASSWORD, DSN

def importar_municipios_direto():
    # 1. Caminho do arquivo original na pasta referencias
    caminho_arquivo = os.path.join('referencias', 'CS_Municipios.csv')
    
    if not os.path.exists(caminho_arquivo):
        print(f"Erro: O arquivo não foi encontrado em {caminho_arquivo}")
        return

    print("Carregando o arquivo original CS_Municipios.csv...")
    df = pd.read_csv(caminho_arquivo, encoding='utf-8')
    print(f"Total de registros lidos: {len(df)}")

    # 2. Exclusão das colunas que você não quer enviar ao Oracle
    colunas_para_excluir = ['capital', 'codigo_uf', 'siafi_id', 'ddd', 'fuso_horario']
    df = df.drop(columns=colunas_para_excluir, errors='ignore')
    
    # Padroniza os nomes das colunas para minúsculas
    df.columns = df.columns.str.lower().str.strip()
    print(f"Colunas ativas para importação: {df.columns.tolist()}")

    # 3. Conecta ao Banco Oracle da FIAP
    print("Conectando ao banco Oracle da FIAP...")
    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = conn.cursor()

    try:
        # 4. Cria a tabela CS_MUNICIPIOS (apaga se já existir)
        print("Criando a tabela CS_MUNICIPIOS...")
        cursor.execute("""
            BEGIN
                EXECUTE IMMEDIATE 'DROP TABLE CS_MUNICIPIOS';
            EXCEPTION
                WHEN OTHERS THEN NULL;
            END;
        """)
        
        cursor.execute("""
            CREATE TABLE CS_MUNICIPIOS (
                MUNICIPIO_IBGE NUMBER,
                MUNICIPIO VARCHAR2(255),
                LATITUDE NUMBER,
                LONGITUDE NUMBER
            )
        """)
        conn.commit()
        print("Tabela CS_MUNICIPIOS criada com sucesso.")

        # 5. Prepara e insere os dados em lote
        print("Inserindo dados no Oracle...")
        dados_para_inserir = []
        for _, row in df.iterrows():
            ibge = row.get('municipio_ibge')
            mun = row.get('municipio')
            lat = row.get('latitude')
            lon = row.get('longitude')

            dados_para_inserir.append((
                int(ibge) if pd.notnull(ibge) else None,
                str(mun) if pd.notnull(mun) else None,
                float(lat) if pd.notnull(lat) else None,
                float(lon) if pd.notnull(lon) else None
            ))

        sql_insert = """
            INSERT INTO CS_MUNICIPIOS (MUNICIPIO_IBGE, MUNICIPIO, LATITUDE, LONGITUDE)
            VALUES (:1, :2, :3, :4)
        """
        
        cursor.executemany(sql_insert, dados_para_inserir)
        conn.commit()
        print(f"Sucesso absoluto! {len(dados_para_inserir)} municípios inseridos na tabela CS_MUNICIPIOS.")

    except Exception as e:
        conn.rollback()
        print(f"Erro durante a importação: {e}")
    finally:
        cursor.close()
        conn.close()
        print("Conexão fechada.")

if __name__ == "__main__":
    importar_municipios_direto()