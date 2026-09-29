# -*- coding: utf-8 -*-
"""
Campo Seguro: ponto de entrada (streamlit run app.py).

Monta o menu lateral (st.navigation) e aplica o estilo comum a todas as páginas. O conteúdo da tela inicial
(login e atalhos) fica em inicio.py; as demais páginas continuam em pages/.
"""
import sys

# Alguns módulos (modelos/, servicos/) imprimem emoji em print() de log/debug.
# No console padrão do Windows (cp1252) isso derruba o processo com
# UnicodeEncodeError. Força UTF-8 aqui, no único ponto de entrada, para não
# depender de quem/como o `streamlit run` é chamado.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import streamlit as st
from components import aplicar_estilo_global, perfil_do_usuario, paginas_do_perfil

INICIO = st.Page("inicio.py", title="Início", default=True)

if st.session_state.get("logado"):
    perfil = perfil_do_usuario(st.session_state.get("role"))
    menu = {"": [INICIO]}
    # o menu mostra só as páginas que o perfil pode abrir (components.PAGINAS; cada página também confere)
    for grupo, arquivo, titulo in paginas_do_perfil(perfil):
        menu.setdefault(grupo, []).append(st.Page(arquivo, title=titulo))
else:
    menu = [INICIO]            # antes do login só a tela inicial (o menu fica escondido)

pagina = st.navigation(menu)
aplicar_estilo_global()
pagina.run()
