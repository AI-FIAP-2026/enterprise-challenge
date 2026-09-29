# -*- coding: utf-8 -*-
"""
Cadastro do cliente (preenchido pelo próprio cliente, já com login, e atualizado quando quiser)

Três formulários, em abas:
  1. Dados do cliente e da empresa: empresa, contato e responsável (CS_CLIENTES) e as propriedades
     (CS_FAZENDAS), que são a base dos alertas e dos equipamentos.
  2. Equipamentos segurados (CS_EQUIPAMENTOS_SEGURADOS), ligados a uma propriedade e a um modelo de
     CS_EQUIPAMENTOS_MODELOS (ou a um modelo novo, cadastrado no próprio formulário).
  3. Maturidade da gestão da manutenção (CS_SCORE_MANUTENCAO_PROCEDIMENTOS): 15 afirmativas em 3 pilares,
     respostas de 0 a 3. Nota final de 0 a 15 com pesos 40% (Manutenção Preventiva, até 6), 40% (Gestão de
     Riscos, até 6) e 20% (Governança, até 3). Cada envio grava uma nova avaliação (o histórico fica guardado).

Acesso (por enquanto, o cadastro é preenchido pela Sompo):
  - Sompo (analista de subscrição) e administrador: escolhem qualquer cliente ou cadastram um novo.
  - Produtor (operador): sem acesso. Para liberar depois, inclua PERFIL_PRODUTOR em PERFIS_COM_ACESSO: ele verá
    só o cliente do CNPJ do seu login (o código desse caso já está pronto).

Antes de usar, rode sql/estrutura_banco.sql (acrescenta as colunas de contato e responsável em CS_CLIENTES)
e configure a chave de criptografia (tratamento/db_gerar_chave_criptografia.py): nome completo, CPF e telefones
são gravados criptografados (criptografia.py).
A página se adapta às colunas que existirem: campo sem coluna no banco não aparece.
"""
import sys
import os
import re
import datetime

import streamlit as st
import oracledb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

st.set_page_config(page_title="Campo Seguro - Cadastro", layout="wide")

from components import exigir_login, render_footer, tabela, registrar_uso, PERFIL_ADMIN, PERFIL_SOMPO, PERFIL_PRODUTOR
from auth import USER, PASSWORD, DSN
from criptografia import criptografar, descriptografar, diagnostico_chave
from requisitos.anexos import (salvar_anexo, listar_anexos, conteudo_anexo, salvar_manual, listar_manuais,
                             conteudo_manual, validar_arquivo, nome_seguro, AnexoInvalido, TIPOS_DOCUMENTO,
                             TIPOS_MANUAL, TAMANHO_MAXIMO_MB, ENTIDADE_EQUIPAMENTO, ANEXO_NOTA_FISCAL)
from requisitos.programacao_manutencao import programar_equipamento
from requisitos.leituras import registrar_leitura_cadastro

PERFIS_COM_ACESSO = [PERFIL_ADMIN, PERFIL_SOMPO]   # produtor: liberar depois (ver o topo do arquivo)
perfil = exigir_login(PERFIS_COM_ACESSO)
somente_leitura = False

# Depois de salvar, a página recarrega: aqui entram a seleção do item recém-criado e o aviso de sucesso
# (a seleção de um widget só pode ser trocada antes de ele ser desenhado).
for _chave, _valor in st.session_state.pop("_proxima_selecao", {}).items():
    st.session_state[_chave] = _valor
aviso_salvo = st.session_state.pop("_aviso_salvo", None)


def depois_de_salvar(mensagem, chave=None, valor=None):
    registrar_uso("CADASTRO", "CLIENTE", globals().get("id_cliente") or valor, mensagem)
    st.session_state["_aviso_salvo"] = mensagem
    if chave is not None:
        st.session_state.setdefault("_proxima_selecao", {})[chave] = valor
    st.rerun()

# ---------------------------------------------------------------------------
# Conteúdo dos formulários
# ---------------------------------------------------------------------------
UFS = ["AC", "AL", "AM", "AP", "BA", "CE", "DF", "ES", "GO", "MA", "MG", "MS", "MT", "PA", "PB", "PE", "PI", "PR",
       "RJ", "RN", "RO", "RR", "RS", "SC", "SE", "SP", "TO"]

ESCALA = {
    3: ("Concordo totalmente", "A afirmativa reflete uma prática plenamente consolidada, formalizada e cumprida de "
                               "forma rigorosa."),
    2: ("Concordo parcialmente", "A declaração descreve uma conduta habitual, porém executada mediante controle "
                                 "manual ou informal."),
    1: ("Discordo parcialmente", "A ação é aplicada raramente ou possui caráter pontual e reativo diante de falhas "
                                 "ou alertas."),
    0: ("Discordo totalmente", "A diretriz não é aplicada, inexistindo padrão estabelecido na unidade produtiva."),
}
PILARES = [
    ("Manutenção Preventiva", [
        "Dispõe-se de mecanismos de controle para certificar que as revisões preventivas sejam cumpridas conforme "
        "os prazos e padrões estipulados pelas montadoras.",
        "Os técnicos incumbidos da conservação mecânica da frota contam com habilitação formal ou treinamento "
        "concedido pelas marcas das máquinas.",
        "Adotam-se rotinas rigorosas de suprimento para assegurar o uso estrito de peças de reposição e componentes "
        "homologados.",
    ]),
    ("Gestão de Riscos", [
        "Mantém-se uma vistoria pré-operacional regular antes do início dos trabalhos com o maquinário.",
        "A propriedade emprega recursos de telemetria para o acompanhamento em tempo real dos parâmetros do veículo "
        "e do clima externo.",
        "Estabeleceram-se diretrizes específicas para a condução dos ativos em condições térmicas adversas, "
        "superando ou ficando aquém dos limites recomendados.",
        "Vigora uma instrução clara de paralisação instantânea das atividades ao se constatar qualquer indício ou "
        "ameaça de incêndio.",
        "Definiram-se normas de trafegabilidade para o manejo sob precipitações severas, terreno encharcado ou "
        "pistas escorregadias.",
        "Contam-se com orientações de segurança voltadas para o deslocamento e trabalho em áreas de relevo ou "
        "inclinação acentuada.",
        "Realiza-se a higienização reforçada do conjunto de radiadores e telas nos ciclos de temperatura elevada, "
        "poeira e estiagem.",
        "Os veículos agrícolas dispõem de dispositivos de combate a incêndio devidamente inspecionados, validados e "
        "operacionais.",
    ]),
    ("Governança", [
        "O acervo de processos que regem a administração da frota segurada encontra-se formalmente documentado e "
        "acessível.",
        "Os condutores participam sistematicamente de capacitações focadas em boas práticas de manejo e normas de "
        "segurança.",
        "As atividades de conservação técnica passam por acompanhamento constante e auditoria de conformidade.",
        "O conjunto de instruções mecânicas e operacionais é revisado em intervalos regulares para incorporação de "
        "melhorias.",
    ]),
]
# Nota final de 0 a 15. Peso de cada pilar: 40%, 40% e 20% -> vale até 6, 6 e 3 pontos da nota.
# Cada pilar = (pontos / máximo do pilar) x (peso / 100) x NOTA_MAXIMA
NOTA_MAXIMA = 15
PESOS_PILARES = [40, 40, 20]
# Colunas das 15 respostas em CS_SCORE_MANUTENCAO_PROCEDIMENTOS, na ordem das afirmativas
COLUNAS_PERGUNTAS = [
    "P1_Q1_MANUTENCAO", "P1_Q2_CAPACITACAO_TECNICA", "P1_Q3_PECAS_HOMOLOGADAS",
    "P2_Q4_VISTORIA_PRE_OPERACIONAL", "P2_Q5_TELEMETRIA_MONITORAMENTO", "P2_Q6_CONDICOES_TERMICAS",
    "P2_Q7_PARALISACAO_INCENDIO", "P2_Q8_TRAFEGABILIDADE_CHUVA", "P2_Q9_AREAS_INCLINADAS",
    "P2_Q10_LIMPEZA_RADIADORES", "P2_Q11_COMBATE_INCENDIO",
    "P3_Q12_PROCESSOS_DOCUMENTADOS", "P3_Q13_CAPACITACAO", "P3_Q14_AUDITORIA_CONFORMIDADE", "P3_Q15_REVISAO_REGULAR",
]
TOTAL_PERGUNTAS = sum(len(p[1]) for p in PILARES)
# Nível de maturidade pelo % da nota máxima
NIVEIS_MATURIDADE = [
    (75, "Otimizado", "Práticas formalizadas, cumpridas e revisadas: gestão de referência."),
    (50, "Gerenciado", "Práticas habituais, mas parte do controle ainda é manual ou informal."),
    (25, "Em desenvolvimento", "Práticas pontuais e reativas: há pontos importantes a formalizar."),
    (0, "Inicial", "Poucas práticas estabelecidas: risco operacional elevado."),
]
# Dados pessoais gravados criptografados (criptografia.py): coluna -> tamanho máximo do texto aberto.
# No banco, o texto criptografado ocupa bem mais (ver sql/estrutura_banco.sql).
CAMPOS_CRIPTOGRAFADOS = {"RESPONSAVEL_NOME": 150, "RESPONSAVEL_CPF": 14, "TELEFONE": 20, "RESPONSAVEL_TELEFONE": 20,
                         "EMAIL": 150, "RESPONSAVEL_EMAIL": 150}
TAMANHO_COLUNA_CRIPTOGRAFADA = {"RESPONSAVEL_NOME": 600, "RESPONSAVEL_CPF": 200, "TELEFONE": 200,
                                "RESPONSAVEL_TELEFONE": 200, "EMAIL": 400, "RESPONSAVEL_EMAIL": 400}
TELEMETRIA_PADRAO = ["SIM", "NAO"]
STATUS_PADRAO = ["ATIVO", "INATIVO", "CANCELADO"]
ORA_VALOR_NULO = 1400   # ORA-01400: coluna ID sem valor automático
NOVO = "__novo__"

# ---------------------------------------------------------------------------
# Banco de dados
# ---------------------------------------------------------------------------
@st.cache_resource(show_spinner=False)
def pool_conexoes():
    return oracledb.create_pool(user=USER, password=PASSWORD, dsn=DSN, min=1, max=4, increment=1)


def consultar(sql, parametros=None):
    with pool_conexoes().acquire() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, parametros or {})
            colunas = [c[0] for c in cursor.description]
            linhas = []
            for linha in cursor.fetchall():
                linhas.append({c: (v.read() if hasattr(v, "read") else v) for c, v in zip(colunas, linha)})
            return linhas


def executar(sql, parametros):
    with pool_conexoes().acquire() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, parametros)
        conn.commit()


def inserir(tabela, dados):
    """INSERT com o ID gerado pelo banco; se a coluna ID não tiver valor automático, usa MAX(ID) + 1.
    Devolve o ID da linha nova."""
    colunas = list(dados)
    with pool_conexoes().acquire() as conn:
        with conn.cursor() as cursor:
            novo_id = cursor.var(oracledb.NUMBER)
            try:
                cursor.execute(f"INSERT INTO {tabela} ({', '.join(colunas)}) "
                               f"VALUES ({', '.join(':' + c for c in colunas)}) RETURNING ID INTO :novo_id",
                               dict(dados, novo_id=novo_id))
            except oracledb.DatabaseError as e:
                if getattr(e.args[0], "code", None) != ORA_VALOR_NULO:
                    raise
                cursor.execute(f"LOCK TABLE {tabela} IN EXCLUSIVE MODE")
                cursor.execute(f"SELECT NVL(MAX(ID), 0) + 1 FROM {tabela}")
                proximo = cursor.fetchone()[0]
                cursor.execute(f"INSERT INTO {tabela} (ID, {', '.join(colunas)}) "
                               f"VALUES (:id_novo, {', '.join(':' + c for c in colunas)})",
                               dict(dados, id_novo=proximo))
                conn.commit()
                return int(proximo)
        conn.commit()
        valor = novo_id.getvalue()
        return int(valor[0] if isinstance(valor, list) else valor)


@st.cache_data(ttl=60, show_spinner=False)
def colunas_da_tabela(tabela):
    """{COLUNA: {'tipo', 'tamanho', 'nulo'}} na ordem da tabela."""
    linhas = consultar("""
        SELECT COLUMN_NAME, DATA_TYPE, DATA_LENGTH, NULLABLE FROM USER_TAB_COLUMNS
        WHERE TABLE_NAME = :tabela ORDER BY COLUMN_ID
    """, {"tabela": tabela})
    return {l["COLUMN_NAME"]: {"tipo": l["DATA_TYPE"], "tamanho": l["DATA_LENGTH"], "nulo": l["NULLABLE"] == "Y"}
            for l in linhas}


@st.cache_data(ttl=600, show_spinner=False)
def valores_permitidos(tabela, coluna):
    """Valores aceitos por uma restrição CHECK (ex.: STATUS IN ('ATIVO','INATIVO')), ou None se não houver."""
    try:
        linhas = consultar("""
            SELECT SEARCH_CONDITION FROM USER_CONSTRAINTS
            WHERE TABLE_NAME = :tabela AND CONSTRAINT_TYPE = 'C'
        """, {"tabela": tabela})
    except oracledb.Error:
        return None
    for l in linhas:
        condicao = str(l["SEARCH_CONDITION"] or "")
        achado = re.search(rf"\b{coluna}\b\s+IN\s*\(([^)]*)\)", condicao, re.IGNORECASE)
        if achado:
            return re.findall(r"'([^']*)'", achado.group(1))
    return None


# ---------------------------------------------------------------------------
# Validação e formatação
# ---------------------------------------------------------------------------
def so_digitos(texto):
    return re.sub(r"\D", "", str(texto or ""))


def cpf_valido(cpf):
    d = so_digitos(cpf)
    if len(d) != 11 or d == d[0] * 11:
        return False
    for tamanho in (9, 10):
        soma = sum(int(n) * p for n, p in zip(d[:tamanho], range(tamanho + 1, 1, -1)))
        digito = (soma * 10) % 11 % 10
        if int(d[tamanho]) != digito:
            return False
    return True


def email_valido(email):
    return bool(re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email or ""))


def formatar_cnpj(cnpj):
    d = so_digitos(cnpj)
    return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}" if len(d) == 14 else str(cnpj or "")


def formatar_cpf(cpf):
    d = so_digitos(cpf)
    return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}" if len(d) == 11 else str(cpf or "")


def formatar_cep(cep):
    d = so_digitos(cep)
    return f"{d[:5]}-{d[5:]}" if len(d) == 8 else str(cep or "")


def moeda(valor):
    return "-" if valor is None else f"R$ {float(valor):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def texto(valor):
    return "" if valor is None else str(valor)


def mostrar_erros(erros):
    for erro in erros:
        st.error(erro)


def rotulo_valor(valor):
    """'NAO' -> 'Não', 'ATIVO' -> 'Ativo' (só para exibir)."""
    return {"NAO": "Não", "SIM": "Sim"}.get(str(valor).upper(), str(valor).capitalize())


# ---------------------------------------------------------------------------
# Estilo
# ---------------------------------------------------------------------------
st.markdown("""
    <style>
    div.stButton > button, div.stFormSubmitButton > button, div.stDownloadButton > button {
        background-color: #1A4A75 !important; color: white !important; border: 1px solid #1A4A75 !important;
        border-radius: 4px !important; font-weight: 600 !important;
    }
    div.stButton > button:hover, div.stFormSubmitButton > button:hover {
        background-color: #133557 !important; border-color: #133557 !important; color: white !important;
    }
    .secao-cadastro { color: #1A4A75; font-size: 1.25rem; font-weight: 700; margin: 18px 0 6px 0; }
    .pergunta { font-size: 15px; margin: 14px 0 2px 0; }
    </style>
""", unsafe_allow_html=True)

st.markdown("<h1 style='color: #1A4A75; margin-bottom: 0;'>Cadastro do cliente</h1>", unsafe_allow_html=True)
st.markdown("Dados da empresa, propriedades, equipamentos segurados e maturidade da gestão da manutenção. "
            "Os dados podem ser atualizados sempre que houver mudanças.")
if somente_leitura:
    st.info("Perfil de consulta: os dados aparecem para leitura e não podem ser alterados.")
if aviso_salvo:
    st.success(aviso_salvo)

# ---------------------------------------------------------------------------
# Cliente atual
# ---------------------------------------------------------------------------
try:
    colunas_clientes = colunas_da_tabela("CS_CLIENTES")
except oracledb.Error as e:
    st.error(f"Não foi possível conectar ao banco de dados: {e}")
    st.stop()


def cliente_por_cnpj(cnpj):
    linhas = consultar("SELECT * FROM CS_CLIENTES WHERE REGEXP_REPLACE(CNPJ, '[^0-9]', '') = :cnpj",
                       {"cnpj": so_digitos(cnpj)})
    return linhas[0] if linhas else None


def cliente_por_id(id_cliente):
    linhas = consultar("SELECT * FROM CS_CLIENTES WHERE ID = :id", {"id": id_cliente})
    return linhas[0] if linhas else None


if perfil == PERFIL_PRODUTOR:
    cnpj_login = so_digitos(st.session_state.get("cliente_cnpj"))
    if not cnpj_login:
        st.error("Seu usuário não está vinculado a um CNPJ. Solicite o vínculo ao administrador.")
        render_footer()
        st.stop()
    cliente = cliente_por_cnpj(cnpj_login)
else:
    cnpj_login = None
    todos = consultar("SELECT ID, RAZAO_SOCIAL, CNPJ FROM CS_CLIENTES ORDER BY RAZAO_SOCIAL")
    opcoes = [NOVO] + [c["ID"] for c in todos]
    nomes = {c["ID"]: f"{c['RAZAO_SOCIAL']} ({formatar_cnpj(c['CNPJ'])})" for c in todos}
    nomes[NOVO] = "Cadastrar novo cliente"
    if not opcoes:
        st.info("Nenhum cliente cadastrado.")
        render_footer()
        st.stop()
    if st.session_state.get("cadastro_cliente") not in opcoes:
        st.session_state["cadastro_cliente"] = opcoes[1] if len(opcoes) > 1 else opcoes[0]
    escolhido = st.selectbox("Cliente", opcoes, format_func=lambda i: nomes[i], key="cadastro_cliente")
    cliente = None if escolhido == NOVO else cliente_por_id(escolhido)

id_cliente = cliente["ID"] if cliente else None


def fazendas_do_cliente():
    if id_cliente is None:
        return []
    return consultar("""
        SELECT ID, NOME_FAZENDA, CULTURA, MUNICIPIO, ESTADO, CODIGO_IBGE, LATITUDE, LONGITUDE, TAMANHO, NUM_CAR
        FROM CS_FAZENDAS WHERE ID_CLIENTE = :cliente ORDER BY NOME_FAZENDA
    """, {"cliente": id_cliente})


@st.cache_data(ttl=3600, show_spinner=False)
def municipios_da_uf(uf):
    return consultar("""
        SELECT MUNICIPIO_IBGE, MUNICIPIO, LATITUDE, LONGITUDE FROM CS_MUNICIPIOS
        WHERE UF = :uf ORDER BY MUNICIPIO
    """, {"uf": uf})


# Painel: 4 indicadores do cliente escolhido (o filtro é o próprio cliente)
if id_cliente is not None:
    try:
        _faz = consultar("SELECT ID FROM CS_FAZENDAS WHERE ID_CLIENTE = :cliente", {"cliente": id_cliente})
        _eq = consultar("""
            SELECT e.VALOR_SEGURADO, e.STATUS FROM CS_EQUIPAMENTOS_SEGURADOS e
            JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA WHERE f.ID_CLIENTE = :cliente
        """, {"cliente": id_cliente})
        _aval = consultar("""
            SELECT COUNT(DISTINCT p.ID_FAZENDA) AS QTD FROM CS_SCORE_MANUTENCAO_PROCEDIMENTOS p
            JOIN CS_FAZENDAS f ON f.ID = p.ID_FAZENDA WHERE f.ID_CLIENTE = :cliente
        """, {"cliente": id_cliente})
        _ativos = [e for e in _eq if str(e.get("STATUS") or "ATIVO").upper().startswith("ATIV")]
        _valor = sum(float(e["VALOR_SEGURADO"] or 0) for e in _ativos)
        _k1, _k2, _k3, _k4 = st.columns(4)
        _k1.metric("Propriedades", len(_faz))
        _k2.metric("Equipamentos segurados ativos", len(_ativos))
        _k3.metric("Valor segurado", f"R$ {_valor / 1_000_000:.1f} mi".replace(".", ",") if _valor >= 1_000_000
                   else f"R$ {_valor:,.0f}".replace(",", "."))
        _k4.metric("Questionário de maturidade", f"{int(_aval[0]['QTD'] or 0) if _aval else 0} de {len(_faz)}",
                   help="Propriedades com o questionário de maturidade da manutenção respondido.")
    except oracledb.Error:
        pass

aba_cliente, aba_equipamentos, aba_maturidade = st.tabs([
    "1. Dados do cliente e da empresa", "2. Equipamentos segurados", "3. Maturidade da gestão da manutenção"])

# ===========================================================================
# 1. Dados do cliente e da empresa
# ===========================================================================
# (coluna, rótulo, grupo, obrigatório, tipo de validação)
CAMPOS_CLIENTE = [   # só os dados essenciais
    ("RAZAO_SOCIAL", "Razão social", "empresa", True, None),
    ("RESPONSAVEL_NOME", "Nome completo do responsável", "responsavel", True, None),
    ("RESPONSAVEL_CPF", "CPF", "responsavel", False, "cpf"),
    ("RESPONSAVEL_TELEFONE", "Telefone", "responsavel", False, "telefone"),
    ("RESPONSAVEL_EMAIL", "E-mail", "responsavel", False, "email"),
]
GRUPOS_CLIENTE = [("empresa", "Empresa"), ("responsavel", "Responsável pela conta")]


def validar_campo(tipo, valor, rotulo):
    if not valor:
        return None
    if tipo == "email" and not email_valido(valor):
        return f"{rotulo}: e-mail inválido."
    if tipo == "cpf" and not cpf_valido(valor):
        return f"{rotulo}: CPF inválido."
    if tipo == "cep" and len(so_digitos(valor)) != 8:
        return f"{rotulo}: o CEP deve ter 8 dígitos."
    if tipo == "telefone" and len(so_digitos(valor)) not in (10, 11):
        return f"{rotulo}: informe DDD e número (10 ou 11 dígitos)."
    return None


def valor_aberto(registro, coluna):
    """Valor para mostrar no formulário (descriptografa os dados pessoais)."""
    valor = registro.get(coluna) if registro else None
    if coluna in CAMPOS_CRIPTOGRAFADOS and valor is not None:
        try:
            return texto(descriptografar(valor))
        except Exception:
            return ""
    return texto(valor)


with aba_cliente:
    campos_disponiveis = [c for c in CAMPOS_CLIENTE if c[0] in colunas_clientes]
    problema_chave = diagnostico_chave()
    chave_ok = problema_chave is None
    pequenas = [c for c, t in TAMANHO_COLUNA_CRIPTOGRAFADA.items()
                if c in colunas_clientes and colunas_clientes[c]["tamanho"] < t]
    if not chave_ok:
        st.error("Os dados do cliente não podem ser salvos: a chave de criptografia dos dados pessoais (nome, CPF, e-mail e "
                 f"telefones) não está pronta. Motivo: {problema_chave}")
    elif pequenas:
        st.error("As colunas " + ", ".join(pequenas) + " são pequenas para guardar os dados criptografados. Rode "
                 "de novo o script sql/estrutura_banco.sql. Até lá, os dados do cliente não podem ser salvos.")
    pode_salvar_cliente = chave_ok and not pequenas and not somente_leitura
    faltando = [c[1] for c in CAMPOS_CLIENTE if c[0] not in colunas_clientes]
    if faltando:
        st.warning("Alguns campos ainda não existem no banco e não aparecem no formulário: "
                   + ", ".join(faltando) + ". Rode o script sql/estrutura_banco.sql para incluí-los.")
    if cliente is None:
        st.info("Cliente ainda não cadastrado. Preencha os dados abaixo para criar o cadastro.")
    elif cliente.get("DATA_ATUALIZACAO"):
        st.caption(f"Última atualização: {cliente['DATA_ATUALIZACAO']:%d/%m/%Y às %H:%M}.")

    with st.form("form_cliente"):
        st.markdown('<div class="secao-cadastro">Empresa</div>', unsafe_allow_html=True)
        cnpj_atual = formatar_cnpj(cliente["CNPJ"]) if cliente else formatar_cnpj(cnpj_login)
        valores = {"CNPJ": st.text_input("CNPJ *", value=cnpj_atual,
                                         disabled=somente_leitura or cliente is not None or perfil == PERFIL_PRODUTOR,
                                         help="Depois de cadastrado, o CNPJ não pode ser alterado aqui.")}
        for grupo, titulo in GRUPOS_CLIENTE:
            if grupo != "empresa":
                st.markdown(f'<div class="secao-cadastro">{titulo}</div>', unsafe_allow_html=True)
            campos_grupo = [c for c in campos_disponiveis if c[2] == grupo]
            colunas_form = st.columns(2)
            for i, (coluna, rotulo, _, obrigatorio, tipo) in enumerate(campos_grupo):
                with colunas_form[i % 2]:
                    atual = valor_aberto(cliente, coluna)
                    if tipo == "uf":
                        valores[coluna] = st.selectbox(rotulo, UFS, index=UFS.index(atual) if atual in UFS else None,
                                                       placeholder="Escolha a UF", disabled=somente_leitura) or ""
                    else:
                        tamanho = CAMPOS_CRIPTOGRAFADOS.get(coluna) or colunas_clientes[coluna]["tamanho"]
                        valores[coluna] = st.text_input(rotulo + (" *" if obrigatorio else ""), value=atual,
                                                        max_chars=tamanho, disabled=somente_leitura).strip()
        st.caption("Campos com * são obrigatórios.")
        st.caption("Nome, CPF, e-mail e telefone são gravados criptografados.")
        enviar_cliente = st.form_submit_button("Salvar dados do cliente", disabled=not pode_salvar_cliente)

    if enviar_cliente:
        erros = []
        cnpj_digitos = so_digitos(valores["CNPJ"])   # sem validação dos dígitos (base de testes)
        if not cnpj_digitos:
            erros.append("CNPJ: preenchimento obrigatório.")
        elif cliente is None and cliente_por_cnpj(cnpj_digitos):
            erros.append("Já existe um cliente com esse CNPJ.")
        for coluna, rotulo, _, obrigatorio, tipo in campos_disponiveis:
            if obrigatorio and not valores[coluna]:
                erros.append(f"{rotulo}: preenchimento obrigatório.")
            erro = validar_campo(tipo, valores[coluna], rotulo)
            if erro:
                erros.append(erro)
        if erros:
            mostrar_erros(erros)
        else:
            dados = {}
            for coluna, _, _, _, tipo in campos_disponiveis:
                valor = valores[coluna] or None
                if valor and tipo == "cpf":
                    valor = formatar_cpf(valor)
                elif valor and tipo == "cep":
                    valor = formatar_cep(valor)
                elif valor and tipo == "email":
                    valor = valor.lower()
                dados[coluna] = criptografar(valor) if coluna in CAMPOS_CRIPTOGRAFADOS else valor
            try:
                if cliente is None:
                    dados["CNPJ"] = formatar_cnpj(cnpj_digitos) if len(cnpj_digitos) == 14 else valores["CNPJ"].strip()
                    novo = inserir("CS_CLIENTES", dados)
                    if "DATA_ATUALIZACAO" in colunas_clientes:
                        executar("UPDATE CS_CLIENTES SET DATA_ATUALIZACAO = SYSTIMESTAMP WHERE ID = :id", {"id": novo})
                    depois_de_salvar("Cliente cadastrado. Agora inclua as propriedades (aba 1, mais abaixo).",
                                     "cadastro_cliente" if perfil != PERFIL_PRODUTOR else None, novo)
                else:
                    atualizacao = ", DATA_ATUALIZACAO = SYSTIMESTAMP" if "DATA_ATUALIZACAO" in colunas_clientes else ""
                    executar(f"UPDATE CS_CLIENTES SET {', '.join(f'{c} = :{c}' for c in dados)}{atualizacao} "
                             f"WHERE ID = :id_cliente", dict(dados, id_cliente=id_cliente))
                    depois_de_salvar("Dados do cliente atualizados.")
            except oracledb.Error as e:
                st.error(f"Erro ao salvar os dados do cliente: {e}")

    # ---------------- Propriedades ----------------
    st.markdown('<div class="secao-cadastro">Propriedades (fazendas)</div>', unsafe_allow_html=True)
    if id_cliente is None:
        st.caption("Salve os dados do cliente para incluir as propriedades.")
    else:
        fazendas = fazendas_do_cliente()
        if fazendas:
            tabela([{
                "Propriedade": f["NOME_FAZENDA"], "Cultura": texto(f["CULTURA"]),
                "Município": f"{texto(f['MUNICIPIO'])}/{texto(f['ESTADO'])}",
            } for f in fazendas])
        else:
            st.caption("Nenhuma propriedade cadastrada ainda.")

        if not somente_leitura:
            opcoes_faz = [NOVO] + [f["ID"] for f in fazendas]
            nomes_faz = {f["ID"]: f["NOME_FAZENDA"] for f in fazendas}
            nomes_faz[NOVO] = "Incluir nova propriedade"
            id_faz = st.selectbox("Propriedade", opcoes_faz, format_func=lambda i: nomes_faz[i],
                                  key=f"cadastro_fazenda_{id_cliente}")
            fazenda = next((f for f in fazendas if f["ID"] == id_faz), None)

            # UF e município ficam fora do formulário: a lista de municípios depende da UF escolhida
            col_uf, col_mun = st.columns([1, 3])
            with col_uf:
                uf_atual = texto(fazenda["ESTADO"]) if fazenda else ""
                uf_faz = st.selectbox("UF da propriedade *", UFS, index=UFS.index(uf_atual) if uf_atual in UFS else None,
                                      placeholder="Escolha a UF", key=f"uf_faz_{id_faz}")
            municipios = municipios_da_uf(uf_faz) if uf_faz else []
            nomes_mun = [m["MUNICIPIO"] for m in municipios]
            with col_mun:
                mun_atual = texto(fazenda["MUNICIPIO"]) if fazenda else ""
                mun_faz = st.selectbox("Município da propriedade *", nomes_mun,
                                       index=nomes_mun.index(mun_atual) if mun_atual in nomes_mun else None,
                                       placeholder="Escolha o município", key=f"mun_faz_{id_faz}_{uf_faz}")
            municipio = next((m for m in municipios if m["MUNICIPIO"] == mun_faz), None)

            with st.form(f"form_fazenda_{id_faz}"):
                c1, c2 = st.columns(2)
                with c1:
                    nome_faz = st.text_input("Nome da propriedade *", value=texto(fazenda["NOME_FAZENDA"]) if fazenda else "",
                                             max_chars=100).strip()
                with c2:
                    cultura = st.text_input("Cultura principal", value=texto(fazenda["CULTURA"]) if fazenda else "",
                                            max_chars=50).strip()
                lat_padrao = (float(fazenda["LATITUDE"]) if fazenda and fazenda["LATITUDE"] is not None
                              and (municipio is None or fazenda["MUNICIPIO"] == municipio["MUNICIPIO"])
                              else float(municipio["LATITUDE"]) if municipio and municipio["LATITUDE"] is not None
                              else None)
                lon_padrao = (float(fazenda["LONGITUDE"]) if fazenda and fazenda["LONGITUDE"] is not None
                              and (municipio is None or fazenda["MUNICIPIO"] == municipio["MUNICIPIO"])
                              else float(municipio["LONGITUDE"]) if municipio and municipio["LONGITUDE"] is not None
                              else None)
                # A localização (usada nos alertas) vem do centro do município; o ajuste é opcional
                with st.expander("Ajustar a localização da sede (opcional)"):
                    l1, l2 = st.columns(2)
                    latitude = l1.number_input("Latitude", min_value=-34.0, max_value=6.0,
                                               value=None if lat_padrao is None else min(max(lat_padrao, -34.0), 6.0),
                                               format="%.6f", step=0.000001, placeholder="Centro do município")
                    longitude = l2.number_input("Longitude", min_value=-74.0, max_value=-34.0,
                                                value=None if lon_padrao is None else min(max(lon_padrao, -74.0), -34.0),
                                                format="%.6f", step=0.000001, placeholder="Centro do município")
                    st.caption("Sem ajuste, a propriedade fica no centro do município escolhido.")
                enviar_faz = st.form_submit_button("Salvar propriedade")

            if enviar_faz:
                erros = []
                if not nome_faz:
                    erros.append("Nome da propriedade: preenchimento obrigatório.")
                elif any(f["NOME_FAZENDA"].strip().lower() == nome_faz.lower() and f["ID"] != id_faz for f in fazendas):
                    erros.append("Já existe uma propriedade com esse nome para este cliente.")
                if not uf_faz or municipio is None:
                    erros.append("Escolha a UF e o município da propriedade.")
                if municipio is not None and (latitude is None or longitude is None):
                    latitude = latitude if latitude is not None else municipio["LATITUDE"]
                    longitude = longitude if longitude is not None else municipio["LONGITUDE"]
                if erros:
                    mostrar_erros(erros)
                else:
                    dados = {"NOME_FAZENDA": nome_faz, "CULTURA": cultura or None, "MUNICIPIO": municipio["MUNICIPIO"],
                             "ESTADO": uf_faz, "CODIGO_IBGE": str(municipio["MUNICIPIO_IBGE"]),
                             "LATITUDE": round(float(latitude), 6) if latitude is not None else None,
                             "LONGITUDE": round(float(longitude), 6) if longitude is not None else None}
                    try:
                        if fazenda is None:
                            novo = inserir("CS_FAZENDAS", dict(dados, ID_CLIENTE=id_cliente))
                            depois_de_salvar(f"Propriedade {nome_faz} incluída.", f"cadastro_fazenda_{id_cliente}", novo)
                        else:
                            executar(f"UPDATE CS_FAZENDAS SET {', '.join(f'{c} = :{c}' for c in dados)} "
                                     "WHERE ID = :id_faz AND ID_CLIENTE = :id_cliente",
                                     dict(dados, id_faz=id_faz, id_cliente=id_cliente))
                            depois_de_salvar(f"Propriedade {nome_faz} atualizada.")
                    except oracledb.Error as e:
                        st.error(f"Erro ao salvar a propriedade: {e}")

# ===========================================================================
# 2. Equipamentos segurados
# ===========================================================================
@st.cache_data(ttl=600, show_spinner=False)
def modelos_equipamentos():
    return consultar("""
        SELECT ID, FABRICANTE, MODELO, TIPO, ANO_FABRICACAO, VALOR_ESTIMADO FROM CS_EQUIPAMENTOS_MODELOS
        ORDER BY FABRICANTE, MODELO
    """)


# Dados de uso (sql/estrutura_banco.sql): base da programação das manutenções e dos relatórios por operação
COLUNAS_USO = ["DATA_AQUISICAO", "HORIMETRO_ATUAL", "HODOMETRO_ATUAL", "DATA_LEITURA", "USO_MEDIO_HORAS_MES",
               "USO_MEDIO_KM_MES", "TIPO_OPERACAO"]
TIPOS_OPERACAO = ["Preparo do solo", "Plantio", "Pulverização", "Colheita", "Transporte", "Tratos culturais",
                  "Irrigação", "Outra"]
# Tipo de tração (sql/estrutura_banco.sql): manutenções "somente 4WD" ou "somente 2WD" do manual
TIPOS_TRACAO = {"4WD": "Tração nas 4 rodas (4WD / TDA)", "2WD": "Tração em 2 rodas (2WD)", "ESTEIRA": "Esteira"}


def tem_coluna_tracao():
    return "TIPO_TRACAO" in colunas_da_tabela("CS_EQUIPAMENTOS_SEGURADOS")


def tem_colunas_uso():
    colunas = colunas_da_tabela("CS_EQUIPAMENTOS_SEGURADOS")
    return all(c in colunas for c in COLUNAS_USO)


def equipamentos_do_cliente():
    if id_cliente is None:
        return []
    extras = "".join(f", e.{c}" for c in COLUNAS_USO) if tem_colunas_uso() else ""
    extras += ", e.TIPO_TRACAO" if tem_coluna_tracao() else ""
    return consultar(f"""
        SELECT e.ID, e.ID_FAZENDA, e.ID_EQUIPAMENTO_MODELO, e.NUMERO_SERIE, e.IDENTIFICACAO_INTERNA,
               e.VALOR_SEGURADO, e.TELEMETRIA, e.NUMERO_APOLICE, e.DATA_INICIO_VIGENCIA, e.DATA_FIM_VIGENCIA,
               e.STATUS, f.NOME_FAZENDA, m.FABRICANTE, m.MODELO, m.TIPO{extras}
        FROM CS_EQUIPAMENTOS_SEGURADOS e
        JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
        LEFT JOIN CS_EQUIPAMENTOS_MODELOS m ON m.ID = e.ID_EQUIPAMENTO_MODELO
        WHERE f.ID_CLIENTE = :cliente
        ORDER BY f.NOME_FAZENDA, m.FABRICANTE, m.MODELO
    """, {"cliente": id_cliente})


def para_data(valor):
    if isinstance(valor, datetime.datetime):
        return valor.date()
    return valor


with aba_equipamentos:
    fazendas = fazendas_do_cliente()
    if id_cliente is None or not fazendas:
        st.info("Cadastre o cliente e ao menos uma propriedade (aba 1) antes de incluir os equipamentos.")
    else:
        equipamentos = equipamentos_do_cliente()
        valores_telemetria = valores_permitidos("CS_EQUIPAMENTOS_SEGURADOS", "TELEMETRIA") or TELEMETRIA_PADRAO
        valores_status = valores_permitidos("CS_EQUIPAMENTOS_SEGURADOS", "STATUS") or STATUS_PADRAO
        if equipamentos:
            total_segurado = sum(float(e["VALOR_SEGURADO"] or 0) for e in equipamentos)
            st.caption(f"{len(equipamentos)} equipamento(s), valor segurado total {moeda(total_segurado)}.")
            tabela([{
                "Propriedade": e["NOME_FAZENDA"],
                "Equipamento": f"{texto(e['FABRICANTE'])} {texto(e['MODELO'])}".strip(),
                "Tipo": texto(e["TIPO"]),
                "Identificação": texto(e["IDENTIFICACAO_INTERNA"]), "Nº de série": texto(e["NUMERO_SERIE"]),
                "Valor segurado": moeda(e["VALOR_SEGURADO"]), "Telemetria": rotulo_valor(e["TELEMETRIA"]),
                "Apólice": texto(e["NUMERO_APOLICE"]),
                "Vigência": (f"{para_data(e['DATA_INICIO_VIGENCIA']):%d/%m/%Y} a "
                             f"{para_data(e['DATA_FIM_VIGENCIA']):%d/%m/%Y}"
                             if e["DATA_INICIO_VIGENCIA"] and e["DATA_FIM_VIGENCIA"] else ""),
                "Situação": rotulo_valor(e["STATUS"]),
            } for e in equipamentos])
        else:
            st.caption("Nenhum equipamento cadastrado ainda.")

        if not somente_leitura:
            opcoes_eq = [NOVO] + [e["ID"] for e in equipamentos]
            nomes_eq = {e["ID"]: " | ".join(p for p in [f"{texto(e['FABRICANTE'])} {texto(e['MODELO'])}".strip(),
                                                        texto(e["IDENTIFICACAO_INTERNA"]), texto(e["NUMERO_SERIE"]),
                                                        e["NOME_FAZENDA"]] if p)
                        for e in equipamentos}
            nomes_eq[NOVO] = "Incluir novo equipamento"
            id_eq = st.selectbox("Equipamento", opcoes_eq, format_func=lambda i: nomes_eq[i],
                                 key=f"cadastro_equipamento_{id_cliente}")
            equipamento = next((e for e in equipamentos if e["ID"] == id_eq), None)

            modelos = modelos_equipamentos()
            fabricantes = sorted({m["FABRICANTE"] for m in modelos})
            OUTRO = "Outro (não está na lista)"
            modelo_atual = next((m for m in modelos if equipamento and m["ID"] == equipamento["ID_EQUIPAMENTO_MODELO"]),
                                None)
            c1, c2, c3 = st.columns([2, 2, 3])
            with c1:
                ids_faz = [f["ID"] for f in fazendas]
                nomes_f = {f["ID"]: f["NOME_FAZENDA"] for f in fazendas}
                id_faz_eq = st.selectbox("Propriedade onde fica *", ids_faz, format_func=lambda i: nomes_f[i],
                                         index=ids_faz.index(equipamento["ID_FAZENDA"])
                                         if equipamento and equipamento["ID_FAZENDA"] in ids_faz else 0,
                                         key=f"faz_eq_{id_eq}")
            with c2:
                opcoes_fab = fabricantes + [OUTRO]
                fabricante = st.selectbox("Fabricante *", opcoes_fab,
                                          index=opcoes_fab.index(modelo_atual["FABRICANTE"]) if modelo_atual else 0,
                                          key=f"fab_eq_{id_eq}")
            with c3:
                modelos_fab = [m for m in modelos if m["FABRICANTE"] == fabricante]
                opcoes_mod = [m["ID"] for m in modelos_fab] + [OUTRO]
                nomes_mod = {m["ID"]: f"{m['MODELO']} ({texto(m['TIPO'])})" for m in modelos_fab}
                nomes_mod[OUTRO] = OUTRO
                id_modelo = st.selectbox("Modelo *", opcoes_mod, format_func=lambda i: nomes_mod[i],
                                         index=opcoes_mod.index(modelo_atual["ID"])
                                         if modelo_atual and modelo_atual["ID"] in opcoes_mod else 0,
                                         key=f"mod_eq_{id_eq}_{fabricante}")
            modelo = next((m for m in modelos_fab if m["ID"] == id_modelo), None)
            modelo_novo = fabricante == OUTRO or id_modelo == OUTRO

            # Documentos já enviados: nota fiscal (do equipamento) e manual (do modelo)
            # (tipo, nome, data, arquivo ou mensagem de erro)
            documentos = []
            try:
                with pool_conexoes().acquire() as conn:
                    if equipamento:
                        for anexo in listar_anexos(conn, ENTIDADE_EQUIPAMENTO, [equipamento["ID"]]).get(equipamento["ID"], []):
                            try:
                                arquivo_doc = conteudo_anexo(conn, anexo["ID"])
                            except AnexoInvalido as e:
                                arquivo_doc = str(e)
                            documentos.append(("Nota fiscal", anexo["NOME_ARQUIVO"], anexo["DATA_ENVIO"], arquivo_doc))
                    if modelo:
                        for manual in listar_manuais(conn, [modelo["ID"]]).get(modelo["ID"], []):
                            documentos.append((f"Manual do modelo {modelo['MODELO']}", manual["NOME_ARQUIVO"],
                                               manual["DATA_UPLOAD"], conteudo_manual(conn, manual["ID"])))
            except oracledb.Error:
                documentos = []   # tabela de anexos ainda não criada (sql/estrutura_banco.sql)
            if documentos:
                st.markdown('<div class="secao-cadastro">Documentos enviados</div>', unsafe_allow_html=True)
                for indice_doc, (tipo_doc, nome_doc, data_doc, arquivo_doc) in enumerate(documentos):
                    d1, d2 = st.columns([5, 1])
                    d1.markdown(f"{tipo_doc}: **{nome_doc}**" + (f" — enviado em {data_doc:%d/%m/%Y}" if data_doc else ""))
                    if isinstance(arquivo_doc, str):
                        d2.error(arquivo_doc)
                    elif arquivo_doc:
                        d2.download_button("Baixar", arquivo_doc[1], file_name=arquivo_doc[0],
                                           mime=arquivo_doc[2] or "application/octet-stream",
                                           key=f"doc_{id_eq}_{indice_doc}", use_container_width=True)

            with st.form(f"form_equipamento_{id_eq}"):
                if modelo_novo:
                    st.markdown('<div class="secao-cadastro">Modelo novo</div>', unsafe_allow_html=True)
                    n1, n2 = st.columns(2)
                    with n1:
                        novo_fabricante = st.text_input("Fabricante do modelo novo *",
                                                        value="" if fabricante == OUTRO else fabricante,
                                                        max_chars=100).strip()
                        novo_modelo = st.text_input("Modelo *", max_chars=150).strip()
                    with n2:
                        novo_tipo = st.text_input("Tipo (ex.: trator, colheitadeira, pulverizador) *",
                                                  max_chars=100).strip()
                        novo_ano = st.number_input("Ano de fabricação", min_value=1950,
                                                   max_value=datetime.date.today().year + 1,
                                                   value=datetime.date.today().year)
                st.markdown('<div class="secao-cadastro">Dados do equipamento</div>', unsafe_allow_html=True)
                d1, d2 = st.columns(2)
                with d1:
                    identificacao = st.text_input("Identificação interna (ex.: TR-01, frota 12)",
                                                  value=texto(equipamento["IDENTIFICACAO_INTERNA"]) if equipamento else "",
                                                  max_chars=50).strip()
                    numero_serie = st.text_input("Número de série / chassi",
                                                 value=texto(equipamento["NUMERO_SERIE"]) if equipamento else "",
                                                 max_chars=100).strip()
                    valor_padrao = (float(equipamento["VALOR_SEGURADO"]) if equipamento and equipamento["VALOR_SEGURADO"]
                                    else float(modelo["VALOR_ESTIMADO"]) if modelo and modelo["VALOR_ESTIMADO"] else 0.0)
                    valor_segurado = st.number_input("Valor segurado (R$) *", min_value=0.0, value=valor_padrao,
                                                     step=10000.0, format="%.2f",
                                                     help="Sugerido: valor de referência do modelo novo.")
                    telemetria_atual = equipamento["TELEMETRIA"] if equipamento else None
                    telemetria = st.radio("Tem telemetria? *", valores_telemetria, horizontal=True,
                                          format_func=rotulo_valor,
                                          index=valores_telemetria.index(telemetria_atual)
                                          if telemetria_atual in valores_telemetria else None)
                with d2:
                    apolice = st.text_input("Número da apólice",
                                            value=texto(equipamento["NUMERO_APOLICE"]) if equipamento else "",
                                            max_chars=100).strip()
                    inicio = st.date_input("Início da vigência", format="DD/MM/YYYY",
                                           value=para_data(equipamento["DATA_INICIO_VIGENCIA"]) if equipamento else None)
                    fim = st.date_input("Fim da vigência", format="DD/MM/YYYY",
                                        value=para_data(equipamento["DATA_FIM_VIGENCIA"]) if equipamento else None)
                    status_atual = equipamento["STATUS"] if equipamento else valores_status[0]
                    status = st.selectbox("Situação *", valores_status, format_func=rotulo_valor,
                                          index=valores_status.index(status_atual) if status_atual in valores_status
                                          else 0)
                uso = {}
                if tem_colunas_uso():
                    st.markdown('<div class="secao-cadastro">Uso do equipamento</div>', unsafe_allow_html=True)
                    st.caption("Usado para programar as manutenções recomendadas (a cada N horas, km ou meses) e "
                               "para os relatórios por tipo de operação.")
                    e1, e2, e3 = st.columns(3)
                    atual_op = texto(equipamento.get("TIPO_OPERACAO")) if equipamento else ""
                    uso["TIPO_OPERACAO"] = e1.selectbox("Tipo de operação principal", TIPOS_OPERACAO,
                                                        index=TIPOS_OPERACAO.index(atual_op)
                                                        if atual_op in TIPOS_OPERACAO else None,
                                                        placeholder="Escolha a operação")
                    uso["DATA_AQUISICAO"] = e2.date_input("Data de aquisição", format="DD/MM/YYYY",
                                                          value=para_data(equipamento.get("DATA_AQUISICAO"))
                                                          if equipamento else None,
                                                          max_value=datetime.date.today())
                    uso["DATA_LEITURA"] = e3.date_input("Data da leitura do horímetro/hodômetro", format="DD/MM/YYYY",
                                                        value=(para_data(equipamento.get("DATA_LEITURA"))
                                                               if equipamento and equipamento.get("DATA_LEITURA")
                                                               else datetime.date.today()),
                                                        max_value=datetime.date.today())
                    h1, h2, h3, h4 = st.columns(4)

                    def numero_atual(coluna):
                        return float(equipamento[coluna]) if equipamento and equipamento.get(coluna) is not None else None
                    uso["HORIMETRO_ATUAL"] = h1.number_input("Horímetro (horas)", min_value=0.0, step=10.0,
                                                             value=numero_atual("HORIMETRO_ATUAL"), format="%.1f")
                    uso["USO_MEDIO_HORAS_MES"] = h2.number_input("Uso médio (horas por mês)", min_value=0.0, step=5.0,
                                                                 value=numero_atual("USO_MEDIO_HORAS_MES"), format="%.1f")
                    uso["HODOMETRO_ATUAL"] = h3.number_input("Hodômetro (km)", min_value=0.0, step=100.0,
                                                             value=numero_atual("HODOMETRO_ATUAL"), format="%.1f")
                    uso["USO_MEDIO_KM_MES"] = h4.number_input("Uso médio (km por mês)", min_value=0.0, step=50.0,
                                                              value=numero_atual("USO_MEDIO_KM_MES"), format="%.1f")
                    st.caption("Máquinas agrícolas usam o horímetro; o hodômetro vale para veículos de apoio. "
                               "Preencha o que se aplica. Depois do cadastro, o horímetro é atualizado todo mês na "
                               "página Comprovação, em Manutenções (leitura mensal, com foto do horímetro).")
                    if tem_coluna_tracao():
                        t1, _ = st.columns([1, 2])
                        atual_tracao = texto(equipamento.get("TIPO_TRACAO")) if equipamento else ""
                        uso["TIPO_TRACAO"] = t1.selectbox(
                            "Tipo de tração", list(TIPOS_TRACAO), format_func=TIPOS_TRACAO.get,
                            index=list(TIPOS_TRACAO).index(atual_tracao) if atual_tracao in TIPOS_TRACAO else None,
                            placeholder="Escolha a tração",
                            help="O manual tem manutenções que valem só para 4WD ou só para 2WD: com a tração "
                                 "informada, a lista de manutenções inclui só as da máquina.")

                st.markdown('<div class="secao-cadastro">Documentos</div>', unsafe_allow_html=True)
                u1, u2 = st.columns(2)
                arquivo_nf = u1.file_uploader(f"Nota fiscal (PDF, JPG ou PNG, até {TAMANHO_MAXIMO_MB[ANEXO_NOTA_FISCAL]} MB)",
                                              type=TIPOS_DOCUMENTO, key=f"nf_{id_eq}")
                arquivo_manual = u2.file_uploader(f"Manual do equipamento (PDF, até {TAMANHO_MAXIMO_MB['MANUAL']} MB)",
                                                  type=TIPOS_MANUAL, key=f"manual_{id_eq}")
                st.caption("O manual fica ligado ao modelo e vale para todas as máquinas do mesmo modelo.")
                enviar_eq = st.form_submit_button("Salvar equipamento")

            if enviar_eq:
                erros = []
                if modelo_novo and not (novo_fabricante and novo_modelo and novo_tipo):
                    erros.append("Modelo novo: informe fabricante, modelo e tipo.")
                if valor_segurado <= 0:
                    erros.append("Valor segurado: informe um valor maior que zero.")
                if telemetria is None:
                    erros.append("Informe se o equipamento tem telemetria.")
                if not identificacao and not numero_serie:
                    erros.append("Informe a identificação interna ou o número de série, para distinguir o equipamento.")
                def serie_normalizada(valor):
                    return re.sub(r"[^0-9A-Z]", "", texto(valor).upper())
                if numero_serie and any(serie_normalizada(e["NUMERO_SERIE"]) == serie_normalizada(numero_serie)
                                        and e["ID"] != id_eq for e in equipamentos):
                    erros.append("Já existe um equipamento com esse número de série.")
                if inicio and fim and fim < inicio:
                    erros.append("O fim da vigência deve ser depois do início.")
                if uso.get("DATA_AQUISICAO") and uso.get("DATA_LEITURA") and uso["DATA_LEITURA"] < uso["DATA_AQUISICAO"]:
                    erros.append("A data da leitura do horímetro/hodômetro não pode ser anterior à aquisição.")
                if uso.get("HORIMETRO_ATUAL") and not uso.get("USO_MEDIO_HORAS_MES"):
                    erros.append("Informe o uso médio em horas por mês (necessário para programar as manutenções).")
                if uso.get("HODOMETRO_ATUAL") and not uso.get("USO_MEDIO_KM_MES"):
                    erros.append("Informe o uso médio em km por mês (necessário para programar as manutenções).")
                for arquivo_up, tipos_up, limite_up in ((arquivo_nf, TIPOS_DOCUMENTO, TAMANHO_MAXIMO_MB[ANEXO_NOTA_FISCAL]),
                                                        (arquivo_manual, TIPOS_MANUAL, TAMANHO_MAXIMO_MB["MANUAL"])):
                    if arquivo_up is not None:
                        try:
                            validar_arquivo(nome_seguro(arquivo_up.name), arquivo_up.getvalue(), tipos_up, limite_up)
                        except AnexoInvalido as e:
                            erros.append(str(e))
                if erros:
                    mostrar_erros(erros)
                else:
                    try:
                        if modelo_novo:
                            id_modelo_final = inserir("CS_EQUIPAMENTOS_MODELOS", {
                                "TIPO": novo_tipo, "MODELO": novo_modelo, "FABRICANTE": novo_fabricante,
                                "ANO_FABRICACAO": int(novo_ano), "VALOR_ESTIMADO": None})
                            modelos_equipamentos.clear()
                        else:
                            id_modelo_final = id_modelo
                        dados = {"ID_FAZENDA": id_faz_eq, "ID_EQUIPAMENTO_MODELO": id_modelo_final,
                                 "NUMERO_SERIE": numero_serie or None, "IDENTIFICACAO_INTERNA": identificacao or None,
                                 "VALOR_SEGURADO": round(valor_segurado, 2), "TELEMETRIA": telemetria,
                                 "NUMERO_APOLICE": apolice or None, "DATA_INICIO_VIGENCIA": inicio,
                                 "DATA_FIM_VIGENCIA": fim, "STATUS": status}
                        dados.update(uso)
                        if equipamento is None:
                            id_salvo = inserir("CS_EQUIPAMENTOS_SEGURADOS", dados)
                        else:
                            # Só altera equipamento de propriedade do próprio cliente
                            executar(f"""
                                UPDATE CS_EQUIPAMENTOS_SEGURADOS SET {', '.join(f'{c} = :{c}' for c in dados)}
                                WHERE ID = :id_eq
                                  AND ID_FAZENDA IN (SELECT ID FROM CS_FAZENDAS WHERE ID_CLIENTE = :id_cliente)
                            """, dict(dados, id_eq=id_eq, id_cliente=id_cliente))
                            id_salvo = id_eq
                        enviados = []
                        with pool_conexoes().acquire() as conn:
                            if arquivo_nf is not None:
                                salvar_anexo(conn, ENTIDADE_EQUIPAMENTO, id_salvo, ANEXO_NOTA_FISCAL, arquivo_nf.name,
                                             arquivo_nf.getvalue(), arquivo_nf.type,
                                             st.session_state.get("usuario_ref"))
                                enviados.append("nota fiscal")
                            if arquivo_manual is not None:
                                salvar_manual(conn, id_modelo_final, arquivo_manual.name, arquivo_manual.getvalue())
                                enviados.append("manual")
                            # Histórico do horímetro (a leitura informada no cadastro entra como ponto de partida)
                            registrar_leitura_cadastro(conn, id_salvo, uso.get("DATA_LEITURA"),
                                                       uso.get("HORIMETRO_ATUAL"), uso.get("HODOMETRO_ATUAL"),
                                                       st.session_state.get("usuario_ref"))
                            # Lista de manutenções a comprovar (recomendações do manual do modelo)
                            programacao = programar_equipamento(conn, id_salvo)
                            conn.commit()
                        mensagem = "Equipamento incluído" if equipamento is None else "Equipamento atualizado"
                        if enviados:
                            mensagem += f" com {' e '.join(enviados)}"
                        if programacao["sem_manual"]:
                            mensagem += (". Este modelo não tem manual com recomendações de manutenção vinculado: "
                                         "nenhuma manutenção programada")
                        else:
                            mensagem += f". {programacao['geradas']} manutenção(ões) programada(s) para comprovação"
                        depois_de_salvar(mensagem + ".", f"cadastro_equipamento_{id_cliente}" if equipamento is None
                                         else None, id_salvo)
                    except AnexoInvalido as e:
                        st.error(str(e))
                    except oracledb.Error as e:
                        st.error(f"Erro ao salvar o equipamento ou os documentos: {e}")

# ===========================================================================
# 3. Maturidade da gestão da manutenção
# ===========================================================================
TABELA_AVALIACAO = "CS_SCORE_MANUTENCAO_PROCEDIMENTOS"


def mapear_colunas_avaliacao():
    """Confere no banco as colunas das 15 respostas e acha as dos subtotais por pilar e da nota final.
    Devolve (perguntas, subtotais {pilar: coluna}, extras {coluna: tipo}, erro)."""
    colunas = colunas_da_tabela(TABELA_AVALIACAO)
    if not colunas:
        return [], {}, {}, f"A tabela da avaliação ({TABELA_AVALIACAO}) não foi encontrada."
    faltando = [c for c in COLUNAS_PERGUNTAS if c not in colunas]
    if faltando:
        return [], {}, {}, "Colunas da avaliação não encontradas no banco: " + ", ".join(faltando) + "."
    subtotais = {}
    for coluna in colunas:
        if coluna.startswith("SUBTOTAL"):
            if "MANUTENCAO" in coluna:
                subtotais[0] = coluna
            elif "RISCO" in coluna:
                subtotais[1] = coluna
            elif "GOVERN" in coluna:
                subtotais[2] = coluna
    extras = {}
    usadas = {"ID", "ID_FAZENDA", "DATA_AVALIACAO"} | set(COLUNAS_PERGUNTAS) | set(subtotais.values())
    for coluna, info in colunas.items():
        if coluna in usadas:
            continue
        numerica = info["tipo"] in ("NUMBER", "FLOAT")
        if numerica and any(p in coluna for p in ("SCORE", "TOTAL", "PONTUACAO", "NOTA", "PERCENT", "INDICE")):
            extras[coluna] = "nota"          # SCORE_FINAL: nota ponderada de 0 a 15
        elif not numerica and any(p in coluna for p in ("NIVEL", "CLASSIFICACAO", "MATURIDADE")):
            extras[coluna] = "nivel"
        elif not info["nulo"]:
            return [], {}, {}, f"A coluna obrigatória {coluna} da avaliação não é preenchida pelo formulário."
    return list(COLUNAS_PERGUNTAS), subtotais, extras, None


def nivel_maturidade(percentual):
    return next((n for n in NIVEIS_MATURIDADE if percentual >= n[0]), NIVEIS_MATURIDADE[-1])


def avaliacoes_do_cliente():
    if id_cliente is None:
        return []
    return consultar(f"""
        SELECT a.*, f.NOME_FAZENDA FROM {TABELA_AVALIACAO} a
        JOIN CS_FAZENDAS f ON f.ID = a.ID_FAZENDA
        WHERE f.ID_CLIENTE = :cliente
        ORDER BY a.DATA_AVALIACAO DESC
    """, {"cliente": id_cliente})


def pontuacao(respostas):
    """Por pilar: pontos (0 a 3 por afirmativa), máximo, peso, valor máximo na nota e pontos ponderados;
    e a nota final (0 a NOTA_MAXIMA). Pilar = pontos / máximo x peso% x NOTA_MAXIMA (vale até 6, 6 e 3)."""
    pilares, inicio = [], 0
    for (_, perguntas_pilar), peso in zip(PILARES, PESOS_PILARES):
        fim = inicio + len(perguntas_pilar)
        pontos, maximo = sum(respostas[inicio:fim]), 3 * len(perguntas_pilar)
        vale = peso / 100 * NOTA_MAXIMA
        pilares.append({"pontos": pontos, "maximo": maximo, "peso": peso, "vale": vale,
                        "ponderado": pontos / maximo * vale})
        inicio = fim
    return pilares, sum(p["ponderado"] for p in pilares)


def fmt_nota(valor):
    texto_nota = f"{valor:.2f}".rstrip("0").rstrip(".")
    return texto_nota.replace(".", ",")


def percentual_nota(nota):
    return nota / NOTA_MAXIMA * 100


def mostrar_resultado(respostas, titulo):
    pilares, nota = pontuacao(respostas)
    _, nome_nivel, descricao = nivel_maturidade(percentual_nota(nota))
    st.markdown(f'<div class="secao-cadastro">{titulo}</div>', unsafe_allow_html=True)
    colunas_res = st.columns(len(PILARES) + 1)
    colunas_res[0].metric("Nota final", f"{fmt_nota(nota)} de {NOTA_MAXIMA}", nome_nivel, delta_color="off")
    for coluna_res, (nome_pilar, _), pilar in zip(colunas_res[1:], PILARES, pilares):
        coluna_res.metric(f"{nome_pilar} (peso {pilar['peso']}%)",
                          f"{fmt_nota(pilar['ponderado'])} de {fmt_nota(pilar['vale'])}",
                          f"{pilar['pontos']} de {pilar['maximo']} pontos", delta_color="off")
    st.markdown(f"**Nível {nome_nivel}:** {descricao}")
    # Pontos de atenção: afirmativas com 0 ou 1
    atencao, indice = [], 0
    for nome_pilar, perguntas_pilar in PILARES:
        for pergunta in perguntas_pilar:
            if respostas[indice] <= 1:
                atencao.append(f"- **{nome_pilar}:** {pergunta} ({ESCALA[respostas[indice]][0].lower()})")
            indice += 1
    if atencao:
        st.markdown("**Pontos de atenção** (práticas pontuais ou inexistentes):\n" + "\n".join(atencao))


with aba_maturidade:
    fazendas = fazendas_do_cliente()
    if id_cliente is None or not fazendas:
        st.info("Cadastre o cliente e ao menos uma propriedade (aba 1) antes de responder a avaliação.")
    else:
        colunas_perguntas, colunas_subtotais, colunas_extras, erro_colunas = mapear_colunas_avaliacao()
        avaliacoes = avaliacoes_do_cliente()
        if erro_colunas:
            st.error(erro_colunas)
        else:
            st.markdown("**Formulário de Avaliação de Governança, Manutenção e Risco Operacional**")
            st.markdown("Para cada uma das afirmativas abaixo, assinale a opção que melhor traduz o grau de "
                        "concordância com a realidade operacional da sua propriedade:")
            st.markdown("\n".join(f"- **{nota} — {nome}:** {descricao}" for nota, (nome, descricao) in ESCALA.items()))

            TODAS = "__todas__"
            ids_faz = [f["ID"] for f in fazendas]
            nomes_f = {f["ID"]: f["NOME_FAZENDA"] for f in fazendas}
            nomes_f[TODAS] = f"Todas as propriedades ({len(fazendas)})"
            opcoes_av = ([TODAS] if len(fazendas) > 1 else []) + ids_faz
            alvo = st.selectbox("Propriedade avaliada", opcoes_av, format_func=lambda i: nomes_f[i],
                                key=f"maturidade_fazenda_{id_cliente}",
                                help="Se a gestão da manutenção é a mesma em todas as propriedades, responda uma vez "
                                     "para todas.")
            ids_alvo = ids_faz if alvo == TODAS else [alvo]
            anterior = next((a for a in avaliacoes if a["ID_FAZENDA"] in ids_alvo), None)
            if anterior:
                st.caption(f"Última avaliação: {anterior['DATA_AVALIACAO']:%d/%m/%Y} ({anterior['NOME_FAZENDA']}). "
                           "As respostas anteriores vêm preenchidas: altere o que mudou e envie para registrar a "
                           "nova avaliação.")

            opcoes_nota = [3, 2, 1, 0]
            with st.form(f"form_maturidade_{alvo}"):
                respostas, indice = [], 0
                for numero_pilar, (nome_pilar, perguntas_pilar) in enumerate(PILARES, start=1):
                    st.markdown(f'<div class="secao-cadastro">Pilar {numero_pilar}: {nome_pilar}</div>',
                                unsafe_allow_html=True)
                    for pergunta in perguntas_pilar:
                        coluna = colunas_perguntas[indice]
                        valor_anterior = int(anterior[coluna]) if anterior and anterior.get(coluna) is not None else None
                        # Numeração contínua (1 a 15), igual à das colunas Q1 a Q15 da tabela
                        st.markdown(f'<div class="pergunta"><b>{indice + 1}.</b> {pergunta}</div>', unsafe_allow_html=True)
                        respostas.append(st.radio(
                            pergunta, opcoes_nota, horizontal=True, label_visibility="collapsed",
                            format_func=lambda n: f"{n} — {ESCALA[n][0]}",
                            index=opcoes_nota.index(valor_anterior) if valor_anterior in opcoes_nota else None,
                            disabled=somente_leitura, key=f"resp_{alvo}_{coluna}"))
                        indice += 1
                enviar_av = st.form_submit_button("Enviar avaliação", disabled=somente_leitura)

            if enviar_av:
                sem_resposta = sum(1 for r in respostas if r is None)
                if sem_resposta:
                    st.error(f"Responda todas as afirmativas: falta(m) {sem_resposta}.")
                else:
                    pilares, nota = pontuacao(respostas)
                    dados = {c: r for c, r in zip(colunas_perguntas, respostas)}
                    # Subtotais = pontos ponderados do pilar (0 a 6, 0 a 6, 0 a 3); SCORE_FINAL = soma (0 a 15)
                    for indice_pilar, coluna in colunas_subtotais.items():
                        dados[coluna] = round(pilares[indice_pilar]["ponderado"], 2)
                    for coluna, tipo in colunas_extras.items():
                        dados[coluna] = (round(nota, 2) if tipo == "nota"
                                         else nivel_maturidade(percentual_nota(nota))[1])
                    try:
                        with pool_conexoes().acquire() as conn:
                            with conn.cursor() as cursor:
                                for id_faz in ids_alvo:
                                    colunas_sql = ["ID_FAZENDA", "DATA_AVALIACAO"] + list(dados)
                                    valores_sql = [":ID_FAZENDA", "SYSTIMESTAMP"] + [f":{c}" for c in dados]
                                    parametros = dict(dados, ID_FAZENDA=id_faz)
                                    try:
                                        cursor.execute(f"INSERT INTO {TABELA_AVALIACAO} ({', '.join(colunas_sql)}) "
                                                       f"VALUES ({', '.join(valores_sql)})", parametros)
                                    except oracledb.DatabaseError as e:
                                        if getattr(e.args[0], "code", None) != ORA_VALOR_NULO:
                                            raise
                                        cursor.execute(f"SELECT NVL(MAX(ID), 0) + 1 FROM {TABELA_AVALIACAO}")
                                        parametros["id_novo"] = cursor.fetchone()[0]
                                        cursor.execute(f"INSERT INTO {TABELA_AVALIACAO} (ID, {', '.join(colunas_sql)}) "
                                                       f"VALUES (:id_novo, {', '.join(valores_sql)})", parametros)
                            conn.commit()
                        st.session_state["maturidade_resultado"] = respostas
                        depois_de_salvar("Avaliação registrada" + (f" para as {len(ids_alvo)} propriedades."
                                                                   if len(ids_alvo) > 1 else "."))
                    except oracledb.Error as e:
                        st.error(f"Erro ao salvar a avaliação: {e}")

            if st.session_state.get("maturidade_resultado"):
                mostrar_resultado(st.session_state.pop("maturidade_resultado"), "Resultado da avaliação")
            elif anterior:
                respostas_anteriores = [int(anterior[c] or 0) for c in colunas_perguntas]
                mostrar_resultado(respostas_anteriores, "Resultado da última avaliação")

            if avaliacoes:
                st.markdown('<div class="secao-cadastro">Histórico de avaliações</div>', unsafe_allow_html=True)
                historico = []
                for a in avaliacoes:
                    respostas_a = [int(a[c] or 0) for c in colunas_perguntas]
                    pilares, nota = pontuacao(respostas_a)
                    linha = {"Data": f"{a['DATA_AVALIACAO']:%d/%m/%Y %H:%M}", "Propriedade": a["NOME_FAZENDA"]}
                    for (nome_pilar, _), pilar in zip(PILARES, pilares):
                        linha[f"{nome_pilar} (até {fmt_nota(pilar['vale'])})"] = fmt_nota(pilar["ponderado"])
                    linha["Nota final"] = f"{fmt_nota(nota)} de {NOTA_MAXIMA}"
                    linha["Maturidade"] = nivel_maturidade(percentual_nota(nota))[1]
                    historico.append(linha)
                tabela(historico)

render_footer()
