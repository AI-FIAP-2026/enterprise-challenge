# -*- coding: utf-8 -*-
import pandas as pd
import os
from datetime import datetime, timedelta

def gerar_plano_analise_climatica():
    caminho_arquivo = os.path.join('referencias', 'CS_Sp2_Dataset_Com_IBGE.csv')
    
    if not os.path.exists(caminho_arquivo):
        print(f"Erro: O arquivo {caminho_arquivo} não foi encontrado.")
        return

    print("Carregando base de eventos com IBGE...")
    df = pd.read_csv(caminho_arquivo, sep=';', encoding='utf-8-sig', low_memory=False)

    # Identifica a coluna de data (usando 'Registro')
    candidatas_data = [c for c in df.columns if any(termo in c.lower() for termo in ['data', 'inicio', 'ocorrencia', 'registro', 'ano'])]
    
    if candidatas_data:
        coluna_data = candidatas_data[0]
        print(f"Coluna de referência temporal identificada: '{coluna_data}'")
        df['data_evento'] = pd.to_datetime(df[coluna_data], errors='coerce', dayfirst=True)
    else:
        df['data_evento'] = pd.to_datetime('2025-01-01')

    # Identifica a coluna do COBRADE
    coluna_evento = 'COBRADE' if 'COBRADE' in df.columns else [c for c in df.columns if 'cobrade' in c.lower()][0]

    # Lista oficial exata dos 34 códigos COBRADE permitidos
    cobrades_permitidos = {
        '11110', 
        '11311', '11312', '11313', '11314', '11321', '11331', '11332', '11340',
        '11410', '11420', '11431', '11432', '11433', 
        '12100', '12200', '12300',
        '13111', '13112', '13120', 
        '13211', '13212', '13213', '13214', '13215',
        '13310', '13321', '13322', 
        '14110', '14120', '14131', '14132', '14140', 
        '23110'
    }

    # Extrai estritamente os 5 primeiros caracteres numéricos da coluna COBRADE (ex: "13214" de "13214 - Tempestade...")
    df['codigo_5_digitos'] = df[coluna_evento].astype(str).str.strip().str.slice(0, 5)

    # Filtra mantendo APENAS os registros cujos 5 primeiros dígitos estão na lista oficial permitida
    df_filtrado = df[df['codigo_5_digitos'].isin(cobrades_permitidos)].copy()
    df_filtrado = df_filtrado.dropna(subset=['codigo_ibge'])

    print(f"Total de registros filtrados corretamente (somente os 34 permitidos): {len(df_filtrado)}")

    resultados = []

    print("Calculando janelas de antecedência...")
    for _, row in df_filtrado.iterrows():
        tipo = str(row[coluna_evento])
        codigo_cobrade = row['codigo_5_digitos']
        municipio = str(row['Municipio'])
        uf = str(row['uf'])
        ibge = int(row['codigo_ibge'])
        data_evento = row['data_evento'] if pd.notnull(row['data_evento']) else datetime(2025, 1, 1)

        # Define a janela de antecedência (Lookback)
        # 90 dias para eventos de longo prazo (Estiagem, Seca, Onda de Calor, Baixa Umidade)
        if codigo_cobrade in ['14110', '14120', '14140', '13310']:
            dias_antecedencia = 90  
        else:
            dias_antecedencia = 30  # 30 dias para os demais eventos

        data_inicio_analise = data_evento - timedelta(days=dias_antecedencia)
        data_fim_analise = data_evento

        resultados.append({
            'Codigo_IBGE': ibge,
            'Municipio': municipio,
            'UF': uf,
            'COBRADE': tipo,
            'Data_Ocorrencia': data_evento.strftime('%Y-%m-%d'),
            'Inicio_Analise_Climatica': data_inicio_analise.strftime('%Y-%m-%d'),
            'Fim_Analise_Climatica': data_fim_analise.strftime('%Y-%m-%d'),
            'Dias_Lookback': dias_antecedencia
        })

    df_resultado = pd.DataFrame(resultados)
    df_resultado = df_resultado.drop_duplicates(subset=['Codigo_IBGE', 'COBRADE', 'Inicio_Analise_Climatica'])

    caminho_saida = os.path.join('referencias', 'CS_Plano_Analise_Climatica.csv')
    df_resultado.to_csv(caminho_saida, sep=';', index=False, encoding='utf-8-sig')

    print(f"\nPlano de análise limpo gerado com sucesso! Total de janelas mapeadas: {len(df_resultado)}")
    print(f"Arquivo salvo em: {caminho_saida}")
    print("\nAmostra do Plano de Análise Gerado:")
    print(df_resultado.head(10).to_string(index=False))

if __name__ == "__main__":
    gerar_plano_analise_climatica()