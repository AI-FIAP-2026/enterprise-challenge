import json
from pathlib import Path

INPUT_PATH = Path("data/processed/manual_paginas.json") #---> Caminho para o arquivo JSON contendo as páginas extraídas do manual.
OUTPUT_PATH = Path("data/processed/manual_paginas_filtradas.json") #---> Caminho para o arquivo JSON onde as páginas filtradas serão salvas.

#---> Listas de palavras-chave para identificar páginas relevantes e irrelevantes.
IMPORTANT_KEYWORDS = [
    "seção 95",
    "intervalos de serviço",
    "manutenção",
    "perigo",
    "atenção",
    "cuidado",
    "importante",
    "temperatura",
    "arrefecimento",
    "combustível",
    "diesel",
    "marcha lenta",
    "carga do motor",
    "colheita",
    "segurança",
    "operador",
    "motor",
    "hidráulico",
]
#---> As páginas que contêm palavras-chave importantes serão mantidas, enquanto as que contêm palavras-chave de ignorar serão descartadas.
IGNORE_KEYWORDS = [
    "prefácio",
    "número de identificação",
    "p.i.n",
    "índice",
    "catálogo de peças",
    "outros manuais",
]
#---> A função 'é_pagina_relevante' espera uma string e retorna um booleano indicando se a página é relevante ou não com base na presença de palavras-chave importantes
def is_relevant_page(text: str) -> bool:
    text_lower = text.lower() #---> Primeiro ela transforma o texto em minúsculas para comparar com as palavras-chave que são todas minúsculas.

    if any(k in text_lower for k in IGNORE_KEYWORDS): #---> Em seguida, ela verifica se alguma das palavras-chave das ignoradas está presente no texto
        return False

    return any(k in text_lower for k in IMPORTANT_KEYWORDS) #---> Mesmo processo para palavras que não devem ser ignoradas

if __name__ == "__main__": #---> O bloco principal do código é executado quando o script é rodado diretamente.
    with open(INPUT_PATH, "r", encoding="utf-8") as f: #---> Abre o arquivo no modo de leitura
        pages = json.load(f) #---> E salva o arquivo como um json

#---> Criamos uma lista vazia chamada de "filtrados"
    filtered = [
        page for page in pages #---> Para cada página em páginas
        if is_relevant_page(page["text"]) #---> Se a função é_pagina_relevante retornar True para o texto da página, ela é adicionada à lista filtrada
    ]

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f: #---> Ai com as páginas importantes já filtradas, abrimos o arquivo json la do output no modo de escrita
        json.dump(filtered, f, ensure_ascii=False, indent=2) #---> E exportamos o conteúdo do "filtrados" para ele e salvamos

    print(f"Páginas relevantes: {len(filtered)}") #---> Exibimos a quantidade de páginas filtradas salvas
    print(f"Arquivo salvo em: {OUTPUT_PATH}") #---> E exibimos o caminho onde elas foram salvas