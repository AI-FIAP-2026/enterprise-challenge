# -*- coding: utf-8 -*-
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import pandas as pd
import oracledb
from auth import USER, PASSWORD, DSN

def atualizar_apenas_data_evento():
    print("--- INICIANDO ATUALIZAÇÃO EXCLUSIVA DE INC_DATA_EVENTO ---")
    
    caminho_arquivo = os.path.join(root_dir, 'referencias', 'CS_s2id_tratado.csv')
    
    if not os.path.exists(caminho_arquivo):
        print(f"[ERRO CRÍTICO] Arquivo não encontrado em: {caminho_arquivo}")
        return

    print("Lendo o arquivo CSV do S2iD...")
    try:
        df = pd.read_csv(caminho_arquivo, sep=';', encoding='utf-8', low_memory=False)
    except UnicodeDecodeError:
        df = pd.read_csv(caminho_arquivo, sep=';', encoding='latin1', low_memory=False)

    total_csv_inicial = len(df)
    
    df.columns = [str(c).strip() for c in df.columns]

    # Filtra datas a partir de 2016 (compatível com a base Oracle)
    df['Data_Registro_dt'] = pd.to_datetime(df['Data_Registro'], errors='coerce', dayfirst=True)
    df = df.dropna(subset=['Data_Registro_dt', 'Cod_IBGE_Mun', 'Cod_Cobrade'])
    
    df = df[df['Data_Registro_dt'].dt.year >= 2016]
    total_para_processar = len(df)
    
    print(f"Total geral no CSV: {total_csv_inicial:,}".replace(',', '.'))
    print(f"Total válido para processar (>= 2016): {total_para_processar:,}".replace(',', '.'))
    print("-" * 60)

    try:
        conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = conn.cursor()

        total_atualizados = 0
        total_tentativas = 0
        
        print("\nProcessando e atualizando INC_DATA_EVENTO no Oracle...")
        for idx, row in df.iterrows():
            try:
                total_tentativas += 1
                ibge = int(float(row['Cod_IBGE_Mun']))
                
                data_registro_csv = row['Data_Registro_dt'].strftime('%Y-%m-%d')
                cod_cobrade_limpo = str(int(float(row['Cod_Cobrade']))).strip()

                # UPDATE restrito apenas à coluna INC_DATA_EVENTO
                sql_update = """
                    UPDATE CS_EVENTOS SET
                        INC_DATA_EVENTO = TO_DATE(:1, 'YYYY-MM-DD')
                    WHERE CODIGO_IBGE = :2 
                      AND TRUNC(DATA_OCORRENCIA) = TO_DATE(:3, 'YYYY-MM-DD')
                      AND COBRADE LIKE :4 || '%'
                """
                
                cursor.execute(sql_update, (
                    data_registro_csv,
                    ibge, data_registro_csv, cod_cobrade_limpo
                ))
                
                if cursor.rowcount > 0:
                    conn.commit()
                    total_atualizados += cursor.rowcount

            except Exception as row_err:
                continue

            # Visualização em tempo real do progresso
            if total_tentativas % 1000 == 0 or total_tentativas == total_para_processar:
                falta = total_para_processar - total_tentativas
                porcentagem = (total_tentativas / total_para_processar) * 100
                print(f"[Progresso] Analisadas: {total_tentativas:,} / {total_para_processar:,} ({porcentagem:.1f}%) | "
                      f"Atualizadas: {total_atualizados:,} | Falta: {falta:,}".replace(',', '.'))

        print("\n" + "="*50)
        print(" ATUALIZAÇÃO DE INC_DATA_EVENTO CONCLUÍDA! ")
        print("="*50)
        print(f"Total lido no CSV       : {total_csv_inicial:,}".replace(',', '.'))
        print(f"Total processado        : {total_tentativas:,}".replace(',', '.'))
        print(f"Total alterado no banco : {total_atualizados:,}".replace(',', '.'))
        print("="*50)

        cursor.close()
        conn.close()

    except Exception as e:
        print(f"\n🔴 ERRO GERAL NO BANCO: {e}")

if __name__ == "__main__":
    atualizar_apenas_data_evento()