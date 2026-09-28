# -*- coding: utf-8 -*-
import sys

# Alguns módulos (modelos/, servicos/) imprimem emoji em print() de log/debug.
# No console padrão do Windows (cp1252) isso derruba o processo com
# UnicodeEncodeError. Força UTF-8 aqui, no único ponto de entrada, para não
# depender de quem/como o `streamlit run` é chamado.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import streamlit as st
import oracledb
import bcrypt
from auth import USER, PASSWORD, DSN
from components import render_header, render_footer, render_logo

st.set_page_config(
    page_title="Campo Seguro - Login",
    layout="wide"
)

# Inicializa as variáveis de sessão
if "logado" not in st.session_state:
    st.session_state["logado"] = False
    st.session_state["usuario_nome"] = ""
    st.session_state["role"] = ""
    st.session_state["cliente_cnpj"] = None

def validar_login(email, senha):
    try:
        conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = conn.cursor()
        cursor.execute("SELECT NOME, SENHA, ROLE, CLIENTE_CNPJ FROM CS_USUARIOS WHERE EMAIL = :1", (email,))
        resultado = cursor.fetchone()
        cursor.close()
        conn.close()
        
        if resultado:
            nome_usuario, hash_banco, role_usuario, cnpj_usuario = resultado
            if bcrypt.checkpw(senha.encode('utf-8'), hash_banco.encode('utf-8')):
                return True, nome_usuario, role_usuario, cnpj_usuario
        return False, None, None, None
    except Exception as e:
        st.error(f"Erro ao conectar com o banco de dados: {e}")
        return False, None, None, None

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
                sucesso, nome, role, cnpj = validar_login(email_input, senha_input)
                if sucesso:
                    st.session_state["logado"] = True
                    st.session_state["usuario_nome"] = nome
                    st.session_state["role"] = str(role).strip()
                    st.session_state["cliente_cnpj"] = cnpj
                    st.success(f"Bem-vindo(a), {nome}!")
                    st.rerun()
                else:
                    st.error("E-mail ou senha inválidos.")

    render_footer()

else:
    # --- ÁREA LOGADA / PAINEL PRINCIPAL ---
    render_header()
    
    role_atual = st.session_state['role'].lower()
    
    st.markdown(f"### Olá, {st.session_state['usuario_nome']}!")
    st.info(f"Perfil de Acesso: **{st.session_state['role']}**")
    
    st.markdown("---")
    
    # Restrições de navegação baseadas no perfil de acesso
    if "operador" in role_atual:
        st.write("🔵 **Painel do Operador:** Visualização restrita aos alertas e monitoramento da sua propriedade.")
        st.warning(f"Propriedade vinculada (CNPJ): {st.session_state['cliente_cnpj'] if st.session_state['cliente_cnpj'] else 'Não vinculado'}")
        
        if st.button("Alertas", use_container_width=True):
            st.switch_page("pages/1_Alertas.py")
        
    elif "subscrição" in role_atual or "analista" in role_atual:
        st.write("🟡 **Painel de Subscrição:** Acesso liberado aos módulos de Score de Risco e Alertas.")
        
        b1, b2 = st.columns(2)
        with b1:
            if st.button("Alertas", use_container_width=True):
                st.switch_page("pages/1_Alertas.py")
        with b2:
            if st.button("Score Risk", use_container_width=True):
                st.switch_page("pages/3_Score_Risk.py")
            
    else: # Administrador / Outros
        st.markdown("<br>", unsafe_allow_html=True)
        
        # Três botões solicitados em destaque (maiores, cinzas, sem ícones)
        col_b1, col_b2, col_b3 = st.columns(3)
        
        with col_b1:
            if st.button("Alertas", use_container_width=True):
                st.switch_page("pages/1_Alertas.py")
        with col_b2:
            if st.button("Score Risk", use_container_width=True):
                st.switch_page("pages/3_Score_Risk.py")
        with col_b3:
            if st.button("Monitoramento", use_container_width=True):
                st.switch_page("pages/2_Monitoramento.py")

    st.markdown("<br><hr>", unsafe_allow_html=True)
    if st.button("Encerrar Sessão (Sair)"):
        st.session_state["logado"] = False
        st.session_state["usuario_nome"] = ""
        st.session_state["role"] = ""
        st.session_state["cliente_cnpj"] = None
        st.rerun()
        
    render_footer()