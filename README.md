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


#### 3.3 Sprint 4 — MVP funcional (entrega final)

A Sprint 4 é a última do projeto. Nela o Campo Seguro passou a funcionar de ponta a ponta: coleta automática, banco Oracle, modelos
preditivos, regras de negócio e uma aplicação web com login e perfis. A documentação completa está no relatório
final (`docs/Campo_Seguro_Entrega_Final.docx`) e a matriz do score em `docs/Score_risco_matriz.md`.

- **Arquitetura**: fluxo entrada → banco → modelos → saída, desenhado em `docs/arquitetura_solucao.png`.
- **Pipeline de dados** (`pipeline.py` + `servicos/`): clima horário e previsão (Open-Meteo), relevo das fazendas,
  focos de queimada (INPE), alertas hidrológicos e geológicos (CEMADEN), previsões diárias de risco e cálculo diário do
  score. Cada serviço é incremental, trata os próprios erros e grava o resultado em `CS_PIPELINE_LOGS`.
- **Modelo preditivo de queimadas** (`modelos/ml_alertas_queimadas_predicao.py`): regras interpretáveis por UF,
  aprendidas com o clima diário de cada fazenda e os focos do INPE, com validação temporal e métricas de eventos raros
  (POD, FAR, CSI, ETS, TSS, AUC-ROC). Prevê o risco de amanhã a +3 dias.
- **Modelos preditivos de chuva** (`modelos/ml_alertas_chuva_predicao.py`): risco hidrológico (inundação, enxurrada,
  alagamento e chuva intensa) e de deslizamento, a partir da chuva acumulada de 1, 3, 7 e 30 dias, da declividade da
  fazenda e dos desastres da Defesa Civil e alertas do CEMADEN. Nível pelo ganho sobre a chance normal (Crítico,
  Alto, Médio) e previsão de amanhã a +3 dias.
- **Central de Alertas**: mapa e lista por fazenda, com focos próximos, alertas do CEMADEN e o risco previsto de
  incêndio, hidrológico e de deslizamento, cada um com orientação preventiva.
- **Leitura de manuais (NLP)**: leitura automática das tabelas de manutenção, OCR (Tesseract) para manuais
  digitalizados e IA generativa (Claude Sonnet 5, API da Anthropic) com limite de gasto e revisão humana antes de
  gravar. As recomendações viram o cronograma de manutenção de cada máquina.
- **Comprovação das manutenções**: revisões por equipamento, envio de comprovantes pelo próprio cliente, leitura mensal
  do horímetro, validação da Sompo em quatro níveis e gamificação (níveis Ouro, Prata e Bronze).
- **Score Risk**: 8 itens (cliente, ambiental e operacional) somam de 0 a 100 pontos; Baixo abaixo de 30, Médio de 30
  a 50 e Alto acima de 50. Mostra ranking por cliente e fazenda, detalhe com recomendações preventivas, risco
  ambiental mês a mês por estado, indicadores da frota e a sugestão de ajuste do prêmio (-15% a +15%), com a decisão
  do agente de subscrição registrada. Cada cálculo grava as regras usadas e a medida de cada item.
- **Segurança e LGPD**: login com bcrypt, três perfis com menu por perfil (Administrador, Analista Sompo e Produtor),
  dados pessoais criptografados (Fernet), login pelo hash do e-mail, referência do usuário no lugar do nome nos
  registros e credenciais fora do Git.
- **Rastreabilidade**: `CS_LOG_ACOES` registra entradas, acessos, execuções, treinos, cálculos, decisões de prêmio,
  comprovantes e validações, com consulta na página Monitoramento.

**Correções em relação à Sprint 3**

- O modelo hidrológico da sprint anterior era treinado mas não era usado; foi substituído pelos modelos de chuva, que
  alimentam a Central de Alertas e o Score Risk.
- Os modelos antigos (Random Forest) e as consultas que só serviam a eles foram retirados do repositório; os novos
  modelos são interpretáveis e validados fora da amostra.
- O conteúdo do repositório e o plano de entregas deste README foram atualizados para o que existe de fato.

### 4. Plano de entregas (realizado)

| Sprint       | Período | Foco Principal | Entregas |
|:-------------| :--- | :--- |:---|
| **Sprint 1** | Março/Abril | **Conceituação** | Análise de mercado, definição de personas (João e Mariana) e arquitetura da solução. |
| **Sprint 2** | Maio/Junho | **Design & Modelo** | Dashboard Streamlit com Score Risk, classificação de alertas, modelo preditivo Prophet e integração Oracle. |
| **Sprint 3** | **29/06 a 17/07** | **Backend** | Pipeline de dados, integrações INPE e CEMADEN, reestruturação e tratamento do banco, controle de acesso, monitoramento e primeiros modelos preditivos. |
| **Sprint 4** | **Agosto/Setembro (entrega final)** | **MVP funcional** | Modelos de queimadas e de chuva, Central de Alertas, leitura de manuais (NLP, OCR e IA), cronograma e comprovação das manutenções, Score Risk de 0 a 100 com recomendações e ajuste do prêmio, segurança (LGPD) e registro de uso. |

### 5. Equipe 👥
* **Heitor Exposito de Sousa** - RM 566013
* **Marco Antônio Rodrigues Siqueira** - RM 569975
* **Nádia Nakamura Vieira** - RM 568906
* **Rafael Bassani** - RM 569930
* **Vinicius Xavier da Silva** - RM 572108

### 6. Conteúdo do repositório

- `app.py`: ponto de entrada da aplicação (`streamlit run app.py`); monta o menu só com as páginas do perfil do usuário.
- `inicio.py`: tela de login e atalhos das páginas do perfil.
- `components.py`: componentes comuns das páginas (tabela com quebra de texto, controle de acesso, registro de uso).
- `criptografia.py`: criptografia dos dados pessoais (Fernet) e hash de busca do e-mail.
- `pipeline.py`: orquestrador dos serviços da pasta `servicos/`; também pode ser executado pela página Monitoramento.
- `pages/`: uma página por funcionalidade (Cadastro, Alertas, Monitoramento, Score Risk, Programação e Comprovação).
- `requisitos/`: regras de negócio (score, recomendações, tendências, ajuste do prêmio, risco de chuva, cronograma,
  comprovação, usuários, auditoria, anexos, manuais e OCR).
- `servicos/`: serviços executados pelo pipeline (clima, relevo, INPE, CEMADEN, previsões de queimada e de chuva e
  score diário).
- `modelos/`: treino e validação dos modelos preditivos de queimadas e de chuva.
- `campo_seguro/nlp/`: leitores de manuais (automático, por tabela e com IA), normalização e exportação.
  `campo_seguro/legacy_v1/` guarda a primeira versão da leitura de manuais (não usada pelo app).
- `tratamento/`: cargas e tratamentos únicos (S2iD, histórico de focos, municípios, dados de teste, usuários de teste,
  senhas, chave de criptografia e migração dos dados pessoais).
- `sql/`: `estrutura_banco.sql` (estrutura completa do banco, pode rodar mais de uma vez) e verificação do ambiente.
- `config/` e `prompts/`: vocabulários da leitura de manuais e instruções da IA.
- `referencias/`: bases públicas em CSV usadas nas cargas (municípios e Defesa Civil).
- `docs/`: relatório final da entrega (`Campo_Seguro_Entrega_Final.docx`), diagrama de arquitetura
  (`arquitetura_solucao.png`) e matriz do Score de Risco (`Score_risco_matriz.md`).
- `assets/`: logo e o PDF de exemplo dos comprovantes da carga de teste.
- `requirements.txt`: dependências do app; `requirements-legado.txt`: só para `campo_seguro/legacy_v1`.
- `auth.py` (não vai para o Git): credenciais do Oracle e chave de criptografia.

### 7. Como executar

1. **Python 3.11** (versão em `.python-version`) e um ambiente virtual:
   ```bash
   python -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   python -m pip install -r requirements.txt
   ```
2. **Tesseract** (opcional, só para o OCR de manuais digitalizados): veja as instruções no início do
   `requirements.txt`.
3. **Credenciais**: crie o `auth.py` na pasta principal (ele está no `.gitignore`):
   ```python
   USER = "usuario_oracle"
   PASSWORD = "senha_oracle"
   DSN = "host:porta/servico"
   CHAVE_CRIPTOGRAFIA = "..."   # gere com: python tratamento/db_gerar_chave_criptografia.py
   ```
   Guarde uma cópia da chave: sem ela não há como ler os dados pessoais gravados.
4. **Banco**: rode `sql/estrutura_banco.sql` no Oracle (F5). Ele cria só o que falta e não apaga dados.
5. **Dados pessoais já gravados** (bancos antigos): `python tratamento/dados_pessoais_criptografar.py --simular` e,
   se estiver certo, sem o `--simular`.
6. **Usuários de teste** (opcional): `python tratamento/criar_operadores_teste.py` e
   `python tratamento/redefinir_senhas.py` (a senha é digitada no terminal).
7. **Carga de teste do score** (opcional): `python tratamento/score_dados.py --simular` e depois sem o `--simular`
   (`--continuar` retoma se a conexão cair).
8. **Aplicação**: `streamlit run app.py`. Na página Monitoramento, execute o pipeline e treine os modelos
   (queimadas e chuva); na página Score Risk, calcule o score.

O pipeline também pode rodar pelo terminal: `python pipeline.py`.

### 8. Vídeo Demonstrativo

#### Sprint 1 - Solução completa

[![Campo Seguro](https://img.youtube.com/vi/jFAV5QEnn1Q/hqdefault.jpg)](https://www.youtube.com/watch?v=jFAV5QEnn1Q)

#### Sprint 2 - Evolução com Dashboards e Machine Learning

https://youtu.be/3DVutjk-RAA

#### Sprint 3 - Estruturação do Backend

https://youtu.be/2iyq1lpY2o4

#### Sprint 4 - MVP funcional (entrega final)

https://youtu.be/7iSQ9Hryp2I

### 9. Documentação de Referência

- Relatório final da Sprint 4: [docs/Campo_Seguro_Entrega_Final.docx](./docs/Campo_Seguro_Entrega_Final.pdf)
- Diagrama de arquitetura: [docs/arquitetura_solucao.png](./docs/arquitetura_solucao.png)
- Matriz do Score de Risco: [docs/Score_risco_matriz.md](./docs/Score_risco_matriz.md)
- Documento original da solução: **[Sompo - Solução Campo Seguro.pdf](./docs/Sompo%20-%20Solu%C3%A7%C3%A3o%20Campo%20Seguro.pdf)**

### 10. Repositório GitHub

https://github.com/AI-FIAP-2026/enterprise-challenge

---
*Este projeto é uma iniciativa acadêmica em parceria com a Sompo Seguros.*
