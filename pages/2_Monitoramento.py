# -*- coding: utf-8 -*-
import streamlit as st
import oracledb
import datetime
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
    from modelos.ml_alertas_queimadas_predicao import (rodar_treinamento_queimadas, gravar_regras_queimadas,
                                                       avaliar_previsoes_realizadas, carregar_regras_em_uso)
    from servicos.alertas_queimadas_predicao import (condicoes_da_regra, descrever_condicoes, ORIGEM_FOCO_RECENTE,
                                                     TEXTO_FOCO_RECENTE)
except ImportError:
    rodar_treinamento_queimadas = None
    avaliar_previsoes_realizadas = None
    carregar_regras_em_uso = None


# Configuração da página
st.set_page_config(
    page_title="Campo Seguro - Monitoramento",
    layout="wide"
)

# Somente usuários logados com perfil de administrador
from components import exigir_login, tabela, registrar_uso, nome_usuario, PERFIL_ADMIN
exigir_login([PERFIL_ADMIN])

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
        FROM CS_PIPELINE_LOGS
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
    
    # Filtros primeiro: o painel e a tabela mostram só o que foi filtrado
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


    # Painel: 4 indicadores dentro dos filtros
    kpi1, kpi2, kpi3, kpi4 = st.columns(4)
    kpi1.metric("Execuções do pipeline", df_filtrado['pipeline_exec_id'].nunique())
    kpi2.metric("Registros processados", f"{int(pd.to_numeric(df_filtrado['registros_processados'], errors='coerce').fillna(0).sum()):,}".replace(",", "."))
    kpi3.metric("Serviços com erro ou alerta", int((df_filtrado['status'] != 'SUCESSO').sum()))
    kpi4.metric("Última execução", df_filtrado['data_execucao'].max().strftime('%d/%m/%Y %H:%M')
                if not df_filtrado.empty else "—")

    col_vazia_centro, col_btn1, col_btn2 = st.columns([6, 2, 2])

    with col_btn1:
        st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
        if st.button("Executar", use_container_width=True, key="btn_executar_pipeline"):
            if job_pipeline is not None:
                registrar_uso("PIPELINE", "PIPELINE", None, "execução manual pelo Monitoramento")
                with st.spinner("Executando o pipeline..."):
                    job_pipeline()
                    st.rerun()
            else:
                st.error("Função do pipeline não encontrada.")

    with col_btn2:
        st.markdown("<div style='margin-top: 30px;'></div>", unsafe_allow_html=True)
        if st.button("Atualizar", use_container_width=True, key="btn_atualizar_tela"):
            st.rerun()
    
    def aplicar_farol(status):
        s = str(status).upper()
        if "SUCESSO" in s:
            return "SUCESSO"
        elif "ALERTA" in s:
            return "ATENÇÃO"
        else:
            return "CRÍTICO"

    def colorir_farol(val):
        if val == "SUCESSO":
            return "background-color: #C8E6C9; color: #1b5e20; font-weight: bold;"
        elif "ATENÇÃO" in val:
            return "background-color: #FFE0B2; color: #e65100; font-weight: bold;"
        else:
            return "background-color: #FFCDD2; color: #b71c1c; font-weight: bold;"

    st.markdown('<div class="subsecao-negrito">Detalhamento das Execuções</div>', unsafe_allow_html=True)


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

        tabela(df_tabela_historico.style.map(colorir_farol, subset=['Status (Farol)']))
    else:
        st.info("Nenhum registro encontrado para os filtros selecionados.")
else:
    st.warning("Nenhum registro encontrado no histórico de execuções do pipeline.")

# ==========================================
# SEÇÃO 2: MODELOS PREDITIVOS (NÍVEL 2)
# ==========================================
st.markdown("<hr style='border:0.5px solid #d1d5db; margin: 50px 0;'>", unsafe_allow_html=True)
st.markdown('<div class="secao-destaque">Modelos Preditivos</div>', unsafe_allow_html=True)

# ------------------------------------------
# SUBTÓPICO 3.1: ALERTA QUEIMADAS
# ------------------------------------------
st.markdown('<div class="subsecao-negrito">Alerta Queimadas</div>', unsafe_allow_html=True)
st.markdown("Gera as regras de previsão de queimadas a partir do histórico de focos do INPE e do clima das fazendas "
            "(\"condições semelhantes às que antecederam queimadas\") e valida as regras nos dias a partir de 2026, "
            "que não foram usados para gerá-las. As regras só são gravadas no banco ao clicar em **Gravar regras**.")

col_opcao_q, col_btn_q1, col_btn_q2 = st.columns([6, 2, 2])

METAS_DETECCAO = {
    "85% dos focos (recomendado)": 0.85,
    "90% dos focos": 0.90,
    "95% dos focos": 0.95,
    "80% dos focos": 0.80,
    "Sem meta (Alto a partir de 30% de chance)": 0,
}
EVENTOS_QUEIMADA = {
    "Foco a até 15 km da fazenda em 3 dias (mais específico)": (15, 3),
    "Foco a até 50 km da fazenda em 7 dias (mais abrangente)": (50, 7),
}
with col_opcao_q:
    evento_q = st.selectbox("Evento a prever", list(EVENTOS_QUEIMADA), key="queimadas_evento")
    meta_q = st.selectbox("Meta de detecção: focos que devem ter aviso Alto ou Crítico antes",
                          list(METAS_DETECCAO), key="queimadas_meta",
                          help="Define o limite de chance do nível Alto. Meta maior = menos focos perdidos e mais "
                               "alarmes falsos. O Crítico continua a partir de 50% de chance.")
    so_altos_q = st.checkbox("Somente regras de nível Alto e Crítico (priorizar o acerto dos alertas graves)",
                             key="queimadas_so_altos")

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
            raio_q, janela_q = EVENTOS_QUEIMADA[evento_q]
            resultados_q = rodar_treinamento_queimadas(progress_callback=callback_streamlit_q, so_altos=so_altos_q,
                                                       meta_deteccao=METAS_DETECCAO[meta_q],
                                                       raio_km=raio_q, janela_dias=janela_q)
            st.session_state['resultados_queimadas'] = resultados_q
            st.session_state.pop('executando_queimadas', None)
            registrar_uso("TREINO_MODELO", "MODELO", "Queimadas",
                          f"evento {evento_q}; meta {meta_q}" + ("; só Alto e Crítico" if so_altos_q else ""))

            barra_progresso_q.empty()
            status_texto_q.empty()
            st.rerun()
        except Exception as e:
            st.error(f"Erro ao executar o modelo de queimadas: {e}")
            st.session_state.pop('executando_queimadas', None)
            barra_progresso_q.empty()
            status_texto_q.empty()
    else:
        st.error("O modelo de previsão de queimadas não foi encontrado.")


COR_ACERTO, COR_ERRO = "#2a78d6", "#eb6834"      # paleta validada (daltonismo e contraste)
COR_REFERENCIA, COR_TEXTO = "#52514e", "#0b0b0b"
MINIMO_PROMETIDO = {"Crítico": 50, "Alto": 30, "Médio": 15}


def graficos_validacao_queimadas(val, limite_alto=30):
    """(1) acerto por nível x mínimo prometido x chance normal; (2) acertos e erros em dias com foco e com alerta."""
    import altair as alt

    ordem = ["Crítico", "Alto", "Médio", "Baixo"]
    prometido_nivel = dict(MINIMO_PROMETIDO, Alto=limite_alto)
    if limite_alto <= MINIMO_PROMETIDO["Médio"]:
        prometido_nivel.pop("Médio")
    linhas = []
    for n in val['por_nivel']:
        if not n['dias']:
            continue
        acerto = (n['acerto'] or 0) * 100
        linhas.append({
            "Nível": n['nivel'], "Aconteceu": round(acerto, 1),
            "Prometido": prometido_nivel.get(n['nivel']),
            "Rótulo": f"{acerto:.1f}%".replace(".", ","),
            "Dias": _milhar(n['dias']), "Dias com foco": _milhar(n['com_foco']),
            "Promete": (f"{_decimal(prometido_nivel[n['nivel']], 1)}% ou mais" if n['nivel'] in prometido_nivel
                        else f"menos de {_decimal(min(prometido_nivel.values()), 1)}%"),
        })
    df_nivel = pd.DataFrame(linhas)
    normal = val['taxa_normal'] * 100
    maximo = max([60.0] + [l["Aconteceu"] + 10 for l in linhas])
    eixo_y = alt.Y("Nível:N", sort=ordem, title=None, axis=alt.Axis(labelFontSize=13, ticks=False, domain=False))
    eixo_x = alt.X("Aconteceu:Q", title="% dos dias com foco", scale=alt.Scale(domain=[0, maximo]),
                   axis=alt.Axis(format=".0f", grid=True, gridOpacity=0.35, tickCount=6))
    dica = [alt.Tooltip("Nível:N"), alt.Tooltip("Rótulo:N", title="Aconteceu (dias com foco)"),
            alt.Tooltip("Promete:N"), alt.Tooltip("Dias:N", title="Dias previstos nesse nível"),
            alt.Tooltip("Dias com foco:N")]
    barras = alt.Chart(df_nivel).mark_bar(color=COR_ACERTO, cornerRadiusEnd=4, height=22).encode(
        y=eixo_y, x=eixo_x, tooltip=dica)
    rotulos = alt.Chart(df_nivel).mark_text(align="left", dx=6, fontSize=12, color=COR_TEXTO).encode(
        y=eixo_y, x="Aconteceu:Q", text="Rótulo:N", tooltip=dica)
    prometido = alt.Chart(df_nivel.dropna(subset=["Prometido"])).mark_tick(
        color=COR_REFERENCIA, thickness=3, size=30).encode(
        y=eixo_y, x="Prometido:Q",
        tooltip=[alt.Tooltip("Nível:N"), alt.Tooltip("Promete:N", title="Mínimo prometido")])
    df_normal = pd.DataFrame({"x": [normal], "texto": [f"chance normal {normal:.1f}%".replace(".", ",")]})
    linha_normal = alt.Chart(df_normal).mark_rule(color=COR_REFERENCIA, strokeDash=[4, 4], strokeWidth=1.5).encode(
        x="x:Q", tooltip=[alt.Tooltip("texto:N", title="Referência")])
    texto_normal = alt.Chart(df_normal).mark_text(align="left", dx=4, dy=-6, fontSize=11, color=COR_REFERENCIA,
                                                  baseline="bottom").encode(x="x:Q", y=alt.value(0), text="texto:N")
    grafico_nivel = (barras + prometido + rotulos + linha_normal + texto_normal).properties(height=220)

    alerta_foco = sum(n['com_foco'] for n in val['por_nivel'] if n['nivel'] in ("Alto", "Crítico"))
    alerta_dias = sum(n['dias'] for n in val['por_nivel'] if n['nivel'] in ("Alto", "Crítico"))
    focos = val['eventos_teste']
    grupos = [
        (f"Dias com foco ({_milhar(focos)})", focos, [
            ("Acerto", "Antecipados por aviso Alto ou Crítico", alerta_foco),
            ("Erro", "Sem alerta antes (não antecipados)", focos - alerta_foco)]),
        (f"Dias com aviso Alto/Crítico ({_milhar(alerta_dias)})", alerta_dias, [
            ("Acerto", "Seguidos de foco", alerta_foco),
            ("Erro", "Alarme falso (sem foco)", alerta_dias - alerta_foco)]),
    ]
    linhas2 = []
    for grupo, total, partes in grupos:
        inicio = 0.0
        for ordem_parte, (resultado, descricao, qtd) in enumerate(partes):
            pct = (qtd / total * 100) if total else 0
            meio, inicio = inicio + pct / 2, inicio + pct
            linhas2.append({"Grupo": grupo, "Resultado": resultado, "Descrição": descricao, "Ordem": ordem_parte,
                            "Meio": meio,
                            "Percentual": round(pct, 1), "Rótulo": f"{pct:.0f}%" if pct >= 12 else "",
                            "Texto %": f"{pct:.1f}%".replace(".", ","), "Dias": _milhar(qtd)})
    df_acertos = pd.DataFrame(linhas2)
    eixo_y2 = alt.Y("Grupo:N", title=None, sort=[g[0] for g in grupos],
                    axis=alt.Axis(labelFontSize=12, ticks=False, domain=False, labelLimit=220))
    cor = alt.Color("Resultado:N", scale=alt.Scale(domain=["Acerto", "Erro"], range=[COR_ACERTO, COR_ERRO]),
                    legend=alt.Legend(orient="top", title=None, labelFontSize=12))
    dica2 = [alt.Tooltip("Grupo:N"), alt.Tooltip("Descrição:N"), alt.Tooltip("Texto %:N", title="Percentual"),
             alt.Tooltip("Dias:N")]
    segmentos = alt.Chart(df_acertos).mark_bar(height=34, stroke="white", strokeWidth=2).encode(
        y=eixo_y2,
        x=alt.X("Percentual:Q", stack="zero", title="% do grupo", scale=alt.Scale(domain=[0, 100]),
                axis=alt.Axis(format=".0f", gridOpacity=0.35)),
        color=cor, order=alt.Order("Ordem:Q"), tooltip=dica2)
    textos = alt.Chart(df_acertos).mark_text(color="white", fontWeight="bold", fontSize=12).encode(
        y=eixo_y2, x=alt.X("Meio:Q"), text="Rótulo:N", tooltip=dica2)
    grafico_acertos = (segmentos + textos).properties(height=220)
    return grafico_nivel, grafico_acertos


def grafico_curva_deteccao(curva, limite_escolhido=None):
    import altair as alt
    linhas = []
    for c in curva:
        for serie, chave in (("Focos com aviso antes (POD)", "pod"), ("Alarmes falsos (FAR)", "far")):
            if c[chave] is not None:
                linhas.append({"Limite do Alto (%)": c["limite"], "Série": serie, "Valor": c[chave] * 100,
                               "Texto": f"{c[chave] * 100:.1f}%".replace(".", ","),
                               "Avisos por foco": f"{c['avisos_por_foco']:.1f}".replace(".", ","),
                               "Dias com aviso": f"{c['dias_com_aviso'] * 100:.0f}%"})
    df = pd.DataFrame(linhas)
    cores = alt.Scale(domain=["Focos com aviso antes (POD)", "Alarmes falsos (FAR)"], range=[COR_ACERTO, COR_ERRO])
    base = alt.Chart(df).encode(
        x=alt.X("Limite do Alto (%):Q", scale=alt.Scale(reverse=True), title="Limite de chance do Alto (%)",
                axis=alt.Axis(gridOpacity=0.3)),
        y=alt.Y("Valor:Q", title="%", scale=alt.Scale(domain=[0, 100]), axis=alt.Axis(gridOpacity=0.3)),
        color=alt.Color("Série:N", scale=cores, legend=alt.Legend(orient="top", title=None)),
        tooltip=["Limite do Alto (%):Q", "Série:N", alt.Tooltip("Texto:N", title="Valor"),
                 "Avisos por foco:N", "Dias com aviso:N"])
    grafico = base.mark_line(strokeWidth=2) + base.mark_point(filled=True, size=50)
    if limite_escolhido is not None:
        regua = alt.Chart(pd.DataFrame({"x": [limite_escolhido]})).mark_rule(
            color=COR_REFERENCIA, strokeDash=[4, 4]).encode(x="x:Q")
        grafico = grafico + regua
    return grafico.properties(height=280)


def _pct(valor):
    return "-" if valor is None else f"{valor * 100:.1f}%".replace(".", ",")


def _milhar(valor):
    return f"{valor:,}".replace(",", ".")


@st.cache_data(ttl=300, show_spinner=False)
def regras_em_uso():
    return carregar_regras_em_uso() if carregar_regras_em_uso else None


def _decimal(valor, casas=2):
    return "-" if valor is None else f"{valor:.{casas}f}".replace(".", ",")


PADROES_MERCADO = [
    # (métrica, chave, padrão bom, limite, formato)
    ("AUC-ROC (separação de risco)", "auc", "acima de 0,80 (boa separação de risco)", 0.80, "decimal"),
    ("Recall (focos que tiveram aviso antes)", "recall", "acima de 70% (captura a maioria dos eventos reais)", 0.70, "pct"),
    ("Precisão (avisos seguidos de foco)", "precisao", "acima de 60% (evita falsos alarmes excessivos)", 0.60, "pct"),
    ("F1-Score (equilíbrio entre recall e precisão)", "f1", "acima de 0,65 (boa harmonia geral)", 0.65, "decimal"),
    ("Especificidade (dias sem foco que ficaram sem aviso)", "especificidade", "acima de 80% (poucos alarmes em dias tranquilos)",
     0.80, "pct"),
]


# Verificação de previsões meteorológicas (tabela de contingência 2x2). Referências aproximadas, de avaliações
# publicadas de alertas de tempo severo e de índices de perigo de incêndio; conferir antes de citar formalmente.
# (métrica, chave, o que mede, referência do setor, faixa útil (mín, máx), melhor quando, formato)
METRICAS_SETOR = [
    ("POD - probabilidade de detecção", "pod", "Dos eventos, quantos tiveram aviso antes (= recall).",
     "Alertas de tempo severo: ~0,6 a 0,8", (0.6, None), "maior", "decimal"),
    ("FAR - razão de alarmes falsos", "far", "Dos avisos, quantos não tiveram o evento.",
     "Alertas de tornado: ~0,7; tempo severo: 0,4 a 0,7", (None, 0.75), "menor", "decimal"),
    ("CSI - índice de sucesso crítico", "csi", "Acertos / (acertos + alarmes falsos + eventos perdidos).",
     "Eventos raros e localizados: 0,2 a 0,4 é útil", (0.2, None), "maior", "decimal"),
    ("HSS - Heidke skill score", "hss", "Acerto acima do que se acertaria por acaso (0 = acaso, 1 = perfeito).",
     "Eventos raros: 0,2 a 0,4 útil; acima de 0,5 muito bom", (0.2, None), "maior", "decimal"),
    ("ETS - equitable threat score", "ets", "CSI descontando os acertos por acaso.",
     "Chuva forte e eventos raros: 0,1 a 0,3 é comum", (0.1, None), "maior", "decimal"),
    ("TSS - Peirce / Hanssen-Kuipers", "tss", "Detecção menos alarmes em dias sem evento (0 = sem habilidade).",
     "Índices de perigo de incêndio: acima de 0,4 é bom", (0.4, None), "maior", "decimal"),
    ("AUC-ROC", "auc", "Capacidade de ordenar os dias de mais e menos risco.",
     "Índices de perigo de incêndio (ex.: FWI): ~0,7 a 0,8", (0.7, None), "maior", "decimal"),
    ("Viés de frequência", "vies", "Avisos / eventos (1 = avisa na mesma frequência em que o evento ocorre).",
     "Sistemas de alerta costumam ficar entre 1 e 3 (preferem avisar a mais)", (1.0, 3.0), "perto de 1", "decimal"),
]


def situacao_setor(valor, faixa):
    minimo, maximo = faixa
    if valor is None:
        return "-"
    if minimo is not None and valor < minimo:
        return "Abaixo da referência" if maximo is None or minimo != 1.0 else "Avisa menos que o evento"
    if maximo is not None and valor > maximo:
        return "Pior que a referência" if minimo is None else "Avisa demais"
    return "Dentro da referência"


def secao_metricas_setor(m):
    """Comparativo com as métricas de verificação usadas por serviços meteorológicos."""
    st.markdown("**Comparativo com o setor de previsão meteorológica**")
    linhas = [{"Métrica": nome, "Nosso modelo": _decimal(m.get(chave)),
               "Situação": situacao_setor(m.get(chave), faixa), "Referência do setor": referencia,
               "O que mede": descricao}
              for nome, chave, descricao, referencia, faixa, _, _ in METRICAS_SETOR]
    tabela(pd.DataFrame(linhas))
    dentro = sum(1 for l in linhas if l["Situação"] == "Dentro da referência")
    st.caption(f"{dentro} de {len(linhas)} métricas dentro da referência do setor. Os serviços meteorológicos "
               "verificam alertas de eventos raros (tornado, granizo, enxurrada, incêndio) com POD, FAR, CSI e skill "
               "scores, e não com acurácia ou precisão: nesses eventos, avisar a mais é aceito para não perder o "
               "evento. As referências são aproximadas, de avaliações publicadas de alertas de tempo severo e de "
               "índices de perigo de incêndio; confira as fontes antes de citá-las em documento formal.")


def secao_metricas_tipicas(m, contexto, raio_km, janela_dias):
    """Validação com as métricas típicas de modelos preditivos: matriz de confusão, acurácia, AUC-ROC, F1,
    recall, precisão e especificidade, comparadas aos padrões de mercado."""
    st.markdown('<div class="subsecao-normal">Validação com métricas típicas de modelos preditivos</div>',
                unsafe_allow_html=True)
    st.caption(f"{contexto} Previsão positiva = aviso Alto ou Crítico; evento real = foco do INPE a até {raio_km} km "
               f"da fazenda em {janela_dias} dias.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Acurácia geral", _pct(m['acuracia']))
    c2.metric("AUC-ROC", _decimal(m['auc']))
    c3.metric("F1-Score", _decimal(m['f1']))
    c4.metric("Recall", _pct(m['recall']))

    col_matriz, _ = st.columns([2, 3])
    with col_matriz:
        st.markdown("**Matriz de confusão**")
        tabela(pd.DataFrame(
            [[_milhar(m['vn']), _milhar(m['fp'])], [_milhar(m['fn']), _milhar(m['vp'])]],
            index=["Real: sem foco", "Real: com foco"],
            columns=["Previsto: sem aviso", "Previsto: aviso"]), mostrar_indice=True)
        st.caption(f"Acertos: {_milhar(m['vp'])} avisos seguidos de foco e {_milhar(m['vn'])} dias tranquilos. "
                   f"Erros: {_milhar(m['fp'])} alarmes falsos e {_milhar(m['fn'])} focos sem aviso.")
    st.markdown("**Análise comparativa: padrões de mercado x modelo de queimadas**")
    linhas = []
    for nome, chave, padrao, limite, formato in PADROES_MERCADO:
        valor = m.get(chave)
        linhas.append({"Métrica de avaliação": nome, "Padrão bom de mercado": padrao,
                       "Nosso modelo": _decimal(valor) if formato == "decimal" else _pct(valor),
                       "Situação": "-" if valor is None else ("Atinge" if valor > limite else "Abaixo")})
    tabela(pd.DataFrame(linhas))
    if m.get('csi') is not None:
        secao_metricas_setor(m)
    st.caption(f"A acurácia geral engana em eventos raros: dizer sempre \"não vai ter foco\" daria "
               f"{_pct(m['acuracia_sempre_nao'])}. Por isso as métricas que importam são AUC-ROC, recall e precisão. "
               "Com só variáveis de clima, precisão acima de 60% para foco a até 15 km em 3 dias não é realista: "
               "o fogo depende também de ignição (queima de pasto, raios, ação humana).")


if 'resultados_queimadas' in st.session_state:
    res_q = st.session_state['resultados_queimadas']
    if res_q is None:
        st.warning("Não há histórico suficiente (clima das fazendas e focos do INPE) para gerar as regras de queimadas.")
    elif 'regras' not in res_q:
        st.session_state.pop('resultados_queimadas', None)  # resultado do modelo antigo (arquivado)
    else:
        val_q = res_q['validacao']
        st.success(f"{'Modo só Alto e Crítico | ' if res_q.get('so_altos') else ''}"
                   f"Regras geradas: {len(res_q['regras'])} | Histórico: {_milhar(res_q['total_registros'])} dias de fazenda "
                   f"(regras aprendidas com {res_q['periodo_treino']}; validação a partir de "
                   f"{res_q['inicio_teste']:%d/%m/%Y}). Evento: foco a até {res_q['raio_km']} km da fazenda no dia ou "
                   f"nos {res_q['janela_dias'] - 1} dias seguintes.")
        taxas_ano = res_q.get('taxas_por_ano') or {}
        excluidos = res_q.get('anos_excluidos') or []
        if taxas_ano:
            st.caption("Focos de agosto a outubro por ano: "
                       + " | ".join(f"{ano}: {_pct(taxa)}{' (ano extremo, fora do aprendizado)' if ano in excluidos else ''}"
                                    for ano, taxa in taxas_ano.items())
                       + (f". Chances das regras calculadas com os últimos 12 meses ({res_q['periodo_calibracao']})."
                          if res_q.get('periodo_calibracao') else ""))

        if val_q:
            m_val = val_q.get('metricas') or {}
            mq1, mq2, mq3, mq4 = st.columns(4)
            mq1.metric("Focos com aviso Alto/Crítico", _pct(m_val.get('pod')))
            mq2.metric("Alarmes falsos (FAR)", _pct(m_val.get('far')))
            mq3.metric("Avisos por foco real", _decimal(m_val.get('vies'), 1))
            mq4.metric("Alto a partir de", f"{_decimal(res_q.get('limite_alto_validacao') or 30, 1)}% de chance")
            if res_q.get('meta_deteccao'):
                st.caption(f"Meta de detecção: {_pct(res_q['meta_deteccao'])} dos focos com aviso Alto ou Crítico. "
                           f"Validação (2026): limite do Alto escolhido com os 12 meses anteriores "
                           f"({_decimal(res_q.get('limite_alto_validacao'), 1)}%). Regras geradas para gravar: Alto a "
                           f"partir de {_decimal(res_q.get('limite_alto'), 1)}% "
                           f"({_pct(res_q.get('deteccao_calibracao'))} dos focos com aviso nos últimos 12 meses). "
                           f"Inclui a regra de foco recente: foco do INPE a até "
                           f"{(res_q.get('foco_recente') or {}).get('raio_km', 50)} km nos 2 dias antes deixa o dia "
                           "no mínimo em Alto.")
            curva_q = res_q.get('curva_deteccao')
            if curva_q:
                st.markdown('<div class="subsecao-normal">Detecção x alarmes falsos por limite do Alto</div>',
                            unsafe_allow_html=True)
                st.caption("Dias a partir de 2026. Mostra quantos focos teriam aviso Alto ou Crítico antes e quantos "
                           "avisos seriam alarmes falsos com cada limite de chance para o Alto. Os dias sem nenhuma "
                           "regra ficam sem aviso em qualquer limite: é o teto de detecção das regras atuais.")
                st.altair_chart(grafico_curva_deteccao(curva_q, res_q.get('limite_alto_validacao')),
                                use_container_width=True)

            st.markdown('<div class="subsecao-normal">Acertos e erros na validação</div>', unsafe_allow_html=True)
            st.caption("Dias a partir de 2026, que não foram usados para gerar as regras. "
                       "Passe o mouse sobre as barras para ver as quantidades.")
            graf_calibracao, graf_acertos = graficos_validacao_queimadas(
                val_q, res_q.get('limite_alto_validacao') or 30)
            col_g1, col_g2 = st.columns(2)
            with col_g1:
                st.markdown("**O nível entrega o que promete?**")
                st.altair_chart(graf_calibracao, use_container_width=True)
                st.caption("Barra azul: % dos dias em que houve foco a até "
                           f"{res_q['raio_km']} km. Traço cinza: mínimo prometido pelo nível. "
                           "Linha tracejada: chance normal de foco.")
            with col_g2:
                st.markdown("**Acertos x erros**")
                st.altair_chart(graf_acertos, use_container_width=True)
                st.caption("Dias com foco: antecipados por aviso Alto ou Crítico (acerto) ou sem esse aviso (erro). "
                           "Dias com alerta: seguidos de foco (acerto) ou alarme falso (erro).")

            st.markdown('<div class="subsecao-normal">Validação por nível previsto</div>', unsafe_allow_html=True)
            st.caption(f"{_milhar(val_q['dias_teste'])} dias de fazenda não usados para gerar as regras; "
                       f"{_milhar(val_q['eventos_teste'])} tiveram foco (chance normal {_pct(val_q['taxa_normal'])}).")
            tabela(pd.DataFrame([{
                "Nível previsto": n['nivel'],
                "Dias": _milhar(n['dias']),
                "Dias com foco": _milhar(n['com_foco']),
                "Acerto": _pct(n['acerto']),
                "Focos antecipados": _pct(n['antecipados']),
            } for n in val_q['por_nivel']]))

            if val_q.get('metricas'):
                secao_metricas_tipicas(val_q['metricas'], "Dias a partir de 2026, não usados para gerar as regras.",
                                       res_q['raio_km'], res_q['janela_dias'])
        else:
            st.info("Ainda não há dias a partir de 2026 com clima completo para validar as regras.")

        col_vazia_g, col_btn_g = st.columns([8, 2])
        with col_btn_g:
            if st.button("Gravar regras", use_container_width=True, key="btn_gravar_regras_queimadas"):
                try:
                    qtd = gravar_regras_queimadas(res_q['regras'])
                    regras_em_uso.clear()
                    registrar_uso("GRAVACAO_MODELO", "MODELO", "Queimadas", f"{qtd} regra(s) gravada(s)")
                    st.success(f"{qtd} regra(s) gravada(s). O pipeline passa a usá-las na próxima execução.")
                except Exception as e:
                    st.error(f"Erro ao gravar as regras: {e}")


# ------------------------------------------
# SUBTÓPICO 3.1.1: REGRAS EM USO
# ------------------------------------------
st.markdown("<hr style='border:0.5px dashed #d1d5db; margin: 35px 0;'>", unsafe_allow_html=True)
st.markdown('<div class="subsecao-negrito">Regras de queimadas em uso</div>', unsafe_allow_html=True)
st.markdown("Regras que o pipeline usa todo dia para prever o risco de cada fazenda. "
            "Quando mais de uma regra vale para o mesmo dia, fica a de nível mais alto.")
try:
    regras_uso = regras_em_uso()
except Exception as e:
    regras_uso = None
    st.error(f"Erro ao ler as regras gravadas: {e}")
if regras_uso:
    geracao = max((r['DATA_GERACAO'] for r in regras_uso if r.get('DATA_GERACAO')), default=None)
    por_nivel = [f"{sum(1 for r in regras_uso if r['GRAU_RISCO'] == n)} {n}" for n in ("Crítico", "Alto", "Médio")
                 if any(r['GRAU_RISCO'] == n for r in regras_uso)]
    st.caption(f"{len(regras_uso)} regra(s): {', '.join(por_nivel)}"
               + (f" | geradas em {geracao:%d/%m/%Y}" if geracao else "")
               + ".")
    tabela(pd.DataFrame([{
        "UF": r['UF'] or ("Todas" if r['ORIGEM_REGRA'] in ("Regra 30-30-30", ORIGEM_FOCO_RECENTE) else "Demais UFs"),
        "Nível": r['GRAU_RISCO'],
        "Chance de foco": _pct(float(r['PROBABILIDADE']) / 100 if r['PROBABILIDADE'] is not None else None),
        "Dias observados": _milhar(int(r['CASOS'] or 0)),
        "Condições": (TEXTO_FOCO_RECENTE if r['ORIGEM_REGRA'] == ORIGEM_FOCO_RECENTE
                      else descrever_condicoes(condicoes_da_regra(r))),
        "Origem": r['ORIGEM_REGRA'],
        "Explicação": r.get('DESCRICAO_REGRA') or "",
    } for r in regras_uso]))
    if st.button("Atualizar regras", key="btn_atualizar_regras_uso"):
        regras_em_uso.clear()
        st.rerun()
elif regras_uso is not None:
    st.info("Nenhuma regra de queimada gravada ainda. Gere as regras acima (Executar) e clique em Gravar regras.")

# ------------------------------------------
# SUBTÓPICO 3.1.2: PREVISÕES JÁ FEITAS (aparece sozinha quando houver previsões conferidas)
# ------------------------------------------
def grafico_evolucao(diario):
    import altair as alt
    df = pd.DataFrame(diario)
    df["Data"] = pd.to_datetime(df["Data"])
    longo = df.melt("Data", ["Avisos", "Avisos com foco", "Dias com foco"], var_name="Série", value_name="Qtd")
    cores = alt.Scale(domain=["Avisos", "Avisos com foco", "Dias com foco"],
                      range=[COR_REFERENCIA, COR_ACERTO, COR_ERRO])
    return alt.Chart(longo).mark_line(point=len(df) <= 45, strokeWidth=2).encode(
        x=alt.X("Data:T", title=None, axis=alt.Axis(format="%d/%m", gridOpacity=0.3)),
        y=alt.Y("Qtd:Q", title="Fazendas", axis=alt.Axis(gridOpacity=0.3)),
        color=alt.Color("Série:N", scale=cores, legend=alt.Legend(orient="top", title=None)),
        tooltip=[alt.Tooltip("Data:T", format="%d/%m/%Y"), "Série:N", "Qtd:Q"],
    ).properties(height=320)


@st.cache_data(ttl=600, show_spinner=False)
def previsoes_conferidas_recentes():
    """Previsões já feitas pelo sistema nos últimos 90 dias, conferidas com os focos (sem botão)."""
    return avaliar_previsoes_realizadas(90) if avaliar_previsoes_realizadas else None


def mostrar_evolucao(diario, legenda):
    if diario:
        st.markdown("**Evolução diária**")
        st.altair_chart(grafico_evolucao(diario), use_container_width=True)
        st.caption(legenda)


# Previsões já feitas pelo sistema: aparecem sozinhas quando houver previsões conferidas
try:
    conferidas = previsoes_conferidas_recentes()
except Exception:
    conferidas = None
if conferidas and conferidas.get('previsoes'):
    st.markdown("<hr style='border:0.5px dashed #d1d5db; margin: 35px 0;'>", unsafe_allow_html=True)
    st.markdown('<div class="subsecao-negrito">Previsões já feitas pelo sistema (últimos 90 dias)</div>',
                unsafe_allow_html=True)
    st.caption(f"{conferidas['desde']:%d/%m/%Y} a {conferidas['ate']:%d/%m/%Y} | "
               f"{_milhar(conferidas['previsoes'])} previsões de {_milhar(conferidas['fazendas'])} fazenda(s) | "
               f"{_milhar(conferidas['avisos'])} aviso(s) Alto ou Crítico, conferidos com os focos que ocorreram")
    mp1, mp2, mp3, mp4 = st.columns(4)
    mp1.metric("Acerto dos avisos", _pct(conferidas['precisao']))
    mp2.metric("Focos avisados antes", _pct(conferidas['recall']))
    mp3.metric("Sem aviso e sem foco", _pct(conferidas['sem_foco_tranquilo']))
    sep = conferidas.get('separacao')
    mp4.metric("Com aviso x sem aviso", f"{sep:.1f}x".replace(".", ",") if sep not in (None, float("inf")) else "-")
    mostrar_evolucao(conferidas['diario'], "Por data prevista: fazendas com aviso Alto ou Crítico, avisos seguidos "
                                           "de foco e fazendas que tiveram foco perto.")
    if conferidas.get('metricas'):
        secao_metricas_tipicas(conferidas['metricas'], "Previsões já feitas pelo sistema, conferidas com os focos "
                               "que ocorreram.", conferidas['raio_km'], conferidas['janela_dias'])


# ==========================================
# ALERTAS DE CHUVA: HIDROLÓGICO E DESLIZAMENTO (modelos/ml_alertas_chuva_predicao.py)
# ==========================================
try:
    from modelos.ml_alertas_chuva_predicao import rodar_treinamento_chuva, gravar_regras_chuva, INICIO_TESTE
    from datetime import timedelta as _timedelta
    from requisitos import risco_chuva as rc_chuva
except ImportError:
    rodar_treinamento_chuva = None

st.markdown("<hr style='border:0.5px dashed #d1d5db; margin: 35px 0;'>", unsafe_allow_html=True)
st.markdown('<div class="subsecao-negrito">Alertas de chuva: hidrológico e deslizamento</div>', unsafe_allow_html=True)
st.markdown("Gera as regras de risco hidrológico (inundação, enxurrada, alagamento, chuva intensa) e de deslizamento "
            "a partir da chuva das fazendas e dos desastres registrados pela Defesa Civil e pelo CEMADEN nos municípios "
            "delas (\"chuva parecida com a que antecedeu eventos\"). Cada regra: chuva acumulada numa janela (dia, 72 "
            "horas, 7 ou 30 dias) e, no deslizamento, a declividade da fazenda. Nível pelo ganho sobre a chance normal: "
            "Crítico 8 vezes ou mais, Alto 3 vezes, Médio 1,5 vez. Ao executar, as regras novas substituem as atuais e "
            "alimentam a previsão diária (Central de Alertas) e a projeção de 1 ano do Score Risk.")
cc1, cc2, _ = st.columns([2, 2, 6])
executar_chuva = cc1.button("Executar", use_container_width=True, key="btn_executar_chuva")
if cc2.button("Cancelar", use_container_width=True, key="btn_cancelar_chuva"):
    st.session_state.pop("resultados_chuva", None)
    st.rerun()
if executar_chuva:
    if rodar_treinamento_chuva is None:
        st.error("O modelo de chuva não foi encontrado.")
    else:
        barra_c, texto_c = st.progress(0), st.empty()

        def _progresso_chuva(pct, etapa, descricao):
            barra_c.progress(pct)
            texto_c.markdown(f"**Etapa:** {etapa} ({pct}%) &mdash; {descricao}")
        try:
            st.session_state["resultados_chuva"] = rodar_treinamento_chuva(progress_callback=_progresso_chuva)
            registrar_uso("TREINO_MODELO", "MODELO", "Chuva", "hidrológico e deslizamento")
            # as regras novas substituem as atuais assim que o treino termina (previsão diária e Score Risk)
            qtd_chuva = gravar_regras_chuva(st.session_state["resultados_chuva"])
            registrar_uso("GRAVACAO_MODELO", "MODELO", "Chuva", f"{qtd_chuva} regra(s) gravada(s)")
            st.session_state["aviso_chuva"] = (f"{qtd_chuva} regra(s) gravada(s). A previsão diária e o Score Risk "
                                               "passam a usá-las.")
        except Exception as e:
            st.error(f"Erro ao executar o modelo de chuva: {e}")
        barra_c.empty()
        texto_c.empty()

def _pct_chance(valor):
    return rc_chuva.texto_chance(valor)


res_chuva = st.session_state.get("resultados_chuva")
if res_chuva:
    for tipo_c, r in res_chuva.items():
        st.markdown(f"**{tipo_c}**")
        periodo = r.get("periodo")
        v = r.get("validacao")
        # sem eventos dos dois lados da data de corte não há teste separado: a tela não menciona a validação
        _texto_validacao = (f"Validação: regras aprendidas até {INICIO_TESTE - _timedelta(days=1):%d/%m/%Y} e "
                            f"testadas depois ({_milhar(r['teste_eventos'])} dias com evento no teste)." if v else "")
        st.caption(f"{_milhar(r['dias'])} dias de fazenda"
                   + (f" ({periodo[0]:%d/%m/%Y} a {periodo[1]:%d/%m/%Y})" if periodo else "")
                   + f", {_milhar(r['eventos'])} com evento (chance normal {_pct_chance(r['taxa_normal'] * 100)})."
                   + (" " + _texto_validacao if _texto_validacao else ""))
        if v:
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Eventos com aviso Alto/Crítico", _pct(v["recall"]))
            k2.metric("Acerto dos avisos", _pct(v["precisao"]))
            k3.metric("Ganho sobre o acaso", f"{v['ganho']:.1f}x".replace(".", ",") if v["ganho"] else "-")
            k4.metric("Eventos com aviso Médio ou mais", _pct(v.get("recall_medio")))
            tabela([{"": "Com aviso Alto/Crítico", "Teve evento": _milhar(v["vp"]), "Não teve": _milhar(v["fp"])},
                    {"": "Sem aviso", "Teve evento": _milhar(v["fn"]), "Não teve": _milhar(v["vn"])}], altura=None)
            st.caption("Matriz de confusão nos dias de teste. Eventos de chuva são raros: o que importa é quantos "
                       "eventos tiveram aviso antes e quantas vezes o aviso é melhor que o acaso.")
        if r["regras"]:
            tabela([{"Nível": g["nivel"], "Regra": rc_chuva.descrever(g),
                     "Chance de evento": _pct_chance(g["chance"]),
                     "Ganho": f"{g['ganho']:.1f}x".replace(".", ","), "Dias observados": _milhar(g["casos"])}
                    for g in sorted(r["regras"], key=lambda g: -g["ganho"])], altura=360)
        else:
            st.warning("Nenhuma regra com casos e ganho suficientes. O score usa a regra de reserva.")
    _aviso_chuva = st.session_state.pop("aviso_chuva", None)
    if _aviso_chuva:
        st.success(_aviso_chuva)


# ==========================================
# REGISTRO DE USO (CS_LOG_ACOES): quem entrou, o que abriu e o que executou
# ==========================================
st.markdown('<div class="secao-destaque">Registro de uso</div>', unsafe_allow_html=True)
st.caption("Entradas e saídas do sistema, tentativas recusadas, acesso às páginas, execução do pipeline, treino e "
           "gravação dos modelos, cálculo do score, cadastro, comprovantes e validações. Quem fez aparece pela "
           "referência gravada (sem nome ou e-mail no registro) e é mostrado aqui pelo nome.")
try:
    from requisitos import auditoria as _auditoria
    from datetime import timedelta as _timedelta
    import oracledb as _oracledb
    from auth import USER as _U, PASSWORD as _P, DSN as _D

    @st.cache_data(ttl=60, show_spinner=False)
    def _registro_uso(inicio, fim):
        conn = _oracledb.connect(user=_U, password=_P, dsn=_D)
        try:
            return _auditoria.uso(conn, datetime.combine(inicio, datetime.min.time()),
                                  datetime.combine(fim + _timedelta(days=1), datetime.min.time()))
        finally:
            conn.close()

    _hoje_uso = datetime.now().date()
    _u1, _u2, _u3 = st.columns([2, 3, 3])
    _periodo_uso = _u1.date_input("Período", value=(_hoje_uso - _timedelta(days=7), _hoje_uso),
                                  max_value=_hoje_uso, format="DD/MM/YYYY", key="uso_periodo")
    _ini_uso, _fim_uso = (_periodo_uso if isinstance(_periodo_uso, (list, tuple)) and len(_periodo_uso) == 2
                          else (_hoje_uso - _timedelta(days=7), _hoje_uso))
    _linhas_uso = _registro_uso(_ini_uso, _fim_uso)
    _nomes_acao = _auditoria.ACOES_USO
    _acoes_disp = sorted({l["ACAO"] for l in _linhas_uso}, key=lambda a: _nomes_acao.get(a, a))
    _acoes_sel = _u2.multiselect("Ação", _acoes_disp, format_func=lambda a: _nomes_acao.get(a, a), key="uso_acoes")
    _quem_disp = sorted({nome_usuario(l["USUARIO"]) or "(sem usuário)" for l in _linhas_uso})
    _quem_sel = _u3.multiselect("Usuário", _quem_disp, key="uso_usuarios")
    _filtradas = [l for l in _linhas_uso if (not _acoes_sel or l["ACAO"] in _acoes_sel)
                  and (not _quem_sel or (nome_usuario(l["USUARIO"]) or "(sem usuário)") in _quem_sel)]
    _m1, _m2, _m3, _m4 = st.columns(4)
    _m1.metric("Ações registradas", len(_filtradas))
    _m2.metric("Usuários diferentes", len({l["USUARIO"] for l in _filtradas if l["USUARIO"]}))
    _m3.metric("Entradas no sistema", sum(1 for l in _filtradas if l["ACAO"] == "LOGIN"))
    _m4.metric("Entradas recusadas", sum(1 for l in _filtradas if l["ACAO"] == "LOGIN_RECUSADO"),
               help="Senha incorreta ou e-mail não cadastrado. Muitas tentativas seguidas podem indicar ataque.")
    if _filtradas:
        tabela([{"Quando": l["DATA_HORA"], "Usuário": nome_usuario(l["USUARIO"]) or "—", "Perfil": l["PERFIL"],
                 "Página": l["PAGINA"], "Ação": _nomes_acao.get(l["ACAO"], l["ACAO"]),
                 "Detalhe": " · ".join(str(v) for v in (l["CHAVE"], l["DETALHE"]) if v)} for l in _filtradas],
               altura=420)
    else:
        st.info("Nenhuma ação registrada no período e filtros escolhidos.")
except Exception as _erro_uso:
    st.warning(f"Registro de uso indisponível: {_erro_uso}")


# Rodapé institucional da página
try:
    from components import render_footer
    render_footer()
except ImportError:
    pass