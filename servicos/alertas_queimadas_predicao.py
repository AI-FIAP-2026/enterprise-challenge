# -*- coding: utf-8 -*-
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import datetime
import oracledb
import pandas as pd
import numpy as np
import joblib
from auth import USER, PASSWORD, DSN

def obter_recomendacao_queimadas(probabilidade):
    """Gera categorias de risco e orientações operacionais específicas para incêndios florestais."""
    if probabilidade >= 60.0:
        return "Crítico", (
            "🚨 ALERTA MÁXIMO DE INCÊNDIO FLORESTAL: Risco iminente de propagação rápida de fogo. "
            "Paralise imediatamente a operação de colheita e maquinário pesado nas áreas de risco, "
            "mobilize a brigada de incêndio e acione os órgãos de contenção locais."
        )
    elif probabilidade >= 40.0:
        return "Alto", (
            "⚠️ ALERTA DE ALTO RISCO DE INCÊNDIO: Condições severas de estiagem e umidade crítica. "
            "Inspecione aceiros, restrinja o tráfego de frotas com centelhas próximas a palhadas e "
            "mantenha equipes de prontidão com caminhões-pipa."
        )
    elif probabilidade >= 25.0:
        return "Médio", (
            "⚠️ ATENÇÃO REDOBRADA (Risco Moderado): Aumento do estresse térmico na região. "
            "Monitore constantemente as condições climáticas e evite qualquer tipo de queima controlada ou "
            "limpeza de terreno por fogo."
        )
    else:
        return "Baixo", (
            "ℹ️ Monitoramento preventivo ativo: Condições climáticas estáveis para incêndio, "
            "porém mantenha os aceiros limpos e siga as normas de boas práticas agrícolas."
        )

def executar():
    print("\n[PREDIÇÃO ML - QUEIMADAS] Iniciando análise preditiva de risco nas fazendas...")
    
    # Caminho do modelo treinado salvo em disco
    caminho_modelo_pkl = os.path.abspath(os.path.join(root_dir, 'modelos', 'ml_queimadas_treinado.pkl'))
    
    if not os.path.exists(caminho_modelo_pkl):
        msg = "Modelo preditivo (.pkl) não encontrado. Execute o treinamento em 'ml_queimadas.py' primeiro."
        print(f"   -> 🔴 {msg}")
        return "ALERTA", msg, 0

    try:
        # Carrega o modelo de Machine Learning já treinado
        modelo = joblib.load(caminho_modelo_pkl)
        print("   -> 🧠 Modelo preditivo carregado com sucesso do disco.")
    except Exception as e:
        msg = f"Falha ao carregar o arquivo .pkl do modelo: {e}"
        print(f"   -> 🔴 {msg}")
        return "ERRO", msg, 0

    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    total_alertas_gerados = 0
    
    try:
        # Extrai o histórico climático recente das fazendas da tabela CS_FAZENDAS_CLIMA[cite: 8]
        sql_clima = """
            SELECT 
                fc.ID_FAZENDA, fc.TEMPERATURA, fc.UMIDADE, fc.VELOCIDADE_VENTO, fc.PRECIPITACAO, fc.DATA_HORA,
                f.NOME_FAZENDA, f.MUNICIPIO, f.ESTADO, f.CODIGO_IBGE
            FROM CS_FAZENDAS_CLIMA fc
            JOIN CS_FAZENDAS f ON fc.ID_FAZENDA = f.ID
            WHERE fc.DATA_HORA >= SYSDATE - 30
            ORDER BY fc.ID_FAZENDA, fc.DATA_HORA
        """
        cursor.execute(sql_clima)
        colunas = [col[0].lower() for col in cursor.description]
        dados_brutos = cursor.fetchall()
        
        if not dados_brutos:
            msg = "Nenhum dado climático recente encontrado em CS_FAZENDAS_CLIMA para predição."
            print(f"   -> {msg}")
            return "ALERTA", msg, 0

        df = pd.DataFrame(dados_brutos, columns=colunas)

        for col in ['temperatura', 'umidade', 'velocidade_vento', 'precipitacao']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
        df = df.dropna(subset=['temperatura', 'umidade', 'velocidade_vento', 'precipitacao'])

        # Engenharia de features temporal em Pandas (Memória de Estiagem)
        df['data_hora'] = pd.to_datetime(df['data_hora'], errors='coerce')
        df = df.sort_values(by=['id_fazenda', 'data_hora'])
        
        df['temp_media_7d'] = df.groupby('id_fazenda')['temperatura'].transform(lambda x: x.rolling(7, min_periods=1).mean())
        df['umidade_media_7d'] = df.groupby('id_fazenda')['umidade'].transform(lambda x: x.rolling(7, min_periods=1).mean())
        df['chuva_acumulada_15d'] = df.groupby('id_fazenda')['precipitacao'].transform(lambda x: x.rolling(15, min_periods=1).sum())

        df_recente = df.groupby('id_fazenda').tail(1).copy()

        features = [
            'temperatura', 'umidade', 'velocidade_vento', 'precipitacao',
            'temp_media_7d', 'umidade_media_7d', 'chuva_acumulada_15d'
        ]
        
        X_atual = df_recente[features].fillna(0)

        # Predição utilizando o modelo real carregado do disco (.pkl)
        probabilidades = modelo.predict_proba(X_atual)[:, 1]
        df_recente['risco_predito'] = probabilidades

        # Limiar de alerta de 15% (0.15)
        limiar_alerta = 0.15
        fazendas_em_risco = df_recente[df_recente['risco_predito'] >= limiar_alerta]

        for _, row in fazendas_em_risco.iterrows():
            id_fazenda = row['id_fazenda']
            nome_fazenda = row['nome_fazenda']
            municipio = row['municipio']
            uf = row['estado']
            prob = row['risco_predito'] * 100
            
            data_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            tipo_alerta = "Risco de Incêndio Florestal (IA Preditiva)"
            
            categoria_risco, orientacao = obter_recomendacao_queimadas(prob)
            
            sql_check = """
                SELECT COUNT(1) FROM CS_ALERTAS 
                WHERE ORIGEM_ALERTA = 'IA Preditiva — Machine Learning' 
                  AND DETALHAMENTO_1 LIKE :1 
                  AND TRUNC(DATA_HORA) = TRUNC(SYSDATE)
            """
            cursor.execute(sql_check, [f"%Fazenda ID: {id_fazenda}%"])
            
            if cursor.fetchone()[0] == 0:
                sql_insert = """
                    INSERT INTO CS_ALERTAS (TIPO_ALERTA, ORIGEM_ALERTA, DATA_HORA, CATEGORIA_RISCO, ORIENTACAO, DETALHAMENTO_1, DETALHAMENTO_2)
                    VALUES (:1, 'IA Preditiva — Machine Learning', TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS'), :3, :4, :5, :6)
                """
                det1 = f"Fazenda ID: {id_fazenda} - {nome_fazenda} ({municipio}/{uf})"
                det2 = f"Modelo Random Forest (.pkl) — Probabilidade: {prob:.1f}%"
                
                cursor.execute(sql_insert, [tipo_alerta, data_str, categoria_risco, orientacao, det1, det2])
                total_alertas_gerados += 1

        connection.commit()
        msg_sucesso = f"Predição concluída via .pkl. {total_alertas_gerados} novo(s) alerta(s) gerados para as fazendas."
        print(f"   -> {msg_sucesso}")
        return "SUCESSO", msg_sucesso, total_alertas_gerados

    except Exception as e:
        connection.rollback()
        msg_err = f"Erro no serviço de predição ML: {str(e)}"
        print(f"[ERRO GERAL] {msg_err}")
        return "ERRO", msg_err, 0
    finally:
        cursor.close()
        connection.close()

if __name__ == "__main__":
    print("--- EXECUTANDO SERVIÇO DE PREDIÇÃO DE INCÊNDIOS (Isolado) ---")
    executar()