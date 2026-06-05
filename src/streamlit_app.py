import pandas as pd
import oracledb
import plotly.express as px
import streamlit as st
from datetime import datetime

st.set_page_config(layout="wide", page_title="Campo Seguro")

ORACLE_USER = st.secrets["ORACLE_USER"]
ORACLE_PASSWORD = st.secrets["ORACLE_PASSWORD"]
ORACLE_DSN = st.secrets["ORACLE_DSN"]

has_auth = "auth" in st.secrets and "google" in st.secrets["auth"]

if has_auth:
    if not st.user.get("is_logged_in", False):
        st.title("🌾 Campo Seguro")
        st.markdown("Faça login com sua conta Google para acessar o sistema.")
        st.login(provider="google")
        st.stop()
    else:
        user_email = st.user.get("email", "Usuário")
        st.sidebar.markdown(f"👤 **{user_email}**")
        if st.sidebar.button("Sair"):
            st.logout()

# ─── Style Guide ───

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Montserrat:wght@600;700&family=Roboto:wght@400;500&display=swap');

    body, p, li, .stMarkdown, .stSelectbox, div.stText {
        font-family: 'Roboto', sans-serif;
        font-size: 16px;
    }
    h1, h2, h3 {
        font-family: 'Montserrat', sans-serif !important;
        color: #1A4A75 !important;
    }
    h1 { font-size: 48px !important; font-weight: 700 !important; }
    h2 { font-size: 32px !important; font-weight: 600 !important; }
    h3 { font-size: 24px !important; font-weight: 600 !important; }
    .caption {
        font-family: 'Roboto', sans-serif;
        font-size: 12px;
        color: #666;
    }
    .stButton > button {
        background-color: #388E3C;
        color: white;
        border-radius: 4px;
        border: none;
        font-family: 'Roboto', sans-serif;
    }
    .alert-badge {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)

PASTEL_COLORS = {
    "Moderado": {"bg": "#FFF9C4", "fg": "#856404"},
    "Alto": {"bg": "#FFE0B2", "fg": "#8D4E00"},
    "Crítico": {"bg": "#FFCDD2", "fg": "#9B1D20"},
}

# ─── Data ───

@st.cache_data(ttl=300)
def load_farms_climate():
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)

    farms = pd.read_sql("""
        SELECT f.ID, f.NOME_FAZENDA, f.ESTADO, f.MUNICIPIO,
               f.TAMANHO, f.ID_CLIENTE,
               c.RAZAO_SOCIAL, c.FIDELIDADE
        FROM CS_FAZENDAS f
        JOIN CS_CLIENTES c ON f.ID_CLIENTE = c.ID
        ORDER BY f.NOME_FAZENDA
    """, conn)
    farms.columns = [c.lower() for c in farms.columns]

    climate = pd.read_sql("""
        SELECT c.ID_FAZENDA, c.TEMPERATURA, c.UMIDADE,
               c.VELOCIDADE_VENTO, c.DATA_HORA,
               f.ID_CLIENTE
        FROM CS_CLIMA c
        JOIN CS_FAZENDAS f ON c.ID_FAZENDA = f.ID
        WHERE c.DATA_HORA >= SYSDATE - 30
        ORDER BY c.ID_FAZENDA, c.DATA_HORA
    """, conn)
    climate.columns = [c.lower() for c in climate.columns]

    conn.close()
    return farms, climate


@st.cache_data(ttl=300)
def load_events():
    conn = oracledb.connect(user=ORACLE_USER, password=ORACLE_PASSWORD, dsn=ORACLE_DSN)

    df = pd.read_sql("""
        SELECT UF, MUNICIPIO, COBRADE, REGISTRO, PEPR_AGRICULTURA,
               NVL(DM_17,0)+NVL(DM_18,0)+NVL(DM_19,0)+NVL(DM_20,0)+
               NVL(DM_21,0)+NVL(DM_22,0)+
               NVL(PEPL_1,0)+NVL(PEPL_2,0)+NVL(PEPL_3,0)+NVL(PEPL_4,0)+
               NVL(PEPL_S5,0)+NVL(PEPL_6,0)+NVL(PEPL_7,0)+NVL(PEPL_8,0)+
               NVL(PEPL_9,0)+NVL(PEPL_10,0)+NVL(PEPL_11,0)+
               NVL(PEPR_13,0)+NVL(PEPR_14,0)+NVL(PEPR_15,0)+NVL(PEPR_16,0)
               AS IMPACTO_TOTAL
        FROM CS_CLIMA_EVENTOS
        ORDER BY UF, MUNICIPIO, REGISTRO
    """, conn)
    df.columns = [c.lower() for c in df.columns]
    df["impacto_agricultura"] = pd.to_numeric(df["pepr_agricultura"], errors="coerce").fillna(0).clip(0, 1e9)
    df["registro"] = pd.to_datetime(df["registro"])

    conn.close()
    return df


# ─── US-002: Alert Classification ───

def classify_alerts(temp, humidity, wind, avg_humidity_6h):
    alerts = []
    if temp > 30 and humidity < 30 and wind > 30:
        alerts.append(("Incêndio Crítico", "Crítico"))
    if humidity < 12:
        alerts.append(("Incêndio Alto - Umidade Crítica", "Alto"))
    if temp > 40:
        alerts.append(("Incêndio Alto - Temperatura Extrema", "Alto"))
    if temp < 5 or temp > 30:
        alerts.append(("Operacional Alto - Temperatura", "Alto"))
    if wind > 60:
        alerts.append(("Operacional Alto - Ventania", "Alto"))
    if wind > 75:
        alerts.append(("Operacional Crítico - Ventania", "Crítico"))
    if avg_humidity_6h is not None and not pd.isna(avg_humidity_6h):
        if avg_humidity_6h >= 90:
            alerts.append(("Solo Crítico", "Crítico"))
        elif avg_humidity_6h >= 81:
            alerts.append(("Solo Alto", "Alto"))
        elif avg_humidity_6h >= 70:
            alerts.append(("Solo Moderado", "Moderado"))
    return alerts


def severity_pastel(severity):
    return PASTEL_COLORS.get(severity, {"bg": "#C8E6C9", "fg": "#1B5E20"})


# ─── US-001: Score Risk ───

def frequency_score(count):
    if count == 0:   return 5
    if count <= 2:   return 4
    if count <= 4:   return 3
    if count <= 6:   return 2
    return 1


def impact_score(total):
    if total <= 10e6:    return 5
    if total <= 50e6:    return 4
    if total <= 100e6:   return 3
    if total <= 1e9:     return 2
    return 1


def risk_color_5(score):
    if score >= 4.1:  return "#C8E6C9"
    if score >= 3.1:  return "#FFF9C4"
    if score >= 2.1:  return "#FFE0B2"
    return "#FFCDD2"


def risk_border_5(score):
    if score >= 4.1:  return "#4CAF50"
    if score >= 3.1:  return "#FFC107"
    if score >= 2.1:  return "#FB8C00"
    return "#E53935"


def risk_label_5(score):
    if score >= 4.1:  return "Baixo Risco"
    if score >= 3.1:  return "Risco Moderado"
    if score >= 2.1:  return "Risco Alto"
    return "Risco Crítico"


FREQ_LABEL = {5: "inexistente", 4: "pouco frequente", 3: "moderado", 2: "frequente", 1: "muito frequente"}
IMPACT_LABEL = {5: "insignificante", 4: "baixo", 3: "moderado", 2: "alto", 1: "crítico"}

# ─── Pie color mapping by severity ───

SEVERITY_PIE_COLORS = {
    "Moderado": "#FFF9C4",
    "Alto": "#FFE0B2",
    "Crítico": "#FFCDD2",
}

PIE_COLOR_SEQUENCE = ["#FFCDD2", "#FFE0B2", "#FFF9C4", "#C8E6C9", "#FFCCBC"]


def alert_pie_color(alert_type):
    if "Crítico" in alert_type: return "#FFCDD2"
    if "Alto" in alert_type: return "#FFE0B2"
    if "Moderado" in alert_type: return "#FFF9C4"
    return "#C8E6C9"


# ═══════════════════════════════════════════════════════════
# PAGE: Alertas (US-002)
# ═══════════════════════════════════════════════════════════
def page_alertas():
    farms, climate = load_farms_climate()

    climate = climate.sort_values(["id_fazenda", "data_hora"])
    climate["avg_humidity_6h"] = climate.groupby("id_fazenda")["umidade"].transform(
        lambda x: x.rolling(6, min_periods=1).mean()
    )

    alert_rows = []
    for _, row in climate.iterrows():
        for tipo, severidade in classify_alerts(
            row["temperatura"], row["umidade"], row["velocidade_vento"], row["avg_humidity_6h"]
        ):
            alert_rows.append({
                "id_fazenda": row["id_fazenda"],
                "id_cliente": row["id_cliente"],
                "temperatura": row["temperatura"],
                "umidade": row["umidade"],
                "velocidade_vento": row["velocidade_vento"],
                "tipo_alarme": tipo,
                "severidade": severidade,
                "data_hora": row["data_hora"],
            })
    alerts_df = pd.DataFrame(alert_rows)

    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    st.markdown(f'<p class="caption">🔄 Atualizado em {now_str} | Período: últimos 30 dias</p>',
                unsafe_allow_html=True)
    st.markdown('<h1>🔔 Alertas - Condições climáticas</h1>', unsafe_allow_html=True)

    col_filtro, _ = st.columns([2, 1])
    with col_filtro:
        st.markdown('<h3>Filtros</h3>', unsafe_allow_html=True)
        clientes = ["Todos"] + sorted(farms["razao_social"].unique().tolist())
        cliente_sel = st.selectbox("Cliente", clientes, label_visibility="collapsed")

    if cliente_sel != "Todos":
        client_farm_ids = farms[farms["razao_social"] == cliente_sel]["id"].tolist()
        client_alerts = alerts_df[alerts_df["id_fazenda"].isin(client_farm_ids)]
        client_label = cliente_sel
    else:
        client_alerts = alerts_df
        client_label = "todos os clientes"

    if client_alerts.empty:
        st.success("✅ Nenhuma ocorrência fora do padrão detectada nos últimos 30 dias.")
        return

    st.info(f"⚠️ **{len(client_alerts)}** ocorrências fora do padrão detectadas para **{client_label}** nos últimos 30 dias.")

    col1, col2 = st.columns(2)

    with col1:
        tipo_counts = client_alerts["tipo_alarme"].value_counts().reset_index()
        tipo_counts.columns = ["tipo_alarme", "quantidade"]
        tipo_counts["cor"] = tipo_counts["tipo_alarme"].apply(alert_pie_color)
        color_map = dict(zip(tipo_counts["tipo_alarme"], tipo_counts["cor"]))

        fig_pie = px.pie(
            tipo_counts, values="quantidade", names="tipo_alarme",
            color="tipo_alarme", color_discrete_map=color_map,
            title="Proporção por tipo de alerta", height=350,
        )
        fig_pie.update_traces(textposition="inside", textinfo="percent+label")
        fig_pie.update_layout(
            font_family="Roboto",
            title_font_family="Montserrat",
            title_font_size=24,
            title_font_color="#1A4A75",
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with col2:
        monthly = client_alerts.copy()
        monthly["mes"] = monthly["data_hora"].dt.to_period("M")
        mes_counts = monthly.groupby("mes").size().reset_index(name="quantidade")
        mes_counts["mes_label"] = mes_counts["mes"].astype(str)

        fig_mes = px.bar(
            mes_counts, x="mes_label", y="quantidade",
            title="Alertas por mês", height=350,
            color_discrete_sequence=["#FFE0B2"],
        )
        fig_mes.update_layout(
            font_family="Roboto",
            title_font_family="Montserrat",
            title_font_size=24,
            title_font_color="#1A4A75",
            xaxis_title="",
            yaxis_title="Quantidade",
        )
        fig_mes.update_traces(marker_line_width=0)
        st.plotly_chart(fig_mes, use_container_width=True)

    st.divider()
    st.markdown('<h3>Registros de ocorrências</h3>', unsafe_allow_html=True)

    display = client_alerts.merge(
        farms[["id", "nome_fazenda", "razao_social"]],
        left_on="id_fazenda", right_on="id", how="left"
    )
    table = display[["razao_social", "temperatura", "umidade",
                      "velocidade_vento", "tipo_alarme", "data_hora"]].copy()
    table.columns = ["Cliente", "Temp (°C)", "Umid (%)",
                     "Vento (km/h)", "Tipo Alarme", "Data/Hora"]
    st.dataframe(
        table.sort_values("Data/Hora", ascending=False),
        use_container_width=True, hide_index=True,
    )


# ═══════════════════════════════════════════════════════════
# PAGE: Score Risk (US-001)
# ═══════════════════════════════════════════════════════════
def page_scorerisk():
    now_str = datetime.now().strftime("%d/%m/%Y %H:%M")
    st.markdown(f'<p class="caption">🔄 Atualizado em {now_str} | Fonte: S2ID (Defesa Civil)</p>',
                unsafe_allow_html=True)
    st.markdown('<h1>📊 Score Risk - Exposição a eventos climáticos</h1>', unsafe_allow_html=True)

    events = load_events()
    if events.empty:
        st.error("Nenhum dado de eventos climáticos disponível.")
        return

    events["impacto_total"] = events["impacto_total"].fillna(0)

    col_filtro, col_score = st.columns([2, 1])

    with col_filtro:
        st.markdown('<h3>Filtros</h3>', unsafe_allow_html=True)
        ufs = ["Selecione"] + sorted(events["uf"].unique().tolist())
        uf_sel = st.selectbox("UF", ufs)

        if uf_sel != "Selecione":
            municipios = ["Selecione"] + sorted(
                events[events["uf"] == uf_sel]["municipio"].unique().tolist()
            )
            mun_sel = st.selectbox("Município", municipios)
        else:
            mun_sel = "Selecione"

    if uf_sel == "Selecione" or mun_sel == "Selecione":
        st.info("👈 Selecione uma UF e um município para visualizar a análise.")
        return

    mun_data = events[(events["uf"] == uf_sel) & (events["municipio"] == mun_sel)]

    if mun_data.empty:
        with col_score:
            st.markdown('<h3>Score risk</h3>', unsafe_allow_html=True)
            st.markdown(f"""
            <div style="text-align:center; padding:20px; border:2px solid #4CAF50; border-radius:15px;
                        background-color:#C8E6C9;">
                <p style="font-size:1em; margin:0;">Score de exposição</p>
                <p style="font-size:4em; font-weight:bold; color:#1B5E20; margin:0;">5.0</p>
                <p style="font-size:1.2em; color:#1B5E20; font-weight:bold;">Baixo risco</p>
                <p style="font-size:0.8em; color:gray;">Sem eventos registrados</p>
            </div>
            """, unsafe_allow_html=True)
        st.warning("Não há dados disponíveis para o município selecionado.")
        return

    num_events = len(mun_data)
    total_impact = mun_data["impacto_total"].sum()
    freq_pts = frequency_score(num_events)
    impact_pts = impact_score(total_impact)
    final_score = round((freq_pts + impact_pts) / 2, 1)
    bg_color = risk_color_5(final_score)
    border_color = risk_border_5(final_score)
    label = risk_label_5(final_score)
    fg_color = "#1B5E20" if final_score >= 4.1 else "#856404" if final_score >= 3.1 else "#8D4E00" if final_score >= 2.1 else "#9B1D20"

    st.markdown(f'<h3>📌 Eventos em {mun_sel}/{uf_sel}</h3>', unsafe_allow_html=True)

    mun_data_plot = mun_data.copy()
    mun_data_plot["tipo_resumido"] = mun_data_plot["cobrade"].str.split(" - ").str[1]
    mun_data_plot["impacto_agri_milhoes"] = mun_data_plot["impacto_agricultura"] / 1e6
    mun_data_plot["impacto_total_milhoes"] = mun_data_plot["impacto_total"] / 1e6
    mun_data_plot["marker_size"] = mun_data_plot["impacto_agri_milhoes"].clip(lower=2)

    fig = px.scatter(
        mun_data_plot, x="registro", y="impacto_total_milhoes",
        size="marker_size", color="tipo_resumido",
        hover_data={
            "cobrade": True,
            "impacto_agri_milhoes": ":.2f",
            "marker_size": False,
        },
        labels={
            "registro": "Data do evento",
            "impacto_total_milhoes": "Impacto econômico total (R$ milhões)",
            "impacto_agri_milhoes": "Impacto na agricultura (R$ milhões)",
            "tipo_resumido": "Tipo de evento",
            "cobrade": "Evento",
        },
        title=f"Eventos climáticos em {mun_sel}/{uf_sel}",
        height=500,
        color_discrete_sequence=PIE_COLOR_SEQUENCE,
    )
    fig.update_layout(
        font_family="Roboto",
        title_font_family="Montserrat",
        hoverlabel=dict(font_size=12),
        legend_title_text="Tipo de evento",
    )
    fig.update_traces(
        hovertemplate=(
            "<b>%{customdata[0]}</b><br>"
            "Data: %{x|%d/%m/%Y}<br>"
            "Impacto Total: R$ %{y:.2f}M<br>"
            "Impacto Agricultura: R$ %{marker.size:.2f}M<extra></extra>"
        )
    )
    fig.update_layout(
        font_family="Roboto",
        title_font_family="Montserrat",
        hoverlabel=dict(font_size=12),
    )
    st.plotly_chart(fig, use_container_width=True)

    col_info, col_score_display = st.columns([2, 1])

    with col_info:
        st.markdown('<h3>Resumo</h3>', unsafe_allow_html=True)
        st.markdown(f"""
        | Indicador | Valor |
        |-----------|-------|
        | **Total de eventos** | {num_events} |
        | **Impacto econômico total** | R$ {total_impact:,.2f} |
        | **Pontuação de frequência** | {freq_pts} ({FREQ_LABEL[freq_pts]}) |
        | **Pontuação de impacto** | {impact_pts} ({IMPACT_LABEL[impact_pts]}) |
        | **Tipos de eventos** | {mun_data["cobrade"].nunique()} |
        """)

    with col_score_display:
        st.markdown('<h3>Score risk</h3>', unsafe_allow_html=True)
        st.markdown(f"""
        <div style="text-align:center; padding:20px; border:2px solid {border_color}; border-radius:15px;
                    background-color:{bg_color};">
            <p style="font-size:1em; margin:0; color:{fg_color};">Score de exposição</p>
            <p style="font-size:4em; font-weight:bold; color:{fg_color}; margin:0;">{final_score}</p>
            <p style="font-size:1.2em; color:{fg_color}; font-weight:bold;">{label}</p>
            <p style="font-size:0.8em; color:gray;">Escala 1 (crítico) a 5 (baixo risco)</p>
        </div>
        """, unsafe_allow_html=True)

    st.divider()
    st.markdown('<h3>Registros de eventos</h3>', unsafe_allow_html=True)
    if not mun_data.empty:
        table = mun_data[["registro", "cobrade", "impacto_total", "impacto_agricultura"]].copy()
        table["registro"] = table["registro"].dt.date
        table.columns = ["Data", "Tipo de evento", "Impacto total (R$)", "Impacto agricultura (R$)"]
        st.dataframe(
            table.sort_values("Data", ascending=False),
            use_container_width=True, hide_index=True,
        )


# ═══════════════════════════════════════════════════════════
# Navigation
# ═══════════════════════════════════════════════════════════
alertas_page = st.Page(page_alertas, title="Alertas", icon="🔔", default=True)
scorerisk_page = st.Page(page_scorerisk, title="Score Risk", icon="📊")
pg = st.navigation([alertas_page, scorerisk_page])
pg.run()
