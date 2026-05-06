import json
from pathlib import Path
import chromadb
from sentence_transformers import SentenceTransformer

INPUT_PATH = Path("data/processed/manual_paginas_filtradas.json")
CHROMA_PATH = "rag/chroma_db"

COLLECTION_NAME = "manual_trator"

def chunk_text(text, chunk_size=1000, overlap=150):
    chunks = []

    start = 0
    while start < len(text):
        end = start + chunk_size
        chunk = text[start:end]

        if len(chunk.strip()) > 100:
            chunks.append(chunk.strip())

        start += chunk_size - overlap

    return chunks

if __name__ == "__main__":
    with open(INPUT_PATH, "r", encoding="utf-8") as f:
        pages = json.load(f)

    model = SentenceTransformer("sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")

    client = chromadb.PersistentClient(path=CHROMA_PATH)

    collection = client.get_or_create_collection(name=COLLECTION_NAME)

    ids = []
    documents = []
    metadatas = []
    embeddings = []

    for page in pages:
        chunks = chunk_text(page["text"])

        for idx, chunk in enumerate(chunks):
            doc_id = f"page_{page['page_number']}_chunk_{idx}"

            ids.append(doc_id)
            documents.append(chunk)
            metadatas.append({
                "page_number": page["page_number"]
            })
            embeddings.append(model.encode(chunk).tolist())

    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings
    )

    print(f"Base vetorial criada com {len(documents)} chunks.")