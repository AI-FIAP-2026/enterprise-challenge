import chromadb
from sentence_transformers import SentenceTransformer

CHROMA_PATH = "rag/chroma_db" #---> Caminho onde o banco de dados está
COLLECTION_NAME = "manual_trator" #---> Coleção onde os dados estão

def search(query, n_results=5): #---> Função para realizar a busca no ChromaDB
    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    #---> Carregamos o mesmo modelo transformer para garantir que as embeddings sejam compatíveis com as do banco de dados

    client = chromadb.PersistentClient(path=CHROMA_PATH) #---> Criamos um cliente para acessar o banco de dados do ChromaDB

    collection = client.get_collection(name=COLLECTION_NAME) #---> Acessamos a coleção onde os dados do manual do trator estão armazenados

    query_embedding = model.encode(query).tolist() #---> Transformamos a consulta do usuário em uma lista de vetores

    results = collection.query( #---> Os resultados da consulta são obtidos usando o query
        query_embeddings=[query_embedding], #---> Passamos o embedding da consulta como uma lista
        n_results=n_results #---> Definimos o número de resultados que queremos obter (padrão aqui é 5)
    )

    return results #---> E ai retorna os resultados da consulta

if __name__ == "__main__": #---> Bloco principal do código, onde a execução começa
    pergunta = input("Digite sua pergunta sobre o manual do trator: ") #---> O usuário entra com uma pergunta sobre o manual do trator
    resultados = search(pergunta) #---> Ai com o search a gente busca os resultados relacionados à pergunta do usuário no banco
    for i, doc in enumerate(resultados["documents"][0]): #---> para cada documento retornado nos resultados, a gente itera e exibe o texto do documento e os metadados dele
        page = resultados["metadatas"][0][i]["page_number"] #---> Pegamos o número da página do metadado para exibir junto com o resultado, o que ajuda muito a entender de onde aquele trecho do texto foi extraído no manual original
        
        print("\n"+"="*80) #---> Exibimos uma linha de separação
        print(f"Resultado {i+1}:") #---> O número do resultado
        print(f"Texto: {doc}") #---> O trecho do manual relacionado à pergunta do usuário
        print(f"Metadata: {resultados['metadatas'][0][i]}") #---> E os metadados associados a esse trecho, como o número da página e etc
        print("\n"+"="*80) #---> Outra linha de separação
        print(doc[:1500] + "...") #---> Exibimos os primeiros 1500 caracteres do texto do resultado para dar uma ideia do conteúdo
