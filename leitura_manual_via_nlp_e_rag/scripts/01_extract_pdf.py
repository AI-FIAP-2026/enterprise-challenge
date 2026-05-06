import fitz
import json
from pathlib import Path

#---> Configurações de caminho
PDF_PATH = Path("data/raw/manual_colhedora.pdf") #---> Caminho do PDF (manual da colhedora)
OUTPUT_PATH = Path("data/processed/manual_paginas.json") #---> Caminho do arquivo de saída

def extract_pdf_text(pdf_path: Path): #---> Função para abrir o pdf e extrair o texto de cada página
    doc = fitz.open(pdf_path) #---> Documento vai ser cada página do pdf
    pages = [] #---> Lista vazia para armazenar o número da página e o texto extraído

    for page_index, page in enumerate(doc): #---> paga cada página e número da página no documento
        text = page.get_text("text") #---> Extrai o texto da página usando o método get_text com o formato "text"

        pages.append({ #---> Adiciona um dicionário à lista 
            "page_number": page_index + 1, #---> Adiciona no dicionário da página o número da página (index + 1 para começar em 1)
            "text": text.strip() #---> Adiciona no dicionário da página o texto extraído, usando strip() para remover espaços em branco extras
        })

    return pages

if __name__ == "__main__":
    pages = extract_pdf_text(PDF_PATH) #---> Chama a função para extrair o texto do PDF e armazena o resultado na pages

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True) #---> Cria o diretório de saída se ele não existir

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f: #---> Abre o arquivo de saída no nível de escrita, usando encoding="utf-8" para garantir que os caracteres acentuados sejam salvos corretamente
        json.dump(pages, f, ensure_ascii=False, indent=2) #---> Salva a lista de páginas como um arquivo JSON, usando ensure_ascii=False para preservar caracteres e usando o indent=2 para formatar o JSON de forma mais legível

        print(f"Extração concluída: {len(pages)} páginas salvas em {OUTPUT_PATH}")