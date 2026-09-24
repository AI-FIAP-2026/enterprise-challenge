import json
import re
from pathlib import Path

INPUT_PATH = Path("data/processed/manual_paginas_filtradas.json") #---> Caminho do JSON com as páginas filtradas
OUTPUT_PATH = Path("data/processed/regras_candidatas.json") #---> Caminho onde vão estar as regras candidatas para nosso modelo validar

#---> Padrões regex para identificar sentenças que provavelmente contêm regras de segurança ou manutenção
RULE_PATTERNS = [
    r"\bdeve\b.*",
    r"\bnão deve\b.*",
    r"\bnunca\b.*",
    r"\bproibido\b.*",
    r"\bevitar\b.*",
    r"\bverificar\b.*",
    r"\binspecionar\b.*",
    r"\blimpar\b.*",
    r"\bsubstituir\b.*",
    r"\btrocar\b.*",
    r"\blubrificar\b.*",
    r"\bajustar\b.*",
    r"\ba cada\s+\d+\s+horas?.*",
    r"\bantes de\b.*",
    r"\bapós\b.*",
    r"\btemperatura\b.*",
    r"\bmarcha lenta\b.*",
    r"\bcarga\b.*",
    r"\bdiesel\b.*",
    r"\bppm\b.*",
]

#---> Palavras de sinalização que indicam a presença de informações críticas, mesmo que não sigam os padrões de regras
SIGNAL_WORDS = ["PERIGO", "ATENÇÃO", "CUIDADO", "IMPORTANTE"]

#---> Função para dividir o texto em frases
def split_sentences(text: str):
    text = text.replace("\n", " ") #---> Substitui quebras de linha por espaços para evitar quebras de frases
    parts = re.split(r"(?<=[.!?])\s+", text) #---> Usa regex para dividir o texto em frases com base em pontuação seguida de espaço
    return [p.strip() for p in parts if len(p.strip()) > 20] #---> Retorna apenas as frases que têm mais de 20 caracteres

#---> Função para extrair regras candidatas
def extract_candidates(page): 
    text = page["text"] #---> O texto será o texto das paginas
    sentences = split_sentences(text) #---> As frases serão a aplicação da ultima função sobre esse texto das páginas 

    candidates = [] #---> As regras candidatas começam com uma lista vazia

    for sentence in sentences: #---> Para cada frase em frases
        sentence_lower = sentence.lower() #---> Transformaremos a frase em minúscula

        #---> Correspondência será igual a todos os itens do RULE_PATTERNS que correspondem à frase atual
        matched = any(
            re.search(pattern, sentence_lower, flags=re.IGNORECASE) #---> Verificamos se a frase corresponde a algum dos padrões de regra definidos, ignorando maiúsculas e minúsculas
            for pattern in RULE_PATTERNS #---> Para cada padrão dentro do RULE_PATTERNS
        )

        #---> Sinal será a primeira palavra de sinalização encontrada na frase, ou None se nenhuma for encontrada
        signal = next(
            (word for word in SIGNAL_WORDS if word in sentence.upper()), #---> Verificamos se alguma das palavras de sinalização está presente na frase (convertida para maiúscula para comparação)
            None
        )

        #---> Se a frase corresponder a um padrão de regra ou se tiver uma palavra de sinalização
        if matched or signal:
            candidates.append({ #---> Adicionamos um dicionário à lista de candidatos contendo
                "page_number": page["page_number"], #---> O número da página de onde a regra foi extraída
                "signal_word": signal, #---> A palavra de sinalização (se houver)
                "raw_text": sentence #---> O texto bruto da frase
            })

    return candidates #---> Retornando a lista de regras candidatas extraídas da página

if __name__ == "__main__": #---> O bloco principal do código é executado quando o script é rodado diretamente.
    with open(INPUT_PATH, "r", encoding="utf-8") as f: #---> Com o open abrimos o arquivo do caminho de entrada no modo de leitura
        pages = json.load(f) #---> Carregamos as páginas no formato json

    all_candidates = [] #---> Criamos uma lista vazia "todas as candidatas" para receber todas as regras candidatas

    for page in pages: #---> para cada página em páginas
        all_candidates.extend(extract_candidates(page)) #---> Aplicamos a função de extrair regras e salvamos na lista "todas as candidatas"   

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f: #---> Depois com o open, abrimos o arquivo no caminho de saída no formato de escrita
        json.dump(all_candidates, f, ensure_ascii=False, indent=2) #---> E inserimos todas as regras candidatas nele

    print(f"Regras candidatas extraídas: {len(all_candidates)}") #---> Exibimos o número de regras candidatas extraídas
    print(f"Arquivo salvo em: {OUTPUT_PATH}") #---> E exibimos o caminho onde esse arquivo das regras foi salvo