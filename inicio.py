# -*- coding: utf-8 -*-
"""Início: login e atalhos para as páginas, conforme o perfil de acesso (aberta pelo menu montado no app.py)."""
import streamlit as st
import oracledb
import bcrypt
from auth import USER, PASSWORD, DSN
from components import (render_header, render_footer, render_logo, registrar_uso, perfil_do_usuario,
                        paginas_do_perfil, PERFIL_SOMPO, PERFIL_PRODUTOR)
from requisitos import usuarios

st.set_page_config(page_title="Campo Seguro - Início", layout="wide")

# Inicializa as variáveis de sessão
if "logado" not in st.session_state:
    st.session_state["logado"] = False
    st.session_state["usuario_nome"] = ""
    st.session_state["usuario_id"] = None
    st.session_state["usuario_ref"] = None
    st.session_state["role"] = ""
    st.session_state["cliente_cnpj"] = None


def validar_login(email, senha):
    """Procura o usuário pelo hash do e-mail (dados pessoais criptografados, requisitos/usuarios.py) e confere a
    senha (bcrypt). Devolve o usuário (dict) ou None; registra a entrada ou a recusa em CS_LOG_ACOES."""
    try:
        conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        try:
            with conn.cursor() as cursor:
                usuario = usuarios.buscar_para_login(cursor, email)
        finally:
            conn.close()
    except Exception as e:
        st.error(f"Erro ao conectar com o banco de dados: {e}")
        return None
    if usuario and usuario["SENHA"] and bcrypt.checkpw(senha.encode("utf-8"), usuario["SENHA"].encode("utf-8")):
        return usuario
    # sem o e-mail no registro (dado pessoal): só o motivo, e a referência quando o usuário existe
    registrar_uso("LOGIN_RECUSADO", "ACESSO", usuarios.referencia(usuario["ID"]) if usuario else None,
                  "senha incorreta" if usuario else "e-mail não cadastrado", pagina="inicio")
    return None


def sair():
    registrar_uso("LOGOUT", "ACESSO", st.session_state.get("usuario_ref"), pagina="inicio")
    for chave in ("usuario_id", "usuario_ref", "cliente_cnpj", "paginas_registradas", "pagina_atual"):
        st.session_state.pop(chave, None)
    st.session_state["logado"] = False
    st.session_state["usuario_nome"] = ""
    st.session_state["role"] = ""
    st.session_state["cliente_cnpj"] = None


# --- ESTILIZAÇÃO COMPLEMENTAR PARA OS BOTÕES CINZAS E MAIORES ---
st.markdown("""
    <style>
    /* Estilo para botões cinzas maiores e sem ícones */
    div.stButton > button:first-child {
        background-color: #e9ecef !important;
        color: #1A4A75 !important;
        border-radius: 6px !important;
        border: 1px solid #ced4da !important;
        width: 100% !important;
        font-weight: 700 !important;
        font-size: 1.1rem !important;
        padding: 14px 20px !important;
    }
    /* atalhos das páginas: todos com a mesma altura, com espaço para o texto em duas linhas */
    .st-key-atalhos div.stButton > button {
        min-height: 5.6rem !important; height: 100% !important;
        display: flex !important; align-items: center !important; justify-content: center !important;
    }
    div.stButton > button:first-child:hover {
        background-color: #dde2e6 !important;
        color: #133557 !important;
        border-color: #adb5bd !important;
    }
    </style>
""", unsafe_allow_html=True)

# --- TELA DE LOGIN ---
if not st.session_state["logado"]:
    render_logo()
    
    col1, col2, col3 = st.columns([1, 1.5, 1])
    
    with col2:
        st.markdown(
            "<p style='text-align: center; color: #1A4A75; font-size: 18px; font-weight: 600; margin-bottom: 20px;'>Faça o seu login para continuar</p>", 
            unsafe_allow_html=True
        )
        
        email_input = st.text_input("E-mail corporativo")
        senha_input = st.text_input("Senha", type="password")
        
        st.markdown("<br>", unsafe_allow_html=True)
        
        if st.button("Entrar", use_container_width=True):
            if not email_input or not senha_input:
                st.warning("Por favor, preencha o e-mail e a senha.")
            else:
                usuario = validar_login(email_input, senha_input)
                if usuario:
                    st.session_state["logado"] = True
                    st.session_state["usuario_nome"] = usuario["NOME"]
                    st.session_state["usuario_id"] = usuario["ID"]
                    st.session_state["usuario_ref"] = usuarios.referencia(usuario["ID"])
                    st.session_state["role"] = str(usuario["ROLE"]).strip()
                    st.session_state["cliente_cnpj"] = usuario["CLIENTE_CNPJ"]
                    registrar_uso("LOGIN", "ACESSO", st.session_state["usuario_ref"], pagina="inicio")
                    st.success(f"Bem-vindo(a), {usuario['NOME']}!")
                    st.rerun()
                else:
                    st.error("E-mail ou senha inválidos.")

    render_footer()

else:
    # --- ÁREA LOGADA / PAINEL PRINCIPAL ---
    render_header()
    
    perfil_atual = perfil_do_usuario(st.session_state["role"])

    st.markdown(f"### Olá, {st.session_state['usuario_nome']}!")
    st.info(f"Perfil de Acesso: **{st.session_state['role']}**")

    st.markdown("---")

    # Atalhos só para as páginas que o perfil pode abrir (as mesmas do menu lateral)
    if perfil_atual == PERFIL_PRODUTOR:
        st.write("**Painel do Cliente:** alertas das suas fazendas e comprovação das manutenções dos seus "
                 "equipamentos.")
        st.warning(f"Cliente vinculado (CNPJ): {st.session_state['cliente_cnpj'] or 'Não vinculado'}")
    elif perfil_atual == PERFIL_SOMPO:
        st.write("**Painel de Subscrição:** Score Risk, alertas, cadastro e manutenções dos clientes.")
    else:
        st.markdown("<br>", unsafe_allow_html=True)
    atalhos = [(arquivo, titulo) for _, arquivo, titulo in paginas_do_perfil(perfil_atual)]
    ordem = ["Alertas", "Score Risk", "Monitoramento", "Cadastro", "Programação", "Comprovação"]
    atalhos.sort(key=lambda a: ordem.index(a[1]) if a[1] in ordem else len(ordem))
    if atalhos:
        colunas = st.container(key="atalhos").columns(len(atalhos))
        for coluna, (arquivo, titulo) in zip(colunas, atalhos):
            with coluna:
                if st.button(titulo, use_container_width=True):
                    st.switch_page(arquivo)

    st.markdown("<br><hr>", unsafe_allow_html=True)
    if st.button("Encerrar Sessão (Sair)"):
        sair()
        st.rerun()

    render_footer()