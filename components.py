import streamlit as st
from pathlib import Path

# Estilo comum a todas as páginas (aplicado pelo app.py): textos longos quebram linha em vez de serem cortados
# com "..." (botões, rótulos, métricas, abas, itens escolhidos nas listas e menu lateral).
ESTILO_GLOBAL = """
<style>
/* botões: altura acompanha o texto */
div.stButton > button, div.stDownloadButton > button, div.stFormSubmitButton > button,
div.stLinkButton > a { height: auto !important; min-height: 2.5rem; }
/* textos que o Streamlit corta com "..." passam a quebrar linha (só entre palavras) */
div.stButton > button *, div.stDownloadButton > button *, div.stFormSubmitButton > button *,
div.stLinkButton > a *, [data-testid="stPageLink"] *,
[data-testid="stMetricLabel"] *, [data-testid="stMetricValue"] *, [data-testid="stMetricDelta"] *,
[data-testid="stWidgetLabel"] *, button[role="tab"] *,
[data-testid="stSidebarNav"] span, [data-testid="stSidebarNavLink"] * {
    white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
    word-break: normal !important; overflow-wrap: break-word !important;
}
[data-testid="stMetricValue"] > div { overflow: visible !important; }
/* listas de múltipla escolha: os itens escolhidos quebram linha em vez de rolar escondidos */
[data-testid="stMultiSelect"] div:has(> [data-testid="stMultiSelectTagsContainer"]) {
    max-height: none !important; height: auto !important;
}
[data-testid="stMultiSelect"] div:has(> div > [data-testid="stMultiSelectTagsContainer"]) { height: auto !important; }
[data-testid="stMultiSelectTagsContainer"] {
    flex-wrap: wrap !important; overflow: visible !important; height: auto !important;
}
/* menu lateral: título do grupo (Manutenções) alinhado aos itens e páginas do grupo recuadas */
[data-testid="stNavSectionHeader"] { padding-left: 0.5rem !important; margin-top: 0.25rem; }
[data-testid="stNavSectionHeader"] p {
    font-size: 0.875rem !important; font-weight: 600 !important; letter-spacing: 0.04em; color: inherit !important;
}
[data-testid="stSidebarNavItems"] > div li [data-testid="stSidebarNavLink"] { padding-left: 1.75rem !important; }
/* tabelas (st.table, usada por components.tabela): o texto das células quebra linha */
[data-testid="stTable"] td, [data-testid="stTable"] th {
    white-space: normal !important; word-break: normal !important; overflow-wrap: break-word !important;
    vertical-align: top;
}
/* títulos sem o ícone de link que o Streamlit põe ao lado */
[data-testid="stHeaderActionElements"], h1 > a[href^="#"], h2 > a[href^="#"], h3 > a[href^="#"],
h4 > a[href^="#"], h5 > a[href^="#"], h6 > a[href^="#"] { display: none !important; }
[data-testid="stMultiSelectTagsContainer"] span {
    white-space: normal !important; overflow: visible !important; text-overflow: clip !important;
    max-width: none !important; height: auto !important;
}
</style>
"""


def aplicar_estilo_global():
    """CSS comum e rótulos dos gráficos sem corte (Altair corta rótulos longos com "..." por padrão)."""
    st.markdown(ESTILO_GLOBAL, unsafe_allow_html=True)
    try:
        import altair as alt

        @alt.theme.register("campo_seguro", enable=True)
        def _tema():
            return {"config": {"view": {"continuousWidth": 300, "continuousHeight": 300},
                               "axis": {"labelLimit": 0}, "legend": {"labelLimit": 0}}}
    except Exception:                                   # Altair antigo (sem alt.theme): mantém o padrão
        pass


LINHAS_TABELA_COMPLETA = 500     # acima disso, a tabela usa a grade do Streamlit (mais leve), também com quebra
ALTURA_LINHA_COM_QUEBRA = 72     # altura de linha da grade que ativa a quebra de texto (acima de 64 px)


def _celula(valor):
    """Texto da célula: vazio para nulo, número sem casas desnecessárias (vírgula decimal) e data dd/mm/aaaa."""
    import math
    import datetime
    if valor is None or (isinstance(valor, float) and math.isnan(valor)):
        return ""
    if hasattr(valor, "strftime") and not isinstance(valor, datetime.time):
        tem_hora = isinstance(valor, datetime.datetime) and (valor.hour or valor.minute)
        return valor.strftime("%d/%m/%Y %H:%M" if tem_hora else "%d/%m/%Y")
    if isinstance(valor, bool):
        return "Sim" if valor else "Não"
    if isinstance(valor, float):
        if valor.is_integer():
            return f"{int(valor):,}".replace(",", ".")
        return f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if isinstance(valor, int):
        return f"{valor:,}".replace(",", ".") if abs(valor) >= 10000 else str(valor)
    return str(valor)


def _celula_tabela(valor):
    """Célula do st.table, que lê o texto como markdown: o cifrão vira texto comum (dois "R$" seguidos formatavam o
    trecho entre eles como fórmula) e o traço sozinho não vira marcador de lista."""
    texto = _celula(valor)
    if texto.strip() == "-":
        return "—"
    return texto.replace("$", "\\$")


def tabela(dados, altura=560, mostrar_indice=False):
    """Tabela só de leitura em que o texto das células QUEBRA LINHA (padrão do app: usar no lugar de st.dataframe).
    Aceita DataFrame, lista de dicts ou Styler (cores por célula). Com muitas linhas, rola dentro de uma área de
    `altura` px (altura=None: mostra todas as linhas, sem rolagem); acima de LINHAS_TABELA_COMPLETA linhas usa a grade
    do Streamlit com linhas altas (também quebra)."""
    import pandas as pd
    from pandas.io.formats.style import Styler
    estilo = dados if isinstance(dados, Styler) else None
    df = estilo.data if estilo is not None else (dados if isinstance(dados, pd.DataFrame) else pd.DataFrame(dados))
    if df.empty:
        return
    if len(df) > LINHAS_TABELA_COMPLETA:
        st.dataframe(dados, hide_index=not mostrar_indice, width="stretch", height=altura or 560,
                     row_height=ALTURA_LINHA_COM_QUEBRA)
        return
    if estilo is None:
        estilo = df.map(_celula_tabela).style if hasattr(df, "map") else df.applymap(_celula_tabela).style
    else:
        estilo = estilo.format(_celula_tabela)
    if not mostrar_indice:
        estilo = estilo.hide(axis="index")
    if altura and len(df) > 12:
        with st.container(height=altura):
            st.table(estilo)
    else:
        st.table(estilo)


@st.cache_data(show_spinner=False)
def _logo_base64(caminho):
    import base64
    return base64.b64encode(Path(caminho).read_bytes()).decode()


def render_logo():
    """Renderiza a logo ampliada e centralizada na página (st.image dentro de uma coluna fica alinhado à esquerda)."""
    caminho_logo = Path(__file__).parent / "assets" / "CS_Logo.jpeg"
    if caminho_logo.exists():
        st.markdown(f"<div style='text-align:center;'><img src='data:image/jpeg;base64,{_logo_base64(str(caminho_logo))}'"
                    " style='width:350px; max-width:100%;' alt='Campo Seguro'></div>", unsafe_allow_html=True)

def render_header():
    """Renderiza o cabeçalho completo."""
    render_logo()
    st.markdown("---")

def render_footer():
    """Renderiza o rodapé."""
    st.markdown(
        "<p style='text-align: center; color: #888; font-size: 12px; margin-top: 40px;'>"
        "Campo Seguro &copy; 2026 &mdash; Todos os direitos reservados | Proteção e Monitoramento para o Agronegócio"
        "</p>",
        unsafe_allow_html=True
    )

# ---------------------------------------------------------------------------
# Controle de acesso
# ---------------------------------------------------------------------------
PERFIL_ADMIN = "admin"
PERFIL_SOMPO = "sompo"
PERFIL_PRODUTOR = "produtor"


# Páginas do menu (grupo, arquivo, título) e perfis que podem abrir cada uma. O menu (app.py) e os atalhos do início
# mostram só as páginas do perfil; a própria página confere de novo com exigir_login.
PAGINAS = [
    ("", "pages/0_Cadastro_cliente.py", "Cadastro", (PERFIL_ADMIN, PERFIL_SOMPO)),
    ("", "pages/1_Alertas.py", "Alertas", (PERFIL_ADMIN, PERFIL_SOMPO, PERFIL_PRODUTOR)),
    ("", "pages/2_Monitoramento.py", "Monitoramento", (PERFIL_ADMIN,)),
    ("", "pages/3_Score_Risk.py", "Score Risk", (PERFIL_ADMIN, PERFIL_SOMPO)),
    ("Manutenções", "pages/4_Manutencoes_Programadas.py", "Programação", (PERFIL_ADMIN, PERFIL_SOMPO)),
    ("Manutenções", "pages/5_Manutencoes_Comprovantes.py", "Comprovação", (PERFIL_ADMIN, PERFIL_SOMPO,
                                                                          PERFIL_PRODUTOR)),
]


def paginas_do_perfil(perfil):
    """[(grupo, arquivo, título)] das páginas que o perfil pode abrir."""
    return [(grupo, arquivo, titulo) for grupo, arquivo, titulo, perfis in PAGINAS if perfil in perfis]


def _link_inicio(texto):
    try:
        st.page_link("inicio.py", label=texto)
    except Exception:
        st.markdown(f"[{texto}](/)")


def perfil_do_usuario(role):
    """Traduz o ROLE de CS_USUARIOS para um perfil de acesso (ou None se não reconhecido)."""
    r = str(role or "").strip().lower()
    if "admin" in r:
        return PERFIL_ADMIN
    if any(chave in r for chave in ("subscri", "analista", "sompo")):
        return PERFIL_SOMPO
    if any(chave in r for chave in ("operador", "produtor")):
        return PERFIL_PRODUTOR
    return None


def exigir_login(perfis_permitidos=None):
    """Interrompe a página se o usuário não estiver logado ou se o perfil não tiver acesso.
    Devolve o perfil (admin, sompo ou produtor). Use no início de cada página, logo após set_page_config."""
    if not st.session_state.get("logado"):
        render_logo()
        st.warning("Acesso restrito. Faça o login para continuar.")
        _link_inicio("Ir para o login")
        st.stop()
    perfil = perfil_do_usuario(st.session_state.get("role"))
    if perfil is None or (perfis_permitidos and perfil not in perfis_permitidos):
        render_logo()
        registrar_uso("ACESSO_NEGADO", "PAGINA", None, pagina=_pagina_chamadora())
        st.error("Seu perfil não tem acesso a esta página.")
        _link_inicio("Voltar ao início")
        st.stop()
    _registrar_acesso_pagina()
    return perfil


# ---------------------------------------------------------------------------
# Registro de uso (CS_LOG_ACOES, requisitos/auditoria.py)
# ---------------------------------------------------------------------------
def _pagina_chamadora():
    """Título (do menu) da página que chamou exigir_login; o nome do arquivo se não estiver no menu."""
    import inspect
    for quadro in inspect.stack():
        caminho = Path(quadro.filename)
        if caminho.parent.name == "pages":
            return next((titulo for _, arquivo, titulo, _ in PAGINAS if Path(arquivo).name == caminho.name),
                        caminho.stem)
    return ""


def registrar_uso(acao, entidade=None, chave=None, detalhe=None, pagina=None):
    """Registra uma ação do usuário logado (referência "USR-<ID>", perfil e página). Nunca interrompe a tela."""
    try:
        from requisitos import auditoria
        auditoria.registrar_avulso(acao, entidade, None if chave is None else str(chave), detalhe,
                                   st.session_state.get("usuario_ref") or None,
                                   perfil_do_usuario(st.session_state.get("role")) or st.session_state.get("role"),
                                   pagina or st.session_state.get("pagina_atual"))
    except Exception:
        pass


def _registrar_acesso_pagina():
    """Registra o acesso à página uma vez por sessão (as interações dentro da página não geram novos registros)."""
    pagina = _pagina_chamadora()
    st.session_state["pagina_atual"] = pagina
    vistas = st.session_state.setdefault("paginas_registradas", set())
    if pagina and pagina not in vistas:
        vistas.add(pagina)
        registrar_uso("ACESSO_PAGINA", "PAGINA", None, pagina=pagina)


@st.cache_data(ttl=600, show_spinner=False)
def _nomes_usuarios():
    try:
        import oracledb
        from auth import USER, PASSWORD, DSN
        from requisitos import usuarios
        conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        try:
            with conn.cursor() as cursor:
                return usuarios.nomes_por_referencia(cursor)
        finally:
            conn.close()
    except Exception:
        return {}


def _so_digitos(valor):
    return "".join(c for c in str(valor or "") if c.isdigit())


def cliente_do_usuario(clientes):
    """Clientes (dicts com CNPJ) filtrados pelo CNPJ ligado ao usuário logado (perfil cliente/produtor)."""
    cnpj = _so_digitos(st.session_state.get("cliente_cnpj"))
    if not cnpj:
        return []
    return [c for c in clientes if _so_digitos(c.get("CNPJ")) == cnpj]


def nome_usuario(valor):
    """Quem fez (gravado como "USR-<ID>") -> nome do usuário, para mostrar na tela."""
    from requisitos import usuarios
    return usuarios.nome_para_exibir(valor, _nomes_usuarios())
