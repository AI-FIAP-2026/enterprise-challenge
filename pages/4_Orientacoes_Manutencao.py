# -*- coding: utf-8 -*-
import os

import altair as alt
import oracledb
import pandas as pd
import streamlit as st

st.set_page_config(
    page_title="Campo Seguro - Orientações de Manutenção",
    layout="wide"
)

# ==========================================
# CONTROLE DE ACESSO POR PERFIL
# ==========================================
if "logado" not in st.session_state or not st.session_state["logado"]:
    st.warning("Por favor, faça o login na página principal.")
    st.stop()

# ==========================================
# ESTILO INSTITUCIONAL (mesmo padrão das outras páginas)
# ==========================================
st.markdown("""
    <style>
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
    </style>
""", unsafe_allow_html=True)

col_vazia_hdr, col_logo = st.columns([7, 3])
with col_logo:
    caminho_logo = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'assets', 'CS_Logo.jpeg'))
    if os.path.exists(caminho_logo):
        st.image(caminho_logo, width=270)

st.markdown("""
    <div class="admin-title-container">
        <h1>Orientações de Manutenção (NLP)</h1>
        <p>Extração determinística das tabelas de intervalo e limiares de sensor dos manuais dos equipamentos — sem LLM, com rastreabilidade de manual e página.</p>
    </div>
""", unsafe_allow_html=True)

BRAND_BLUE = "#1A4A75"
BRAND_GREEN = "#388e3c"

# ==========================================
# CARGA DE DADOS (CS_EQUIPAMENTOS_ORIENTACOES)
# ==========================================
@st.cache_data(ttl=300)
def carregar_orientacoes() -> pd.DataFrame:
    sql = """
        SELECT cod_fonte_manual, tipo_orientacao, subsistema, acao_tecnica,
               detalhamento_orientacao, metrica_gatilho, valor_gatilho,
               unidade_medida, fator_condicional, texto_bruto_original
        FROM CS_EQUIPAMENTOS_ORIENTACOES
        ORDER BY cod_fonte_manual, subsistema
    """
    from auth import USER, PASSWORD, DSN

    # cursor manual em vez de pd.read_sql(sql, connection) (padrão já usado em
    # pages/1_Alertas.py e pages/2_Monitoramento.py). IMPORTANTE: DETALHAMENTO_
    # ORIENTACAO e TEXTO_BRUTO_ORIGINAL são CLOB — o valor que volta no fetch é
    # só um "locator" preguiçoso; .read() tem que ser chamado com a conexão
    # ainda ABERTA, senão dá DPY-1001 (not connected to database).
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    cursor.execute(sql)
    colunas = [c[0].lower() for c in cursor.description]
    linhas = [
        tuple(v.read() if hasattr(v, "read") else v for v in linha)
        for linha in cursor.fetchall()
    ]
    cursor.close()
    connection.close()
    return pd.DataFrame(linhas, columns=colunas)


try:
    df = carregar_orientacoes()
except Exception as e:
    st.error(f"Não foi possível carregar as orientações do Oracle: {e}")
    st.stop()

if df.empty:
    st.info("Nenhuma orientação encontrada em CS_EQUIPAMENTOS_ORIENTACOES ainda.")
    st.stop()

# ==========================================
# FILTROS
# ==========================================
col_f1, col_f2, col_f3 = st.columns(3)
with col_f1:
    equipamentos = st.multiselect(
        "Equipamento (cod_fonte_manual)",
        options=sorted(df["cod_fonte_manual"].unique()),
        default=sorted(df["cod_fonte_manual"].unique()),
    )
with col_f2:
    tipos = st.multiselect(
        "Tipo de orientação",
        options=sorted(df["tipo_orientacao"].unique()),
        default=sorted(df["tipo_orientacao"].unique()),
    )
with col_f3:
    subsistemas = st.multiselect(
        "Subsistema",
        options=sorted(df["subsistema"].unique()),
        default=sorted(df["subsistema"].unique()),
    )

df_filtrado = df[
    df["cod_fonte_manual"].isin(equipamentos)
    & df["tipo_orientacao"].isin(tipos)
    & df["subsistema"].isin(subsistemas)
]

# ==========================================
# MÉTRICAS
# ==========================================
col_m1, col_m2, col_m3, col_m4 = st.columns(4)
col_m1.metric("Orientações (filtro atual)", len(df_filtrado))
col_m2.metric("Manutenção programada", int((df_filtrado["tipo_orientacao"] == "MANUTENCAO_PROGRAMADA").sum()))
col_m3.metric("Limiares de sensor", int((df_filtrado["tipo_orientacao"] == "LIMIAR_OPERACIONAL_SENSOR").sum()))
col_m4.metric("Equipamentos", df_filtrado["cod_fonte_manual"].nunique())

st.markdown('<div class="secao-destaque" style="font-size:1.3rem;font-weight:800;color:#1A4A75;margin-top:20px;">Orientações por subsistema</div>', unsafe_allow_html=True)

# ==========================================
# GRÁFICO — contagem por subsistema
# ==========================================
contagem_subsistema = (
    df_filtrado.groupby("subsistema").size().reset_index(name="qtd").sort_values("qtd", ascending=False)
)
if not contagem_subsistema.empty:
    grafico_subsistema = (
        alt.Chart(contagem_subsistema)
        .mark_bar(color=BRAND_BLUE, cornerRadiusEnd=3)
        .encode(
            x=alt.X("qtd:Q", title="Quantidade de orientações"),
            y=alt.Y("subsistema:N", sort="-x", title=None),
            tooltip=["subsistema", "qtd"],
        )
        .properties(height=28 * len(contagem_subsistema))
    )
    rotulos = grafico_subsistema.mark_text(align="left", dx=4, color="#333333").encode(text="qtd:Q")
    st.altair_chart(grafico_subsistema + rotulos, width="stretch")

col_g1, col_g2 = st.columns(2)
with col_g1:
    st.caption("Orientações por equipamento")
    contagem_equip = df_filtrado.groupby("cod_fonte_manual").size().reset_index(name="qtd")
    grafico_equip = (
        alt.Chart(contagem_equip)
        .mark_bar(color=BRAND_GREEN, cornerRadiusEnd=3)
        .encode(x=alt.X("cod_fonte_manual:N", title=None), y=alt.Y("qtd:Q", title="Quantidade"), tooltip=["cod_fonte_manual", "qtd"])
    )
    st.altair_chart(grafico_equip, width="stretch")

with col_g2:
    st.caption("Manutenção programada por faixa de horímetro")
    faixas = df_filtrado[df_filtrado["metrica_gatilho"] == "HORIMETRO"].copy()
    if not faixas.empty:
        contagem_faixa = faixas.groupby("valor_gatilho").size().reset_index(name="qtd").sort_values("valor_gatilho")
        grafico_faixa = (
            alt.Chart(contagem_faixa)
            .mark_bar(color=BRAND_BLUE, cornerRadiusEnd=3)
            .encode(
                x=alt.X("valor_gatilho:O", title="Horas"),
                y=alt.Y("qtd:Q", title="Quantidade"),
                tooltip=["valor_gatilho", "qtd"],
            )
        )
        st.altair_chart(grafico_faixa, width="stretch")
    else:
        st.caption("Sem itens por horímetro no filtro atual.")

# ==========================================
# TABELA DETALHADA
# ==========================================
st.markdown('<div style="font-size:1.3rem;font-weight:800;color:#1A4A75;margin-top:20px;">Detalhamento</div>', unsafe_allow_html=True)
st.dataframe(
    df_filtrado[[
        "cod_fonte_manual", "tipo_orientacao", "subsistema", "acao_tecnica",
        "detalhamento_orientacao", "metrica_gatilho", "valor_gatilho", "unidade_medida",
    ]],
    width="stretch",
    hide_index=True,
)

st.download_button(
    "Baixar CSV filtrado",
    data=df_filtrado.to_csv(index=False, sep=";").encode("utf-8-sig"),
    file_name="orientacoes_manutencao_filtrado.csv",
    mime="text/csv",
)
