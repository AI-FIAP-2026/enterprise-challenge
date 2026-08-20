# -*- coding: utf-8 -*-
import streamlit as st
import bcrypt
import oracledb
import os

# Tenta importar o oracle_config localmente (caso exista no seu Mac)
try:
    from oracle_config import USER as CFG_USER, PASSWORD as CFG_PASSWORD, DSN as CFG_DSN
except ImportError:
    CFG_USER, CFG_PASSWORD, CFG_DSN = None, None, None

# Configuração da página e identidade visual
st.set_page_config(page_title="Campo Seguro - Login", page_icon="🛡️")

# Estilização baseada no Guia de Estilo (Botão azul #1A4A75)
st.markdown("""
    <style>
    .main {
        background-color: #FCFCFC;
    }
    .stButton>button {
        background-color: #1A4A75;
        color: white;
        border-radius: 4px;
        border: none;
        font-weight: bold;
        width: 100%;
        padding: 0.5rem;
    }
    .stButton>button:hover {
        background-color: #14375a;
        color: white;
    }
    .login-title {
        font-size: 24px;
        font-weight: 600;
        color: #262730;
        margin-top: 20px;
        margin-bottom: 10px;
    }
    </style>
""", unsafe_allow_html=True)

# Exibindo o Logo (verifique se a extensão é .jpeg ou .png)
col1, col2, col3 = st.columns([1, 3, 1])
with col2:
    try:
        st.image("assets/CS_Logo.jpeg", width=450)
    except:
        try:
            st.image("assets/CS_Logo.png", width=450)
        except:
            st.warning("Logotipo não encontrado na pasta assets.")

st.write("")

# Define credenciais: Prioriza a Vercel (os.getenv) e usa o oracle_config local se estiver vazio
USER = os.getenv('DB_USER') or CFG_USER
PASSWORD = os.getenv('DB_PASSWORD') or CFG_PASSWORD
DSN = os.getenv('DB_DSN') or CFG_DSN

# Título limpo sem o ícone de hiperlink
st.markdown('<p class="login-title">Faça o seu login para continuar</p>', unsafe_allow_html=True)

email = st.text_input("E-mail corporativo")
senha = st.text_input("Senha", type="password")

if st.button("Entrar"):
    if not email or not senha:
        st.error("Por favor, preencha todos os campos.")
    else:
        try:
            # Conecta ao banco Oracle
            connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
            cursor = connection.cursor()
            
            cursor.execute("SELECT senha, nome FROM CS_USUARIOS WHERE email = :email", {"email": email})
            resultado = cursor.fetchone()
            
            cursor.close()
            connection.close()
            
            if resultado:
                senha_hash_banco, nome_usuario = resultado[0], resultado[1]
                
                # Valida a senha criptografada com bcrypt
                if bcrypt.checkpw(senha.encode('utf-8'), senha_hash_banco.encode('utf-8')):
                    st.success(f"Login bem-sucedido! Seja bem-vindo(a), {nome_usuario}.")
                else:
                    st.error("E-mail ou senha incorretos.")
            else:
                st.error("E-mail ou senha incorretos.")
                
        except Exception as e:
            st.error(f"Erro ao conectar com o banco de dados: {e}")