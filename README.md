# Projeto Campo Seguro

*Campo Seguro* é uma plataforma integrada de monitoramento e gestão de riscos para o agronegócio, desenvolvida para a **Sompo Seguros**. A solução abarcará sensores IoT, Inteligência Artificial (NLP) e dados meteorológicos para prevenir sinistros, reduzir prejuízos operacionais e estreitar a relação entre seguradora e produtor rural.


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

#### 3.2 Sprint 3 — Backend

Nessa sprint, alocamos nossos esforços para organizar a estrutura que irá fornecer o alicerce para o crescimento e evolução dos principais épicos da solução Campo Seguro. Nessa reestruturação, grande parte dos desenvolvimentos passados foram descontinuados.

- **Ampliação das integrações disponíveis**: inclusão de alertas sobre queimadas (INPE) e risco hidrológico (Cemaden) que são rodados periodicamente.
  
- **Reestruturação do banco de dados**: criamos novas tabelas para tornar a solução mais escalável: usuários, municípios do Brasil com codígo IBGE e informações geográficas (latitude e longitude) e logs do pipeline.
  
- **Tratamento da base de dados existente**: diante das dificuldades enfrentadas na sprint passada, alocamos bastante tempo para tratar os registros da Defesa Civil sobre eventos climáticos extremos, uniformizamos os dados (correção do nome dos municípios, inclusão de código do IBGE, entre oturos), excluímos erros de valores financeiros impossíveis, o que resultou em uma base de cerca de 70 mil registros sobre desastres nos últimos 10 anos.

- **Inclusão de dados massivos do clima**: foram incluídos mais de 60 milhões de registros sobre temperatura, velocidade do vento, precipitação e umidade para contextualizar a situação nos dias que antecederam o desastre. Para eventos de estiagem e incêndio integramos dados de 90 dias antes do incidente, de hora em hora, para todos os municípios impactados. Para incidentes hidrológicos buscamos informações 30 dias antes.

- **Organização da nomenclatura dos arquivos e tabelas**: foi dedicado tempo para reorganizar a nomenclatura dos arquivos e das tabelas, para que haja mais eficiência na estruturação do projeto daqui para drente, tendo em vista que se trata de uma solução robusta.

- **Controle de acesso**: incluímos solução criptografada de controle de acessos por perfil e também restringindo o acesso por cliente. A solução atualmente conta com 3 perfis: operador de equipamento (cliente), analista de subscrição (Sompo) e Administrador da solução Campo Seguro).

- **Pipeline**: estruturamos um orquestardor de serviços para facilitar e tornar mais disponíveis os dados. Com facilidade podemos rodar todos os serviços simultanemante, controlando erros e obtendo logs dos registros integrados.

- **Interface de monitoramento**: para facilitar o controle de tantos serviços, criamos uma interface de monitoramento onde o Administrador da solução poderá avaliar falhas nos serviços e também a qualidade dos modelos preditivos.

- **Engenharia de dados e otimização de memória**: para viabilizar o processamento de grandes volumes transacionais sem estourar os limites de memória RAM (*Out-Of-Memory*), aplicamos filtros na origem (SQL), filtragem temporal (ano de 2025), leitura em lotes (Chunksize), carregamento no Pandas é feito de forma fracionada (blocos de registros) e conversão dinâmica de tipos numéricos de 64 bits para estruturas otimizadas de menor consumo de RAM (Downcast).
  
- **Incremento do modelo preditivo para queimadas e incidentes hidrológicos**: a partir da maior disponibilidade de dados, eliminamos o modelo anterior que era bastante insatisfatório e com isso conseguimos criar modelos melhores para antecipar a ocorrência de eventos de desastres (como tempestades, chuvas intensas, estiagens e riscos hidrológicos/geológicos) com base em dados meteorológicos em tempo real e histórico transacional.
  
- **Algoritmo e Hiperparâmetros**: o modelo foi construído utilizando o Scikit-Learn com base em árvores de decisão ensacadas. Algoritmo `Random Forest Classifier`; número reduzido de árvores para garantir eficiência de processamento e baixo consumo de memória `n_estimators = 50`; limitação de profundidade para evitar overfitting `max_depth = 15`. Features de Entrada: condições climáticas (Temperatura, Umidade, Velocidade do Vento e Precipitação) e metadados geográficos e categóricos do evento (Código IBGE, UF, COBRADE, Grupo de Desastre).

- **Métricas de avaliação e desempenho**: o modelo é avaliado quantitativamente por meio de validações cruzadas e métricas robustas de classificação: acurácia geral (percentual total de acertos preditivos do modelo), AUC-ROC Score (avaliação da capacidade de discriminação entre classes de risco e normalidade); F1-Score e Recall (foco na sensibilidade para garantir a alta captura de eventos reais de desastres (*Recall* para a classe positiva).

- **Categorização dos alertas por níveis**: com o incremento dos serviços de alertas, conseguimos melhorar a classificação dos riscos e enviar alertas orientativos mais claros.

- **Síntese e oportunidades de melhoria**: nesse sprint focamos nossos esforços em organizar o projeto, a partir da estruturação do backend. A partir dos resultados já disponíveis, ampliaremos nossa produtividade para avançar mais profundamente nas partes faltantes do projeto e também melhorar nosso modelo preditivo.


### 4. Plano de entregas 📅

| Sprint       | Período | Foco Principal | Entregas Detalhadas                                                                             |
|:-------------| :--- | :--- |:------------------------------------------------------------------------------------------------|
| **Sprint 1** | Março/Abril | **Conceituação** | Análise de mercado, definição de personas (João e Mariana) e arquitetura da solução.            |
| **Sprint 2** | Maio/Junho | **Design & Modelo** | Dashboard Streamlit com Score Risk, classificação de alertas em tempo real, modelo preditivo Prophet e integração Oracle. |
| **Sprint 3** | **29/06 a 17/07** | **Alertas Real-time** | Integração com APIs do INPE/CPTEC e envio de push notifications de emergência; alertas de sobre eminências de riscos no uso do equipamento.  | 
| **Sprint 4** | **29/06 a 17/07** | **Inteligência NLP** | Motor de NLP para leitura de manuais, geração de trilhas de manutenção e checklists auditáveis.|
| **Sprint 5** | **31/08 a 18/09** | **BI & Data Lake** | Dashboards analíticos e integração de dados brutos ao Data Lake da Sompo. |

### 5. Equipe 👥
* **Heitor Exposito de Sousa** - RM 566013
* **Marco Antônio Rodrigues Siqueira** - RM 569975
* **Nádia Nakamura Vieira** - RM 568906
* **Rafael Bassani** - RM 569930
* **Vinicius Xavier da Silva** - RM 572108

### 6. Conteúdo do repositório

- `pages/`: contém repositórios das páginas criadas para visualização da solução pelos diferentes perfis.
- `servicos/`: códigos utilizados para coletar dados por APIs e ingerir no banco de dados. Todos os códigos incluídos nessa pasta são automaticamente lidos pelo Pipeline.
- `modelos/`: repositório com os modelos preditos e aqruivos de 
- `sql/`: consultas SQL para otimizar os modelos preditivos
- `referencias/`: pasta contendo os arquivos em CSV utilizados para coletar e tratar dados de repositórios que ainda não dispões de APIs, como os da Defesa Civil.
- `tratamento/`:
- `assets/`: imagens, diagramas e recursos visuais da documentação
- `app.py/`: ponto de entrada (entrypoint) da aplicação web do Campo Seguro. Gerencia o ciclo de vida da interface gráfica utilizando Streamlit, controlando desde a autenticação dos usuários até o roteamento para as diferentes visões do sistema com base nos perfis de acesso
- `components.py`: componentes padronizados das páginas, como header e footer
- `pipeline.py`: orquestrador dos serviços internos e externos que são consumidos pela solução. É possível ativar o pipeline a partir da página de Monitoramento do sistema
- `auth.py`: credenciais do banco Oracle que não foram disponibilizadas por boa prática de segurança
- `docs/Sompo - Solução Campo Seguro.pdf`: Documentação completa e visualmente formatada da solução (contexto, detalhes técnicos, arquitetura, estrutura de dados, personas, plano de entregas e referências)
- `docs/Campo Seguro - Sprint 1 - Apresentação.pdf`: Apresentação utilizada para criação do Vídeo Demonstrativo
- `docs/CS_Sp2_Documentacao.pdf`: Documentação completa da Sprint 2
- [LEGADO]`src/streamlit_app.py`: Dashboard interativo com Score Risk e Classificação de Alertas
- [LEGADO]`src/modelo_preditivo.py`: Modelo preditivo Prophet para projeção de riscos
- [LEGADO]`src/analise_correlacao.py`: Análise de correlação entre variáveis climáticas
- [LEGADO]`src/atualizacao_clima_1h.py`: Script de atualização horária de dados climáticos
- [LEGADO]`src/geracao_dataset_dez_anos.py`: Geração de dataset sintético de 10 anos
- [LEGADO]`src/query.sql`: Consultas SQL para o banco Oracle

### 7. Vídeo Demonstrativo

#### Sprint 1 - Solução completa

[![Campo Seguro](https://img.youtube.com/vi/jFAV5QEnn1Q/hqdefault.jpg)](https://www.youtube.com/watch?v=jFAV5QEnn1Q)

#### Sprint 2 - Evolução com Dashboards e Machine Learning

https://youtu.be/3DVutjk-RAA

#### Sprint 3 - Estruturação do Backend

https://youtu.be/2iyq1lpY2o4

### 8. Documentação de Referência

Para informações técnicas detalhadas, consulte o documento original:
- **[Sompo - Solução Campo Seguro.pdf](./docs/Sompo%20-%20Solu%C3%A7%C3%A3o%20Campo%20Seguro.pdf)**

### 9. Repositório GitHub

https://github.com/AI-FIAP-2026/enterprise-challenge/tree/campo-seguro-v2

---
*Este projeto é uma iniciativa acadêmica em parceria com a Sompo Seguros.*
