import pandas as pd
import unicodedata
import os

def cruzar_e_corrigir_eventos_com_relatorio():
    # 1. Caminhos oficiais dentro da pasta 'referencias'
    caminho_mun = os.path.join('referencias', 'CS_Municipios.csv')
    caminho_danos = os.path.join('referencias', 'CS_Sp2_Dataset_s2id_Danos_Tratado_v3.csv')
    
    if not os.path.exists(caminho_mun) or not os.path.exists(caminho_danos):
        print("Erro: Os arquivos de entrada não foram encontrados na pasta 'referencias'.")
        return

    print("Carregando bases de dados de dentro de 'referencias'...")
    df_mun = pd.read_csv(caminho_mun, encoding='utf-8')
    df_danos = pd.read_csv(caminho_danos, encoding='utf-8-sig', sep=None, engine='python', on_bad_lines='skip')

    # Dicionário robusto de correções manuais já mapeadas
    correcoes_manuais = {
        "MOJI MIRIM_SP": "MOGI MIRIM_SP",
        "ESTANCIA TURISTICA DE SAO ROQUE_SP": "SAO ROQUE_SP",
        "SANTO ¬NGELO_RS": "SANTO ANGELO_RS",
        "AGUIARNAPOLIS_TO": "AGUIARNOPOLIS_TO",
        "ALVINOPOLIS_MG": "ALVINOPOLIS_MG",
        "AUGUSTINAPOLIS_TO": "AUGUSTINOPOLIS_TO",
        "ALPINOPOLIS_MG": "ALPINOPOLIS_MG",
        "ALCINOPOLIS_MS": "ALCINOPOLIS_MS",
        "BRASOPOLIS_MG": "BRASOPOLIS_MG",
        "AMORINOPOLIS_GO": "AMORINOPOLIS_GO",
        "ARENAPOLIS_GO": "ARENAPOLIS_GO",
        "AVELINOPOLIS_GO": "AVELINOPOLIS_GO",
        "SAO LUIS DO PARAITINGA_SP": "SAO LUIS DO PARAITINGA_SP",
        "MUQUEM DO SAO FRANCISCO_BA": "MUQUEM DO SAO FRANCISCO_BA",
        "BOA SAUDE_RN": "BOA SAUDE_RN"
    }

    # Função avançada de normalização
    def limpar_nome_municipio(texto):
        if not isinstance(texto, str):
            return ""
        texto = texto.replace("Õ", "O").replace("Ã", "A").replace("Ç", "C").replace("¬", "A")
        for char in ["`", "'", "’", "´", "-", "_", "–"]:
            texto = texto.replace(char, " ")
        nfkd = unicodedata.normalize('NFKD', texto)
        texto_sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
        return " ".join(texto_sem_acento.upper().split())

    # Mapeamento da UF numérica para sigla textual
    uf_map = {
        11: 'RO', 12: 'AC', 13: 'AM', 14: 'RR', 15: 'PA', 16: 'AP', 17: 'TO',
        21: 'MA', 22: 'PI', 23: 'CE', 24: 'RN', 25: 'PB', 26: 'PE', 27: 'AL',
        28: 'SE', 29: 'BA', 31: 'MG', 32: 'ES', 33: 'RJ', 35: 'SP', 41: 'PR',
        42: 'SC', 43: 'RS', 50: 'MS', 51: 'MT', 52: 'GO', 53: 'DF'
    }

    # 3. Prepara a tabela de Municípios
    df_mun['sigla_uf'] = df_mun['codigo_uf'].map(uf_map)
    df_mun['nome_limpo'] = df_mun['nome'].apply(limpar_nome_municipio)
    df_mun['chave'] = df_mun['nome_limpo'] + "_" + df_mun['sigla_uf']
    
    mapa_ibge = dict(zip(df_mun['chave'], df_mun['codigo_ibge']))
    mapa_nome_oficial = dict(zip(df_mun['chave'], df_mun['nome']))

    # 4. Prepara e cruza a base de Danos
    print("Processando e aplicando correções finais...")
    df_danos['mun_limpo'] = df_danos['Municipio'].apply(limpar_nome_municipio)
    df_danos['uf_limpa'] = df_danos['uf'].str.upper().str.strip()
    df_danos['chave'] = df_danos['mun_limpo'] + "_" + df_danos['uf_limpa']

    df_danos['chave'] = df_danos['chave'].replace(correcoes_manuais)

    df_danos['codigo_ibge'] = df_danos['chave'].map(mapa_ibge)
    nome_oficial_atualizado = df_danos['chave'].map(mapa_nome_oficial)
    df_danos['Municipio'] = nome_oficial_atualizado.fillna(df_danos['Municipio'])

    # Relatório detalhado
    total = len(df_danos)
    encontrados = df_danos['codigo_ibge'].notnull().sum()
    nao_encontrados = df_danos['codigo_ibge'].isnull().sum()
    
    print(f"\n--- Relatório Final Consolidado ---")
    print(f"Total de registros: {total}")
    print(f"Municípios corrigidos e com IBGE: {encontrados}")
    print(f"Ainda não encontrados (NULL): {nao_encontrados}")

    # EXIBE NO TERMINAL APENAS OS MUNICÍPIOS QUE AINDA FICARAM PENDENTES
    if nao_encontrados > 0:
        print("\n[ATENÇÃO] Lista exata de municípios únicos que continuaram sem match (NULL):")
        pendentes = df_danos[df_danos['codigo_ibge'].isnull()][['Municipio', 'uf']].drop_duplicates()
        print("-" * 35)
        print(pendentes.to_string(index=False))
        print("-" * 35)

    # 5. Salva o resultado final dentro da pasta 'referencias'
    df_danos = df_danos.drop(columns=['mun_limpo', 'uf_limpa', 'chave'], errors='ignore')
    arquivo_saida = os.path.join('referencias', 'CS_Sp2_Dataset_Com_IBGE.csv')
    df_danos.to_csv(arquivo_saida, sep=';', index=False, encoding='utf-8-sig', float_format='%.2f')
    print(f"\nArquivo final salvo com sucesso em: {arquivo_saida}")

if __name__ == "__main__":
    cruzar_e_corrigir_eventos_com_relatorio()