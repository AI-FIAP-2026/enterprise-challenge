# v1 (legado) — pipeline NLP + RAG via LLM

Este diretório é a **v1** do módulo NLP, preservada intocada (caminhos ainda relativos à raiz do repo: `data/`, `outputs/`, `prompts/`). Rodou só sobre o CH950 (`manual_colhedora.pdf`), gerou 216 regras via Gemma-3-1B/LM Studio, com defeitos documentados em `docs/memoria.md` §3 (PERIGO não sobrevive, campos vazios, sumário virando regra).

A partir da Fase 1 (ver `docs/prompt_claude_code.md`), a extração de manutenção programada e limiares de sensor passa a ser **determinística** (`campo_seguro/nlp/`, sem LLM). Esta v1 fica como referência e é migrada para o schema canônico com `origem_extracao = LLM_GEMMA_V1` — não é descartada.

---

# 📘 README.md (para GitHub)

Aqui está um README profissional, direto para colar:

````markdown
# 🚜 NLP + RAG para Extração de Regras de Manuais de Máquinas Agrícolas

Projeto desenvolvido em parceria com a **Sompo Seguros**, com o objetivo de transformar manuais técnicos (PDFs extensos) em **regras estruturadas e acionáveis**, permitindo a integração com sistemas de sensores e prevenção de sinistros.

---

## 🎯 Objetivo

Automatizar a leitura de manuais técnicos (470+ páginas) para:

- Extrair regras de manutenção, operação e segurança
- Estruturar essas regras em formato utilizável
- Permitir consultas inteligentes (RAG)
- Apoiar sistemas de telemetria na geração de alertas

---

## 🧠 Arquitetura do Projeto

```text
PDF (Manual)
   ↓
Extração de texto (PyMuPDF)
   ↓
Estruturação por página (JSON)
   ↓
Filtro por relevância (palavras-chave)
   ↓
Extração de regras candidatas (regex + NLP)
   ↓
Estruturação com LLM (Gemma via LM Studio)
   ↓
Base estruturada (CSV/JSON)
   ↓
Embeddings + ChromaDB
   ↓
Consulta via RAG
````

---

## 🛠️ Tecnologias Utilizadas

* Python
* PyMuPDF
* spaCy / regex
* LM Studio (Gemma)
* Sentence Transformers
* ChromaDB
* Pandas

---

## 📂 Estrutura do Projeto

```text
sompo-tratores-nlp/
│
├── data/
│   ├── raw/                # PDF original
│   └── processed/          # JSONs intermediários
│
├── outputs/                # Regras estruturadas
├── scripts/                # Pipeline NLP
├── rag/                    # Base vetorial e consultas
├── prompts/                # Prompts do LLM
└── requirements.txt
```

---

## ⚙️ Pipeline

### 1. Extração do PDF

```bash
python scripts/01_extract_pdf.py
```

---

### 2. Filtro de páginas relevantes

```bash
python scripts/02_filter_sections.py
```

---

### 3. Extração de regras candidatas

```bash
python scripts/03_extract_candidate_rules.py
```

---

### 4. Estruturação com LLM

(Requer LM Studio rodando)

```bash
python scripts/04_structure_rules_with_llm.py
```

---

### 5. Exportação

```bash
python scripts/05_export_rules.py
```

---

### 6. Criação da base vetorial (RAG)

```bash
python rag/01_build_vector_db.py
```

---

### 7. Consulta

```bash
python rag/02_query_rag.py
```

---

## 💡 Exemplos de perguntas

* O que o manual diz sobre marcha lenta?
* Quais regras existem para temperatura do motor?
* Quais manutenções devem ser feitas a cada 100 horas?
* Quais itens são críticos (PERIGO / IMPORTANTE)?

---

## 🔥 Diferencial do Projeto

Este projeto transforma:

```text
Manual técnico (texto não estruturado)
→
Regras estruturadas
→
Entradas para sistemas de sensores
→
Prevenção de sinistros
```

---

## 🚧 Próximos Passos

* Interface de validação (Streamlit)
* Extração de tabelas (Seção 95)
* Integração direta com sensores
* Classificação automática de criticidade
* Geração automática de alertas

---

## 👨‍💻 Autor

- Heitor Exposito de Sousa


