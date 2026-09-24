# Campo Seguro — Memória do Projeto (módulo NLP + Score de Risco do Equipamento)

> Última atualização: 2026-09-24 · Mantenedor: Heitor · Uso: cole este arquivo no início de uma nova conversa com o Claude para restaurar o contexto. Atualize a seção 7 (Decisões) e 10 (Pendências) a cada assunto concluído.

---

## 1. Contexto

- **Challenge FIAP × Sompo Seguros** (tema agro): reduzir sinistros de maquinário agrícola (tratores, colhedoras).
- **Grupo**: Heitor Exposito de Sousa (NLP/RAG/score equipamento), Nádia Nakamura Vieira (régua de risco, formulário do cliente, dados clima/Defesa Civil, dashboard), Vinicius Xavier (alertas, front, arquitetura), Marco Antônio Siqueira, Rafael Bassani.
- **Repo**: `github.com/AI-FIAP-2026/enterprise-challenge` · branch do módulo NLP: `nlp_e_rag`.
- **Personas**: João Vale (segurado, produtor MT) e Mariana Torres (analista de subscrição Sompo).
- **Solução Campo Seguro** (Sprint 1): gamificação + datalogger IoT (temperatura, umidade, GPS, trepidação) + NLP de manuais + dashboards. Cláusula Sompo: sem indenização por má conservação que descumpra o fabricante → o NLP é a prova de conformidade.
- **Entrega FIAP corrente**: documento anexado é o brief do **Sprint 4 (final, consolidação)**. Planilha da Nádia está nomeada "S3". **Confirmar qual sprint está em curso.**

### Requisitos FIAP que afetam este módulo
- Sprint 2: score de risco **0–100** por equipamento/operação; **ML supervisionado**; validação com **matriz de confusão / erro médio**; fluxo sensores → modelo → dashboard/alertas.
- Sprint 4: código Python **modular** com funções padronizadas e **tratamento de exceções**; pipelines tratando faltantes/duplicidades; **logs** rastreando entradas, saídas e decisões (explicabilidade do score); controle de acesso; dashboards por equipamento/região/operação; alertas com **critérios explícitos**; README final; diagrama; vídeo ≤ 5 min.

---

## 2. Objetivos do módulo

1. Transformar manuais (PT/EN) em **orientações estruturadas** (verbo + componente + gatilho + fonte/página).
2. Gerar **score de risco do equipamento 0–100** (manual + utilização/telemetria), composto com o **score do cliente** (formulário da Nádia) e o **score ambiental** (região/fazenda).
3. Emitir **10–20 alertas por equipamento**, ranqueados, no formato "verbo + componente + prazo".
4. Rastreabilidade total: toda orientação aponta manual, código de edição e página.

---

## 3. Estado atual do pipeline `nlp_e_rag` (fatos medidos em 2026-09-24)

Pipeline: `01_extract_pdf` (PyMuPDF) → `02_filter_sections` (keywords) → `03_extract_candidate_rules` (regex + PERIGO/ATENÇÃO/CUIDADO/IMPORTANTE) → `04_structure_rules_with_llm` (Gemma-3-1B via LM Studio, temp 0.1, 700 tokens, 1 frase por chamada) → `05_export_rules` (CSV/XLSX) · RAG: `01_build_vector_db` (chunks 1.000/150, `paraphrase-multilingual-MiniLM-L12-v2`, ChromaDB coleção `manual_trator`) → `02_query_rag`.

Funil (só CH950): 428 páginas → 363 filtradas → 1.485 candidatas → **216 regras (14,5 %)**.

Defeitos medidos:
- **0/10 candidatas PERIGO sobreviveram**; `criticidade = critica` nunca emitida (prompt exigia).
- `sensor_possivel = none` em 210/216; `frequencia = none` em 176/216; só ~21 regras com intervalo em horas.
- Linhas de sumário (págs. 8–10, "95-F-2 Verificar … ......") viraram regras — filtro `índice` não pega "Conteúdo".
- Fragmentos por split de sentença ("de arrefecimento atingir 93 °C…"); `componente = Operação`, `acao = manutencao` em ~15 regras.
- RAG indexa páginas brutas, não regras; nome da coleção inconsistente com o conteúdo.
- Manual do trator 5060E **nunca processado**. Pipeline é mono-manual (paths hardcoded).
- Pontos fortes a preservar: código comentado, separação em etapas, `safe_json_loads`, rastreio de `page_number`/`signal_word`/`raw_text`.

---

## 4. Fontes de dados disponíveis

### Manuais (em `/mnt/project` e no repo)
| Arquivo | Conteúdo real | Status |
|---|---|---|
| John Deere CH950/CH960 Manual do Operador (`OMCXT31163` Ed. B3, PT) | 428 págs. Tabela geral de intervalos em 90-3; seções 95-A…95-L | processado (com defeitos) |
| John Deere 5060E/5070E/5080E/5078E/5090E Manual do Operador (`OMTR132548` Ed. E6, PT) | Tabela `Periodicidade | Sistema | Serviço` em 207-2 a 207-4, notas de rodapé a–l | **não processado** |
| CH950 Guia de Consulta Rápida (`QRGCXT31162` A1) | controles e cabine; pouca manutenção | baixo valor |
| "5060E Guia rápido" | é o **Termo de Garantia**: 8 revisões com horímetro; garantia cancelada por manutenção negligenciada, horímetro violado, lubrificante fora de spec | usar como regra de negócio |
| Mahindra Max Series / 6075 | **arquivo vazio (270 bytes)** | obter manual real |

### Intervalos de manutenção (parse determinístico das tabelas)
- **CH950** — 11 faixas: primeiras 100 h (95-A); 25 h ou diário (95-B); 50 h (95-C); 100 h (95-D); 250 h (95-E/F); 500 h (95-G); 1.000 h (95-H); 1.500 h (95-I); anual/3.000 h (95-J); 6.000 h (95-K); conforme necessário (95-L). ≈ 98 itens.
- **5060E** — 12+ faixas: amaciamento; primeiras 500 h; 10 h/diário; 50 h/semanal; 125 h; 200 h/mensal; 250 h; 400 h; 500 h; 750 h; anual; 1.500 h/2 anos; 2.000 h/2 anos; 6.000 h/6 anos; conforme necessário. ≈ 73 itens. Sistemas do manual: Motor · Combustível/Admissão/Exaustão/Arrefecimento · Trem de acionamento · Sistema Hidráulico · Rodas e Peças de Fixação · Direção e Freios · Plataforma do Operador · Sistema Elétrico.
- **Modificadores de intervalo** (5060E notas a–l e 205-x): lama/umidade (b); diesel com enxofre 5.000–10.000 ppm reduz troca de óleo (e, g, h, j); Cool-Gard II altera troca de arrefecedor (k, l); **altitude > 1.675 m → intervalo 50 %** (205-10); tração 4WD/2WD (c, f).

### Limiares de sensor (CH950 25-6; 5060E 220-3) → tipo `LIMIAR_OPERACIONAL_SENSOR`
| Grandeza | Normal | Alerta | Crítico |
|---|---|---|---|
| Temperatura do motor | 64–112 °C (operação típica 77–90 °C) | — | **113–116 °C** |
| Temperatura óleo hidráulico | −18–92 °C | — | **93–102 °C** |
| Carga do motor (potenciômetro) | 35–100 % | 101–110 % | **111–114 %** |
| Marcha lenta antes de carga / antes de desligar | 3–5 min | — | — |
| Combustível | — | 10 % (pisca + alarme) | — |
| DEF (Tier 4) | — | 10 % | 0 % → potência limitada |
| Pressão cortador de base | 0–250 bar | — | — |
| Termostato 5060E | abre 80–84 °C (nominal 82) | totalmente aberto 94 °C | — |
| Temperatura ambiente (combustível/DEF) | — | < 0 °C aditivo; DEF degrada > 30 °C | DEF congela −11 °C; < −10 °C e < −20 °C regras de diesel |

### Outras fontes
- **Defesa Civil S2iD** (`CS_Sp2_Dataset_s2id_Danos_Tratado.csv`): 61.751 registros, 2019–2025, colunas `uf, Municipio, Registro, Protocolo, COBRADE, Status, DM_17…22, PEPL_1…11, PEPR_Agricultura, PEPR_13…16`. Correlação Pearson clima × incidentes ≈ 0 (Nádia). Uso: **feature regional**, não rótulo de falha mecânica.
- **Open-Meteo** (temperatura, umidade, vento) — histórico e previsão por fazenda.
- **Oracle FIAP** (`oracle.fiap.com.br:1521/orcl`): tabelas `CS_CLIENTES`, `CS_FAZENDAS`, `CS_CLIMA`, `CS_CLIMA_EVENTOS`. 10 clientes e 10 fazendas fictícios (cana; SP/MS/GO). Credenciais devem ir para `.env`.
- **Régua da Nádia** (`FIAP_Sompo_S3_Analise_equipamentos - 2026.09.23.xlsx`, aba Score) — pesos somam 1,00, escala 1–3 (3 = maior risco):

| Categoria | Risco | Peso | Faixas 1 / 2 / 3 |
|---|---|---|---|
| Cliente | Valor segurado total | 0,05 | ≤ 500 mil / 500 mil–1,5 mi / > 1,5 mi |
| Cliente | Histórico do cliente | 0,25 | ≥ 80 % conformidade / 51–80 % / novo ou ≤ 50 % |
| Cliente | Sinistros na região (10 anos) | 0,025 | 0 / ≤ 2 / > 2 |
| Ambiental | Queimadas | 0,10 | a definir |
| Ambiental | Hidrológico | 0,10 | a definir |
| Ambiental | Eventos extremos | 0,025 | a definir |
| Operacional | Climático (danos ao equipamento) | 0,05 | a definir |
| Operacional | **Complexidade da manutenção** (qtd itens × periodicidade) | **0,25** | a definir — **alimentado pelo NLP** |
| Operacional | Procedimentos de manutenção (questionário) | 0,15 | a definir |

- **User Stories**: US-001 (score de exposição climática por município, 1–5 com **5 = baixo risco** — direção invertida em relação à régua); US-002 (alertas: incêndio crítico T > 30 °C ∧ UR < 30 % ∧ vento > 30 km/h; UR < 12 % alto; T > 40 °C alto; operacional T < 5 °C ou > 30 °C alto; vento > 60 km/h alto, > 75 km/h crítico; umidade do solo 70–80 % moderado, 81–89 % alto, ≥ 90 % crítico/parada).

---

## 5. Arquitetura alvo (direção aprovada; detalhes a validar)

```
Manual (PDF PT/EN)
  ├─[A] Parser determinístico das tabelas de intervalo ──► MANUTENCAO_PROGRAMADA (~80 % da NPL 1, sem LLM)
  ├─[B] Extrator determinístico de limiares (regex °C/%/bar/min) ─► LIMIAR_OPERACIONAL_SENSOR (4º tipo, IoT)
  └─[C] LLM (modelo ≥ 4B ou API) sobre páginas filtradas
        ├─ NPL 1 (prosa: condicionais altitude/enxofre/amaciamento)
        ├─ NPL 2 ─► ALERTA_TEMP_MINIMA / ALERTA_TEMP_MAXIMA (temperatura ambiente)
        └─ NPL 3 ─► RISCO_CHUVA_DESLIZE
                     │
                     ▼
        Tabela canônica CS_EQUIPAMENTOS_ORIENTACOES (+ colunas de rastreabilidade)
                     │
Equipamento + telemetria + clima (fazenda)
  ├─[D] Score do equipamento 0–100 (ver §6) ──► CS_EQUIPAMENTOS_SCORE
  ├─[E] Alertas: ranking criticidade × atraso × proximidade → top 10–20 → "verbo + componente + prazo"
  └─[F] ML supervisionado (FIAP): dataset simulado → classificador → matriz de confusão / F1 / AUC
                     │
              Dashboard (Streamlit) por equipamento / região / operação · logs de decisão
```

### Schema canônico (o da Nádia + 4 colunas)
`cod_fonte_manual, nome_documento_origem, data_publicacao_manual, link_manual, tipo_orientacao, subsistema, acao_tecnica, detalhamento_orientacao, metrica_gatilho, valor_gatilho, unidade_medida, fator_condicional, texto_bruto_original, idioma_origem` **+** `pagina_origem (int), criticidade (BAIXA|MEDIA|ALTA|CRITICA), sinal_seguranca (PERIGO|ATENCAO|CUIDADO|IMPORTANTE|null), sensor_iot (TEMP_MOTOR|TEMP_HIDRAULICO|CARGA_MOTOR|HORIMETRO|GPS|ACELEROMETRO|TEMP_AMBIENTE|UMIDADE|null)`.

Enums:
- `tipo_orientacao`: `MANUTENCAO_PROGRAMADA | ALERTA_TEMP_MINIMA | ALERTA_TEMP_MAXIMA | RISCO_CHUVA_DESLIZE | LIMIAR_OPERACIONAL_SENSOR`
- `metrica_gatilho`: `HORIMETRO | CALENDARIO | HODOMETRO | TEMPERATURA_AMBIENTE | TEMPERATURA_MOTOR | TEMPERATURA_HIDRAULICO | CARGA_MOTOR | VELOCIDADE_KMH | UMIDADE_SOLO | CONDICAO_CLIMATICA`
- `subsistema` (**unificar** — os 3 prompts da Nádia usam listas diferentes): proposta `MOTOR | COMBUSTIVEL | ARREFECIMENTO | TRANSMISSAO | TREM_ACIONAMENTO | HIDRAULICO | FREIOS | RODANTE_PNEUS | ELETRICO | CORTE_PROCESSAMENTO | CABINE_ESTRUTURA | CHASSI`
- `acao_tecnica` (verbos canônicos PT): `VERIFICAR | INSPECIONAR | LIMPAR | LUBRIFICAR | TROCAR | SUBSTITUIR | DRENAR | AJUSTAR | APERTAR | TESTAR | LAVAR | CALIBRAR | TRAVAR_PEDAIS_FREIO | ENGATAR_TDA | REDUZIR_VELOCIDADE | TRATAR_COMBUSTIVEL | AQUECER_MOTOR | PARAR_OPERACAO`

---

## 6. Regras de negócio (decididas ou propostas — marcadas)

- **[DECIDIDO]** Escala canônica **0–100**, maior = maior risco. Visualização opcional 1–5 por faixas de 20 (5 = maior risco). Cores: verde 0–20, amarelo 21–40, laranja 41–60, vermelho 61–80, crítico 81–100 (proposta).
- **[DECIDIDO]** Score final = composição de score do cliente (formulário Nádia, categoria *Cliente*) + score do equipamento (nosso, categoria *Operacional*) + score ambiental (fazenda/município, categoria *Ambiental*). Pesos da régua da Nádia valem para a composição; pesos internos do score do equipamento a definir.
- **[DECIDIDO]** Alertas por equipamento: mínimo 10, máximo 20.
- **[PROPOSTA]** Score do equipamento (0–100) = soma ponderada de:
  1. *Complexidade estática* (manual): Σ itens × peso da faixa, peso ∝ 1/intervalo_h (itens diários pesam mais); normalizar por tipo de equipamento.
  2. *Conformidade dinâmica*: itens vencidos vs horímetro/calendário, ponderados por criticidade; usa comprovantes/checklists do app.
  3. *Exposição operacional (telemetria)*: % do tempo em zona amarela/vermelha (temp. motor, hidráulico, carga), horas contínuas sem pausa, partida/desligamento sem marcha lenta.
  4. *Exposição climática do equipamento*: dias/ano em que gatilhos NPL 2/3 e US-002 disparam na fazenda (Open-Meteo).
- **[PROPOSTA]** Ranking de alertas: `criticidade × (1 + atraso_normalizado) × proximidade_do_vencimento`, com PERIGO/CRITICA sempre no topo; desempate por subsistema com maior histórico de sinistro.
- **[PROPOSTA]** ML supervisionado (requisito FIAP): rótulo binário simulado "falha/sinistro evitável em 12 meses"; features = os 4 componentes acima + variáveis da régua; baseline regressão logística → gradient boosting; métricas: matriz de confusão, precisão/recall/F1, ROC-AUC; a régua ponderada é o baseline explicável.
- Modificadores de intervalo do manual (enxofre ppm, altitude, lama, arrefecedor) reduzem o intervalo efetivo antes de calcular vencimento.
- Termo de garantia JD como regra: revisões nas horas previstas e horímetro íntegro são condições de conformidade.

---

## 7. Log de decisões

| Data | Decisão | Motivo |
|---|---|---|
| 2026-09-24 | Escala 0–100 (filtro 1–5 opcional) | Requisito FIAP; evita inversão da US-001 |
| 2026-09-24 | Dois scores (cliente + equipamento) compostos com ambiental | Divisão de trabalho Nádia/Heitor; espelha categorias da régua |
| 2026-09-24 | Adotar schema `CS_EQUIPAMENTOS_ORIENTACOES` da Nádia como canônico, + 4 colunas | É a tabela do banco; colunas extras garantem rastreabilidade e IoT |
| 2026-09-24 | Criar 4º tipo `LIMIAR_OPERACIONAL_SENSOR` | Prompts NPL 1/2 excluem temperatura interna e marcha lenta; sem ele o score por telemetria fica sem base |
| 2026-09-24 | NPL 1 majoritariamente determinística (parse de tabela); LLM só para prosa | 0 % de erro na tabela, rastreável, sem custo |
| 2026-09-24 | Abandonar Gemma-3-1B para estruturação | 0/10 PERIGO retidos; campos-chave vazios |
| 2026-09-24 | Não clonar repo / não instalar LM Studio agora | Arquivos já disponíveis; camada A/B não usa LLM |
| 2026-09-24 | Modelo Claude: seguir no atual; reavaliar se escopo mudar | Regra de governança da organização |

---

## 8. Convenções

- Python 3.11.9 (`.python-version`); pacotes em `requirements.txt` (pymupdf, pdfplumber, pandas, tqdm, spacy, nltk, requests, python-dotenv, sentence-transformers, chromadb, streamlit, openpyxl).
- Estrutura alvo do módulo: `campo_seguro/nlp/{extract,filter,parse_tables,thresholds,llm,export}.py`, `campo_seguro/score/`, `campo_seguro/alerts/`, `campo_seguro/ml/`, `data/{raw,processed}/`, `outputs/`, `prompts/`, `tests/`, `docs/`.
- Config por `.env` (`LM_STUDIO_URL`, modelo, credenciais Oracle). **Nunca** credenciais no código.
- Saídas em PT-BR, unidades SI, vírgula decimal na apresentação, ponto no armazenamento.
- Logs estruturados (JSON) com `equipamento_id, orientacao_id, score_componentes, timestamp` para explicabilidade.
- Nomes de arquivo sem espaços/acentos; um manual = um `cod_fonte_manual` (ex.: `JD-CH950-OMCXT31163-B3`).

---

## 9. Riscos

| Risco | Impacto | Mitigação |
|---|---|---|
| Manual Mahindra 6075 inexistente | Linha da planilha sem fonte | Obter PDF oficial (EN) — prompts da Nádia já são bilíngues |
| Modelo local fraco para NPL 2/3 | Extração ruim/inconsistente | Gemma-3-4B/12B se hardware permitir, ou API; validar em amostra de 30 páginas com gabarito manual |
| Dataset de falhas real inexistente | ML supervisionado só com dados simulados | Deixar explícito no relatório; simular com regras físicas coerentes (manual + telemetria) |
| Enums divergentes entre integrantes | Dashboard/SQL quebram | Fechar enum único (§5) antes de codar |
| Sobrecarga de alertas | UX ruim | Limite 10–20 + ranking |
| Prazo do Sprint 4 (consolidação) | Novas features competem com refatoração | Priorizar A, B, D, E, F; NPL 3 e Mahindra como "se der" |

---

## 10. Pendências e próximos passos (ordem)

1. **Confirmar sprint corrente** (S3 ou S4) e data de entrega.
2. Fechar com a Nádia: schema final (+4 colunas), enum de subsistema, metadados reais dos manuais, remoção dos placeholders `storage.minhaseguradora.com`.
3. **[A]** `parse_tables.py`: CH950 (sumário 95-A…L + seção 90-3) e 5060E (207-2 a 207-4 com notas a–l) → `CS_EQUIPAMENTOS_ORIENTACOES`.
4. **[B]** `thresholds.py`: limiares da §4 → `LIMIAR_OPERACIONAL_SENSOR`.
5. Decidir modelo para **[C]**; adaptar NPL 1/2/3 para entrada por página filtrada + saída em array; rodar; validar amostra.
6. **[D]** score do equipamento 0–100 (pesos internos a definir com a Nádia).
7. **[E]** ranking e geração dos 10–20 alertas.
8. **[F]** dataset simulado + classificador + métricas; relatório de validação.
9. Refatoração em pacote, `.env`, logging, exceções, testes; reindexar RAG sobre as orientações (não sobre páginas).
10. README/diagrama/vídeo; obter manual Mahindra 6075.

---

## 11. DO_NOT_DO

- Não usar Gemma-3-1B para estruturação de regras.
- Não commitar credenciais (Oracle FIAP) — já existem hardcoded em `CS_Sp2_Python_*.py`; remover.
- Não indexar páginas brutas no RAG como fonte de alertas; indexar orientações estruturadas.
- Não tratar linhas de sumário/"Conteúdo" como regras.
- Não usar a base S2iD como rótulo de falha mecânica.
- Não misturar direção de escala (US-001 5 = baixo risco) com a canônica.
- Não gerar mais de 20 nem menos de 10 alertas por equipamento.
- Não inventar metadados de manual (datas, links).

---

## 12. Governança da conversa com o Claude

- Regra da organização: menor modelo capaz. Decisão registrada: seguir no modelo atual para arquitetura/análise; para tarefas simples (README, e-mails, SQL simples) usar Haiku; para refatoração e código usar Sonnet.
- Compactar contexto ao fim de cada assunto; pedir respostas curtas quando possível.
- Ao concluir assuntos importantes: atualizar §7 e §10 deste arquivo.
