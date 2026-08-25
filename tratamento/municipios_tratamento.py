# -*- coding: utf-8 -*-
import pandas as pd
import os

def tratar_municipios_para_oracle():
    caminho_entrada = os.path.join('referencias', 'CS_Municipios.csv')
    caminho_saida = os.path.join('referencias', 'CS_Municipios_Formatado_Oracle.csv')

    if not os.path.exists(caminho_entrada):
        print(f"Erro: O arquivo {caminho_entrada} não foi encontrado.")
        return

    print("Carregando arquivo original de municípios...")
    df = pd.read_csv(caminho_entrada, encoding='utf-8')

    # Dicionário de mapeamento do ID da UF para a Sigla
    uf_map_sigla = {
        11: 'RO', 12: 'AC', 13: 'AM', 14: 'RR', 15: 'PA', 16: 'AP', 17: 'TO',
        21: 'MA', 22: 'PI', 23: 'CE', 24: 'RN', 25: 'PB', 26: 'PE', 27: 'AL',
        28: 'SE', 29: 'BA', 31: 'MG', 32: 'ES', 33: 'RJ', 35: 'SP', 41: 'PR',
        42: 'SC', 43: 'RS', 50: 'MS', 51: 'MT', 52: 'GO', 53: 'DF'
    }

    print("Formatando colunas e mapeando a sigla da UF...")
    
    df['codigo_ibge'] = pd.to_numeric(df['codigo_ibge'], errors='coerce').astype('Int64')
    df['codigo_uf'] = pd.to_numeric(df['codigo_uf'], errors='coerce').astype('Int64')
    df['sigla_uf'] = df['codigo_uf'].map(uf_map_sigla)
    df['nome'] = df['nome'].astype(str).str.strip()
    df['latitude'] = pd.to_numeric(df['latitude'], errors='coerce')
    df['longitude'] = pd.to_numeric(df['longitude'], errors='coerce')

    # Ordem das colunas: MUNICIPIO_IBGE, MUNICIPIO, UF, LATITUDE, LONGITUDE
    df_final = df[['codigo_ibge', 'nome', 'sigla_uf', 'latitude', 'longitude']]

    df_final.to_csv(caminho_saida, sep=';', index=False, encoding='utf-8-sig', float_format='%.6f')
    print(f"Sucesso! Arquivo formatado salvo em: {caminho_saida}")
    print(f"Total de registros processados: {len(df_final)}")

if __name__ == "__main__":
    tratar_municipios_para_oracle()