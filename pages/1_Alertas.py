# -*- coding: utf-8 -*-
import streamlit as st
import oracledb
from datetime import datetime
from auth import USER, PASSWORD, DSN
from components import render_header, render_footer

st.set_page_config(page_title="Campo Seguro - Central de Alertas", layout="wide")

render_header()

CONFIG_RISCO = {
    "Mínimo": "#C8E6C9",
    "Baixo": "#FFF9C4",
    "Médio": "#FFE0B2",
    "Alto": "#FFCCBC",
    "Crítico": "#FFCDD2"
}

def carregar_alertas():
    """Carrega os alertas do Oracle aplicando filtro de CNPJ caso seja Operador."""
    try:
        conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = conn.cursor()
        
        role_atual = st.session_state.get("role", "").lower()
        cnpj_operador = st.session_state.get("cliente_cnpj")
        
        # Se for operador e possuir CNPJ vinculado, filtra apenas os alertas da fazenda dele
        if "operador" in role_atual and cnpj_operador:
            sql = """
                SELECT DISTINCT a.ID, a.TIPO_ALERTA, a.ORIGEM_ALERTA, a.DATA_HORA, a.CATEGORIA_RISCO, a.LATITUDE, a.LONGITUDE, a.ORIENTACAO, a.DETALHAMENTO_1, a.DETALHAMENTO_2 
                FROM CS_ALERTAS a
                JOIN CS_FAZENDAS f ON a.DETALHAMENTO_1 LIKE '%' || f.CODIGO_IBGE || '%'
                JOIN CS_CLIENTES c ON f.ID_CLIENTE = c.ID
                WHERE c.CNPJ = :1
                ORDER BY a.DATA_HORA DESC
            """
            cursor.execute(sql, [cnpj_operador])
        else:
            # Admin e Analista de Subscrição visualizam todos os alertas
            sql = """
                SELECT ID, TIPO_ALERTA, ORIGEM_ALERTA, DATA_HORA, CATEGORIA_RISCO, LATITUDE, LONGITUDE, ORIENTACAO, DETALHAMENTO_1, DETALHAMENTO_2 
                FROM CS_ALERTAS
                ORDER BY DATA_HORA DESC
            """
            cursor.execute(sql)
            
        colunas = [col[0] for col in cursor.description]
        dados = [dict(zip(colunas, linha)) for linha in cursor.fetchall()]
        cursor.close()
        conn.close()
        return dados
    except Exception as e:
        st.error(f"Erro ao carregar alertas do banco: {e}")
        return []

st.markdown("<h1 style='color: #1A4A75;'>🚨 Central de Alertas</h1>", unsafe_allow_html=True)

# Exibe aviso caso seja operador para dar clareza na interface
role_atual = st.session_state.get("role", "").lower()
if "operador" in role_atual:
    st.info(f"Visualizando alertas restritos à sua propriedade (CNPJ: {st.session_state.get('cliente_cnpj', 'Não informado')})")
else:
    st.markdown("Feed consolidado de monitoramento de ameaças integradas para a segurança da frota e lavoura.")

alertas = carregar_alertas()

if not alertas:
    st.info("Nenhum alerta registrado no momento para o seu perfil. Suas fazendas estão em segurança.")
else:
    st.sidebar.markdown("<h2>🔍 Filtros da Central</h2>", unsafe_allow_html=True)
    
    tipos_disponiveis = ["Todos"] + list(set(a["TIPO_ALERTA"] for a in alertas))
    filtro_tipo = st.sidebar.selectbox("Tipo de Alerta", tipos_disponiveis)
    
    riscos_disponiveis = ["Todos"] + list(set(a["CATEGORIA_RISCO"] for a in alertas))
    filtro_risco = st.sidebar.selectbox("Nível de Risco", riscos_disponiveis)
    
    st.sidebar.markdown("---")
    usar_filtro_data = st.sidebar.checkbox("Filtrar por Período")
    
    if usar_filtro_data:
        data_inicio = st.sidebar.date_input("Data Inicial", datetime.now().date())
        data_fim = st.sidebar.date_input("Data Final", datetime.now().date())

    alertas_filtrados = alertas
    
    if filtro_tipo != "Todos":
        alertas_filtrados = [a for a in alertas_filtrados if a["TIPO_ALERTA"] == filtro_tipo]
        
    if filtro_risco != "Todos":
        alertas_filtrados = [a for a in alertas_filtrados if a["CATEGORIA_RISCO"] == filtro_risco]
        
    if usar_filtro_data:
        alertas_filtrados = [
            a for a in alertas_filtrados 
            if isinstance(a["DATA_HORA"], datetime) and 
               data_inicio <= a["DATA_HORA"].date() <= data_fim
        ]

    st.markdown(f"Exibindo **{len(alertas_filtrados)}** alerta(s) encontrado(s).")
    st.markdown("---")

    if not alertas_filtrados:
        st.warning("Nenhum alerta corresponde aos filtros selecionados.")
    else:
        for alerta in alertas_filtrados:
            categoria = alerta["CATEGORIA_RISCO"]
            cor_card = CONFIG_RISCO.get(categoria, "#FCFCFC")
            
            data_br = alerta["DATA_HORA"]
            if isinstance(data_br, datetime):
                data_formatada = data_br.strftime("%d/%m/%y às %H:%M")
            else:
                data_formatada = str(data_br)
            
            det1 = alerta["DETALHAMENTO_1"] if alerta["DETALHAMENTO_1"] else ""
            det2 = alerta["DETALHAMENTO_2"] if alerta["DETALHAMENTO_2"] else ""
            orientacao = alerta["ORIENTACAO"] if alerta["ORIENTACAO"] else "Sem orientações específicas."
            
            st.markdown(
                f"""
                <div style="background-color: {cor_card}; padding: 20px; border-radius: 8px; margin-bottom: 15px; border-left: 6px solid #1A4A75;">
                    <h3 style="margin: 0; color: #1A4A75;">ALERTA — {alerta['TIPO_ALERTA']}</h3>
                    <p style="margin: 5px 0; font-size: 14px;"><b>Origem:</b> {alerta['ORIGEM_ALERTA']} | <b>Data/Hora:</b> {data_formatada}</p>
                    <p style="margin: 5px 0; font-size: 16px;"><b>NÍVEL DO RISCO:</b> {categoria.upper()}</p>
                    <hr style="border: 0; border-top: 1px solid rgba(0,0,0,0.1);">
                    <p style="margin: 0; font-size: 15px;"><b>Orientação Operacional:</b> {orientacao}</p>
                    <p style="margin: 5px 0 0 0; font-size: 13px; color: #333;"><b>Detalhes:</b> {det1} | {det2}</p>
                </div>
                """,
                unsafe_allow_html=True
            )

render_footer()