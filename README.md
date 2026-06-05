# Projeto Campo Seguro 🛡️

![Logo - Campo Seguro](./assets/logo.png)

O *Campo Seguro* é uma plataforma integrada de monitoramento e gestão de riscos para o agronegócio, desenvolvida para a **Sompo Seguros**. A solução utiliza sensores IoT, Inteligência Artificial (NLP) e dados meteorológicos para prevenir sinistros, reduzir prejuízos operacionais e estreitar a relação entre seguradora e produtor rural.

---

### 1. Introdução
O projeto aborda o aumento de 80% nos desastres naturais no Brasil e a oportunidade para desenvolver soluções que contribuam para amenizar seus impactos. A plataforma Campo Segura foca em mitigar o impacto da volatilidade climática no valor das apólices e na sustentabilidade do seguro agrícola.

### 2. Propósito
Focado na **redução da assimetria informativa**. O sistema atua proativamente na manutenção de ativos e na antecipação de eventos críticos como incêndios, inundações e geadas.

### 3. Tecnologia e arquitetura
* **Gamificação**: garantindo que o segurado tenha incentivos e recompensas em troca de dados.
* **NLP (Natural Language Processing):** processamento de manuais para planos de manutenção automáticos.
* **IoT Dataloggers**: sensores de GPS, umidade, temperatura e vibração para monitoramento de maquinário.
* **Data Analytics**: para transformar dados em ações e insights.

#### 3.1 Sprint 2 — Modelo Preditivo e Dashboard

A segunda sprint entregou um **dashboard interativo Streamlit** com dois módulos principais:

- **US-001 — Score Risk**: Motor de pontuação de risco por município, combinando frequência histórica e impacto econômico de eventos climáticos (dados do S2ID/Defesa Civil via Oracle). Exibe score de 1 (crítico) a 5 (baixo risco) com visualização de séries temporais.
- **US-002 — Alertas**: Classificação automática de alertas climáticos (Incêndio, Solo, Operacional) em severidades *Moderado/Alto/Crítico* com base em limiares de temperatura, umidade e vento. Inclui autenticação Google e dados dos últimos 30 dias.

Complementam a sprint:

- **Modelo Preditivo (Prophet)**: Modelo ML que projeta impactos futuros por município usando sazonalidade anual, auxiliando a subscrição de apólices.
- **Scripts de análise climática**: Pipelines ETL que geram datasets de 10 anos, atualizações horárias via API e análises de correlação.
- **Integração Oracle**: Consulta direta às tabelas `CS_CLIMA_EVENTOS`, `CS_CLIMA`, `CS_FAZENDAS` e `CS_CLIENTES`.
- **Arquitetura atualizada**: Diagrama da solução específico da Sprint 2 (`assets/campo-seguro-fluxo-de-dados.drawio`).

### 4. Plano de entregas 📅

| Sprint       | Período | Foco Principal | Entregas Detalhadas                                                                             |
|:-------------| :--- | :--- |:------------------------------------------------------------------------------------------------|
| **Sprint 1** | Março/Abril | **Conceituação** | Análise de mercado, definição de personas (João e Mariana) e arquitetura da solução.            |
| **Sprint 2** | Maio/Junho | **Design & Modelo** | Dashboard Streamlit com Score Risk, classificação de alertas em tempo real, modelo preditivo Prophet e integração Oracle. |
| **Sprint 3** | **29/06 a 17/07** | **Inteligência NLP** | Motor de NLP para leitura de manuais, geração de trilhas de manutenção e checklists auditáveis. |
| **Sprint 4** | **20/07 a 07/08** | **Integração IoT** | Datalogger e alertas de sobre eminências de riscos no uso do equipamento.                       |
| **Sprint 5** | **10/08 a 28/08** | **Alertas Real-time** | Integração com APIs do INPE/CPTEC e envio de push notifications de emergência.                  |
| **Sprint 6** | **31/08 a 18/09** | **BI & Data Lake** | Dashboards analíticos e integração de dados brutos ao Data Lake da Sompo.                       |


### 5. Equipe 👥
* **Heitor Exposito de Sousa** - RM 566013
* **Marco Antônio Rodrigues Siqueira** - RM 569975
* **Nádia Nakamura Vieira** - RM 568906
* **Rafael Bassani** - RM 569930
* **Vinicius Xavier da Silva** - RM 572108

### 6. Conteúdo do repositório
- `docs/Sompo - Solução Campo Seguro.pdf`: Documentação completa e visualmente formatada da solução (contexto, detalhes técnicos, arquitetura, estrutura de dados, personas, plano de entregas e referências)
- `docs/Campo Seguro - Sprint 1 - Apresentação.pdf`: Apresentação utilizada para criação do Vídeo Demonstrativo
- `docs/CS_Sp2_Documentacao.pdf`: Documentação completa da Sprint 2
- `src/streamlit_app.py`: Dashboard interativo com Score Risk e Classificação de Alertas
- `src/modelo_preditivo.py`: Modelo preditivo Prophet para projeção de riscos
- `src/analise_correlacao.py`: Análise de correlação entre variáveis climáticas
- `src/atualizacao_clima_1h.py`: Script de atualização horária de dados climáticos
- `src/geracao_dataset_dez_anos.py`: Geração de dataset sintético de 10 anos
- `src/query.sql`: Consultas SQL para o banco Oracle
- `assets/`: Imagens, diagramas e recursos visuais da documentação

### 7. Vídeo Demonstrativo 📺 

#### Sprint 1 - Solução completa

Assista à demonstração da solução e das interfaces:

[![Campo Seguro](https://img.youtube.com/vi/jFAV5QEnn1Q/hqdefault.jpg)](https://www.youtube.com/watch?v=jFAV5QEnn1Q)

#### Sprint 2 - Evolução com Dashboards e Machine Learning

Vídeo: https://youtu.be/3DVutjk-RAA

### 8. Documentação de Referência 📄

Para informações técnicas detalhadas, consulte o documento original:
- **[Sompo - Solução Campo Seguro.pdf](./docs/Sompo%20-%20Solu%C3%A7%C3%A3o%20Campo%20Seguro.pdf)**
---
*Este projeto é uma iniciativa acadêmica em parceria com a Sompo Seguros.*
