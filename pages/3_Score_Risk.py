# -*- coding: utf-8 -*-
import streamlit as st
import oracledb
import os

# Configuração da página
st.set_page_config(
    page_title="Campo Seguro - Score de Risco",
    layout="wide"
)

# Somente usuários logados com perfil de administrador ou Sompo (analista de subscrição)
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from components import exigir_login, tabela, registrar_uso, nome_usuario, PERFIL_ADMIN, PERFIL_SOMPO
exigir_login([PERFIL_ADMIN, PERFIL_SOMPO])

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
        <h1>Score de Risco</h1>
        <p>Risco de cada cliente e fazenda: exposição, manutenção, sinistros, ambiente e clima.</p>
    </div>
""", unsafe_allow_html=True)

# ==========================================
# SCORE DE RISCO DOS CLIENTES (matriz: docs/Score_risco_matriz.md, cálculo: requisitos/score_risco.py)
# ==========================================
import pandas as pd
import altair as alt
from requisitos import score_risco as sr
from requisitos import score_tendencias as st_hist
from requisitos import score_recomendacoes as rec
from requisitos import score_premio as premio
from requisitos import auditoria
from auth import USER as _USER, PASSWORD as _PASSWORD, DSN as _DSN


@st.cache_resource(show_spinner=False)
def pool_score():
    return oracledb.create_pool(user=_USER, password=_PASSWORD, dsn=_DSN, min=1, max=2, increment=1)


CORES_CLASSE = {"Baixo": "#2E7D32", "Médio": "#F9A825", "Alto": "#C62828"}

st.markdown('<div class="secao-destaque">Score Risk</div>', unsafe_allow_html=True)
st.caption("Score de 0 a 100 pontos por cliente e por fazenda (quanto maior, maior o risco), somando 8 itens do "
           "cliente, do ambiente e da operação.")

# 1) Regras do score (antes dos filtros), com a classificação em destaque no fim da tabela
with st.expander(f"Regras do score (matriz versão {sr.VERSAO_MATRIZ})"):
    tabela(sr.matriz(), altura=None)
    _cor_b, _cor_m, _cor_a = CORES_CLASSE["Baixo"], CORES_CLASSE["Médio"], CORES_CLASSE["Alto"]
    st.markdown(f"""
<div style="border:2px solid #1A4A75; border-radius:10px; padding:14px 18px; margin-top:10px; background:#F4F8FC;">
<div style="font-size:1.1rem; font-weight:800; color:#1A4A75; margin-bottom:6px;">Como o score é calculado e classificado</div>
<div>Cada item recebe nota 1, 2 ou 3 pelos critérios da tabela e soma pontos: <b>nota 1 soma 0</b>, <b>nota 2 soma
metade do peso</b> e <b>nota 3 soma o peso inteiro</b>. Como os pesos somam 100, o score vai de <b>0 (menor risco) a
100 (maior risco)</b>. Revisões feitas pela regra ideal do manual descontam pontos: {sr.texto_bonus()}.</div>
<div style="margin:10px 0 6px 0;">
<span style="background:{_cor_b}; color:#fff; padding:4px 12px; border-radius:14px; font-weight:700;">Baixo: abaixo de {sr.LIMITE_BAIXO:g}</span>&nbsp;
<span style="background:{_cor_m}; color:#222; padding:4px 12px; border-radius:14px; font-weight:700;">Médio: de {sr.LIMITE_BAIXO:g} a {sr.LIMITE_MEDIO:g}</span>&nbsp;
<span style="background:{_cor_a}; color:#fff; padding:4px 12px; border-radius:14px; font-weight:700;">Alto: acima de {sr.LIMITE_MEDIO:g}</span>
</div>
<div style="color:#444;">Item sem dados (cliente novo ou informação ainda não coletada) entra com <b>nota 2 (neutra)</b>
e aparece sinalizado: assim a falta de dado não reduz o risco. O score do cliente usa, nos itens de fazenda
(queimadas, hidrológico, climático e procedimentos), a média das fazendas ponderada pelo valor
segurado; o score de cada fazenda usa os itens dela. Cada cálculo guarda as regras e a medida de cada item, para que o
score continue explicável.</div>
</div>""", unsafe_allow_html=True)

# 2) Cálculo (acima dos filtros)
st.caption("O score é calculado automaticamente uma vez por dia. O botão recalcula na hora.")
if st.button("Calcular o score de todos os clientes agora", key="calcular_score"):
    _barra = st.progress(0.0, text="Calculando...")
    try:
        with pool_score().acquire() as _conn:
            _clientes_calc = sr.clientes_com_equipamentos(_conn)
            for _n, _c in enumerate(_clientes_calc, start=1):
                sr.gravar(_conn, sr.calcular_cliente(_conn, _c["ID"]))
                _barra.progress(_n / len(_clientes_calc), text=f"{_n} de {len(_clientes_calc)}: {_c['RAZAO_SOCIAL']}")
            _conn.commit()
        registrar_uso("CALCULO_SCORE", "SCORE", None, f"{len(_clientes_calc)} cliente(s) calculado(s)")
        st.session_state.pop("score_detalhe", None)
        st.rerun()
    except oracledb.Error as _e:
        st.error(f"Erro ao calcular: {_e}")

try:
    with pool_score().acquire() as _conn:
        _clientes_score = sr.clientes_com_equipamentos(_conn)
        _historico = st_hist.historico_scores(_conn)
        _fazendas_score = sr.fazendas_dos_clientes(_conn)
        _ultimos_faz = sr.ultimos_scores_fazendas(_conn)
        _valor_cliente = sr.valor_segurado_por_cliente(_conn)
        _valor_fazenda = sr.valor_segurado_por_fazenda(_conn)
except oracledb.Error as _e:
    st.error(f"Não foi possível ler o score: {_e}")
    _clientes_score, _historico, _fazendas_score, _ultimos_faz, _valor_cliente, _valor_fazenda = [], [], [], {}, {}, {}
_situacao = st_hist.situacao_atual(_historico)

# 3) Filtros: estado (das fazendas), cliente e fazenda (do cliente escolhido)
TODOS = None
_nomes = {c["ID"]: c["RAZAO_SOCIAL"] for c in _clientes_score}
_estados_cliente = {}
for _f in _fazendas_score:
    if _f["ESTADO"]:
        _estados_cliente.setdefault(_f["ID_CLIENTE"], set()).add(_f["ESTADO"])
_f1, _f2, _f3 = st.columns(3)
_filtro_uf = _f1.multiselect("Estado", sorted({_f["ESTADO"] for _f in _fazendas_score if _f["ESTADO"]}),
                             placeholder="Todos", key="score_filtro_uf")
_opcoes_cliente = [i for i in _nomes if not _filtro_uf or _estados_cliente.get(i, set()) & set(_filtro_uf)]
_cliente = _f2.selectbox("Cliente", [TODOS] + _opcoes_cliente,
                         format_func=lambda i: "Todos" if i is None else _nomes[i], key="score_filtro_cliente_unico")
_fazendas_do_cliente = [_f for _f in _fazendas_score if _cliente is not None and _f["ID_CLIENTE"] == _cliente
                        and (not _filtro_uf or _f["ESTADO"] in _filtro_uf)]
_nomes_faz = {_f["ID"]: _f["NOME_FAZENDA"] for _f in _fazendas_do_cliente}
_fazenda = _f3.selectbox("Fazenda", [TODOS] + list(_nomes_faz),
                         format_func=lambda i: "Todas" if i is None else _nomes_faz[i],
                         key=f"score_filtro_fazenda_{_cliente}", disabled=_cliente is None,
                         help="Escolha um cliente para filtrar uma fazenda.")
_clientes_filtrados = [i for i in _opcoes_cliente if _cliente is None or i == _cliente]
_fazendas_filtradas = [_f for _f in _fazendas_score if _f["ID_CLIENTE"] in _clientes_filtrados
                       and (not _filtro_uf or _f["ESTADO"] in _filtro_uf)
                       and (_fazenda is None or _f["ID"] == _fazenda)]


def _data_curta(valor):
    return valor.strftime("%d/%m/%Y %H:%M") if hasattr(valor, "strftime") else str(valor or "")[:16]


def _num(valor, casas=1, sufixo=""):
    return "—" if valor is None else f"{valor:.{casas}f}{sufixo}".replace(".", ",")


def _moeda(valor):
    if valor >= 1_000_000:
        return f"R$ {valor / 1_000_000:.1f} mi".replace(".", ",")
    return f"R$ {valor:,.0f}".replace(",", ".")


def _variacao(valor):
    if valor is None:
        return "—"
    if abs(valor) < 0.05:
        return "="
    return ("▲ " if valor > 0 else "▼ ") + f"{abs(valor):.1f}".replace(".", ",")


# 4) Painel: 4 indicadores. Com uma fazenda escolhida, só os dados da fazenda.
if _fazenda is not None:
    _sf = _ultimos_faz.get(_fazenda)
    _p1, _p2, _p3, _p4 = st.columns(4)
    _p1.metric("Score da fazenda (0 a 100)", _num(_sf["score"]) if _sf else "—")
    _p2.metric("Classificação", (_sf["classe"] if _sf else None) or "—")
    _p3.metric("Valor segurado da fazenda", _moeda(_valor_fazenda.get(_fazenda, 0.0)))
    _p4.metric("Principal fator de risco", (rec.principal_fator(_sf["notas"]) if _sf else None) or "—")
else:
    _com_score = [i for i in _clientes_filtrados if i in _situacao]
    if _com_score:
        _altos = [i for i in _com_score if _situacao[i]["atual"]["classe"] == "Alto"]
        _valor_total = sum(_valor_cliente.get(i, 0) for i in _com_score)
        _valor_alto = sum(_valor_cliente.get(i, 0) for i in _altos)
        _pioraram = [i for i in _com_score if _situacao[i]["alerta"]]
        _media = sum(_situacao[i]["atual"]["score"] for i in _com_score) / len(_com_score)
        _p1, _p2, _p3, _p4 = st.columns(4)
        _p1.metric("Score médio (0 a 100)" if len(_com_score) > 1 else "Score do cliente (0 a 100)", _num(_media),
                   help=sr.texto_classificacao())
        _p2.metric("Clientes em risco alto", f"{len(_altos)} de {len(_com_score)}",
                   help="Baixo / Médio / Alto: " + " / ".join(str(sum(1 for i in _com_score
                                                                      if _situacao[i]["atual"]["classe"] == c))
                                                               for c in ("Baixo", "Médio", "Alto")))
        _p3.metric("Valor segurado em risco alto", _moeda(_valor_alto)
                   + (f" ({_valor_alto / _valor_total * 100:.0f}%)" if _valor_total else ""),
                   help="Soma do valor segurado dos clientes em risco alto e o percentual sobre o total filtrado.")
        _p4.metric("Clientes que pioraram", len(_pioraram), help="Subiram de classe ou ganharam 7,5 pontos ou mais "
                   "desde o cálculo anterior.")
        _alertas = [(i, _situacao[i]) for i in _com_score if _situacao[i]["alerta"]]
        if _alertas:
            st.warning("**Alertas do score (piora desde o cálculo anterior):**  \n" + "  \n".join(
                f"{_nomes[i]}: {s['alerta']} (de {_num(s['anterior']['score'])} para {_num(s['atual']['score'])}; "
                f"principal fator: {rec.principal_fator(s['atual']['notas']) or '-'})" for i, s in _alertas))


@st.cache_data(ttl=6 * 3600, show_spinner=False)
def tendencia_regional(ids_fazendas):
    with pool_score().acquire() as conn:
        return st_hist.risco_mensal_por_estado(conn, list(ids_fazendas))


@st.cache_data(ttl=3600, show_spinner=False)
def indicadores_frota(ids_clientes):
    with pool_score().acquire() as conn:
        return st_hist.indicadores_equipamentos(conn, list(ids_clientes))


def mostrar_detalhe(itens, score, classe, bonus, subtitulo, fazendas_para_recs, fazendas_tabela):
    """Detalhe do score (cliente ou fazenda): 8 itens, recomendações e, no cliente, as notas por fazenda."""
    _cor = CORES_CLASSE.get(classe, "#607D8B")
    st.markdown(f"<div style='border:1px solid #d8dee4; border-radius:10px; padding:14px 20px; margin:8px 0;'>"
                f"<span style='font-size:1.6rem; font-weight:800; color:{_cor};'>"
                f"{score:.1f} pontos".replace(".", ",") + f" · Risco {(classe or '-').lower()}</span><br>"
                f"<span style='color:#555;'>{subtitulo}"
                + (f" · bônus da regra ideal: -{bonus:g} pontos".replace(".", ",") if bonus else "")
                + "</span></div>", unsafe_allow_html=True)
    _da_fazenda = fazendas_tabela is None and len(fazendas_para_recs or []) == 1
    tabela([{"Categoria": sr.CATEGORIAS[k] + (" (do cliente)" if _da_fazenda and k not in sr.ITENS_FAZENDA
                                             and sr.CATEGORIAS[k] != "Cliente" else ""), "Item": sr.NOMES[k],
             "Nota": f"{itens[k]['nota']:g}".replace(".", ",") + (" (sem dados: neutra)" if itens[k]["sem_dados"]
                                                                  else ""),
             "Pontos": f"{_num(sr.pontos(k, itens[k]['nota']))} de {sr.PESOS[k]:g}".replace(".", ","),
             "Medida": itens[k]["valor"], "Detalhe": itens[k]["detalhe"]} for k in sr.PESOS], altura=None)
    base = {"itens": itens, "score": score, "bonus": bonus, "fazendas": fazendas_para_recs}
    _recs = rec.recomendacoes(base)
    if _da_fazenda:
        st.caption("Queimadas, hidrológico, climático e procedimentos são desta fazenda. Os itens de "
                   "Cliente e a complexidade (marcada \"do cliente\") são medidos no cliente como um todo e entram "
                   "igual no score de cada fazenda.")
    st.markdown('<div style="font-weight:700; font-size:1.15rem; color:#1A4A75; margin-top:18px;">'
                'Recomendações preventivas</div>', unsafe_allow_html=True)
    if not _recs:
        st.success("Todos os itens estão na nota 1 e o bônus da regra ideal já é o máximo. Manter as práticas atuais.")
    else:
        _cen = rec.cenario(base, _recs)
        if _cen["itens"] and _cen["score"] < score:
            st.info(f"Cumprindo as recomendações de prioridade Alta ({', '.join(_cen['itens'])}), o score cai de "
                    f"{_num(score)} para {_num(_cen['score'])} (risco {(_cen['classe'] or '-').lower()}).")
        tabela([{"Prioridade": x["prioridade"], "Item": x["item"],
                 "Nota": "—" if x["nota"] is None else f"{x['nota']:g}".replace(".", ","),
                 "Critério": x["criterio"], "Ação recomendada": x["acao"], "Quem age": x["responsavel"],
                 "Redução possível (pontos)": "−" + _num(x["impacto"])} for x in _recs], altura=None)
        st.caption("Redução possível: quantos pontos o score perde se o item chegar à nota 1 (os pontos que o item "
                   "soma hoje). Prioridade Alta: nota 3 ou redução de 7,5 pontos ou mais.")
    if fazendas_tabela:
        st.markdown("**Itens de cada fazenda**")
        tabela([{"Fazenda": f["NOME_FAZENDA"], "Município": f["MUNICIPIO"],
                 **{sr.NOMES[k]: f"{f['itens'][k]['nota']:g} ({f['itens'][k]['valor']})" for k in sr.ITENS_FAZENDA},
                 "Score da fazenda": f"{f['score']:.1f}".replace(".", ",")} for f in fazendas_tabela], altura=360)


# 5) Abas. O detalhe só aparece com um cliente escolhido (e é da fazenda, se uma fazenda foi escolhida).
if not _clientes_score:
    st.info("Nenhum cliente com equipamentos segurados.")
elif not _situacao:
    st.info("Nenhum score calculado ainda. Clique em \"Calcular o score de todos os clientes agora\".")
elif not _clientes_filtrados:
    st.info("Nenhum cliente nos filtros escolhidos.")
else:
    _nome_detalhe = ("Detalhe da fazenda" if _fazenda is not None else "Detalhe do cliente") \
        if _cliente is not None else None
    _abas_nomes = ([_nome_detalhe] if _nome_detalhe else []) + \
        (["Por cliente"] if _cliente is None else []) + \
        ["Por fazenda", "Risco ambiental por região", "Equipamentos e operação"]
    _abas = dict(zip(_abas_nomes, st.tabs(_abas_nomes)))

    if _nome_detalhe:
        with _abas[_nome_detalhe]:
            _cache = st.session_state.setdefault("score_detalhe", {})
            if _cliente not in _cache:
                with st.spinner("Calculando os itens do cliente..."):
                    with pool_score().acquire() as _conn:
                        _cache[_cliente] = sr.calcular_cliente(_conn, _cliente)
            _r = _cache[_cliente]
            if _fazenda is None:
                _faz_uf = [f for f in _r["fazendas"] if not _filtro_uf or f["MUNICIPIO"].split("/")[-1] in _filtro_uf]
                mostrar_detalhe(_r["itens"], _r["score"], _r["classe"], _r["bonus"],
                                f"{_nomes[_cliente]} · {_r['equipamentos']} equipamento(s) em "
                                f"{len(_r['fazendas'])} fazenda(s)", _r["fazendas"], _faz_uf)
            else:
                _fr = next((f for f in _r["fazendas"] if f["ID"] == _fazenda), None)
                if _fr is None:
                    st.info("Sem cálculo para esta fazenda.")
                else:
                    _itens_faz = {k: (_fr["itens"][k] if k in sr.ITENS_FAZENDA else _r["itens"][k]) for k in sr.PESOS}
                    mostrar_detalhe(_itens_faz, _fr["score"], sr.classificar(_fr["score"]), _r["bonus"],
                                    f"{_fr['NOME_FAZENDA']} ({_fr['MUNICIPIO']}) · cliente {_nomes[_cliente]}",
                                    [_fr], None)

    if "Por cliente" in _abas:
        with _abas["Por cliente"]:
            _linhas = sorted(({"Cliente": _nomes[i], "Estados": ", ".join(sorted(_estados_cliente.get(i, []))),
                               "Score": _situacao[i]["atual"]["score"], "Classificação": _situacao[i]["atual"]["classe"],
                               "Variação": _variacao(_situacao[i]["variacao"]),
                               "Principal fator de risco": rec.principal_fator(_situacao[i]["atual"]["notas"]) or "—",
                               "Calculado em": _data_curta(_situacao[i]["atual"]["data"])}
                              for i in _clientes_filtrados if i in _situacao), key=lambda l: l["Score"])
            if _linhas:
                tabela([dict(l, Score=_num(l["Score"])) for l in _linhas], altura=360)
                st.caption("Score em pontos (0 a 100). Variação: diferença para o cálculo anterior (▲ piora, ▼ melhora). "
                           "Principal fator: o item que mais soma pontos. Escolha um cliente no filtro para ver o "
                           "detalhe e as recomendações.")
            else:
                st.info("Nenhum cliente com score nos filtros escolhidos.")

    with _abas["Por fazenda"]:
        st.caption("Score de cada fazenda: itens da fazenda (queimadas, hidrológico, climático e "
                   "procedimentos) com os itens do cliente.")
        _linhas_faz = sorted(({"Fazenda": _f["NOME_FAZENDA"], "Cliente": _nomes.get(_f["ID_CLIENTE"], ""),
                               "Município": _f["MUNICIPIO"] or "", "Estado": _f["ESTADO"] or "",
                               "Score": _ultimos_faz[_f["ID"]]["score"],
                               "Classificação": _ultimos_faz[_f["ID"]]["classe"],
                               "Principal fator de risco": rec.principal_fator(_ultimos_faz[_f["ID"]]["notas"]) or "—",
                               "Calculado em": _data_curta(_ultimos_faz[_f["ID"]]["data"])}
                              for _f in _fazendas_filtradas if _f["ID"] in _ultimos_faz), key=lambda l: l["Score"])
        if _linhas_faz:
            tabela([dict(l, Score=_num(l["Score"])) for l in _linhas_faz], altura=360)
        else:
            st.info("Nenhuma fazenda com score nos filtros escolhidos.")

    with _abas["Risco ambiental por região"]:
        st.caption("Últimos 12 meses, mês a mês: % dos dias com foco de queimada a até 15 km (INPE), com chuva de "
                   "risco hidrológico Alto ou Crítico e com risco de deslizamento ou atolamento (regras do modelo de "
                   "chuva, as mesmas do score). Cada estado é a média das suas fazendas dentro dos filtros.")
        # sem botão: calcula ao abrir (fica guardado por 6 horas para os mesmos filtros)
        _serie, _erro_serie = [], None
        with st.spinner("Calculando o risco ambiental dos últimos 12 meses..."):
            try:
                _serie = tendencia_regional(tuple(sorted(_f["ID"] for _f in _fazendas_filtradas)))
            except Exception as _e:
                _erro_serie = _e
        if _erro_serie is not None:
            st.error(f"Não foi possível calcular o risco ambiental: {_erro_serie}")
        elif not _serie:
            st.info("Sem dados ambientais para as fazendas escolhidas.")
        else:
            _df = pd.DataFrame(_serie)
            _df["Mês"] = pd.to_datetime(_df["mes"])
            _df["percentual"] = _df["percentual"].astype(float)
            _risco = st.radio("Risco", ["Queimadas", "Hidrológico", "Climático"], horizontal=True,
                              key="score_risco_regiao")
            _sel = _df[_df["risco"] == _risco]
            if _sel.empty:
                st.info(f"Sem dados de {_risco.lower()} para as fazendas escolhidas.")
            else:
                st.altair_chart(alt.Chart(_sel).mark_line(point=True).encode(
                    x=alt.X("Mês:T", title="Mês", axis=alt.Axis(format="%m/%Y")),
                    y=alt.Y("percentual:Q", title="% dos dias do mês"),
                    color=alt.Color("estado:N", title="Estado"),
                    tooltip=[alt.Tooltip("Mês:T", format="%m/%Y"), alt.Tooltip("estado:N", title="Estado"),
                             alt.Tooltip("percentual:Q", title="% dos dias", format=".1f"),
                             alt.Tooltip("fazendas:Q", title="Fazendas")]).properties(height=340),
                    width="stretch")
                _pivot = _sel.pivot_table(index="estado", columns="Mês", values="percentual")
                _pivot.columns = [c.strftime("%m/%Y") for c in _pivot.columns]
                tabela(_pivot.reset_index().rename(columns={"estado": "Estado"}).round(1), altura=None)

    with _abas["Equipamentos e operação"]:
        _dimensoes = {"Tipo de equipamento": "tipo", "Modelo": "modelo", "Tipo de operação": "operacao",
                      "Estado": "estado"}
        _dim = st.radio("Agrupar por", list(_dimensoes), horizontal=True, key="score_dim_frota")
        with st.spinner("Calculando os indicadores da frota..."):
            _frota = [l for l in indicadores_frota(tuple(sorted(_clientes_filtrados)))
                      if (not _filtro_uf or l["estado"] in _filtro_uf)
                      and (_fazenda is None or l.get("id_fazenda") == _fazenda)]
        if not _frota:
            st.info("Nenhum equipamento nos filtros escolhidos.")
        else:
            _grupos = st_hist.agrupar_equipamentos(_frota, _dimensoes[_dim])
            tabela([{_dim: g["grupo"], "Máquinas": g["maquinas"],
                     "Valor segurado": f"R$ {g['valor']:,.0f}".replace(",", "."),
                     "Manutenções programadas comprovadas": _num(g["realizacao"], 1, "%"),
                     "Aprovação na validação Sompo": _num(g["atende"], 1, "%"),
                     "Sinistros (5 anos)": g["sinistros"],
                     "Indenizado": f"R$ {g['indenizado']:,.0f}".replace(",", "."),
                     "Sinistralidade": _num(g["sinistralidade"], 1, "%")} for g in _grupos], altura=None)
            # Dois indicadores independentes (não são parte um do outro): um gráfico para cada, com cor própria
            _graficos = [("realizacao", "Manutenções programadas comprovadas",
                          "% das revisões vencidas (prazo de 90 dias encerrado) que têm comprovante", "#1A4A75"),
                         ("atende", "Aprovação na validação Sompo",
                          "Nota média dos comprovantes validados pela Sompo (Atende 100, maior parte 75, menor parte 25, não atende 0)", "#E07B00")]
            _g1, _g2 = st.columns(2)
            for _col, (_chave, _titulo, _explica, _cor) in zip((_g1, _g2), _graficos):
                _dados = pd.DataFrame([{"Grupo": g["grupo"], "Percentual": g[_chave]} for g in _grupos
                                       if g[_chave] is not None])
                with _col:
                    st.markdown(f"**{_titulo} por {_dim.lower()}**")
                    st.caption(f"{_explica}. Linha tracejada: meta de 80% do score.")
                    if _dados.empty:
                        st.info("Sem dados.")
                        continue
                    _meta = alt.Chart(pd.DataFrame({"Meta": [80]})).mark_rule(
                        strokeDash=[5, 4], color="#2E7D32").encode(x="Meta:Q")
                    st.altair_chart((alt.Chart(_dados).mark_bar(color=_cor).encode(
                        y=alt.Y("Grupo:N", title=None, sort="-x", axis=alt.Axis(labelLimit=0)),
                        x=alt.X("Percentual:Q", scale=alt.Scale(domain=[0, 100]), title="%"),
                        tooltip=[alt.Tooltip("Grupo:N", title=_dim),
                                 alt.Tooltip("Percentual:Q", title=_titulo, format=".1f")]) + _meta)
                                    .properties(height=max(120, 42 * len(_dados))), width="stretch")
            st.caption("Sinistralidade (tabela): indenizações de 5 anos sobre o valor segurado. Os dois indicadores "
                       "dos gráficos são independentes: um mede se as revisões foram comprovadas, o outro a nota que "
                       "a Sompo deu aos comprovantes enviados.")

# 6) Ação de subscrição: ajuste do prêmio sugerido pelo score. A decisão fica no registro de ações (CS_LOG_ACOES).
if _cliente is not None and _cliente in _situacao:
    _atual = _situacao[_cliente]["atual"]
    _piorou = bool(_situacao[_cliente]["alerta"])
    _sugerido = premio.sugestao(_atual["score"], _piorou)
    st.markdown('<div class="secao-destaque">Ação de subscrição</div>', unsafe_allow_html=True)
    st.markdown(f"Cliente **{_nomes[_cliente]}**: {premio.motivo(_atual['score'], _atual['classe'], _piorou)}. "
                f"Sugestão: **{premio.rotulo(_sugerido).lower()}**.")
    st.caption(premio.texto_faixas() + " A sugestão é um apoio: a decisão é do agente de subscrição e fica "
               "registrada com quem decidiu e quando." + (" O ajuste vale para o cliente (todas as fazendas)."
                                                            if _fazenda is not None else ""))
    _aviso_premio = st.session_state.pop("_aviso_premio", None)
    if _aviso_premio:
        st.success(_aviso_premio)
    _justificativa = st.text_input("Justificativa (opcional, fica no registro)", max_chars=300,
                                   key=f"premio_justificativa_{_cliente}")
    _colunas_premio = st.columns(len(premio.AJUSTES))
    for _col_p, _ajuste in zip(_colunas_premio, premio.AJUSTES):
        _rotulo_p = ("Manter" if _ajuste == 0 else f"{_ajuste:+d}%") + (" (sugerido)" if _ajuste == _sugerido else "")
        if _col_p.button(_rotulo_p, key=f"premio_{_cliente}_{_ajuste}", use_container_width=True,
                         type="primary" if _ajuste == _sugerido else "secondary",
                         help=premio.rotulo(_ajuste)):
            _detalhe = (f"{premio.rotulo(_ajuste)} (sugerido: {premio.rotulo(_sugerido).lower()}); "
                        f"{premio.motivo(_atual['score'], _atual['classe'], _piorou)}; matriz {sr.VERSAO_MATRIZ}"
                        + (f"; justificativa: {_justificativa.strip()}" if _justificativa.strip() else ""))
            registrar_uso(premio.ACAO_PREMIO, premio.ENTIDADE_PREMIO, _cliente, _detalhe)
            st.session_state["_aviso_premio"] = f"Decisão registrada: {premio.rotulo(_ajuste).lower()}."
            st.rerun()
    try:
        with pool_score().acquire() as _conn:
            _decisoes = auditoria.historico(_conn, premio.ENTIDADE_PREMIO, str(_cliente))
    except oracledb.Error:
        _decisoes = []
    if _decisoes:
        st.markdown("**Decisões registradas para este cliente**")
        tabela([{"Quando": d["DATA_HORA"], "Quem": nome_usuario(d["USUARIO"]), "Perfil": d["PERFIL"],
                 "Decisão": d["DETALHE"]} for d in _decisoes[:20]], altura=300)
