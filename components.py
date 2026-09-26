import streamlit as st
from pathlib import Path

def render_logo():
    """Renderiza a logo ampliada e centralizada."""
    caminho_logo = Path(__file__).parent / "assets" / "CS_Logo.jpeg"
    if caminho_logo.exists():
        col1, col2, col3 = st.columns([1, 1.8, 1])  # Ajusta a proporção central
        with col2:
            st.image(str(caminho_logo), width=350)  # Logo ampliada para 350px

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