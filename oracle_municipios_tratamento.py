import pandas as pd
import os

# Caminho do arquivo
caminho_arquivo = os.path.join('referencias', 'CS_Municipios.csv')

def processar_e_validar_municipios():
    if not os.path.exists(caminho_arquivo):
        print(f"Erro: Arquivo não encontrado em {caminho_arquivo}")
        return

    # 1. Carrega a tabela
    df = pd.read_csv(caminho_arquivo, encoding='utf-8')
    print(f"Arquivo carregado com {len(df)} registros.")

    # 2. Exclusão das colunas solicitadas
    colunas_para_excluir = ['capital', 'codigo_uf', 'siafi_id', 'ddd', 'fuso_horario']
    # O comando 'errors="ignore"' evita erro caso alguma dessas colunas já não exista no arquivo
    df = df.drop(columns=colunas_para_excluir, errors='ignore')
    print(f"Colunas removidas: {colunas_para_excluir}")

    # 3. Auditoria e Validação
    print("\n--- Iniciando Auditoria ---")
    
    # Validações de valores faltantes
    nulos = df.isnull().sum()
    if nulos.sum() > 0:
        print("[AVISO] Valores nulos encontrados nas colunas:")
        print(nulos[nulos > 0])
    
    # Validação Numérica
    for col in ['latitude', 'longitude', 'municipio_ibge']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')
            if df[col].isnull().any():
                print(f"[ERRO] A coluna '{col}' possui valores inválidos (não numéricos)!")

    # 4. Formatação Final para Oracle (Salva na pasta referencias)
    caminho_saida = os.path.join('referencias', 'CS_Municipios_Formatado_Oracle.csv')
    df.to_csv(caminho_saida, sep=';', index=False, encoding='utf-8-sig', float_format='%.4f')
    
    print(f"\n--- Concluído ---")
    print(f"Arquivo pronto salvo em: {caminho_saida}")

processar_e_validar_municipios()