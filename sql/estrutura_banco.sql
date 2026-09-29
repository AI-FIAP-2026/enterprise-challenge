-- =====================================================================
-- CAMPO SEGURO - ESTRUTURA DO BANCO (Oracle) - consolidada em 29/09/2026
-- ---------------------------------------------------------------------
-- Um único script para montar o banco do zero ou completar um banco já existente. Rodar com F5.
-- Pode rodar mais de uma vez: cria só o que faltar (tabela, coluna, restrição ou índice) e não apaga dados.
--
-- Parte 1 - Tabelas, restrições e índices (espelho do banco em uso, extraído com DBMS_METADATA)
-- Parte 2 - Dados de referência: modelos de equipamentos e vínculo manual <-> modelo
-- As recomendações dos manuais (CS_EQUIPAMENTOS_ORIENTACOES) são geradas pelo NLP e ficam em
-- outputs/orientacoes/CS_EQUIPAMENTOS_ORIENTACOES.sql (rodar depois deste script, se a tabela estiver vazia).
--
-- Grupos de tabelas
--   Clientes e acesso ....... CS_CLIENTES, CS_USUARIOS, CS_FAZENDAS, CS_MUNICIPIOS
--   Clima e alertas ......... CS_FAZENDAS_CLIMA, CS_ALERTAS, CS_ALERTAS_PREDICAO, CS_EVENTOS, CS_EVENTOS_CLIMA,
--                             CS_EVENTOS_PREDICAO, CS_PIPELINE_LOGS
--   Equipamentos ............ CS_EQUIPAMENTOS_MODELOS, CS_EQUIPAMENTOS_MANUAIS, CS_EQUIPAMENTOS_ORIENTACOES,
--                             CS_EQUIPAMENTOS_SEGURADOS, CS_LEITURAS_MEDIDOR, CS_MANUTENCOES_REALIZADAS,
--                             CS_ANEXOS, CS_SINISTROS
--   Score e maturidade ...... CS_SCORE_MANUTENCAO_PROCEDIMENTOS, CS_SCORE_GESTAO
--   Leitura de manuais (NLP)  CS_NLP_REVISOES, CS_NLP_USO_IA
--   Auditoria ............... CS_LOG_ACOES
-- =====================================================================

SET SERVEROUTPUT ON

-- =====================================================================
-- PARTE 1 - TABELAS, RESTRIÇÕES E ÍNDICES
-- =====================================================================
DECLARE
    PROCEDURE tabela(p_nome VARCHAR2, p_ddl VARCHAR2) IS
        v_existe NUMBER;
    BEGIN
        SELECT COUNT(*) INTO v_existe FROM USER_TABLES WHERE TABLE_NAME = p_nome;
        IF v_existe = 0 THEN
            EXECUTE IMMEDIATE p_ddl;
            DBMS_OUTPUT.PUT_LINE('Tabela criada: ' || p_nome);
        END IF;
    END;

    -- Colunas acrescentadas depois da criação original (para completar bancos mais antigos)
    PROCEDURE coluna(p_tabela VARCHAR2, p_coluna VARCHAR2, p_tipo VARCHAR2) IS
        v_existe NUMBER;
    BEGIN
        SELECT COUNT(*) INTO v_existe FROM USER_TAB_COLUMNS WHERE TABLE_NAME = p_tabela AND COLUMN_NAME = p_coluna;
        IF v_existe = 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE ' || p_tabela || ' ADD (' || p_coluna || ' ' || p_tipo || ')';
            DBMS_OUTPUT.PUT_LINE('Coluna criada: ' || p_tabela || '.' || p_coluna);
        END IF;
    END;

    PROCEDURE restricao(p_tabela VARCHAR2, p_nome VARCHAR2, p_definicao VARCHAR2) IS
        v_existe NUMBER;
    BEGIN
        SELECT COUNT(*) INTO v_existe FROM USER_CONSTRAINTS WHERE TABLE_NAME = p_tabela AND CONSTRAINT_NAME = p_nome;
        IF v_existe = 0 THEN
            EXECUTE IMMEDIATE 'ALTER TABLE ' || p_tabela || ' ADD CONSTRAINT ' || p_nome || ' ' || p_definicao;
            DBMS_OUTPUT.PUT_LINE('Restrição criada: ' || p_nome);
        END IF;
    END;

    -- Aumenta uma coluna texto (VARCHAR2) que ficou pequena (ex.: dado pessoal que passou a ser gravado criptografado)
    PROCEDURE ampliar(p_tabela VARCHAR2, p_coluna VARCHAR2, p_tamanho NUMBER) IS
        v_atual NUMBER;
    BEGIN
        SELECT MAX(CHAR_LENGTH) INTO v_atual FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = p_tabela AND COLUMN_NAME = p_coluna AND DATA_TYPE = 'VARCHAR2';
        IF v_atual IS NOT NULL AND v_atual < p_tamanho THEN
            EXECUTE IMMEDIATE 'ALTER TABLE ' || p_tabela || ' MODIFY (' || p_coluna || ' VARCHAR2(' || p_tamanho || '))';
            DBMS_OUTPUT.PUT_LINE('Coluna ampliada: ' || p_tabela || '.' || p_coluna || ' -> ' || p_tamanho);
        END IF;
    END;

    -- Aumenta as casas decimais de uma coluna NUMBER (ex.: chance de eventos raros, 0,0123%). Só aumenta.
    PROCEDURE ampliar_numero(p_tabela VARCHAR2, p_coluna VARCHAR2, p_precisao NUMBER, p_escala NUMBER) IS
        v_escala NUMBER;
    BEGIN
        SELECT MAX(NVL(DATA_SCALE, 0)) INTO v_escala FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = p_tabela AND COLUMN_NAME = p_coluna AND DATA_TYPE = 'NUMBER';
        IF v_escala IS NOT NULL AND v_escala < p_escala THEN
            EXECUTE IMMEDIATE 'ALTER TABLE ' || p_tabela || ' MODIFY (' || p_coluna || ' NUMBER(' || p_precisao
                              || ',' || p_escala || '))';
            DBMS_OUTPUT.PUT_LINE('Coluna ampliada: ' || p_tabela || '.' || p_coluna);
        END IF;
    END;

    PROCEDURE indice(p_nome VARCHAR2, p_ddl VARCHAR2) IS
        v_existe NUMBER;
    BEGIN
        SELECT COUNT(*) INTO v_existe FROM USER_INDEXES WHERE INDEX_NAME = p_nome;
        IF v_existe = 0 THEN
            EXECUTE IMMEDIATE p_ddl;
            DBMS_OUTPUT.PUT_LINE('Índice criado: ' || p_nome);
        END IF;
    END;
BEGIN
    -- ---------------------------------------------------------------- Clientes e acesso
    tabela('CS_CLIENTES', q'[
        CREATE TABLE CS_CLIENTES (
            ID                    NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            RAZAO_SOCIAL          VARCHAR2(150) NOT NULL,
            CNPJ                  VARCHAR2(20)  NOT NULL UNIQUE,
            FIDELIDADE            VARCHAR2(50),
            NOME_FANTASIA         VARCHAR2(150),
            INSCRICAO_ESTADUAL    VARCHAR2(30),
            ATIVIDADE_PRINCIPAL   VARCHAR2(150),
            TELEFONE              VARCHAR2(200),     -- criptografado (criptografia.py)
            EMAIL                 VARCHAR2(400),     -- criptografado
            ENDERECO              VARCHAR2(200),
            MUNICIPIO             VARCHAR2(100),
            UF                    VARCHAR2(2),
            CEP                   VARCHAR2(9),
            RESPONSAVEL_NOME      VARCHAR2(600),     -- criptografado
            RESPONSAVEL_CPF       VARCHAR2(200),     -- criptografado
            RESPONSAVEL_CARGO     VARCHAR2(100),
            RESPONSAVEL_TELEFONE  VARCHAR2(200),     -- criptografado
            RESPONSAVEL_EMAIL     VARCHAR2(400),     -- criptografado
            DATA_ATUALIZACAO      TIMESTAMP
        )]');
    coluna('CS_CLIENTES', 'NOME_FANTASIA', 'VARCHAR2(150)');
    coluna('CS_CLIENTES', 'INSCRICAO_ESTADUAL', 'VARCHAR2(30)');
    coluna('CS_CLIENTES', 'ATIVIDADE_PRINCIPAL', 'VARCHAR2(150)');
    coluna('CS_CLIENTES', 'TELEFONE', 'VARCHAR2(200)');
    coluna('CS_CLIENTES', 'EMAIL', 'VARCHAR2(150)');
    coluna('CS_CLIENTES', 'ENDERECO', 'VARCHAR2(200)');
    coluna('CS_CLIENTES', 'MUNICIPIO', 'VARCHAR2(100)');
    coluna('CS_CLIENTES', 'UF', 'VARCHAR2(2)');
    coluna('CS_CLIENTES', 'CEP', 'VARCHAR2(9)');
    coluna('CS_CLIENTES', 'RESPONSAVEL_NOME', 'VARCHAR2(600)');
    coluna('CS_CLIENTES', 'RESPONSAVEL_CPF', 'VARCHAR2(200)');
    coluna('CS_CLIENTES', 'RESPONSAVEL_CARGO', 'VARCHAR2(100)');
    coluna('CS_CLIENTES', 'RESPONSAVEL_TELEFONE', 'VARCHAR2(200)');
    coluna('CS_CLIENTES', 'RESPONSAVEL_EMAIL', 'VARCHAR2(150)');
    coluna('CS_CLIENTES', 'DATA_ATUALIZACAO', 'TIMESTAMP');
    -- dados pessoais criptografados ocupam mais espaço (tratamento/dados_pessoais_criptografar.py)
    ampliar('CS_CLIENTES', 'EMAIL', 400);
    ampliar('CS_CLIENTES', 'RESPONSAVEL_EMAIL', 400);

    tabela('CS_USUARIOS', q'[
        CREATE TABLE CS_USUARIOS (
            ID            NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            EMAIL         VARCHAR2(400) NOT NULL,    -- criptografado (criptografia.py)
            EMAIL_HASH    VARCHAR2(64),              -- HMAC-SHA256 do e-mail: busca no login sem abrir o e-mail
            SENHA         VARCHAR2(100) NOT NULL,    -- hash bcrypt (irreversível)
            NOME          VARCHAR2(400) NOT NULL,    -- criptografado
            CELULAR       VARCHAR2(200),             -- criptografado
            TIPO_ACESSO   VARCHAR2(20) CHECK (TIPO_ACESSO IN ('Empresa', 'Cliente')),
            ROLE          VARCHAR2(50)  NOT NULL,
            CLIENTE_CNPJ  VARCHAR2(20)
        )]');
    coluna('CS_USUARIOS', 'EMAIL_HASH', 'VARCHAR2(64)');
    ampliar('CS_USUARIOS', 'EMAIL', 400);
    ampliar('CS_USUARIOS', 'NOME', 400);
    ampliar('CS_USUARIOS', 'CELULAR', 200);
    indice('IX_CS_USUARIOS_EMAIL_HASH', 'CREATE INDEX IX_CS_USUARIOS_EMAIL_HASH ON CS_USUARIOS (EMAIL_HASH)');

    tabela('CS_MUNICIPIOS', q'[
        CREATE TABLE CS_MUNICIPIOS (
            MUNICIPIO_IBGE  NUMBER,
            MUNICIPIO       VARCHAR2(255),
            LATITUDE        NUMBER,
            LONGITUDE       NUMBER,
            UF              VARCHAR2(2)
        )]');

    tabela('CS_FAZENDAS', q'[
        CREATE TABLE CS_FAZENDAS (
            ID                  NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            NOME_FAZENDA        VARCHAR2(100) NOT NULL,
            ID_CLIENTE          NUMBER        NOT NULL,
            CULTURA             VARCHAR2(50),
            MUNICIPIO           VARCHAR2(100),
            ESTADO              VARCHAR2(2),
            LATITUDE            NUMBER(10,8),
            LONGITUDE           NUMBER(11,8),
            TAMANHO             NUMBER(15,2),
            NUM_CAR             VARCHAR2(100),
            CODIGO_IBGE         VARCHAR2(10),
            ALTITUDE_M          NUMBER(7,1),       -- relevo (servicos/fazendas_relevo.py)
            DECLIVIDADE_PCT     NUMBER(6,2),
            CLASSE_RELEVO       VARCHAR2(30),
            ORIENTACAO_ENCOSTA  VARCHAR2(10),
            CONSTRAINT FK_FAZENDAS_CLIENTE FOREIGN KEY (ID_CLIENTE) REFERENCES CS_CLIENTES (ID)
        )]');
    coluna('CS_FAZENDAS', 'CODIGO_IBGE', 'VARCHAR2(10)');
    coluna('CS_FAZENDAS', 'ALTITUDE_M', 'NUMBER(7,1)');
    coluna('CS_FAZENDAS', 'DECLIVIDADE_PCT', 'NUMBER(6,2)');
    coluna('CS_FAZENDAS', 'CLASSE_RELEVO', 'VARCHAR2(30)');
    coluna('CS_FAZENDAS', 'ORIENTACAO_ENCOSTA', 'VARCHAR2(10)');

    -- ---------------------------------------------------------------- Clima e alertas
    tabela('CS_FAZENDAS_CLIMA', q'[
        CREATE TABLE CS_FAZENDAS_CLIMA (
            ID                NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            ID_FAZENDA        NUMBER NOT NULL,
            TEMPERATURA       NUMBER(5,2),
            UMIDADE           NUMBER(5,2),
            VELOCIDADE_VENTO  NUMBER(5,2),
            DATA_HORA         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRECIPITACAO      NUMBER(5,2),
            DATA_HORA_BR      TIMESTAMP GENERATED ALWAYS AS (DATA_HORA - INTERVAL '3' HOUR) VIRTUAL,
            CONSTRAINT FK_CLIMA_FAZENDA FOREIGN KEY (ID_FAZENDA) REFERENCES CS_FAZENDAS (ID)
        )]');
    -- clima por fazenda e período (Score de Risco: chuva acumulada do item Climático; página de Monitoramento)
    indice('IX_CS_FAZENDAS_CLIMA_FAZ', 'CREATE INDEX IX_CS_FAZENDAS_CLIMA_FAZ ON CS_FAZENDAS_CLIMA (ID_FAZENDA, DATA_HORA)');

    tabela('CS_ALERTAS', q'[
        CREATE TABLE CS_ALERTAS (
            ID               NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            TIPO_ALERTA      VARCHAR2(100) NOT NULL,
            ORIGEM_ALERTA    VARCHAR2(100) NOT NULL,     -- INPE, CEMADEN, IA Preditiva...
            DATA_HORA        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            CATEGORIA_RISCO  VARCHAR2(50)  NOT NULL,
            LATITUDE         NUMBER(10,8)  NOT NULL,
            LONGITUDE        NUMBER(11,8)  NOT NULL,
            ORIENTACAO       VARCHAR2(500),
            DETALHAMENTO_1   VARCHAR2(300),
            DETALHAMENTO_2   VARCHAR2(300),
            CHAVE_FOCO       VARCHAR2(80)                -- SATELITE|AAAAMMDDHH24MI|LAT|LON (focos do INPE)
        )]');
    coluna('CS_ALERTAS', 'CHAVE_FOCO', 'VARCHAR2(80)');
    -- o mesmo foco vem nos arquivos de 10 minutos, diários e mensais do INPE: a chave evita gravar duas vezes
    indice('UX_CS_ALERTAS_CHAVE_FOCO', 'CREATE UNIQUE INDEX UX_CS_ALERTAS_CHAVE_FOCO ON CS_ALERTAS (CHAVE_FOCO)');
    -- a Central de Alertas sempre filtra por período
    indice('IX_CS_ALERTAS_DATA',
           'CREATE INDEX IX_CS_ALERTAS_DATA ON CS_ALERTAS (DATA_HORA, ORIGEM_ALERTA, CATEGORIA_RISCO)');

    tabela('CS_ALERTAS_PREDICAO', q'[
        CREATE TABLE CS_ALERTAS_PREDICAO (
            ID               NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            ID_FAZENDA       NUMBER        NOT NULL,
            TIPO_RISCO       VARCHAR2(30)  NOT NULL,     -- Queimada (depois: Equipamento, Hidrológico)
            DATA_REFERENCIA  DATE          NOT NULL,
            HORIZONTE_DIAS   NUMBER(2),
            VALOR            NUMBER(9,4),                -- chance do evento previsto, em % (eventos raros: 0,0123)
            NIVEL            VARCHAR2(30),
            ID_REGRA         NUMBER,
            MOTIVOS          VARCHAR2(1000),
            TEMPERATURA_MAX  NUMBER(5,1),
            UMIDADE_MIN      NUMBER(5,1),
            VENTO_MAX        NUMBER(5,1),
            CHUVA_7D         NUMBER(7,1),
            DIAS_SEM_CHUVA   NUMBER(4),
            DATA_CALCULO     TIMESTAMP DEFAULT SYSTIMESTAMP,
            CONSTRAINT UX_CS_ALERTAS_PREDICAO UNIQUE (ID_FAZENDA, TIPO_RISCO, DATA_REFERENCIA)
        )]');

    tabela('CS_EVENTOS', q'[
        CREATE TABLE CS_EVENTOS (
            ID_EVENTO                 NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            CODIGO_IBGE               NUMBER(10),
            MUNICIPIO                 VARCHAR2(150),
            UF                        VARCHAR2(2),
            COBRADE                   VARCHAR2(255),
            DATA_OCORRENCIA           DATE,
            INICIO_ANALISE_CLIMATICA  DATE,
            FIM_ANALISE_CLIMATICA     DATE,
            DIAS_LOOKBACK             NUMBER(5),
            IMPACTO_FINANCEIRO_TOTAL  NUMBER(18,2),
            IMPACTO_PEPR_AGRICULTURA  NUMBER(18,2),
            INC_REGIAO                VARCHAR2(100),
            INC_GRUPO_DESASTRE        VARCHAR2(100),
            INC_DH_DESCRICAO          VARCHAR2(2000),
            INC_DA_DESCRICAO          VARCHAR2(2000),
            INC_PEPL_DESCRICAO        VARCHAR2(2000),
            INC_PEPR_DESCRICAO        VARCHAR2(2000),
            INC_PEPR_AGRICULTURA      NUMBER(18,2),
            INC_PEPR_PECUARIA         NUMBER(18,2),
            INC_PEPR_INDUSTRIA        NUMBER(18,2),
            INC_COMERCIO              NUMBER(18,2),
            INC_SERVICOS              NUMBER(18,2),
            INC_TOTAL_PRIVADO         NUMBER(18,2),
            INC_PE_PLEPR              NUMBER(18,2),
            INC_PROTOCOLO             VARCHAR2(100),
            INC_DATA_EVENTO           DATE
        )]');

    tabela('CS_EVENTOS_CLIMA', q'[
        CREATE TABLE CS_EVENTOS_CLIMA (
            ID_EVENTO_CLIMA   NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            ID_EVENTO         NUMBER(10),
            CODIGO_IBGE       NUMBER(10),
            MUNICIPIO         VARCHAR2(150),
            UF                VARCHAR2(2),
            DATA_HORA         TIMESTAMP,
            TEMPERATURA       NUMBER(5,2),
            UMIDADE           NUMBER(5,2),
            VELOCIDADE_VENTO  NUMBER(6,2),
            PRECIPITACAO      NUMBER(6,2),
            DATA_HORA_BR      TIMESTAMP GENERATED ALWAYS AS (DATA_HORA - INTERVAL '3' HOUR) VIRTUAL
        )]');

    tabela('CS_EVENTOS_PREDICAO', q'[
        CREATE TABLE CS_EVENTOS_PREDICAO (
            ID                     NUMBER GENERATED BY DEFAULT AS IDENTITY,
            TIPO_RISCO             VARCHAR2(100) NOT NULL,
            REGIAO                 VARCHAR2(150),
            MUNICIPIO_IBGE         NUMBER(22),
            UF                     VARCHAR2(2),
            GRAU_RISCO             VARCHAR2(30)  NOT NULL,
            TEMPERATURA_CONDICAO   VARCHAR2(20),
            TEMPERATURA_VALOR      NUMBER(5,2),
            UMIDADE_CONDICAO       VARCHAR2(20),
            UMIDADE_VALOR          NUMBER(5,2),
            VENTO_CONDICAO         VARCHAR2(20),
            VENTO_VALOR            NUMBER(5,2),
            PRECIPITACAO_CONDICAO  VARCHAR2(20),
            PRECIPITACAO_VALOR     NUMBER(5,2),
            DIAS_TIPO              VARCHAR2(30),
            DIAS_CONDICAO          VARCHAR2(20),
            DIAS_VALOR             NUMBER(5),
            INCLINACAO_CONDICAO    VARCHAR2(20),
            INCLINACAO_VALOR       NUMBER(5,2),
            DESCRICAO_REGRA        CLOB,
            PROBABILIDADE          NUMBER(9,4),      -- estatística da regra (modelos/ml_alertas_queimadas_predicao.py)
            CASOS                  NUMBER,
            ORIGEM_REGRA           VARCHAR2(40),
            DATA_GERACAO           DATE,
            CONSTRAINT PK_CS_EVENTOS_PREDICAO PRIMARY KEY (ID),
            CONSTRAINT CK_PREDICAO_GRAU CHECK (GRAU_RISCO IN ('BAIXO', 'MEDIO', 'ALTO', 'CRITICO'))
        )]');
    coluna('CS_EVENTOS_PREDICAO', 'DESCRICAO_REGRA', 'CLOB');
    -- chance de eventos raros (hidrológico e deslizamento) com 4 casas decimais
    ampliar_numero('CS_EVENTOS_PREDICAO', 'PROBABILIDADE', 9, 4);
    ampliar_numero('CS_ALERTAS_PREDICAO', 'VALOR', 9, 4);
    coluna('CS_EVENTOS_PREDICAO', 'PROBABILIDADE', 'NUMBER(5,2)');
    coluna('CS_EVENTOS_PREDICAO', 'CASOS', 'NUMBER');
    coluna('CS_EVENTOS_PREDICAO', 'ORIGEM_REGRA', 'VARCHAR2(40)');
    coluna('CS_EVENTOS_PREDICAO', 'DATA_GERACAO', 'DATE');

    tabela('CS_PIPELINE_LOGS', q'[
        CREATE TABLE CS_PIPELINE_LOGS (
            ID                     NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            PIPELINE_EXEC_ID       VARCHAR2(50)  NOT NULL,
            SCRIPT_NOME            VARCHAR2(150) NOT NULL,
            TENTATIVA              NUMBER(2)     NOT NULL,
            STATUS                 VARCHAR2(20)  NOT NULL,
            MENSAGEM_RETORNO       CLOB,
            DATA_EXECUCAO          TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL,
            DATA_EXECUCAO_BR       TIMESTAMP GENERATED ALWAYS AS (DATA_EXECUCAO - INTERVAL '3' HOUR) VIRTUAL,
            REGISTROS_PROCESSADOS  NUMBER DEFAULT 0
        )]');
    indice('IDX_CS_PIPE_LOGS_DATA', 'CREATE INDEX IDX_CS_PIPE_LOGS_DATA ON CS_PIPELINE_LOGS (DATA_EXECUCAO DESC)');

    -- ---------------------------------------------------------------- Equipamentos
    tabela('CS_EQUIPAMENTOS_MODELOS', q'[
        CREATE TABLE CS_EQUIPAMENTOS_MODELOS (
            ID                   NUMBER GENERATED BY DEFAULT AS IDENTITY,
            TIPO                 VARCHAR2(100) NOT NULL,
            MODELO               VARCHAR2(150) NOT NULL,
            DESCRICAO_DETALHADA  CLOB,
            FABRICANTE           VARCHAR2(100) NOT NULL,
            ANO_FABRICACAO       NUMBER(4),
            VALOR_ESTIMADO       NUMBER(22,2),
            CONSTRAINT PK_CS_EQUIP_MODELOS PRIMARY KEY (ID)
        )]');

    -- Manual de cada MODELO (vale para todas as máquinas do modelo); COD_FONTE_MANUAL liga às recomendações
    tabela('CS_EQUIPAMENTOS_MANUAIS', q'[
        CREATE TABLE CS_EQUIPAMENTOS_MANUAIS (
            ID                NUMBER GENERATED BY DEFAULT AS IDENTITY,
            ID_EQUIPAMENTO    NUMBER(22)    NOT NULL,    -- CS_EQUIPAMENTOS_MODELOS.ID
            COD_FONTE_MANUAL  VARCHAR2(100) NOT NULL,    -- ex.: JD-CH950-OMCXT31163-B3
            NOME_ARQUIVO      VARCHAR2(255) NOT NULL,
            ARQUIVO_PDF       BLOB,
            DATA_UPLOAD       TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            CONSTRAINT PK_CS_EQUIP_MANUAIS PRIMARY KEY (ID),
            CONSTRAINT UK_MANUAL_COD_MODELO UNIQUE (COD_FONTE_MANUAL, ID_EQUIPAMENTO),
            CONSTRAINT FK_MANUAL_EQUIP FOREIGN KEY (ID_EQUIPAMENTO) REFERENCES CS_EQUIPAMENTOS_MODELOS (ID)
        )]');
    -- banco antigo: um manual só podia ser ligado a um modelo (UK_MANUAL_COD). Um manual pode valer para vários
    -- modelos da mesma série (ex.: Mahindra 6065 e 6075): a regra passa a ser um vínculo por manual + modelo.
    FOR r IN (SELECT 1 FROM USER_CONSTRAINTS WHERE TABLE_NAME = 'CS_EQUIPAMENTOS_MANUAIS'
              AND CONSTRAINT_NAME = 'UK_MANUAL_COD') LOOP
        EXECUTE IMMEDIATE 'ALTER TABLE CS_EQUIPAMENTOS_MANUAIS DROP CONSTRAINT UK_MANUAL_COD DROP INDEX';
        DBMS_OUTPUT.PUT_LINE('Restrição removida: UK_MANUAL_COD');
    END LOOP;
    restricao('CS_EQUIPAMENTOS_MANUAIS', 'UK_MANUAL_COD_MODELO', 'UNIQUE (COD_FONTE_MANUAL, ID_EQUIPAMENTO)');

    -- Recomendações dos manuais (NPL 1 manutenção programada, NPL 2 temperatura extrema, NPL 3 chuva)
    tabela('CS_EQUIPAMENTOS_ORIENTACOES', q'[
        CREATE TABLE CS_EQUIPAMENTOS_ORIENTACOES (
            ID                       NUMBER GENERATED BY DEFAULT AS IDENTITY,
            COD_FONTE_MANUAL         VARCHAR2(100) NOT NULL,
            TIPO_ORIENTACAO          VARCHAR2(100) NOT NULL,   -- MANUTENCAO_PROGRAMADA, ALERTA_TEMP_MINIMA, ALERTA_TEMP_MAXIMA, RISCO_CHUVA_DESLIZE
            SUBSISTEMA               VARCHAR2(100) NOT NULL,
            ACAO_TECNICA             VARCHAR2(100) NOT NULL,
            DETALHAMENTO_ORIENTACAO  CLOB,
            METRICA_GATILHO          VARCHAR2(50),             -- HORIMETRO, CALENDARIO, HODOMETRO, CONDICIONAL...
            VALOR_GATILHO            NUMBER(22,2),
            UNIDADE_MEDIDA           VARCHAR2(50),             -- HORAS, KM, DIAS, CELSIUS, KM_H...
            FATOR_CONDICIONAL        VARCHAR2(255),            -- ex.: OU_CALENDARIO:ANUAL, AMACIAMENTO_UNICA_VEZ, SOMENTE_4WD
            TEXTO_BRUTO_ORIGINAL     CLOB,
            CONSTRAINT PK_CS_EQUIP_ORIENTACOES PRIMARY KEY (ID)
        )]');

    tabela('CS_EQUIPAMENTOS_SEGURADOS', q'[
        CREATE TABLE CS_EQUIPAMENTOS_SEGURADOS (
            ID                     NUMBER GENERATED BY DEFAULT AS IDENTITY,
            ID_FAZENDA             NUMBER(22)    NOT NULL,
            ID_EQUIPAMENTO_MODELO  NUMBER(22)    NOT NULL,
            NUMERO_SERIE           VARCHAR2(100),
            IDENTIFICACAO_INTERNA  VARCHAR2(50),
            VALOR_SEGURADO         NUMBER(22,2)  NOT NULL,
            TELEMETRIA             VARCHAR2(3)   DEFAULT 'NAO' NOT NULL,
            NUMERO_APOLICE         VARCHAR2(100),
            DATA_INICIO_VIGENCIA   DATE,
            DATA_FIM_VIGENCIA      DATE,
            STATUS                 VARCHAR2(30)  DEFAULT 'ATIVO' NOT NULL,
            DATA_AQUISICAO         DATE,                  -- uso: base da programação das manutenções
            HORIMETRO_ATUAL        NUMBER(10,1),
            HODOMETRO_ATUAL        NUMBER(10,1),
            DATA_LEITURA           DATE,
            USO_MEDIO_HORAS_MES    NUMBER(8,1),
            USO_MEDIO_KM_MES       NUMBER(10,1),
            TIPO_OPERACAO          VARCHAR2(50),
            TIPO_TRACAO            VARCHAR2(10),          -- 2WD, 4WD ou ESTEIRA
            CONSTRAINT PK_CS_EQUIP_SEGURADOS PRIMARY KEY (ID),
            CONSTRAINT CK_STATUS_SEGURADO CHECK (STATUS IN ('ATIVO', 'SUSPENSO', 'BAIXADO')),
            CONSTRAINT CK_TELEMETRIA CHECK (TELEMETRIA IN ('SIM', 'NAO')),
            CONSTRAINT FK_SEGURADO_FAZENDA FOREIGN KEY (ID_FAZENDA) REFERENCES CS_FAZENDAS (ID),
            CONSTRAINT FK_SEGURADO_MODELO FOREIGN KEY (ID_EQUIPAMENTO_MODELO) REFERENCES CS_EQUIPAMENTOS_MODELOS (ID)
        )]');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'DATA_AQUISICAO', 'DATE');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'HORIMETRO_ATUAL', 'NUMBER(10,1)');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'HODOMETRO_ATUAL', 'NUMBER(10,1)');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'DATA_LEITURA', 'DATE');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'USO_MEDIO_HORAS_MES', 'NUMBER(8,1)');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'USO_MEDIO_KM_MES', 'NUMBER(10,1)');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'TIPO_OPERACAO', 'VARCHAR2(50)');
    coluna('CS_EQUIPAMENTOS_SEGURADOS', 'TIPO_TRACAO', 'VARCHAR2(10)');
    restricao('CS_EQUIPAMENTOS_SEGURADOS', 'CK_EQUIP_TIPO_TRACAO', q'[CHECK (TIPO_TRACAO IN ('2WD', '4WD', 'ESTEIRA'))]');

    -- Leitura mensal do horímetro/hodômetro, com foto de evidência (CS_ANEXOS, TIPO_ENTIDADE = 'LEITURA')
    tabela('CS_LEITURAS_MEDIDOR', q'[
        CREATE TABLE CS_LEITURAS_MEDIDOR (
            ID                       NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            ID_EQUIPAMENTO_SEGURADO  NUMBER       NOT NULL,
            DATA_LEITURA             DATE         NOT NULL,
            HORIMETRO                NUMBER(10,1),
            HODOMETRO                NUMBER(10,1),
            ID_ANEXO                 NUMBER,
            ORIGEM                   VARCHAR2(20) DEFAULT 'LEITURA_MENSAL' NOT NULL,
            USUARIO                  VARCHAR2(150),
            DATA_REGISTRO            TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            CONSTRAINT CK_LEITURA_ORIGEM CHECK (ORIGEM IN ('LEITURA_MENSAL', 'CADASTRO'))
        )]');
    indice('IX_CS_LEITURAS_EQUIP',
           'CREATE INDEX IX_CS_LEITURAS_EQUIP ON CS_LEITURAS_MEDIDOR (ID_EQUIPAMENTO_SEGURADO, DATA_LEITURA)');

    -- Lista de manutenções a comprovar (gerada por requisitos/programacao_manutencao.py)
    tabela('CS_MANUTENCOES_REALIZADAS', q'[
        CREATE TABLE CS_MANUTENCOES_REALIZADAS (
            ID                       NUMBER GENERATED BY DEFAULT AS IDENTITY,
            ID_EQUIPAMENTO_SEGURADO  NUMBER(22)   NOT NULL,
            ID_ORIENTACAO            NUMBER(22)   NOT NULL,
            DATA_PREVISTA            DATE         NOT NULL,
            DATA_REALIZADA           DATE,
            REALIZACAO               VARCHAR2(30) DEFAULT 'NAO_DISPONIVEL' NOT NULL,  -- SIM = realizada; NAO_DISPONIVEL = pendente
            COMPROVANTE_ENTREGUE     VARCHAR2(3)  DEFAULT 'NAO' NOT NULL,
            ATENDE_CRITERIOS         NUMBER(5,2),                                      -- avaliação da Sompo: 100 atende, 0 não
            REGRA                    VARCHAR2(10) DEFAULT 'MINIMA' NOT NULL,           -- MINIMA = cronograma; IDEAL = comprovante a mais (regra do manual)
            ID_COMPROVANTE           NUMBER,                                           -- anexo (CS_ANEXOS) do comprovante da revisão
            AVALIADO_POR             VARCHAR2(150),
            DATA_AVALIACAO           DATE,
            MOTIVO_AVALIACAO         VARCHAR2(500),
            CONSTRAINT PK_CS_MANUTENCOES_REALIZADAS PRIMARY KEY (ID),
            CONSTRAINT CK_REALIZACAO CHECK (REALIZACAO IN ('SIM', 'NAO', 'NAO_SE_APLICA', 'NAO_DISPONIVEL', 'CANCELADA')),
            CONSTRAINT CK_COMPROVANTE CHECK (COMPROVANTE_ENTREGUE IN ('SIM', 'NAO')),
            CONSTRAINT CK_ATENDE_CRITERIOS CHECK (ATENDE_CRITERIOS BETWEEN 0 AND 100),
            CONSTRAINT CK_MANUT_REGRA CHECK (REGRA IN ('MINIMA', 'IDEAL')),
            CONSTRAINT FK_MANUT_EQUIP_SEGURADO FOREIGN KEY (ID_EQUIPAMENTO_SEGURADO) REFERENCES CS_EQUIPAMENTOS_SEGURADOS (ID),
            CONSTRAINT FK_MANUT_ORIENTACAO FOREIGN KEY (ID_ORIENTACAO) REFERENCES CS_EQUIPAMENTOS_ORIENTACOES (ID)
        )]');
    -- Regra da manutenção: MINIMA = prevista no cronograma (regra de intervalo maior); IDEAL = comprovante enviado além
    -- do cronograma, que conta para a regra ideal do manual ("o que ocorrer primeiro") e dá bônus no Score de Risco
    coluna('CS_MANUTENCOES_REALIZADAS', 'REGRA', 'VARCHAR2(10) DEFAULT ''MINIMA'' NOT NULL');
    restricao('CS_MANUTENCOES_REALIZADAS', 'CK_MANUT_REGRA', 'CHECK (REGRA IN (''MINIMA'', ''IDEAL''))');
    -- Comprovante da revisão: um arquivo (CS_ANEXOS) vale para todos os serviços feitos juntos na mesma revisão
    coluna('CS_MANUTENCOES_REALIZADAS', 'ID_COMPROVANTE', 'NUMBER');
    -- Validação da Sompo (ATENDE_CRITERIOS): quem avaliou, quando e o motivo da reprovação
    coluna('CS_MANUTENCOES_REALIZADAS', 'AVALIADO_POR', 'VARCHAR2(150)');
    coluna('CS_MANUTENCOES_REALIZADAS', 'DATA_AVALIACAO', 'DATE');
    coluna('CS_MANUTENCOES_REALIZADAS', 'MOTIVO_AVALIACAO', 'VARCHAR2(500)');

    -- Comprovantes de manutenção, notas fiscais e fotos do horímetro (com SHA-256 para conferir a integridade)
    tabela('CS_ANEXOS', q'[
        CREATE TABLE CS_ANEXOS (
            ID             NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            TIPO_ENTIDADE  VARCHAR2(30)  NOT NULL,     -- MANUTENCAO, EQUIPAMENTO ou LEITURA
            ID_ENTIDADE    NUMBER        NOT NULL,
            TIPO_ANEXO     VARCHAR2(30)  NOT NULL,     -- COMPROVANTE, NOTA_FISCAL ou HORIMETRO
            NOME_ARQUIVO   VARCHAR2(255) NOT NULL,
            TIPO_CONTEUDO  VARCHAR2(100),
            TAMANHO_BYTES  NUMBER,
            HASH_SHA256    VARCHAR2(64)  NOT NULL,
            ARQUIVO        BLOB          NOT NULL,
            USUARIO_ENVIO  VARCHAR2(150),
            DATA_ENVIO     TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            CONSTRAINT CK_ANEXOS_ENTIDADE CHECK (TIPO_ENTIDADE IN ('MANUTENCAO', 'EQUIPAMENTO', 'LEITURA'))
        )]');
    -- banco antigo: a restrição não aceitava LEITURA
    DECLARE
        v_condicao VARCHAR2(4000);
    BEGIN
        FOR r IN (SELECT SEARCH_CONDITION FROM USER_CONSTRAINTS
                  WHERE TABLE_NAME = 'CS_ANEXOS' AND CONSTRAINT_NAME = 'CK_ANEXOS_ENTIDADE') LOOP
            v_condicao := r.SEARCH_CONDITION;
            IF INSTR(v_condicao, 'LEITURA') = 0 THEN
                EXECUTE IMMEDIATE 'ALTER TABLE CS_ANEXOS DROP CONSTRAINT CK_ANEXOS_ENTIDADE';
            END IF;
        END LOOP;
    END;
    restricao('CS_ANEXOS', 'CK_ANEXOS_ENTIDADE', q'[CHECK (TIPO_ENTIDADE IN ('MANUTENCAO', 'EQUIPAMENTO', 'LEITURA'))]');
    indice('IX_CS_ANEXOS_ENTIDADE', 'CREATE INDEX IX_CS_ANEXOS_ENTIDADE ON CS_ANEXOS (TIPO_ENTIDADE, ID_ENTIDADE)');

    tabela('CS_SINISTROS', q'[
        CREATE TABLE CS_SINISTROS (
            ID                       NUMBER GENERATED BY DEFAULT AS IDENTITY,
            ID_EQUIPAMENTO_SEGURADO  NUMBER(22)    NOT NULL,
            NUMERO_APOLICE           VARCHAR2(100) NOT NULL,
            DATA_OCORRENCIA          TIMESTAMP     NOT NULL,
            TIPO_SINISTRO            VARCHAR2(100) NOT NULL,
            VALOR_INDENIZACAO        NUMBER(22,2)  DEFAULT 0,
            NUMERO_AVISO_SINISTRO    VARCHAR2(100),
            ESTADO_SINISTRO          VARCHAR2(30)  DEFAULT 'ABERTO' NOT NULL,
            DATA_ENCERRAMENTO        DATE,
            CONSTRAINT PK_CS_SINISTROS PRIMARY KEY (ID),
            CONSTRAINT CK_ESTADO_SINISTRO CHECK (ESTADO_SINISTRO IN ('ABERTO', 'EM_ANALISE', 'INDENIZADO', 'ENCERRADO', 'RECUSADO')),
            CONSTRAINT FK_SINISTRO_EQUIPAMENTO FOREIGN KEY (ID_EQUIPAMENTO_SEGURADO) REFERENCES CS_EQUIPAMENTOS_SEGURADOS (ID)
        )]');
    coluna('CS_SINISTROS', 'DATA_ENCERRAMENTO', 'DATE');

    -- ---------------------------------------------------------------- Score e maturidade
    -- Maturidade da gestão da manutenção: 15 afirmativas (0 a 3), pesos 40/40/20, nota de 0 a 15
    tabela('CS_SCORE_MANUTENCAO_PROCEDIMENTOS', q'[
        CREATE TABLE CS_SCORE_MANUTENCAO_PROCEDIMENTOS (
            ID                              NUMBER GENERATED BY DEFAULT AS IDENTITY,
            ID_FAZENDA                      NUMBER(22) NOT NULL,
            DATA_AVALIACAO                  TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            P1_Q1_MANUTENCAO                NUMBER(1) NOT NULL,
            P1_Q2_CAPACITACAO_TECNICA       NUMBER(1) NOT NULL,
            P1_Q3_PECAS_HOMOLOGADAS         NUMBER(1) NOT NULL,
            SUBTOTAL_MANUTENCAO_PREVENTIVA  NUMBER(5,2),
            P2_Q4_VISTORIA_PRE_OPERACIONAL  NUMBER(1) NOT NULL,
            P2_Q5_TELEMETRIA_MONITORAMENTO  NUMBER(1) NOT NULL,
            P2_Q6_CONDICOES_TERMICAS        NUMBER(1) NOT NULL,
            P2_Q7_PARALISACAO_INCENDIO      NUMBER(1) NOT NULL,
            P2_Q8_TRAFEGABILIDADE_CHUVA     NUMBER(1) NOT NULL,
            P2_Q9_AREAS_INCLINADAS          NUMBER(1) NOT NULL,
            P2_Q10_LIMPEZA_RADIADORES       NUMBER(1) NOT NULL,
            P2_Q11_COMBATE_INCENDIO         NUMBER(1) NOT NULL,
            SUBTOTAL_GESTAO_RISCO           NUMBER(5,2),
            P3_Q12_PROCESSOS_DOCUMENTADOS   NUMBER(1) NOT NULL,
            P3_Q13_CAPACITACAO              NUMBER(1) NOT NULL,
            P3_Q14_AUDITORIA_CONFORMIDADE   NUMBER(1) NOT NULL,
            P3_Q15_REVISAO_REGULAR          NUMBER(1) NOT NULL,
            SUBTOTAL_GOVERNANCA             NUMBER(5,2),
            SCORE_FINAL                     NUMBER(5,2) NOT NULL,
            CONSTRAINT PK_CS_SCORE_MANUTENCAO PRIMARY KEY (ID),
            CONSTRAINT FK_SCORE_FAZENDA FOREIGN KEY (ID_FAZENDA) REFERENCES CS_FAZENDAS (ID),
            CONSTRAINT CK_P1_Q1 CHECK (P1_Q1_MANUTENCAO BETWEEN 0 AND 3),
            CONSTRAINT CK_P1_Q2 CHECK (P1_Q2_CAPACITACAO_TECNICA BETWEEN 0 AND 3),
            CONSTRAINT CK_P1_Q3 CHECK (P1_Q3_PECAS_HOMOLOGADAS BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q4 CHECK (P2_Q4_VISTORIA_PRE_OPERACIONAL BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q5 CHECK (P2_Q5_TELEMETRIA_MONITORAMENTO BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q6 CHECK (P2_Q6_CONDICOES_TERMICAS BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q7 CHECK (P2_Q7_PARALISACAO_INCENDIO BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q8 CHECK (P2_Q8_TRAFEGABILIDADE_CHUVA BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q9 CHECK (P2_Q9_AREAS_INCLINADAS BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q10 CHECK (P2_Q10_LIMPEZA_RADIADORES BETWEEN 0 AND 3),
            CONSTRAINT CK_P2_Q11 CHECK (P2_Q11_COMBATE_INCENDIO BETWEEN 0 AND 3),
            CONSTRAINT CK_P3_Q12 CHECK (P3_Q12_PROCESSOS_DOCUMENTADOS BETWEEN 0 AND 3),
            CONSTRAINT CK_P3_Q13 CHECK (P3_Q13_CAPACITACAO BETWEEN 0 AND 3),
            CONSTRAINT CK_P3_Q14 CHECK (P3_Q14_AUDITORIA_CONFORMIDADE BETWEEN 0 AND 3),
            CONSTRAINT CK_P3_Q15 CHECK (P3_Q15_REVISAO_REGULAR BETWEEN 0 AND 3)
        )]');

    tabela('CS_SCORE_GESTAO', q'[
        CREATE TABLE CS_SCORE_GESTAO (
            ID                                  NUMBER GENERATED BY DEFAULT AS IDENTITY,
            ID_FAZENDA                          NUMBER(22) NOT NULL,
            DATA_CALCULO                        TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            SCORE_CLIENTE_EXPOSICAO             NUMBER(5,2),
            SCORE_CLIENTE_ADERENCIA_MANUTENCAO  NUMBER(5,2),
            SCORE_CLIENTE_HISTORICO_SINISTROS   NUMBER(5,2),
            SCORE_AMBIENTAL_QUEIMADAS           NUMBER(5,2),
            SCORE_AMBIENTAL_HIDROLOGICO         NUMBER(5,2),
            SCORE_AMBIENTAL_EVENTOS_EXTREMOS    NUMBER(5,2),
            SCORE_OPERACIONAL_CLIMATICO         NUMBER(5,2),
            SCORE_OPERACIONAL_MANUTENCAO        NUMBER(5,2),
            SCORE_OPERACIONAL_PROCEDIMENTOS     NUMBER(5,2),
            SCORE_RISCO_TOTAL                   NUMBER(5,2) NOT NULL,
            CLASSIFICACAO_RISCO                 VARCHAR2(50),
            CONSTRAINT PK_CS_SCORE_GESTAO PRIMARY KEY (ID),
            CONSTRAINT FK_SCORE_GESTAO_FAZENDA FOREIGN KEY (ID_FAZENDA) REFERENCES CS_FAZENDAS (ID)
        )]');
    -- Score do cliente (requisitos/score_risco.py): SCORE_RISCO_TOTAL e CLASSIFICACAO_RISCO são do cliente; a linha
    -- também guarda o cliente, o score só da fazenda e o bônus da regra ideal
    coluna('CS_SCORE_GESTAO', 'ID_CLIENTE', 'NUMBER');
    coluna('CS_SCORE_GESTAO', 'SCORE_FAZENDA', 'NUMBER(5,2)');
    coluna('CS_SCORE_GESTAO', 'BONUS_REGRA_IDEAL', 'NUMBER(4,2)');
    -- Regras publicadas junto com o resultado: versão da matriz, a matriz completa usada no cálculo (pesos, faixas das
    -- notas 1, 2 e 3, classificação e bônus) e a medida de cada item daquela fazenda
    coluna('CS_SCORE_GESTAO', 'VERSAO_MATRIZ', 'VARCHAR2(30)');
    coluna('CS_SCORE_GESTAO', 'REGRAS_SCORE', 'CLOB');
    coluna('CS_SCORE_GESTAO', 'DETALHE_CALCULO', 'CLOB');

    -- ---------------------------------------------------------------- Leitura de manuais (NLP)
    -- Rejeições dos revisores: o "aprendizado" da página Manutenções Programadas
    tabela('CS_NLP_REVISOES', q'[
        CREATE TABLE CS_NLP_REVISOES (
            ID                NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            COD_FONTE_MANUAL  VARCHAR2(100)  NOT NULL,
            ORIGEM            VARCHAR2(20)   NOT NULL,
            TIPO_ORIENTACAO   VARCHAR2(100),
            SUBSISTEMA        VARCHAR2(100),
            DETALHAMENTO      VARCHAR2(2000) NOT NULL,
            TEXTO_BRUTO       VARCHAR2(4000),
            MOTIVO            VARCHAR2(500),
            USUARIO           VARCHAR2(150),
            DATA_REVISAO      TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            CONSTRAINT CK_NLP_REVISOES_ORIGEM CHECK (ORIGEM IN ('AUTOMATICA', 'IA'))
        )]');
    indice('IX_CS_NLP_REVISOES_DATA', 'CREATE INDEX IX_CS_NLP_REVISOES_DATA ON CS_NLP_REVISOES (DATA_REVISAO)');

    -- Cada chamada paga à IA (Claude): tokens, custo e quem autorizou
    tabela('CS_NLP_USO_IA', q'[
        CREATE TABLE CS_NLP_USO_IA (
            ID                NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            COD_FONTE_MANUAL  VARCHAR2(100) NOT NULL,
            MODELO_IA         VARCHAR2(50)  NOT NULL,
            NPL               VARCHAR2(20),
            TOKENS_ENTRADA    NUMBER        NOT NULL,
            TOKENS_SAIDA      NUMBER        NOT NULL,
            CUSTO_USD         NUMBER(10,4)  NOT NULL,
            LIMITE_USD        NUMBER(10,4),
            USUARIO           VARCHAR2(150),
            DATA_USO          TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL
        )]');

    -- Registro de ações (auditoria): quem fez, quando e o quê (envio de comprovante, validação da Sompo, gravação de
    -- manual...). Só recebe linhas novas: o histórico não é sobrescrito (requisitos/auditoria.py)
    tabela('CS_LOG_ACOES', q'[
        CREATE TABLE CS_LOG_ACOES (
            ID          NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            DATA_HORA   TIMESTAMP DEFAULT SYSTIMESTAMP NOT NULL,
            USUARIO     VARCHAR2(150),
            PERFIL      VARCHAR2(30),
            PAGINA      VARCHAR2(50),
            ACAO        VARCHAR2(50)  NOT NULL,
            ENTIDADE    VARCHAR2(50),
            CHAVE       VARCHAR2(200),
            DETALHE     VARCHAR2(1000)
        )]');
    indice('IX_CS_LOG_ACOES_CHAVE', 'CREATE INDEX IX_CS_LOG_ACOES_CHAVE ON CS_LOG_ACOES (ENTIDADE, CHAVE)');
    indice('IX_CS_LOG_ACOES_DATA', 'CREATE INDEX IX_CS_LOG_ACOES_DATA ON CS_LOG_ACOES (DATA_HORA)');
END;
/

-- =====================================================================
-- PARTE 2 - DADOS DE REFERÊNCIA
-- ---------------------------------------------------------------------
-- 2.1 Modelos de equipamentos (CS_EQUIPAMENTOS_MODELOS): 22 equipamentos e 4 plataformas de John Deere, Case IH,
--     New Holland, Jacto e Mahindra. VALOR_ESTIMADO = valor de referência de uma unidade nova (R$), estimado a partir de
--     anúncios (MF Rural, MarketBook, Agrofy, Mercado Livre) para simulação. Cada INSERT só grava se o modelo
--     ainda não existir.
-- =====================================================================

-- ---------------- EQUIPAMENTOS PRINCIPAIS ----------------

-- 01. John Deere CP770 | valor: anúncios MarketBook
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Colhedora de algodão com enfardamento a bordo', 'CP770',
       'Colhedora de algodão autopropelida que forma e envolve os módulos (fardos cilíndricos) sem parar a colheita, dispensando bass boy e prensa de módulos. Motor John Deere PowerTech de 13,5 L, telemetria JDLink e integração com o Operations Center. Uma das máquinas agrícolas de maior valor do mercado brasileiro.',
       'John Deere', 2025, 5200000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'John Deere' AND MODELO = 'CP770');

-- 02. John Deere X9 1100 | valor: anúncios (R$ 4,1 mi) e média MarketBook
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Colheitadeira de grãos classe 10', 'X9 1100',
       'Colheitadeira de grãos de altíssima capacidade com sistema de separação X-Series de rotor duplo (Dual Separator). Motor John Deere de 13,6 L com potência máxima na faixa de 690 cv, tanque graneleiro de grande capacidade e automação de ajustes (Combine Advisor). Indicada para grandes áreas de soja e milho.',
       'John Deere', 2025, 4800000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'John Deere' AND MODELO = 'X9 1100');

-- 03. John Deere CH950 | valor: estimativa (sem preço público)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Colhedora de cana-de-açúcar de duas linhas', 'CH950',
       'Colhedora de cana-de-açúcar que colhe duas linhas simultaneamente em espaçamentos de 1,40 m a 1,50 m, com tecnologia RowAdapt, novo sistema de limpeza e pacote completo de agricultura de precisão (AutoTrac, JDLink). Fabricada em Catalão/GO.',
       'John Deere', 2025, 4000000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'John Deere' AND MODELO = 'CH950');

-- 04. John Deere 9R 640 | valor: média MarketBook
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator articulado de pneus de alta potência', '9R 640',
       'Trator articulado 4x4 de 640 cv para grandes propriedades, com rodado duplo, transmissão e8 ou e18 e piloto automático. Indicado para preparo de solo e tração de plantadeiras de grande porte em janelas de plantio curtas.',
       'John Deere', 2025, 3600000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'John Deere' AND MODELO = '9R 640');

-- 05. John Deere DB90 | valor: anúncios MarketBook
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Plantadeira de arrasto de grande porte', 'DB90',
       'Plantadeira pneumática de arrasto da série DB com 55 a 61 linhas, dosadores MaxEmerge 5e acionados eletricamente, controle de seção por linha e dobra em sanfona para transporte. Requer trator de alta potência.',
       'John Deere', 2025, 3000000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'John Deere' AND MODELO = 'DB90');

-- 06. John Deere 5060E | valor: média MF Rural 2025 (R$ 203 mil) e anúncios
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator utilitário de pequeno porte', '5060E',
       'Trator de 60 cv da Série 5E, versátil para tarefas diárias, plantio e tratos culturais em pequenas e médias propriedades. Garantia de 36 meses ou 2.000 horas e plano de revisões a cada 100/400/800/1000/1500 horas.',
       'John Deere', 2025, 230000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'John Deere' AND MODELO = '5060E');

-- 07. Case IH Steiger Quadtrac 620 | valor: faixa de R$ 5 a 7 milhões
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator articulado de esteiras', 'Steiger Quadtrac 620',
       'Maior trator comercializado no Brasil. Articulado sobre quatro esteiras de borracha (Quadtrac), com 629 cv de potência nominal, 669 cv de potência máxima e até 691 cv com gerenciamento de potência. Menor compactação do solo e alta tração.',
       'Case IH', 2025, 5800000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Case IH' AND MODELO = 'Steiger Quadtrac 620');

-- 08. Case IH Axial-Flow 9250 | valor: estimativa (sem preço público)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Colheitadeira de grãos de fluxo axial', 'Axial-Flow 9250',
       'Colheitadeira de rotor axial único da série 250 Automation, com o sistema AFS Harvest Command que ajusta a máquina automaticamente por sensores em quatro modos de colheita. Nova transmissão hidrostática com troca automatizada de marchas.',
       'Case IH', 2025, 4200000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Case IH' AND MODELO = 'Axial-Flow 9250');

-- 09. Case IH Austoft A8810 | valor: estimativa (sem preço público)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Colhedora de cana-de-açúcar de uma linha', 'Austoft A8810',
       'Colhedora de cana-de-açúcar de uma linha sobre esteiras, com corte de base, picador e extratores primário e secundário para limpeza da cana. Fabricada em Piracicaba/SP.',
       'Case IH', 2025, 2800000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Case IH' AND MODELO = 'Austoft A8810');

-- 10. Case IH Magnum 400 AFS Connect | valor: anúncio (R$ 1,65 mi em 2022) e média MarketBook
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator de rodas de alta potência', 'Magnum 400 AFS Connect',
       'Trator de rodas de 400 cv com conectividade 4G nativa (AFS Connect), transmissão CVX ou Powershift e piloto automático. Indicado para plantio, preparo de solo e transporte em médias e grandes propriedades.',
       'Case IH', 2025, 2300000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Case IH' AND MODELO = 'Magnum 400 AFS Connect');

-- 11. Case IH Patriot 350 | valor: estimativa (Patriot 250 2024 a R$ 930 mil)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Pulverizador autopropelido de grande porte', 'Patriot 350',
       'Pulverizador autopropelido 4x4 com transmissão hidrostática, tanque de 3.500 L, barra de 30 a 36 m e vão livre de 1,73 m, que permite aplicar no fim do ciclo em milho e cana. Motor de cerca de 250 cv.',
       'Case IH', 2025, 1900000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Case IH' AND MODELO = 'Patriot 350');

-- 12. New Holland FR 920 Forage Cruiser | valor: estimativa (média MarketBook de usados)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Forrageira autopropelida', 'FR 920 Forage Cruiser',
       'Forrageira autopropelida mais potente da New Holland, com motor FPT V20 de 20 L, 923 cv e torque máximo de 4.100 Nm. Utilizada na colheita de milho e capim para silagem em grandes operações de pecuária.',
       'New Holland', 2025, 5000000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'New Holland' AND MODELO = 'FR 920 Forage Cruiser');

-- 13. New Holland CR 8.90 | valor: anúncios (até R$ 3,25 mi)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Colheitadeira de grãos de duplo rotor', 'CR 8.90',
       'Colheitadeira classe 8 com sistema Twin Rotor (dois rotores axiais), alta qualidade de grão e palha, e piloto automático IntelliSteer. Indicada para soja, milho e trigo em grandes áreas.',
       'New Holland', 2025, 3300000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'New Holland' AND MODELO = 'CR 8.90');

-- 14. New Holland T7.245 | valor: estimativa (média MF Rural 2024 de usados R$ 545 mil)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator de médio porte', 'T7.245',
       'Trator de médio-grande porte da série T7, com cabine, transmissão Power Command ou Auto Command e sistema hidráulico de alta vazão. Muito utilizado em plantio, pulverização de arrasto e transporte de grãos.',
       'New Holland', 2025, 800000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'New Holland' AND MODELO = 'T7.245');

-- 15. New Holland TT4 | valor: estimativa (anúncios de TT usados R$ 150-180 mil)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator especial para culturas perenes (fruteiro/cafeeiro)', 'TT4',
       'Trator compacto e robusto de bitola estreita, projetado para trabalhar entre as linhas de café, citros e frutas. Layout funcional e versatilidade em terrenos declivosos.',
       'New Holland', 2025, 250000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'New Holland' AND MODELO = 'TT4');

-- 16. Jacto K3500 | valor: estimativa (K3 usadas R$ 140-280 mil)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Colhedora de café automotriz', 'K3500',
       'Colhedora de café automotriz que trabalha sobre a linha de plantio, com hastes vibratórias para derriça, sistema de recolhimento e descarga. Referência na mecanização do café no Brasil.',
       'Jacto', 2025, 950000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Jacto' AND MODELO = 'K3500');

-- 17. Jacto Uniport Planter 500 | valor: estimativa (sem preço público)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Plantadeira automotriz híbrida', 'Uniport Planter 500',
       'Primeira plantadeira automotriz com sistema híbrido do Brasil (transmissão com motores elétricos). Cinco seções articuladas e independentes para acompanhar o terreno e tanque de sementes de 8.700 L. Dispensa trator.',
       'Jacto', 2025, 3200000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Jacto' AND MODELO = 'Uniport Planter 500');

-- 18. Jacto Uniport 5030 NPK | valor: estimativa (sem preço público)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Distribuidor autopropelido de fertilizantes sólidos', 'Uniport 5030 NPK',
       'Distribuidor autopropelido de fertilizantes e corretivos sólidos com capacidade de 5.000 kg, aplicação em taxa variável e alta velocidade operacional.',
       'Jacto', 2025, 1500000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Jacto' AND MODELO = 'Uniport 5030 NPK');

-- 19. Jacto Arbus 4000 JAV | valor: estimativa (sem preço público)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Pulverizador autônomo (sem operador)', 'Arbus 4000 JAV',
       'Pulverizador turbo autônomo, que opera sem cabine e sem operador a bordo, guiado por GPS e sensores. Tanque de 4.000 L, indicado para citros, café e frutas.',
       'Jacto', 2025, 750000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Jacto' AND MODELO = 'Arbus 4000 JAV');

-- 20. Jacto Arbus 4000 Valência | valor: estimativa (usados R$ 70-120 mil)
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Pulverizador atomizador (turbo) de arrasto', 'Arbus 4000 Valência',
       'Pulverizador atomizador de arrasto com tanque de 4.000 L e ventilador de alta vazão para aplicação em pomares de citros e lavouras de café. Tracionado por trator.',
       'Jacto', 2025, 180000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Jacto' AND MODELO = 'Arbus 4000 Valência');

-- 21. Mahindra 6075 | valor: estimativa a partir de anúncios de tratores de 75 cv
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator agrícola de médio porte', '6075',
       'Trator de 75 cv para preparo de solo, plantio, pulverização e transporte em pequenas e médias propriedades. Versões com tração 2WD e 4WD, tomada de potência e levante hidráulico de três pontos.',
       'Mahindra', 2025, 260000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Mahindra' AND MODELO = '6075');

-- 22. Mahindra Max Series | valor: estimativa a partir de anúncios de tratores compactos
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Trator compacto', 'Max Series',
       'Trator compacto da linha Max para tarefas leves: roçada, transporte, tratos culturais em hortifrúti, pomares e serviços gerais da fazenda. Tração 4WD, tomada de potência e engate de três pontos.',
       'Mahindra', 2025, 140000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Mahindra' AND MODELO = 'Max Series');

-- ---------------- PLATAFORMAS (acessórios das colheitadeiras e da forrageira) ----------------

-- P1. John Deere Plataforma draper 45 pés (usada com John Deere X9 1100) | valor: estimativa
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Plataforma de corte draper', 'Plataforma draper 45 pés',
       'Plataforma de corte flexível com esteiras (draper) de 45 pés para colheita de soja e trigo, acoplada à colheitadeira X9 1100.',
       'John Deere', 2025, 650000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'John Deere' AND MODELO = 'Plataforma draper 45 pés');

-- P2. Case IH Plataforma draper 40 pés (usada com Case IH Axial-Flow 9250) | valor: estimativa
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Plataforma de corte draper', 'Plataforma draper 40 pés',
       'Plataforma de corte flexível com esteiras (draper) de 40 pés para soja e trigo, acoplada à colheitadeira Axial-Flow 9250.',
       'Case IH', 2025, 550000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'Case IH' AND MODELO = 'Plataforma draper 40 pés');

-- P3. New Holland Plataforma de milho 16 linhas (usada com New Holland CR 8.90) | valor: estimativa
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Plataforma de milho', 'Plataforma de milho 16 linhas',
       'Plataforma recolhedora de milho de 16 linhas, acoplada à colheitadeira CR 8.90.',
       'New Holland', 2025, 600000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'New Holland' AND MODELO = 'Plataforma de milho 16 linhas');

-- P4. New Holland Plataforma de milho 10 linhas (independente de linha) (usada com New Holland FR 920 Forage Cruiser) | valor: estimativa
INSERT INTO CS_EQUIPAMENTOS_MODELOS (TIPO, MODELO, DESCRICAO_DETALHADA, FABRICANTE, ANO_FABRICACAO, VALOR_ESTIMADO)
SELECT 'Plataforma de milho para forrageira', 'Plataforma de milho 10 linhas (independente de linha)',
       'Plataforma de milho com tambores rotativos que colhe independentemente do espaçamento entre linhas, acoplada à forrageira FR 920.',
       'New Holland', 2025, 700000
FROM DUAL
WHERE NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MODELOS WHERE FABRICANTE = 'New Holland' AND MODELO = 'Plataforma de milho 10 linhas (independente de linha)');

COMMIT;

-- ---------------------------------------------------------------------
-- 2.2 Vínculo manual -> modelo (CS_EQUIPAMENTOS_MANUAIS). As recomendações vêm de dois manuais, cada um só do seu
--     modelo exato: JD-CH950-OMCXT31163-B3 -> John Deere CH950 | JD-5060E-OMTR132548-E6 -> John Deere 5060E.
--     Manuais novos entram pela página Manutenções Programadas. Vínculo que já existe é pulado.
-- ---------------------------------------------------------------------
DECLARE
    PROCEDURE vincular(p_modelo VARCHAR2, p_codigo VARCHAR2) IS
        v_modelo NUMBER;
        v_existe NUMBER;
    BEGIN
        SELECT MIN(ID) INTO v_modelo FROM CS_EQUIPAMENTOS_MODELOS
        WHERE FABRICANTE = 'John Deere' AND MODELO = p_modelo;
        IF v_modelo IS NULL THEN
            DBMS_OUTPUT.PUT_LINE('Modelo não encontrado: John Deere ' || p_modelo);
            RETURN;
        END IF;
        SELECT COUNT(*) INTO v_existe FROM CS_EQUIPAMENTOS_MANUAIS WHERE COD_FONTE_MANUAL = p_codigo;
        IF v_existe > 0 THEN
            RETURN;
        END IF;
        BEGIN
            INSERT INTO CS_EQUIPAMENTOS_MANUAIS (ID_EQUIPAMENTO, COD_FONTE_MANUAL, NOME_ARQUIVO, DATA_UPLOAD)
            VALUES (v_modelo, p_codigo, p_codigo, SYSTIMESTAMP);
        EXCEPTION
            WHEN OTHERS THEN
                IF SQLCODE != -1400 THEN RAISE; END IF;
                -- a coluna ID não é gerada automaticamente: usa o próximo número livre
                INSERT INTO CS_EQUIPAMENTOS_MANUAIS (ID, ID_EQUIPAMENTO, COD_FONTE_MANUAL, NOME_ARQUIVO, DATA_UPLOAD)
                SELECT NVL(MAX(ID), 0) + 1, v_modelo, p_codigo, p_codigo, SYSTIMESTAMP FROM CS_EQUIPAMENTOS_MANUAIS;
        END;
    END;
BEGIN
    vincular('CH950', 'JD-CH950-OMCXT31163-B3');
    vincular('5060E', 'JD-5060E-OMTR132548-E6');
    COMMIT;
END;
/

-- =====================================================================
-- CONFERÊNCIA
-- =====================================================================
SELECT TABLE_NAME AS TABELA, NUM_ROWS AS LINHAS_APROXIMADAS FROM USER_TABLES
WHERE TABLE_NAME LIKE 'CS\_%' ESCAPE '\' ORDER BY TABLE_NAME;

SELECT mo.FABRICANTE, mo.MODELO, ma.COD_FONTE_MANUAL,
       (SELECT COUNT(*) FROM CS_EQUIPAMENTOS_ORIENTACOES o WHERE o.COD_FONTE_MANUAL = ma.COD_FONTE_MANUAL) AS RECOMENDACOES
FROM CS_EQUIPAMENTOS_MANUAIS ma JOIN CS_EQUIPAMENTOS_MODELOS mo ON mo.ID = ma.ID_EQUIPAMENTO
ORDER BY mo.FABRICANTE, mo.MODELO;

-- =====================================================================
-- LIMPEZA OPCIONAL (restos de versões anteriores, não usados pelo sistema). Tire o "--" para rodar.
-- =====================================================================
-- Tabela auxiliar da renomeação das fazendas de teste:
-- DROP TABLE CS_FAZENDAS_NOMES_NOVOS;
-- Pergunta 16 do questionário de maturidade (o questionário ficou com 15):
-- ALTER TABLE CS_SCORE_MANUTENCAO_PROCEDIMENTOS DROP COLUMN P3_Q16_RESPONSAVEL_DEDICADO;
