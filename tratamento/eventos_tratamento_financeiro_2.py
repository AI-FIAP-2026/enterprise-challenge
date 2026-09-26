# -*- coding: utf-8 -*-
import pandas as pd
import os

def enriquecer_plano():
    caminho_plano = os.path.join('referencias', 'CS_Plano_Analise_Climatica.csv')
    caminho_danos = os.path.join('referencias', 'CS_Sp2_Dataset_Danos_Limpo.csv')
    caminho_saida = os.path.join('referencias', 'CS_Plano_Analise_Climatica_Financeiro.csv')

    if not os.path.exists(caminho_plano) or not os.path.exists(caminho_danos):
        print("Erro: Arquivos não encontrados na pasta 'referencias'.")
        return

    print("Lendo os arquivos...")
    df_plano = pd.read_csv(caminho_plano, sep=';', encoding='utf-8-sig')
    df_danos = pd.read_csv(caminho_danos, sep=';', encoding='utf-8-sig', low_memory=False)

    # Lista das colunas financeiras de impacto
    colunas_financeiras = [
        'DM_17', 'DM_18', 'DM_19', 'DM_20', 'DM_21', 'DM_22', 
        'PEPL_1', 'PEPL_2', 'PEPL_3', 'PEPL_4', 'PEPL_S5', 'PEPL_6', 
        'PEPL_7', 'PEPL_8', 'PEPL_9', 'PEPL_10', 'PEPL_11', 
        'PEPR_Agricultura', 'PEPR_13', 'PEPR_14', 'PEPR_15', 'PEPR_16'
    ]

    # Converte as colunas financeiras para numérico (tratando vírgulas)
    for col in colunas_financeiras:
        if col in df_danos.columns:
            df_danos[col] = pd.to_numeric(df_danos[col].astype(str).str.replace(',', '.'), errors='coerce')
        else:
            df_danos[col] = 0.0

    # Calcula os valores solicitados
    df_danos['Impacto_Financeiro_Total'] = df_danos[colunas_financeiras].sum(axis=1, skipna=True)
    df_danos['Impacto_PEPR_Agricultura'] = df_danos['PEPR_Agricultura'].fillna(0.0) if 'PEPR_Agricultura' in df_danos.columns else 0.0

    # Identifica dinamicamente as colunas de data e localização em cada base
    col_data_plano = next((c for c in df_plano.columns if 'data' in c.lower() or 'ocorrencia' in c.lower()), 'Data_Ocorrencia')
    col_mun_plano = next((c for c in df_plano.columns if 'municipio' in c.lower()), 'Municipio')
    col_uf_plano = next((c for c in df_plano.columns if c.lower() == 'uf'), 'UF')

    col_data_danos = next((c for c in df_danos.columns if any(t in c.lower() for t in ['registro', 'data', 'inicio'])), 'Registro')
    col_mun_danos = next((c for c in df_danos.columns if 'municipio' in c.lower()), 'Municipio')
    col_uf_danos = next((c for c in df_danos.columns if c.lower() == 'uf'), 'uf')

    print(f"Cruzando dados usando: Município, UF e Data ({col_data_plano} / {col_data_danos})")

    # Padroniza as chaves de cruzamento para formato limpo em maiúsculas e strings de data YYYY-MM-DD
    df_plano['key_data'] = pd.to_datetime(df_plano[col_data_plano], errors='coerce').dt.strftime('%Y-%m-%d')
    df_plano['key_mun'] = df_plano[col_mun_plano].astype(str).str.strip().str.upper()
    df_plano['key_uf'] = df_plano[col_uf_plano].astype(str).str.strip().str.upper()

    df_danos['key_data'] = pd.to_datetime(df_danos[col_data_danos], errors='coerce', dayfirst=True).dt.strftime('%Y-%m-%d')
    df_danos['key_mun'] = df_danos[col_mun_danos].astype(str).str.strip().str.upper()
    df_danos['key_uf'] = df_danos[col_uf_danos].astype(str).str.strip().str.upper()

    # Agrega os dados financeiros da base de danos por município, UF e data
    df_danos_agregado = df_danos.groupby(['key_mun', 'key_uf', 'key_data']).agg({
        'Impacto_Financeiro_Total': 'sum',
        'Impacto_PEPR_Agricultura': 'sum'
    }).reset_index()

    # Faz o cruzamento (Left Join) mantendo todas as linhas do plano de análise
    df_enriquecido = pd.merge(
        df_plano,
        df_danos_agregado,
        on=['key_mun', 'key_uf', 'key_data'],
        how='left'
    )

    # Remove as chaves auxiliares criadas para o match
    df_enriquecido = df_enriquecido.drop(columns=['key_data', 'key_mun', 'key_uf'], errors='ignore')

    # Preenche eventuais valores ausentes (onde não houve correspondência exata) com 0.0
    df_enriquecido['Impacto_Financeiro_Total'] = df_enriquecido['Impacto_Financeiro_Total'].fillna(0.0)
    df_enriquecido['Impacto_PEPR_Agricultura'] = df_enriquecido['Impacto_PEPR_Agricultura'].fillna(0.0)

    # Salva o resultado final enriquecido
    df_enriquecido.to_csv(caminho_saida, sep=';', index=False, encoding='utf-8-sig', float_format='%.2f')

    print(f"\nProcesso concluído com sucesso!")
    print(f"Novo arquivo salvo em: {caminho_saida}")
    print("\nAmostra das primeiras linhas com as novas colunas financeiras:")
    print(df_enriquecido[['Municipio', 'UF', 'Impacto_Financeiro_Total', 'Impacto_PEPR_Agricultura']].head(10).to_string(index=False))

if __name__ == '__main__':
    enriquecer_plano()