import json
import requests
from pathlib import Path
from tqdm import tqdm

INPUT_PATH = Path("data/processed/regras_candidatas.json") #---> Caminho do arquivo json com as regras candidatas
PROMPT_PATH = Path("prompts/extract_rule_prompt.txt") #---> Caminho do arquivo com o prompt do modelo para extrair regras
OUTPUT_PATH = Path("outputs/regras_estruturadas.json") #---> Caminho de saída onde irão as regras estruturadas

LM_STUDIO_URL = "http://localhost:1234/v1/chat/completions" #---> Caminho onde o LMStudio estava rodando o modelo
MODEL_NAME = "gemma" #--> Escolhi o gemma-3-1B por limitações de hardware

def call_lm_studio(prompt: str): #---> Função para chamar o modelo
    payload = {
        "model": MODEL_NAME, #---> O modelo que iremos usar
        "messages": [ #---> A mensagem enviada para o modelo é composta por duas partes
            {
                "role": "system", #---> A primeira parte é a mensagem do sistema, onde definimos o comportamento esperado do modelo
                "content": "Você extrai regras técnicas e responde apenas JSON válido."
            },
            {
                "role": "user", #---> A segunda parte é a mensagem do usuário, onde passamos o prompt com o trecho de texto do manual que queremos extrair a regra
                "content": prompt
            }
        ],
        "temperature": 0.1, #---> Configuramos uma temperatura baixa para obter respostas mais consistentes e menos criativas, já que queremos extrair informações específicas
        "max_tokens": 700 #---> Limitamos o número de tokens para evitar respostas muito longas, já que esperamos um JSON estruturado e não uma resposta extensa
    }

    response = requests.post(LM_STUDIO_URL, json=payload, timeout=120) #---> Fazemos a requisição POST para onde o modelo esta rodando
    #---> Verificamos se a resposta foi bem-sucedida (código 200)
    response.raise_for_status() #---> Se não for, levantamos uma exceção com o status code e a resposta do modelo para ajudar na depuração.

    content = response.json()["choices"][0]["message"]["content"] #---> Se a resposta for bem-sucedida, extraímos o conteúdo da resposta do modelo
    #---> O modelo pode retornar o JSON dentro de blocos de código ou com texto adicional, então fazemos uma limpeza para tentar extrair apenas o JSON válido antes de parsear
    return content #---> Retornamos o conteúdo limpo para ser processado posteriormente

def safe_json_loads(text: str): #---> Função para carregamento seguro do json
    text = text.strip() #---> Texto será o texto sem espaços antes e depois dele

    if text.startswith("```"): #---> Se o texto começar com '''
        text = text.replace("```json", "").replace("```", "").strip() #---> Substituimos o trecho por um sem espaços ou '''

    start = text.find("{") #---> O começo do bloco será demarcado pelo {
    end = text.rfind("}") #---> Bem como o seu final }

    if start != -1 and end != -1: #---> Se o começo e o final foram diferentes disso
        text = text[start:end+1] #---> O texto será o trecho entre o começo e o final, incluindo as chaves

    return json.loads(text) #---> Por fim, tentamos carregar o texto como JSON e retornamos o resultado.
                            #---> Se o texto não for um JSON válido, isso levantará uma exceção que pode ser tratada pelo código chamador.

if __name__ == "__main__": #---> O bloco principal do código é executado quando o script é rodado diretamente.
    with open(INPUT_PATH, "r", encoding="utf-8") as f: #---> Com o open abrimos o arquivode entrada no modo de leitura (r)
        candidates = json.load(f) #---> E carregamos as regras candidadas dele

    prompt_template = PROMPT_PATH.read_text(encoding="utf-8") #---> O template do prompt virá da leitura do arquivo que indicamos o caminho no começo do código

    structured_rules = [] #---> Criamos uma lista vazia para receber as regras estruturadas

    for candidate in tqdm(candidates): #---> E para cada regra candidata na lista de regras candidatas, usando tqdm para mostrar o progresso
        prompt = prompt_template.replace("{{TRECHO}}", candidate["raw_text"]) #---> Substituímos no template do prompt a variável {{TRECHO}} pelo texto bruto da regra candidata atual, criando o prompt específico para essa regra

        try: #---> Tentamos
            llm_output = call_lm_studio(prompt) #---> Chamar a função para puxar o modelo e obter a resposta
            parsed = safe_json_loads(llm_output) #---> E carregar a resposta do modelo como JSON usando a função de carregamento seguro

            parsed["page_number"] = candidate["page_number"] #---> Adicionamos ao dicionário da regra estruturada o número da página de onde a regra foi extraída
            parsed["signal_word_detected"] = candidate["signal_word"] #---> Adicionamos a palavra de sinalização detectada, se houver
            parsed["raw_text"] = candidate["raw_text"] #---> E mantemos o texto bruto original para referência

            if parsed.get("eh_regra") is True: #---> Se o modelo indicou que o trecho é de fato uma regra (campo "eh_regra" é True), adicionamos o dicionário da regra estruturada à lista de regras estruturadas
                structured_rules.append(parsed) #---> Caso contrário, se o modelo indicou que não é uma regra, ou se houve algum problema na extração, simplesmente ignoramos essa regra candidata e seguimos para a próxima

        except Exception as e: #---> Caso aconteça algum erro durante a chamada do modelo ou no processamento da resposta
            print(f"Erro na página {candidate['page_number']}: {e}") #---> Exibimos a mensagem de erro junto com o número da página para facilitar a identificação do problema, e seguimos para a próxima regra candidata

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True) #---> Antes de salvar o arquivo, garantimos que o diretório de saída exista, criando-o se necessário

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f: #---> Com o open, abrimos o arquivo de saída no modo de escrita (w)
        json.dump(structured_rules, f, ensure_ascii=False, indent=2) #---> E salvamos a lista de regras estruturadas no formato JSON

    print(f"Regras estruturadas: {len(structured_rules)}") #---> Exibe a quantidade de regras estruturadas extraídas
    print(f"Arquivo salvo em: {OUTPUT_PATH}") #---> Exibe o caminho onde salvamos o arquivo