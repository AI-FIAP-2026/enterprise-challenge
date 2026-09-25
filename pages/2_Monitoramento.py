# -*- coding: utf-8 -*-
import streamlit as st
import oracledb
import pandas as pd
from datetime import datetime
import time
import sys
import os

# Adiciona a raiz do projeto ao path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

try:
    from pipeline import job_pipeline
except ImportError:
    job_pipeline = None

try:
    from modelos.ml_queimadas import rodar_treinamento_queimadas
except ImportError:
    rodar_treinamento_queimadas = None

try:
    from modelos.ml_hidrologicos import rodar_treinamento_hidrologico
except ImportError:
    rodar_treinamento_hidrologico = None

# Configuração da página
st.set_page_config(
    page_title="Campo Seguro - Monitoramento",
    layout="wide"
)

# ==========================================
# RENDERIZAÇÃO DO LOGO À DIREITA (AMPLIADO 150%)
# ==========================================
col_vazia_hdr, col_logo = st.columns([7, 3])
with col_logo:
    caminho_logo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'assets', 'CS_Logo.jpeg'))
    if os.path.exists(caminho_logo):
        st.image(caminho_logo, width=270)

# ==========================================
# ESTILO INSTITUCIONAL E TIPOGRAFIA PERSONALIZADA
# ==========================================
st.markdown("""
    <style>
    div.stButton > button:first-child {
        background-color: #1A4A75 !important;
        color: white !important;
        border-radius: 4px !important;
        border: none !important;
        width: 100% !important;
        font-weight: 600 !important;
    }
    div.stButton > button:first-child:hover {
        background-color: #133557 !important;
        color: white !important;
    }
    .admin-title-container {
        background: linear-gradient(90deg, rgba(56, 142, 60, 0.18) 0%, rgba(56, 142, 60, 0.04) 100%);
        padding: 18px 32px;
        border-radius: 4px;
        margin-bottom: 48px;
        width: 100%;
        text-align: left;
    }
    .admin-title-container h1 {
        margin: 0;
        color: #1A4A75;
        font-size: 2.4rem;
        font-weight: 700;
    }
    .admin-title-container p {
        margin: 8px 0 0 0;
        color: #333333;
        font-size: 1.1rem;
    }
    .secao-destaque {
        font-size: 1.6rem;
        font-weight: 800;
        color: #1A4A75;
        text-transform: uppercase;
        margin-top: 45px;
        margin-bottom: 25px;
    }
    .subsecao-negrito {
        font-size: 1.15rem;
        font-weight: 700;
        color: #1A4A75;
        margin-top: 35px;
        margin-bottom: 20px;
    }
    .subsecao-normal {
        font-size: 1.15rem;
        font-weight: 400;
        color: #1A4A75;
        margin-top: 25px;
        margin-bottom: 15px;
        text-decoration: none;
    }
    [data-testid="stMetric"] {
        text-align: center;
        background-color: #f8f9fa;
        padding: 12px;
        border-radius: 6px;
        border: 1px solid #e9ecef;
    }
    [data-testid="stMetricValue"] {
        font-size: 1.3rem !important;
        font-weight: 600 !important;
        color: #1A4A75 !important;
        text-align: center !important;
    }
    [data-testid="stMetricLabel"] {
        font-size: 0.9rem !important;
        display: flex;
        justify-content: center !important;
        text-align: center !important;
        width: 100%;
    }
    </style>
""", unsafe_allow_html=True)

from auth import USER, PASSWORD, DSN

def carregar_dados_logs():
    sql = """
        SELECT id, pipeline_exec_id, script_nome, tentativa, status, mensagem_retorno, registros_processados, 
               (data_execucao - INTERVAL '3' HOUR) AS data_execucao
        FROM RM568906.CS_PIPELINE_LOGS
        ORDER BY data_execucao DESC
    """
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()
        cursor.execute(sql)
        colunas = [col[0].lower() for col in cursor.description]
        
        dados_brutos = cursor.fetchall()
        dados_limpos = []
        for linha in dados_brutos:
            linha_convertida = []
            for item in linha:
                if hasattr(item, "read"):
                    linha_convertida.append(str(item.read()))
                else:
                    linha_convertida.append(item)
            dados_limpos.append(linha_convertida)
            
        cursor.close()
        connection.close()
        
        df = pd.DataFrame(dados_limpos, columns=colunas)
        if not df.empty and 'mensagem_retorno' in df.columns:
            df['mensagem_retorno'] = df['mensagem_retorno'].astype(str)
        return df
    except Exception as e:
        return pd.DataFrame()

# ==========================================
# FAIXA DE MONITORAMENTO 100% DE LARGURA
# ==========================================
st.markdown("""
    <div class="admin-title-container">
        <h1>Monitoramento</h1>
        <p>Acompanhamento de serviços e inteligência preditiva.</p>
    </div>
""", unsafe_allow_html=True)

# ==========================================
# SEÇÃO 1: SERVIÇOS
# ==========================================
st.markdown('<div class="secao-destaque">Serviços</div>', unsafe_allow_html=True)

df_logs = carregar_dados_logs()

if not df_logs.empty:
    df_logs['data_execucao'] = pd.to_datetime(df_logs['data_execucao'])
    
    total_execucoes = df_logs['pipeline_exec_id'].nunique()
    total_erros = len(df_logs[df_logs['status'] != 'SUCESSO'])
    falhas_definitivas = df_logs[(df_logs['tentativa'] == 2) & (df_logs['status'] != 'SUCESSO')]

    kpi1, kpi2, kpi3 = st.columns(3)
    kpi1.metric("Total de Lotes Executados", total_execucoes)
    kpi2.metric("Total de Registros de Erro", total_erros)
    kpi3.metric("Falhas Definitivas", len(falhas_definitivas))

    col_titulo_exec, col_vazia_centro, col_btn1, col_btn2 = st.columns([4, 2, 2, 2])

    with col_titulo_exec:
        st.markdown('<div class="subsecao-negrito">Última Execução</div>', unsafe_allow_html=True)

    with col_btn1:
        st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
        if st.button("Executar", use_container_width=True, key="btn_executar_pipeline"):
            if job_pipeline is not None:
                with st.spinner("🔄 Executando o pipeline..."):
                    job_pipeline()
                    st.rerun()
            else:
                st.error("Função do pipeline não encontrada.")

    with col_btn2:
        st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
        if st.button("Atualizar", use_container_width=True, key="btn_atualizar_tela"):
            st.rerun()
    
    sql_status_servicos = """
        SELECT 
            SCRIPT_NOME AS servico,
            MAX(DATA_EXECUCAO - INTERVAL '3' HOUR) AS ultima_execucao,
            SUM(REGISTROS_PROCESSADOS) AS total_registros,
            MAX(STATUS) AS status,
            MAX(TO_CHAR(MENSAGEM_RETORNO)) AS mensagem
        FROM RM568906.CS_PIPELINE_LOGS
        GROUP BY SCRIPT_NOME
        ORDER BY ultima_execucao DESC
    """
    df_servicos = pd.DataFrame()
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()
        cursor.execute(sql_status_servicos)
        colunas_srv = [col[0].lower() for col in cursor.description]
        
        dados_srv_brutos = cursor.fetchall()
        dados_srv_limpos = []
        for linha in dados_srv_brutos:
            linha_convertida = []
            for item in linha:
                if hasattr(item, "read"):
                    linha_convertida.append(str(item.read()))
                else:
                    linha_convertida.append(item)
            dados_srv_limpos.append(linha_convertida)
            
        cursor.close()
        connection.close()
        
        df_servicos = pd.DataFrame(dados_srv_limpos, columns=colunas_srv)
        if not df_servicos.empty and 'mensagem' in df_servicos.columns:
            df_servicos['mensagem'] = df_servicos['mensagem'].astype(str)
    except Exception as e:
        df_servicos = pd.DataFrame()

    if not df_servicos.empty:
        df_servicos['ultima_execucao'] = pd.to_datetime(df_servicos['ultima_execucao'])
        
        def aplicar_farol(status):
            s = str(status).upper()
            if "SUCESSO" in s:
                return "SUCESSO"
            elif "ALERTA" in s:
                return "ATENÇÃO"
            else:
                return "CRÍTICO"

        df_servicos['farol_status'] = df_servicos['status'].apply(aplicar_farol)
        df_servicos['data_formatada'] = df_servicos['ultima_execucao'].dt.strftime('%d/%m/%Y às %H:%M:%S')

        df_exibicao = df_servicos[['servico', 'data_formatada', 'total_registros', 'farol_status', 'mensagem']].copy()
        df_exibicao.columns = ['Serviço / Script', 'Última Integração (Brasília)', 'Qtd Registros', 'Status (Farol)', 'Mensagem de Retorno']
        
        def colorir_farol(val):
            if val == "SUCESSO":
                return "background-color: #C8E6C9; color: #1b5e20; font-weight: bold;"
            elif "ATENÇÃO" in val:
                return "background-color: #FFE0B2; color: #e65100; font-weight: bold;"
            else:
                return "background-color: #FFCDD2; color: #b71c1c; font-weight: bold;"

        st.dataframe(df_exibicao.style.map(colorir_farol, subset=['Status (Farol)']), width='stretch')
    else:
        st.info("Nenhum resumo de serviço disponível.")

    st.markdown('<div class="subsecao-negrito">Detalhamento das Execuções</div>', unsafe_allow_html=True)

    f1, f2, f3 = st.columns(3)
    with f1:
        status_filtro = st.selectbox("Filtrar por Status", ["Todos"] + list(df_logs['status'].unique()))
    with f2:
        script_filtro = st.selectbox("Filtrar por Script", ["Todos"] + list(df_logs['script_nome'].unique()))
    with f3:
        lote_filtro = st.text_input("Filtrar por ID do Lote")

    df_filtrado = df_logs.copy()
    if status_filtro != "Todos":
        df_filtrado = df_filtrado[df_filtrado['status'] == status_filtro]
    if script_filtro != "Todos":
        df_filtrado = df_filtrado[df_filtrado['script_nome'] == script_filtro]
    if lote_filtro:
        df_filtrado = df_filtrado[df_filtrado['pipeline_exec_id'].str.contains(lote_filtro, case=False, na=False)]

    if not df_filtrado.empty:
        df_filtrado['data_execucao_fmt'] = df_filtrado['data_execucao'].dt.strftime('%d/%m/%Y às %H:%M:%S')
        df_filtrado['status_fmt'] = df_filtrado['status'].apply(aplicar_farol)

        colunas_ordenadas = [
            'script_nome', 'data_execucao_fmt', 'registros_processados', 
            'status_fmt', 'mensagem_retorno', 'id', 'pipeline_exec_id', 'tentativa'
        ]
        df_tabela_historico = df_filtrado[[c for c in colunas_ordenadas if c in df_filtrado.columns]].copy()
        
        renomear_colunas = {
            'script_nome': 'Serviço / Script',
            'data_execucao_fmt': 'Última Execução',
            'registros_processados': 'Qtd Registros',
            'status_fmt': 'Status (Farol)',
            'mensagem_retorno': 'Mensagem de Retorno',
            'id': 'ID do Log',
            'pipeline_exec_id': 'ID do Lote',
            'tentativa': 'Tentativa'
        }
        df_tabela_historico = df_tabela_historico.rename(columns=renomear_colunas)

        st.dataframe(df_tabela_historico.style.map(colorir_farol, subset=['Status (Farol)']), width='stretch')
    else:
        st.info("Nenhum registro encontrado para os filtros selecionados.")
else:
    st.warning("⚠️ Nenhum registro encontrado na tabela `CS_PIPELINE_LOGS` do Oracle.")

# ==========================================
# SEÇÃO 2: MODELOS PREDITIVOS (NÍVEL 2)
# ==========================================
st.markdown("<hr style='border:0.5px solid #d1d5db; margin: 50px 0;'>", unsafe_allow_html=True)
st.markdown('<div class="secao-destaque">Modelos Preditivos</div>', unsafe_allow_html=True)

# ------------------------------------------
# SUBTÓPICO 3.1: ALERTA QUEIMADAS
# ------------------------------------------
st.markdown('<div class="subsecao-negrito">Alerta Queimadas</div>', unsafe_allow_html=True)
st.markdown("Execute a validação qualitativa do modelo preditivo de queimadas baseada nos dados oficiais e clima.")

col_vazia_q, col_btn_q1, col_btn_q2 = st.columns([6, 2, 2])

with col_btn_q1:
    executar_modelo_q = st.button("Executar", use_container_width=True, key="btn_executar_queimadas")

with col_btn_q2:
    if st.button("Cancelar", use_container_width=True, key="btn_cancelar_queimadas"):
        st.session_state.pop('resultados_queimadas', None)
        st.session_state.pop('executando_queimadas', None)
        st.rerun()

if executar_modelo_q:
    if rodar_treinamento_queimadas is not None:
        st.session_state['executando_queimadas'] = True
        
        barra_progresso_q = st.progress(0)
        status_texto_q = st.empty()
        
        def callback_streamlit_q(porcentagem, etapa, descricao):
            barra_progresso_q.progress(porcentagem)
            status_texto_q.markdown(f"**Etapa:** {etapa} ({porcentagem}%) &mdash; {descricao}")

        try:
            resultados_q = rodar_treinamento_queimadas(progress_callback=callback_streamlit_q)
            st.session_state['resultados_queimadas'] = resultados_q
            st.session_state.pop('executando_queimadas', None)
            
            barra_progresso_q.empty()
            status_texto_q.empty()
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao executar o modelo de queimadas: {e}")
            st.session_state.pop('executando_queimadas', None)
            barra_progresso_q.empty()
            status_texto_q.empty()
    else:
        st.error("Módulo do modelo preditivo de queimadas (`modelos/ml_queimadas.py`) não foi encontrado.")

if 'resultados_queimadas' in st.session_state:
    res_q = st.session_state['resultados_queimadas']
    if res_q is None:
        st.warning("A consulta não retornou registros suficientes para o modelo de queimadas.")
    else:
        st.success(f"Modelo de queimadas avaliado com sucesso! Total de registros: {res_q['total_registros']:,}".replace(',', '.'))

        mq1, mq2, mq3 = st.columns(3)
        mq1.metric("Acurácia Geral", f"{res_q['acuracia']*100:.2f}%")
        mq2.metric("AUC-ROC Score", f"{res_q['auc_score']:.4f}")
        mq3.metric("F1-Score (Queimadas)", f"{res_q['report_dict'].get('1', {}).get('f1-score', 0):.4f}")

        st.markdown('<div class="subsecao-normal">Matriz de Confusão — Queimadas</div>', unsafe_allow_html=True)
        df_matriz_q = pd.DataFrame(
            res_q['matriz_conf'], 
            index=["Real: Normal (0)", "Real: Queimada (1)"], 
            columns=["Previsto: Normal (0)", "Previsto: Queimada (1)"]
        )
        st.dataframe(df_matriz_q, width='stretch')

        st.markdown('<div class="subsecao-normal">Análise Comparativa: Padrões de Mercado vs. Modelo de Queimadas</div>', unsafe_allow_html=True)
        report_q = res_q['report_dict']

        tabela_comparativa_q = {
            "Métrica de Avaliação": [
                "AUC-ROC Score", 
                "Recall (Sensitividade para Queimadas)", 
                "Precision (Precisão de Alertas)", 
                "F1-Score (Equilíbrio)"
            ],
            "Padrão Bom de Mercado": [
                "> 0.80 (Boa separação de risco)", 
                "> 0.70 (Captura a maioria dos eventos reais)", 
                "> 0.60 (Evita falsos alarmes excessivos)", 
                "> 0.65 (Boa harmonia geral)"
            ],
            "Nosso Modelo de Queimadas": [
                f"{res_q['auc_score']:.4f}",
                f"{report_q.get('1', {}).get('recall', 0):.4f}",
                f"{report_q.get('1', {}).get('precision', 0):.4f}",
                f"{report_q.get('1', {}).get('f1-score', 0):.4f}"
            ]
        }
        st.table(pd.DataFrame(tabela_comparativa_q))


# ------------------------------------------
# SUBTÓPICO 3.2: ALERTA HIDROLÓGICOS
# ------------------------------------------
st.markdown("<hr style='border:0.5px dashed #d1d5db; margin: 35px 0;'>", unsafe_allow_html=True)
st.markdown('<div class="subsecao-negrito">Alerta Hidrológicos</div>', unsafe_allow_html=True)
st.markdown("Execute a validação qualitativa do modelo preditivo de risco hidrológico e alagamentos.")

col_vazia_h, col_btn_h1, col_btn_h2 = st.columns([6, 2, 2])

with col_btn_h1:
    executar_modelo_h = st.button("Executar", use_container_width=True, key="btn_executar_hidrologicos")

with col_btn_h2:
    if st.button("Cancelar", use_container_width=True, key="btn_cancelar_hidrologicos"):
        st.session_state.pop('resultados_hidrologicos', None)
        st.session_state.pop('executando_hidrologicos', None)
        st.rerun()

if executar_modelo_h:
    if rodar_treinamento_hidrologico is not None:
        st.session_state['executando_hidrologicos'] = True
        
        barra_progresso_h = st.progress(0)
        status_texto_h = st.empty()
        
        def callback_streamlit_h(porcentagem, etapa, descricao):
            barra_progresso_h.progress(porcentagem)
            status_texto_h.markdown(f"**Etapa:** {etapa} ({porcentagem}%) &mdash; {descricao}")

        try:
            resultados_h = rodar_treinamento_hidrologico(progress_callback=callback_streamlit_h)
            st.session_state['resultados_hidrologicos'] = resultados_h
            st.session_state.pop('executando_hidrologicos', None)
            
            barra_progresso_h.empty()
            status_texto_h.empty()
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao executar o modelo hidrológico: {e}")
            st.session_state.pop('executando_hidrologicos', None)
            barra_progresso_h.empty()
            status_texto_h.empty()
    else:
        st.error("Módulo do modelo preditivo hidrológico (`modelos/ml_hidrologicos.py`) não foi encontrado.")

if 'resultados_hidrologicos' in st.session_state:
    res_h = st.session_state['resultados_hidrologicos']
    if res_h is None:
        st.warning("A consulta não retornou registros suficientes para o modelo hidrológico.")
    else:
        st.success(f"Modelo hidrológico avaliado com sucesso! Total de registros: {res_h['total_registros']:,}".replace(',', '.'))

        mh1, mh2, mh3 = st.columns(3)
        mh1.metric("Acurácia Geral", f"{res_h['acuracia']*100:.2f}%")
        mh2.metric("AUC-ROC Score", f"{res_h['auc_score']:.4f}")
        mh3.metric("F1-Score (Hidrológico)", f"{res_h['report_dict'].get('1', {}).get('f1-score', 0):.4f}")

        st.markdown('<div class="subsecao-normal">Matriz de Confusão — Hidrológico</div>', unsafe_allow_html=True)
        df_matriz_h = pd.DataFrame(
            res_h['matriz_conf'], 
            index=["Real: Normal (0)", "Real: Hidrológico (1)"], 
            columns=["Previsto: Normal (0)", "Previsto: Hidrológico (1)"]
        )
        st.dataframe(df_matriz_h, width='stretch')

        st.markdown('<div class="subsecao-normal">Análise Comparativa: Padrões de Mercado vs. Modelo Hidrológico</div>', unsafe_allow_html=True)
        report_h = res_h['report_dict']

        tabela_comparativa_h = {
            "Métrica de Avaliação": [
                "AUC-ROC Score", 
                "Recall (Sensitividade para Hidrológicos)", 
                "Precision (Precisão de Alertas)", 
                "F1-Score (Equilíbrio)"
            ],
            "Padrão Bom de Mercado": [
                "> 0.80 (Boa separação de risco)", 
                "> 0.70 (Captura a maioria dos eventos reais)", 
                "> 0.60 (Evita falsos alarmes excessivos)", 
                "> 0.65 (Boa harmonia geral)"
            ],
            "Nosso Modelo Hidrológico": [
                f"{res_h['auc_score']:.4f}",
                f"{report_h.get('1', {}).get('recall', 0):.4f}",
                f"{report_h.get('1', {}).get('precision', 0):.4f}",
                f"{report_h.get('1', {}).get('f1-score', 0):.4f}"
            ]
        }
        st.table(pd.DataFrame(tabela_comparativa_h))

# Rodapé institucional da página
try:
    from components import render_footer
    render_footer()
except ImportError:
    pass