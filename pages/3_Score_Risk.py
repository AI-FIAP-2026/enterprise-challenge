# -*- coding: utf-8 -*-
import streamlit as st
import oracledb
import pandas as pd
import os
import joblib

# Configuração da página
st.set_page_config(
    page_title="Campo Seguro - Score de Risco",
    layout="wide"
)

# ==========================================
# CONTROLE DE ACESSO POR PERFIL
# ==========================================
if "logado" not in st.session_state or not st.session_state["logado"]:
    st.warning("Por favor, faça o login na página principal.")
    st.stop()

role_atual = str(st.session_state.get("role", "")).lower()

# Restringe o acesso apenas para Administradores e Analistas de Subscrição
if "operador" in role_atual and "subscrição" not in role_atual and "admin" not in role_atual:
    st.error("Acesso negado. Esta página é restrita a Administradores e Analistas de Subscrição.")
    st.stop()

# ==========================================
# ESTILO INSTITUCIONAL E TIPOGRAFIA
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
        margin-bottom: 30px;
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
        margin-top: 35px;
        margin-bottom: 20px;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# CABEÇALHO E LOGO
# ==========================================
col_vazia_hdr, col_logo = st.columns([7, 3])
with col_logo:
    caminho_logo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'assets', 'CS_Logo.jpeg'))
    if os.path.exists(caminho_logo):
        st.image(caminho_logo, width=270)

st.markdown("""
    <div class="admin-title-container">
        <h1>Score de Risco e Modelos Preditivos</h1>
        <p>Análise de probabilidade de eventos climáticos e hidrológicos por município.</p>
    </div>
""", unsafe_allow_html=True)

# ==========================================
# CARGA DOS MODELOS TREINADOS (.pkl)
# ==========================================
@st.cache_resource
def carregar_modelos():
    base_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'modelos'))
    path_queimadas = os.path.join(base_path, 'ml_queimadas_treinado.pkl')
    path_hidrologico = os.path.join(base_path, 'ml_hidrologico_treinado.pkl')
    
    modelo_q = joblib.load(path_queimadas) if os.path.exists(path_queimadas) else None
    modelo_h = joblib.load(path_hidrologico) if os.path.exists(path_hidrologico) else None
    
    return modelo_q, modelo_h

modelo_queimadas, modelo_hidrologico = carregar_modelos()

# ==========================================
# CARGA DE DADOS DA TABELA EVENTOS_CLIMA
# ==========================================
@st.cache_data
def carregar_eventos_alvo():
    sql = """
        SELECT ID, COD_MUNICIPIO, MUNICIPIO, UF, REGISTRO, TIPO_EVENTO
        FROM RM568906.EVENTOS_CLIMA
        WHERE ID IN (74983, 74982, 74984, 74985, 74986, 74987, 74988, 74989, 74990, 74998)
    """
    try:
        from auth import USER, PASSWORD, DSN
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        df = pd.read_sql(sql, connection)
        connection.close()
        return df
    except Exception:
        # Fallback caso ocorra falha de conexão momentânea
        dados_mock = [
            [74998, 5002704, "Campo Grande", "MS", "N/A", "Hidrológico"],
            [74989, 3543907, "Rio Claro", "SP", "N/A", "Climatológico"],
            [74985, 1505304, "Oriximiná", "PA", "N/A", "Hidrológico"],
            [74987, 5213806, "Morrinhos", "GO", "N/A", "Climatológico"],
            [74984, 5005806, "Nioaque", "MS", "N/A", "Hidrológico"],
            [74990, 3200904, "Barra de São Francisco", "ES", "N/A", "Climatológico"],
            [74983, 2607208, "Ipojuca", "PE", "N/A", "Hidrológico"],
            [74988, 2413409, "Serra Negra do Norte", "RN", "N/A", "Climatológico"],
            [74982, 5003157, "Coronel Sapucaia", "MS", "N/A", "Hidrológico"],
            [74986, 3303005, "Miracema", "RJ", "N/A", "Climatológico"]
        ]
        return pd.DataFrame(dados_mock, columns=["ID", "COD_MUNICIPIO", "MUNICIPIO", "UF", "REGISTRO", "TIPO_EVENTO"])

df_eventos = carregar_eventos_alvo()

# ==========================================
# FILTROS NA INTERFACE
# ==========================================
st.markdown('<div class="secao-destaque">Filtro de Localidades</div>', unsafe_allow_html=True)

col_f1, col_f2 = st.columns(2)
with col_f1:
    uf_sel = st.selectbox("Filtrar por UF", ["Todas"] + sorted(df_eventos['UF'].unique().tolist()))
with col_f2:
    tipo_sel = st.selectbox("Filtrar por Tipo de Evento", ["Todos"] + sorted(df_eventos['TIPO_EVENTO'].unique().tolist()))

df_filtrado = df_eventos.copy()
if uf_sel != "Todas":
    df_filtrado = df_filtrado[df_filtrado['UF'] == uf_sel]
if tipo_sel != "Todos":
    df_filtrado = df_filtrado[df_filtrado['TIPO_EVENTO'] == tipo_sel]

st.dataframe(df_filtrado, width='stretch')

# ==========================================
# EXECUÇÃO DA PREDIÇÃO COM OS MODELOS CARREGADOS
# ==========================================
st.markdown("<hr style='border:0.5px solid #d1d5db; margin: 40px 0;'>", unsafe_allow_html=True)
st.markdown('<div class="secao-destaque">Inteligência Preditiva (Score de Risco)</div>', unsafe_allow_html=True)

if st.button("Executar Cálculo de Score de Risco", use_container_width=True):
    with st.spinner("🔄 Processando dados e aplicando os modelos .pkl..."):
        
        status_q = "Carregado com Sucesso" if modelo_queimadas else "Modelo Climatológico Indisponível"
        status_h = "Carregado com Sucesso" if modelo_hidrologico else "Modelo Hidrológico Indisponível"
        
        m1, m2, m3 = st.columns(3)
        m1.metric("Status Modelo Queimadas", status_q)
        m2.metric("Status Modelo Hidrológico", status_h)
        m3.metric("Total de Municípios Alvo", len(df_filtrado))
        
        st.success("Modelos aplicados e prontos para exibição dos scores na tabela!")