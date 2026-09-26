# -*- coding: utf-8 -*-
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

requests = __import__('requests')
import datetime
import oracledb
from auth import USER, PASSWORD, DSN

def sincronizar_focos_queimadas():
    print("\n[QUEIMADAS - INPE/SATÉLITE] Iniciando monitoramento de focos de calor nas fazendas...")
    
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    total_focos_inseridos = 0
    
    try:
        # 1. Busca as fazendas cadastradas no Oracle para checar coordenadas e raio de monitoramento
        cursor.execute("SELECT ID, NOME_FAZENDA, LATITUDE, LONGITUDE FROM CS_FAZENDAS WHERE LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL")
        fazendas = cursor.fetchall()
        
        if not fazendas:
            msg = "Nenhuma fazenda com coordenadas válidas encontrada no Oracle."
            print(f"   -> {msg}")
            return "ALERTA", msg, 0

        print(f"   -> Monitorando focos para {len(fazendas)} fazenda(s) cadastrada(s).")

        # Aqui entra a sua rotina de requisição à API/base de focos de queimadas (ex: dados abertos do INPE / BDQueimadas)
        # Abaixo está a estrutura de simulação segura / integração com a lógica que já alimenta a tabela CS_ALERTAS
        
        # Exemplo simulado de varredura ou requisição real:
        focos_detectados_api = [
            # ('ID_FAZENDA', 'Tipo_Alerta', 'Categoria_Risco', 'Data_Hora', 'Detalhe')
        ]

        # Processamento e inserção segura com verificação de duplicidade
        for foco in focos_detectados_api:
            id_fazenda, tipo_alerta, risco, data_str, detalhe_texto = foco

            sql_check = """
                SELECT COUNT(1) FROM CS_ALERTAS 
                WHERE ORIGEM_ALERTA LIKE '%INPE%' AND DETALHAMENTO_1 LIKE :1 AND DATA_HORA = TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS')
            """
            cursor.execute(sql_check, [f"%Fazenda ID: {id_fazenda}%", data_str])
            
            if cursor.fetchone()[0] == 0:
                sql_insert = """
                    INSERT INTO CS_ALERTAS (TIPO_ALERTA, ORIGEM_ALERTA, DATA_HORA, CATEGORIA_RISCO, ORIENTACAO, DETALHAMENTO_1, DETALHAMENTO_2)
                    VALUES (:1, 'INPE — Monitoramento Satelital', TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS'), :3, :4, :5, :6)
                """
                orientacao = "⚠️ ALERTA DE FOCO DE CALOR: Risco de propagação de incêndio nas proximidades. Acione a brigada e verifique aceiros."
                det1 = f"Fazenda ID: {id_fazenda} - {detalhe_texto}"
                det2 = "Monitoramento por satélite (INPE)"
                
                cursor.execute(sql_insert, [tipo_alerta, data_str, risco, orientacao, det1, det2])
                total_focos_inseridos += 1

        connection.commit()
        msg_sucesso = f"Sincronização INPE concluída. {total_focos_inseridos} novo(s) foco(s) registrado(s)."
        print(f"   -> {msg_sucesso}")
        return "SUCESSO", msg_sucesso, total_focos_inseridos

    except Exception as e:
        connection.rollback()
        msg_erro = f"Falha no monitoramento de queimadas: {str(e)}"
        print(f"[ERRO GERAL] {msg_erro}")
        return "ERRO", msg_erro, 0
    finally:
        cursor.close()
        connection.close()

if __name__ == "__main__":
    print("--- EXECUTANDO MÓDULO DE QUEIMADAS (Isolada) ---")
    sincronizar_focos_queimadas()