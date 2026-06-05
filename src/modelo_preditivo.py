import streamlit as st
import pandas as pd
import plotly.express as px
import numpy as np
import oracledb
import os
from prophet import Prophet

# --- 1. CONFIGURAÇÃO DA PÁGINA E ESTILO ---
st.set_page_config(page_title="Campo Seguro - Score Risk", layout="wide")

# Aplicação do Guia de Estilo (CSS)
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@600&family=Roboto&display=swap');
    h1, h2, h3 { font-family: 'Montserrat', sans-serif !important; color: #1A4A75 !important; }
    p, div { font-family: 'Roboto', sans-serif !important; }
    </style>
    """, unsafe_allow_html=True)

# Exibição do Logo com caminho absoluto
logo_path = "/Users/nadiavieira/Desktop/Campo_Seguro_Logo.png"
if os.path.exists(logo_path):
    st.image(logo_path, width=200)

st.title("🛡️ Módulo de Subscrição: Score Risk")

# --- 2. ETL (Oracle) ---
@st.cache_data
def carregar_dados():
    conn = oracledb.connect(user="rm568906", password="fiap26", dsn="oracle.fiap.com.br:1521/orcl")
    query = """
    SELECT UF, MUNICIPIO, REGISTRO AS DATA_EVENTO, COBRADE AS TIPO_EVENTO,
           DM_17, DM_18, DM_19, DM_20, DM_21, DM_22,
           PEPL_1, PEPL_2, PEPL_3, PEPL_4, PEPL_S5, PEPL_6, PEPL_7, PEPL_8, PEPL_9, PEPL_10, PEPL_11,
           PEPR_AGRICULTURA, PEPR_13, PEPR_14, PEPR_15, PEPR_16
    FROM CS_CLIMA_EVENTOS
    """
    df = pd.read_sql(query, conn)
    conn.close()
    df.columns = df.columns.str.upper()
    df['DATA_EVENTO'] = pd.to_datetime(df['DATA_EVENTO'])
    
    cols = ['DM_17', 'DM_18', 'DM_19', 'DM_20', 'DM_21', 'DM_22', 'PEPL_1', 'PEPL_2', 'PEPL_3', 'PEPL_4', 
            'PEPL_S5', 'PEPL_6', 'PEPL_7', 'PEPL_8', 'PEPL_9', 'PEPL_10', 'PEPL_11', 'PEPR_AGRICULTURA', 
            'PEPR_13', 'PEPR_14', 'PEPR_15', 'PEPR_16']
    for c in cols: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
    df['IMPACTO_TOTAL'] = df[cols].sum(axis=1)
    df['IMPACTO_AGRICULTURA'] = df['PEPR_AGRICULTURA']
    df['ORIGEM'] = 'Histórico'
    return df

df_completo = carregar_dados()

# --- 3. MOTOR ML ---
def gerar_predicao(df_hist):
    if len(df_hist) < 2: return pd.DataFrame()
    df_p = df_hist[['DATA_EVENTO', 'IMPACTO_TOTAL']].rename(columns={'DATA_EVENTO': 'ds', 'IMPACTO_TOTAL': 'y'})
    m = Prophet(yearly_seasonality=True, daily_seasonality=False)
    m.fit(df_p)
    fut = m.make_future_dataframe(periods=730)
    prev = m.predict(fut)
    eventos = prev[prev['ds'] > df_hist['DATA_EVENTO'].max()].nlargest(3, 'yhat')
    return pd.DataFrame({
        'UF': df_hist['UF'].iloc[0], 'MUNICIPIO': df_hist['MUNICIPIO'].iloc[0],
        'DATA_EVENTO': eventos['ds'], 'TIPO_EVENTO': 'Projeção ML',
        'IMPACTO_TOTAL': eventos['yhat'], 'ORIGEM': 'Predição (Prophet)'
    })

# --- 4. INTERFACE ---
uf = st.selectbox("Selecione a UF", sorted(df_completo['UF'].unique()))
mun = st.selectbox("Selecione o Município", sorted(df_completo[df_completo['UF'] == uf]['MUNICIPIO'].unique()))

df_hist = df_completo[(df_completo['UF'] == uf) & (df_completo['MUNICIPIO'] == mun)]
df_dash = pd.concat([df_hist, gerar_predicao(df_hist)], ignore_index=True)

# Cálculo Score (Exemplo)
score = 3.5 
# Cores Guia: Seguro #C8E6C9 | Atenção #FFF9C4 | Crítico #FFCDD2
cor = "#C8E6C9" if score >= 4 else ("#FFF9C4" if score >= 3 else "#FFCDD2")

st.markdown(f"""
    <div style="background-color: {cor}; padding: 20px; border-radius: 4px; text-align: center; border: 1px solid #1A4A75;">
        <h2 style="color: #1A4A75;">Score Risk: {score}</h2>
    </div>
""", unsafe_allow_html=True)

fig = px.scatter(df_dash, x="DATA_EVENTO", y="IMPACTO_TOTAL", color="ORIGEM", 
                 title="Histórico e Projeção de Risco",
                 color_discrete_map={'Histórico': '#1A4A75', 'Predição (Prophet)': '#388E3C'})
st.plotly_chart(fig, use_container_width=True)