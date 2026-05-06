import json
from pathlib import Path
import chromadb
from sentence_transformers import SentenceTransformer

INPUT_PATH = Path("data/processed/manual_paginas_filtradas.json") #---> Caminho para o arquivo JSON contendo as páginas filtradas do manual do trator
CHROMA_PATH = "rag/chroma_db" #---> Caminho para o diretório onde o banco de dados vetorial do ChromaDB será armazenado
COLLECTION_NAME = "manual_trator" #---> Nome da coleção no ChromaDB

def chunk_text(text, chunk_size=1000, overlap=150): #---> Função para dividir o texto em pedaços menores (chunks)
    chunks = [] #---> Lista para armazenar os chunks gerados

    start = 0 #---> Começando do início do texto em 0
    while start < len(text): #---> Enquanto o início for menor que o comprimento total do texto
        end = start + chunk_size #---> Calculamos o ponto final do chunk com base no tamanho definido
        chunk = text[start:end] #---> E extraímos o chunk do texto usando slicing

        if len(chunk.strip()) > 100: #---> Se o chunk tiver mais de 100 caracteres (após remover espaços em branco)
            chunks.append(chunk.strip()) #---> Adicionamos à lista de chunks

        start += chunk_size - overlap #---> Avançamos o início para o próximo chunk

    return chunks #---> Retorna a lista de chunks gerados a partir do texto original

if __name__ == "__main__": #---> Bloco principal do código, onde a execução começa
    with open(INPUT_PATH, "r", encoding="utf-8") as f: #---> Abrimos o arquivo JSON no formato de leitura
        pages = json.load(f) #---> Carregamos o conteúdo do arquivo JSON em uma variável chamada 'pages', que é uma lista de dicionários
        #---> Aqui cada dicionário vai representar uma página do manual do trator

    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")  #---> Carregamos um modelo transformer para transformar o texto em vetores
    #---> Esses vetores vão ser usados para criar o banco de dados no ChromaDB, permitindo que façamos buscas semânticas depois

    client = chromadb.PersistentClient(path=CHROMA_PATH) #---> Criamos um cliente no ChromaDB

    collection = client.get_or_create_collection(name=COLLECTION_NAME) #---> Criamos uma coleção no ChromaDB com o nome do manual do trator

    ids = [] #---> Lista para guardar os IDs dos documentos que vamos enviar para o banco de dados
    documents = [] #---> Lista para guardar os textos dos chunks que vamos enviar para o banco de dados
    metadatas = [] #---> Lista para guardar os metadados associados a cada chunk, como o número da página, de onde o chunk foi extraído e etc
    embeddings = [] #---> Lista para guardar os vetores criados a partir dos chunks de texto, que serão usados para buscas semânticas no banco de dados

    for page in pages: #---> Para cada página no nosso conjunto de páginas carregadas do JSON
        chunks = chunk_text(page["text"]) #---> Dividimos o texto da página em chunks usando a função 'chunk_text' que definimos antes

        for idx, chunk in enumerate(chunks): #---> Para cada chunk gerado e seu índice correspondente
            doc_id = f"page_{page['page_number']}_chunk_{idx}" #---> Criamos um ID único para cada chunk, combinando o número da página e o índice do chunk dentro daquela página

            ids.append(doc_id) #---> ID adiciona à lista de IDs
            documents.append(chunk) #---> O texto do chunk é adicionado à lista de documentos
            metadatas.append({ #---> O dicionário de metadados adiciona à lista de metadados
                "page_number": page["page_number"] #---> E adicionamos o número da página como metadado de cada chunk
            })
            embeddings.append(model.encode(chunk).tolist()) #---> Cada vetor de embedding é adicionado à lista de embeddings

    collection.add( #---> Finalmente, adicionamos os dados ao banco de dados do ChromaDB usando o método 'add' da coleção
        ids=ids, #---> Passamos a lista de IDs dos documentos
        documents=documents, #---> Passamos a lista de textos dos chunks
        metadatas=metadatas, #---> Passamos a lista de metadados associados a cada chunk
        embeddings=embeddings #---> E passamos a lista de vetores de embedding para cada chunk, o que permite a gente fazer buscas semânticas no banco depois
    )

    print(f"Base vetorial criada com {len(documents)} chunks.") #---> Se deu tudo certo, exibimos a mensagem indicando quantos chunks foram criados e adicionados no banco