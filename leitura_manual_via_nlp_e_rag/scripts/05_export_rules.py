import json
import pandas as pd
from pathlib import Path

INPUT_PATH = Path("outputs/regras_estruturadas.json") #---> Caminho do arquivo JSON contendo as regras estruturadas extraídas e validadas pelo modelo, que serão exportadas para CSV e Excel.
CSV_PATH = Path("outputs/regras_estruturadas.csv") #---> Caminho do arquivo CSV onde as regras estruturadas serão exportadas.
XLSX_PATH = Path("outputs/regras_estruturadas.xlsx") #---> Caminho do arquivo Excel onde as regras estruturadas serão exportadas.

if __name__ == "__main__": #---> O bloco principal do código é executado quando o script é rodado diretamente.
    with open(INPUT_PATH, "r", encoding="utf-8") as f: #---> Com o open abrimos o arquivo do caminho de entrada no modo de leitura
        rules = json.load(f) #---> E carregamos as regras estruturadas no formato json

    df = pd.DataFrame(rules) #---> Criamos um DataFrame do pandas a partir da lista de regras estruturadas, para facilitar a manipulação e exportação dos dados

    df.to_csv(CSV_PATH, index=False, encoding="utf-8-sig") #---> Exportamos o dataframe para CSV
    df.to_excel(XLSX_PATH, index=False) #---> Exportamos o dataframe para Excel

    print(f"CSV salvo em: {CSV_PATH}") #---> Exibimos o caminho onde o arquivo CSV foi salvo
    print(f"Excel salvo em: {XLSX_PATH}") #---> Exibimos o caminho onde o arquivo Excel foi salvo