from collections import Counter
import os
import unicodedata
import oracledb
import pandas as pd
from auth import DSN, PASSWORD, USER

arquivo_name = "CS_Queimadas_2023.csv"

# Dicionário de dados explícito e focado para tratar os fragmentos corrompidos (Mojibake)
dicionario_carateres_especiais = {
    "√Â": "É",      # PoconÉ
    "√Ä": "Ê",      # TrÊs Lagoas / Querência
    "√Ö": "Ú",      # BaraÚna / Grajaú
    "√ÉO": "ÃO",    # RiachÃO
    "√É": "Á",      # Ceará / Goiás
    "√Å": "Á",      
    "√¢": "Â",      # MATA ATLÂNTICA
    "√ã": "Ã",
    "√à": "À",
    "√Š": "Ê",
    "√©": "É",
    "√ê": "Ê",
    "√Ì": "Ó",      # BaianÓpolis
    "√í": "Í",
    "√¯": "Í",
    "√ô": "Ó",
    "√ó": "Ó",
    "√õ": "Õ",
    "√Ú": "Ú",
    "√ú": "Ú",
    "√û": "Û",
    "√Ç": "Ç",
    "√ç": "Ç",
    "√Á": "Ç",      # São GonÇalo
}

# Contador global para auditar o uso do dicionário
contador_erros_tratados = Counter()

# Mapeamento robusto de Estados para a sigla UF oficial
estado_para_uf = {
    'ACRE': 'AC', 'ALAGOAS': 'AL', 'AMAPA': 'AP', 'AMAZONAS': 'AM', 'BAHIA': 'BA',
    'CEARA': 'CE', 'CEAR√Å': 'CE', 'CEARÁ': 'CE', 'CEARÃ': 'CE',
    'DISTRITO FEDERAL': 'DF', 'ESPIRITO SANTO': 'ES', 
    'GOIAS': 'GO', 'GOI√ÅS': 'GO', 'GOIÁS': 'GO', 'GOIÃS': 'GO',
    'MARANHAO': 'MA', 'MARANHÃO': 'MA', 'MARANH√ÅO': 'MA',
    'MATO GROSSO': 'MT', 'MATO GROSSO DO SUL': 'MS', 'MINAS GERAIS': 'MG',
    'PARA': 'PA', 'PARAIBA': 'PB', 
    'PARANA': 'PR', 'PARAN√Å': 'PR', 'PARANÁ': 'PR',
    'PERNAMBUCO': 'PE', 
    'PIAUI': 'PI', 'PIAU√Ç': 'PI', 'PIAUÍ': 'PI', 'PIAUÇ': 'PI',
    'RIO DE JANEIRO': 'RJ', 'RIO GRANDE DO NORTE': 'RN', 'RIO GRANDE DO SUL': 'RS',
    'RONDONIA': 'RO', 'RORAIMA': 'RR', 'SANTA CATARINA': 'SC', 
    'SAO PAULO': 'SP', 'SÃO PAULO': 'SP', 'S√ÉO PAULO': 'SP',
    'SERGIPE': 'SE', 'TOCANTINS': 'TO', 
    'AC': 'AC', 'AL': 'AL', 'AP': 'AP', 'AM': 'AM', 'BA': 'BA', 'CE': 'CE', 'DF': 'DF',
    'ES': 'ES', 'GO': 'GO', 'MA': 'MA', 'MT': 'MT', 'MS': 'MS', 'MG': 'MG', 'PA': 'PA',
    'PB': 'PB', 'PR': 'PR', 'PE': 'PE', 'PI': 'PI', 'RJ': 'RJ', 'RN': 'RN', 'RS': 'RS',
    'RO': 'RO', 'RR': 'RR', 'SC': 'SC', 'SP': 'SP', 'SE': 'SE', 'TO': 'TO'
}

def limpar_texto_municipio(val):
  if val is None or pd.isna(val):
    return None
  val_str = str(val).strip()
  if val_str == "" or val_str.upper() in ["NAN", "NONE", "NULL", "-999"]:
    return None

  # Aplica o dicionário explicitamente e contabiliza as ocorrências
  for fragmento_errado, caractere_certo in dicionario_carateres_especiais.items():
    if fragmento_errado in val_str:
      quantas = val_str.count(fragmento_errado)
      contador_erros_tratados[f"[{fragmento_errado}] -> [{caractere_certo}]"] += quantas
      val_str = val_str.replace(fragmento_errado, caractere_certo)

  # Normalização Unicode e conversão para maiúsculas para o JOIN
  val_str = unicodedata.normalize("NFKD", val_str).upper()
  
  if "PIAU" in val_str:
    val_str = "PIAUÍ"

  return val_str

def limpar_estado(val):
  if val is None or pd.isna(val):
    return None
  val_str = str(val).strip()
  if val_str == "" or val_str.upper() in ["NAN", "NONE", "NULL", "-999"]:
    return None
  
  for fragmento_errado, caractere_certo in dicionario_carateres_especiais.items():
    if fragmento_errado in val_str:
      quantas = val_str.count(fragmento_errado)
      contador_erros_tratados[f"[ESTADO: {fragmento_errado}] -> [{caractere_certo}]"] += quantas
      val_str = val_str.replace(fragmento_errado, caractere_certo)

  val_str = unicodedata.normalize("NFKD", val_str).upper()
  return estado_para_uf.get(val_str, val_str)

print("A ligar ao Oracle para carregar a tabela de referência CS_MUNICIPIOS...")
connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
query_municipios = (
    "SELECT MUNICIPIO_IBGE AS CD_MUNICIPIO, MUNICIPIO AS MUNICIPIO, UF AS "
    "ESTADO FROM CS_MUNICIPIOS"
)
df_municipios = pd.read_sql(query_municipios, con=connection)
connection.close()

df_municipios.columns = [c.upper() for c in df_municipios.columns]
df_municipios["ESTADO"] = df_municipios["ESTADO"].astype(str).str.strip().str.upper()
df_municipios["MUNICIPIO"] = df_municipios["MUNICIPIO"].apply(lambda x: unicodedata.normalize("NFKD", str(x)).upper())
df_municipios["CD_MUNICIPIO"] = df_municipios["CD_MUNICIPIO"].dropna().astype(int).astype(str)

caminho_arquivo = os.path.join("referencias", arquivo_name)
if not os.path.exists(caminho_arquivo):
  caminho_arquivo = arquivo_name

print(f"\n[TESTE 100 LINHAS - COM DICIONÁRIO EXPLICITO] A ler as primeiras 100 linhas de: {caminho_arquivo}")
try:
  df = pd.read_csv(caminho_arquivo, encoding="utf-8", encoding_errors="replace", nrows=100)
except Exception:
  df = pd.read_csv(caminho_arquivo, encoding="latin1", encoding_errors="replace", nrows=100)

df.columns = [c.upper() for c in df.columns]

if "ESTADO" in df.columns:
  df["ESTADO"] = df["ESTADO"].apply(limpar_estado)

if "MUNICIPIO" in df.columns:
  df["MUNICIPIO"] = df["MUNICIPIO"].apply(limpar_texto_municipio)

if "CD_MUNICIPIO" in df.columns:
  df = df.drop(columns=["CD_MUNICIPIO"])

df_merged = pd.merge(df, df_municipios, on=["ESTADO", "MUNICIPIO"], how="left")

if "CD_MUNICIPIO" in df_merged.columns:
  df_merged["CD_MUNICIPIO"] = df_merged["CD_MUNICIPIO"].apply(
      lambda x: str(int(float(x))) if pd.notna(x) and str(x).strip() != "" and str(x).upper() != "NAN" else None
  )

nan_count = df_merged["CD_MUNICIPIO"].isna().sum()
total_linhas = len(df_merged)

print("\n================ RELATÓRIO DO CONTADOR DO DICIONÁRIO ================")
if contador_erros_tratados:
  for erro, freq in contador_erros_tratados.most_common():
    print(f"  {erro} : {freq} ocorrência(s)")
else:
  print("  [AVISO] Nenhuma substituição do dicionário foi acionada!")
print("======================================================================\n")

print(f"Total de linhas na amostra: {total_linhas}")
print(f"Linhas com CD_MUNICIPIO preenchido com sucesso: {total_linhas - nan_count}")
print(f"Linhas sem correspondência (NaN no CD_MUNICIPIO): {nan_count}")

cols_exibir = [c for c in ["DATA_PAS", "SATELITE", "ESTADO", "MUNICIPIO", "CD_MUNICIPIO", "BIOMA", "RISCO_FOGO"] if c in df_merged.columns]
print("\nPrimeiras 20 linhas processadas:")
print(df_merged[cols_exibir].head(20).to_string())

if nan_count > 0:
  print("\n--- MUNICÍPIOS QUE AINDA FICARAM COM CD_MUNICIPIO COMO NAN ---")
  nan_df = df_merged[df_merged["CD_MUNICIPIO"].isna()][["ESTADO", "MUNICIPIO"]].drop_duplicates()
  print(nan_df.to_string())
  print("-------------------------------------------------------------")