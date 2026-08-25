# -*- coding: utf-8 -*-
import pandas as pd
import numpy as np
import os

# Lista das colunas financeiras de impacto dos eventos climáticos
colunas_financeiras = [
    'DM_17', 'DM_18', 'DM_19', 'DM_20', 'DM_21', 'DM_22', 
    'PEPL_1', 'PEPL_2', 'PEPL_3', 'PEPL_4', 'PEPL_S5', 'PEPL_6', 
    'PEPL_7', 'PEPL_8', 'PEPL_9', 'PEPL_10', 'PEPL_11', 
    'PEPR_Agricultura', 'PEPR_13', 'PEPR_14', 'PEPR_15', 'PEPR_16'
]

# Caminhos apontando corretamente para a pasta 'referencias'
caminho_entrada = os.path.join('referencias', 'CS_Sp2_Dataset_s2id_Danos_Tratado_v3.csv')
caminho_saida = os.path.join('referencias', 'CS_Sp2_Dataset_Danos_Limpo.csv')

if not os.path.exists(caminho_entrada):
    print(f"Erro: O arquivo '{caminho_entrada}' não foi encontrado.")
else:
    print("Carregando base de dados de danos...")
    df = pd.read_csv(caminho_entrada, sep=';', encoding='utf-8-sig', low_memory=False)
    
    total_apagados = 0

    print("Analisando e limpando valores financeiros superiores a 90 bilhões...")
    for col in colunas_financeiras:
        if col in df.columns:
            # Converte para numérico tratando eventuais vírgulas como separadores decimais
            col_numeric = pd.to_numeric(df[col].astype(str).str.replace(',', '.'), errors='coerce')
            
            # Identifica valores absurdos maiores que 90 bilhões
            mascara_invalida = col_numeric > 90000000000
            total_apagados += mascara_invalida.sum()
            
            # Substitui os valores inválidos por NaN (que será gravado como NULL no CSV)
            df.loc[mascara_invalida, col] = np.nan

    # Salva o novo arquivo limpo na pasta referencias
    df.to_csv(caminho_saida, sep=';', index=False, encoding='utf-8-sig')
    
    print(f"\nLimpeza concluída com sucesso!")
    print(f"Total de células setadas para NULL: {total_apagados}")
    print(f"Arquivo limpo salvo em: {caminho_saida}")