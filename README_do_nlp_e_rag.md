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

Projeto acadêmico com aplicação real em seguros agrícolas (Sompo Seguros).

````

---

# 🎤 Texto para apresentação (reunião)

Aqui está um discurso pronto — direto, claro e com impacto:

---

> Pessoal, eu fiquei responsável por resolver um dos principais gargalos do projeto: transformar o manual técnico em algo que o sistema consiga usar.
>
> O manual tem mais de 400 páginas, então não faz sentido ninguém ler isso manualmente toda vez. O que eu desenvolvi foi um pipeline automatizado que faz essa leitura por nós.
>
> Primeiro, eu extraio o texto do PDF e organizo por páginas.  
> Depois, faço um filtro inteligente para manter apenas as partes relevantes, como manutenção, segurança e operação.
>
> Em seguida, uso NLP para identificar possíveis regras dentro do texto, como "verificar", "inspecionar", "não operar", "a cada 100 horas", etc.
>
> A partir daí, entra um LLM local, que transforma esses trechos em regras estruturadas — com ação, componente, frequência, criticidade e até sugestão de sensor.
>
> No final, isso vira uma base organizada em CSV/JSON que vocês podem usar diretamente para definir métricas e alertas.
>
> Além disso, eu construí uma base vetorial com ChromaDB, que permite fazer perguntas sobre o manual, tipo:
>
> "O que o manual diz sobre marcha lenta?"
>
> E o sistema responde trazendo exatamente os trechos relevantes.
>
> Ou seja, a gente deixou de ter um PDF de 400 páginas e passou a ter:
>
> - Um banco de regras acionáveis  
> - E um sistema inteligente de consulta  
>
> Isso conecta diretamente com o que vocês estão fazendo com sensores, porque agora dá pra transformar essas regras em alertas em tempo real.
>
> Basicamente, a gente está convertendo conhecimento técnico em prevenção de sinistro.

---

## 🚀 Observação final (importante)

Seu RAG já está funcionando — e o output que você mandou confirma isso perfeitamente. :contentReference[oaicite:0]{index=0}

O próximo nível (se quiser evoluir depois) é:

```text
RAG + LLM → respostas explicadas, não só trechos
````