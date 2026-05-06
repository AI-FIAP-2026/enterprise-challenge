import chromadb
from sentence_transformers import SentenceTransformer

CHROMA_PATH = "rag/chroma_db"
COLLECTION_NAME = "manual_trator"

def search(query, n_results=5):
    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    client = chromadb.PersistentClient(path=CHROMA_PATH)

    collection = client.get_collection(name=COLLECTION_NAME)

    query_embedding = model.encode(query).tolist()

    results = collection.query(
        query_embeddings=[query_embedding],
        n_results=n_results
    )

    return results

if __name__ == "__main__":
    pergunta = input("Digite sua pergunta sobre o manual do trator: ")
    resultados = search(pergunta)
    for i, doc in enumerate(resultados["documents"][0]):
        page = resultados["metadatas"][0][i]["page_number"]
        
        print("\n"+"="*80)
        print(f"Resultado {i+1}:")
        print(f"Texto: {doc}")
        print(f"Metadata: {resultados['metadatas'][0][i]}")
        print("\n"+"="*80)
        print(doc[:1500] + "...")