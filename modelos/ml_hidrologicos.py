# -*- coding: utf-8 -*-
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import oracledb
import pandas as pd
import numpy as np
import time
import joblib
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score, classification_report, confusion_matrix, roc_curve

from auth import USER, PASSWORD, DSN

def rodar_treinamento_hidrologico(progress_callback=None):
    inicio_total = time.time()
    
    def atualizar_progresso(etapa, progresso, texto):
        print(f"[{progresso}%] {texto}")
        if progress_callback:
            progress_callback(progresso, etapa, texto)

    atualizar_progresso("Conexão", 10, "Conectando ao banco Oracle...")

    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    except Exception as e:
        print(f"🔴 Erro ao conectar no Oracle: {e}")
        return None

    atualizar_progresso("Extração", 30, "Lendo query SQL de risco hidrológico em lotes...")
    
    caminho_sql = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'sql', 'ml_hidrologico.sql'))
    
    try:
        with open(caminho_sql, 'r', encoding='utf-8') as f:
            sql_extracao = f.read().strip()
            if sql_extracao.endswith(';'):
                sql_extracao = sql_extracao[:-1].strip()
    except Exception as e:
        print(f"🔴 Erro ao ler o arquivo SQL hidrológico em {caminho_sql}: {e}")
        connection.close()
        return None
    
    try:
        cursor = connection.cursor()
        cursor.execute(sql_extracao)
        colunas = [col[0].lower() for col in cursor.description]
        
        batch_size = 20000
        lotes_dfs = []
        
        while True:
            linhas_lote = cursor.fetchmany(batch_size)
            if not linhas_lote:
                break
                
            dados_limpos = []
            for linha in linhas_lote:
                linha_convertida = []
                for item in linha:
                    if hasattr(item, "read"):
                        linha_convertida.append(str(item.read()))
                    else:
                        linha_convertida.append(item)
                dados_limpos.append(linha_convertida)
                
            df_lote = pd.DataFrame(dados_limpos, columns=colunas)
            lotes_dfs.append(df_lote)
            print(f"-> Lote carregado: {len(df_lote)} registros processados...")
            
        cursor.close()
        connection.close()
        
        if lotes_dfs:
            df = pd.concat(lotes_dfs, ignore_index=True)
        else:
            df = pd.DataFrame(columns=colunas)
            
    except Exception as e:
        print(f"🔴 Erro na execução da query no Oracle: {e}")
        if 'connection' in locals() and connection:
            connection.close()
        return None

    if df.empty or len(df) < 10:
        print("⚠️ AVISO: A consulta retornou menos de 10 registros.")
        return None

    atualizar_progresso("Pré-processamento", 50, "Calculando features hidrológicas temporais no treino...")
    for col in df.columns:
        if col not in ['uf', 'codigo_ibge', 'data_hora_br', 'inc_data_evento']:
            df[col] = pd.to_numeric(df[col], errors='coerce')
    
    df = df.dropna(subset=['temperatura', 'umidade', 'velocidade_vento', 'precipitacao'])
    
    if 'data_hora_br' in df.columns and 'codigo_ibge' in df.columns:
        df['data_hora_br'] = pd.to_datetime(df['data_hora_br'], errors='coerce')
        df = df.sort_values(by=['codigo_ibge', 'data_hora_br'])
        
        df['temp_media_7d'] = df.groupby('codigo_ibge')['temperatura'].transform(lambda x: x.rolling(7, min_periods=1).mean())
        df['umidade_media_7d'] = df.groupby('codigo_ibge')['umidade'].transform(lambda x: x.rolling(7, min_periods=1).mean())
        df['chuva_acumulada_3d'] = df.groupby('codigo_ibge')['precipitacao'].transform(lambda x: x.rolling(72, min_periods=1).sum()) 
        df['chuva_acumulada_7d'] = df.groupby('codigo_ibge')['precipitacao'].transform(lambda x: x.rolling(168, min_periods=1).sum()) 
    else:
        df['temp_media_7d'] = df['temperatura']
        df['umidade_media_7d'] = df['umidade']
        df['chuva_acumulada_3d'] = df['precipitacao']
        df['chuva_acumulada_7d'] = df['precipitacao']

    coluna_target = 'alerta_desastre'
    if coluna_target not in df.columns:
        for c in df.columns:
            if c.lower() == 'alerta_desastre':
                coluna_target = c
                break

    limite_seguranca = 100000
    if len(df) > limite_seguranca:
        print(f"⚙️ Aplicando amostragem de segurança ({limite_seguranca:,} registros)...")
        df = df.sample(n=limite_seguranca, random_state=42)

    total_registros = len(df)
    print(f"📊 Total de registros efetivos para treino hidrológico: {total_registros:,}")

    features_numericas = [
        'temperatura', 'umidade', 'velocidade_vento', 'precipitacao',
        'temp_media_7d', 'umidade_media_7d', 'chuva_acumulada_3d', 'chuva_acumulada_7d'
    ]
    features_disponiveis = [f for f in features_numericas if f in df.columns]
    
    X = df[features_disponiveis].fillna(0)
    y = df[coluna_target].fillna(0).astype(int)

    if len(y.unique()) < 2:
        print("🔴 ERRO: A base de dados possui apenas uma classe.")
        return None

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    atualizar_progresso("Treinamento", 75, "Treinando Random Forest para Risco Hidrológico...")
    
    modelo = RandomForestClassifier(
        n_estimators=150, 
        max_depth=15, 
        class_weight='balanced_subsample', 
        random_state=42, 
        n_jobs=-1
    )
    modelo.fit(X_train, y_train)

    caminho_modelo_pkl = os.path.abspath(os.path.join(os.path.dirname(__file__), 'ml_hidrologico_treinado.pkl'))
    joblib.dump(modelo, caminho_modelo_pkl)
    print(f"💾 [SUCESSO] Modelo hidrológico salvo em: {caminho_modelo_pkl}")

    atualizar_progresso("Avaliação", 90, "Calculando limiar ótimo via Curva ROC (Youden)...")
    
    y_prob = modelo.predict_proba(X_test)[:, 1]
    
    # Cálculo dinâmico do melhor limiar (Youden's J statistic: Sensibilidade + Especificidade - 1)
    fpr, tpr, thresholds = roc_curve(y_test, y_prob)
    opt_idx = np.argmax(tpr - fpr)
    limiar_otimizado = thresholds[opt_idx]
    
    # Garantir limites seguros para o limiar
    if limiar_otimizado > 0.9:
        limiar_otimizado = 0.5
    elif limiar_otimizado < 0.01:
        limiar_otimizado = 0.1
        
    print(f"🎯 Limiar estatístico ótimo calculado: {limiar_otimizado:.4f}")
    
    y_pred = (y_prob >= limiar_otimizado).astype(int)

    acuracia = accuracy_score(y_test, y_pred)
    try:
        auc_score = roc_auc_score(y_test, y_prob)
    except Exception:
        auc_score = 0.0

    matriz_conf = confusion_matrix(y_test, y_pred)
    report_dict = classification_report(y_test, y_pred, output_dict=True, zero_division=0)

    atualizar_progresso("Concluído", 100, f"Processo finalizado em {time.time() - inicio_total:.2f} segundos!")

    return {
        'total_registros': total_registros,
        'acuracia': acuracia,
        'auc_score': auc_score,
        'matriz_conf': matriz_conf,
        'report_dict': report_dict
    }

if __name__ == "__main__":
    print("==========================================================")
    print(" 💧 INICIANDO TREINAMENTO: MODELO DE RISCO HIDROLÓGICO")
    print("==========================================================")
    
    resultados = rodar_treinamento_hidrologico()
    
    if resultados:
        report = resultados['report_dict']
        print("\n" + "="*60)
        print(" 📊 RESULTADOS DO MODELO HIDROLÓGICO")
        print("="*60)
        print(f" Total de registros analisados: {resultados['total_registros']:,}".replace(',', '.'))
        print(f" Acurácia Geral............: {resultados['acuracia']*100:.2f}%")
        print(f" AUC-ROC Score.............: {resultados['auc_score']:.4f}")
        print(f" Recall (Captura de Cheias): {report.get('1', {}).get('recall', 0):.4f}")
        print(f" F1-Score (Hidrológico)....: {report.get('1', {}).get('f1-score', 0):.4f}")
        print("="*60 + "\n")
    else:
        print("⚠️ O treinamento hidrológico não retornou resultados válidos.")
    