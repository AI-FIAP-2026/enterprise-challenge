# -*- coding: utf-8 -*-
"""
Manutenções programadas: as recomendações dos manuais dos equipamentos. Duas vistas:

Recomendações gravadas: consulta do que está em CS_EQUIPAMENTOS_ORIENTACOES, com filtros (equipamento, tipo,
subsistema), contagens, gráficos e download em CSV (antiga página "Orientações de Manutenção", do módulo NLP).

Ler um manual: leitura do manual com IA, revisão em tela e gravação.
1. Manual: primeiro o PDF (enviado aqui, até 70 MB, já em data/raw ou já gravado no banco); depois os modelos a que
   ele se aplica (um manual pode valer para vários modelos da série, ex.: Mahindra 6065 e 6075). O sistema lê a capa,
   sugere o código fonte e já marca os modelos cadastrados citados nas primeiras páginas; modelo que falta pode ser
   cadastrado na hora. Manual digitalizado (páginas só com imagem): botão "Aplicar OCR" (requisitos/manuais_ocr.py)
   grava uma cópia com o texto reconhecido.
2. Leitura com o Claude Sonnet 5 (pago): NPL 1, 2 e 3 numa leitura só — campo_seguro/nlp/leitor_ia.py. Exige a chave
   da API da Anthropic digitada na hora e um limite de gasto; nenhuma chamada começa se puder passar do limite; cada
   chamada fica registrada (CS_NLP_USO_IA).
3. Revisão: a leitura é comparada com as recomendações já gravadas para os modelos (nova, nova diferente da
   gravada, igual à gravada, gravada que não veio na leitura). O revisor marca o que fica ("Manter", com "Selecionar
   tudo") e pode corrigir os campos. Novas desmarcadas ficam em CS_NLP_REVISOES e ensinam o sistema (itens parecidos
   vêm desmarcados e a IA recebe esses exemplos do que não extrair); gravadas desmarcadas são removidas, menos as que
   já têm manutenção comprovada.
4. Gravação: resumo do que muda; grava em CS_EQUIPAMENTOS_ORIENTACOES, liga o manual aos modelos e, se pedido,
   refaz a programação das manutenções das máquinas desses modelos.

Acesso: Sompo e administrador. Antes de usar: sql/estrutura_banco.sql.
"""
import sys
import os
import json
import re
import math
import hashlib
import datetime
from pathlib import Path

import pandas as pd
import streamlit as st
import oracledb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

st.set_page_config(page_title="Campo Seguro - Programação", layout="wide")

from components import exigir_login, render_footer, tabela, nome_usuario, ALTURA_LINHA_COM_QUEBRA, PERFIL_ADMIN, PERFIL_SOMPO
from auth import USER, PASSWORD, DSN
from campo_seguro.nlp import schema as sch
from campo_seguro.nlp import leitor_ia as ia
from campo_seguro.nlp.leitor_automatico import identificar_manual, sugerir_codigo
from requisitos import manuais as mn
from requisitos import auditoria
from requisitos import manuais_ocr as ocr
from requisitos.anexos import conteudo_manual, PASTA_MANUAIS, TAMANHO_MAXIMO_MB
from requisitos.programacao_manutencao import programar_equipamento, regra_cronograma, REGRA_IDEAL

PERFIS_COM_ACESSO = [PERFIL_ADMIN, PERFIL_SOMPO]
perfil = exigir_login(PERFIS_COM_ACESSO)
# quem fez fica gravado como referência do usuário ("USR-5"), sem o nome (requisitos/usuarios.py)
usuario = st.session_state.get("usuario_ref") or st.session_state.get("usuario_nome") or ""

PASTA_IA = Path(__file__).resolve().parents[1] / "outputs" / "ia"
LIMITE_MAXIMO_USD = 5.00             # teto do campo de limite por leitura
NOMES_TIPO = {"MANUTENCAO_PROGRAMADA": "Manutenção programada (NPL 1)",
              "ALERTA_TEMP_MINIMA": "Frio extremo (NPL 2)", "ALERTA_TEMP_MAXIMA": "Calor extremo (NPL 2)",
              "RISCO_CHUVA_DESLIZE": "Chuva e lama (NPL 3)"}

aviso_salvo = st.session_state.pop("_aviso_manut_prog", None)


@st.cache_resource(show_spinner=False)
def pool_conexoes():
    return oracledb.create_pool(user=USER, password=PASSWORD, dsn=DSN, min=1, max=4, increment=1)


def moeda(valor):
    return f"US$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def moeda_md(valor):
    """Para textos em markdown (o $ sozinho vira fórmula no Streamlit)."""
    return moeda(valor).replace("$", "\\$")


@st.cache_data(show_spinner=False)
def identificar(caminho, hash_pdf):
    import fitz
    doc = fitz.open(caminho)
    try:
        info = identificar_manual(doc, Path(caminho).name)
        info["paginas"] = len(doc)
        # capa e primeiras páginas: onde o manual lista os modelos a que se aplica
        info["texto_inicio"] = "\n".join(doc[i].get_text() for i in range(min(6, len(doc))))
    finally:
        doc.close()
    return info


@st.cache_data(show_spinner=False)
def paginas_com_texto(caminho, hash_pdf):
    return ocr.paginas_com_texto(caminho)


@st.cache_data(show_spinner="Selecionando as páginas para a IA...")
def lotes_ia(caminho, hash_pdf, npls, max_paginas):
    selecao = ia.selecionar_paginas(caminho, list(npls), max_paginas)
    return selecao, ia.montar_lotes(caminho, list(npls), selecao["paginas"])


USO_REFERENCIA_HORAS = 100          # uso de referência para mostrar a regra mínima na consulta (horas por mês)
MAQUINA_REFERENCIA = {"HORIMETRO_ATUAL": 0, "HODOMETRO_ATUAL": 0, "USO_MEDIO_HORAS_MES": USO_REFERENCIA_HORAS,
                      "USO_MEDIO_KM_MES": 2000, "DATA_LEITURA": datetime.date.today()}


def regra_ou_traco(linha, regra="MINIMA"):
    """Regra da recomendação (ideal ou mínima) para a máquina de referência; '-' se não entra no cronograma."""
    orientacao = {"METRICA_GATILHO": linha["metrica_gatilho"], "VALOR_GATILHO": linha["valor_gatilho"],
                  "FATOR_CONDICIONAL": linha["fator_condicional"]}
    return regra_cronograma(orientacao, MAQUINA_REFERENCIA, regra) or "-"


def arquivo_ia(cod):
    return PASTA_IA / f"{cod}.json"


# ---------------------------------------------------------------------------
# Estilo e cabeçalho
# ---------------------------------------------------------------------------
st.markdown("""
    <style>
    div.stButton > button, div.stFormSubmitButton > button, div.stDownloadButton > button {
        background-color: #1A4A75 !important; color: white !important; border: 1px solid #1A4A75 !important;
        border-radius: 4px !important; font-weight: 600 !important;
    }
    div.stButton > button:disabled { background-color: #b8c4d1 !important; border-color: #b8c4d1 !important; }
    .etapa { font-size: 1.25rem; font-weight: 800; color: #1A4A75; margin: 26px 0 6px 0; }
    </style>
""", unsafe_allow_html=True)
st.markdown("<h1 style='color: #1A4A75; margin-bottom: 0;'>Programação</h1>", unsafe_allow_html=True)
st.markdown("Recomendações dos manuais dos equipamentos: manutenção programada (NPL 1), temperatura extrema (NPL 2) "
            "e chuva excessiva (NPL 3). Consulte as recomendações já gravadas ou leia um manual novo.")
if aviso_salvo:
    st.success(aviso_salvo)

VISTA_GRAVADAS, VISTA_LEITURA = "Recomendações gravadas", "Ler um manual"
vista = st.radio("Ver", [VISTA_GRAVADAS, VISTA_LEITURA], horizontal=True, key="manut_prog_vista",
                 label_visibility="collapsed")

# ---------------------------------------------------------------------------
# Recomendações gravadas (consulta, filtros e gráficos; antiga página "Orientações de Manutenção")
# ---------------------------------------------------------------------------
if vista == VISTA_GRAVADAS:
    try:
        with pool_conexoes().acquire() as conn:
            with conn.cursor() as cursor:
                # CLOB: .read() ainda com a conexão aberta
                cursor.execute("""
                    SELECT o.COD_FONTE_MANUAL, mm.FABRICANTE, mm.MODELO, o.TIPO_ORIENTACAO, o.SUBSISTEMA,
                           o.ACAO_TECNICA, o.DETALHAMENTO_ORIENTACAO, o.METRICA_GATILHO, o.VALOR_GATILHO,
                           o.UNIDADE_MEDIDA, o.FATOR_CONDICIONAL
                    FROM CS_EQUIPAMENTOS_ORIENTACOES o
                    LEFT JOIN (SELECT ma.COD_FONTE_MANUAL, MIN(mo.FABRICANTE) AS FABRICANTE,
                                      LISTAGG(mo.MODELO, ' / ') WITHIN GROUP (ORDER BY mo.MODELO) AS MODELO
                               FROM CS_EQUIPAMENTOS_MANUAIS ma
                               JOIN CS_EQUIPAMENTOS_MODELOS mo ON mo.ID = ma.ID_EQUIPAMENTO
                               GROUP BY ma.COD_FONTE_MANUAL) mm ON mm.COD_FONTE_MANUAL = o.COD_FONTE_MANUAL
                    ORDER BY o.COD_FONTE_MANUAL, o.SUBSISTEMA
                """)
                colunas = [c[0].lower() for c in cursor.description]
                gravadas = pd.DataFrame([tuple(v.read() if hasattr(v, "read") else v for v in linha)
                                         for linha in cursor.fetchall()], columns=colunas)
                # onde cada manual é usado: máquinas seguradas dos modelos do manual -> fazenda -> cliente
                cursor.execute("""
                    SELECT DISTINCT ma.COD_FONTE_MANUAL, c.ID AS ID_CLIENTE, c.RAZAO_SOCIAL, c.CNPJ,
                           f.ID AS ID_FAZENDA, f.NOME_FAZENDA
                    FROM CS_EQUIPAMENTOS_MANUAIS ma
                    JOIN CS_EQUIPAMENTOS_SEGURADOS s ON s.ID_EQUIPAMENTO_MODELO = ma.ID_EQUIPAMENTO
                    JOIN CS_FAZENDAS f ON f.ID = s.ID_FAZENDA
                    JOIN CS_CLIENTES c ON c.ID = f.ID_CLIENTE
                """)
                uso_manuais = pd.DataFrame(cursor.fetchall(), columns=[c[0].lower() for c in cursor.description])
    except oracledb.Error as e:
        st.error(f"Não foi possível ler as recomendações: {e}")
        render_footer()
        st.stop()
    if gravadas.empty:
        st.info("Nenhuma recomendação gravada ainda. Use \"Ler um manual\" para gerar as recomendações de um manual.")
        render_footer()
        st.stop()
    gravadas["equipamento"] = gravadas.apply(
        lambda r: f"{r['fabricante']} {r['modelo']}" if r["modelo"] else r["cod_fonte_manual"], axis=1)
    gravadas["tipo"] = gravadas["tipo_orientacao"].map(lambda t: NOMES_TIPO.get(t, t))

    # Cliente e fazenda: mostram só as recomendações dos manuais dos equipamentos segurados deles
    clientes = (uso_manuais[["id_cliente", "razao_social", "cnpj"]].drop_duplicates()
                .sort_values("razao_social").to_dict("records"))
    nomes_clientes = {c["id_cliente"]: f"{c['razao_social']} ({c['cnpj']})" for c in clientes}
    c1, c2 = st.columns(2)
    filtro_cliente = c1.selectbox("Cliente", [None] + list(nomes_clientes),
                                  format_func=lambda i: "Todos os clientes" if i is None else nomes_clientes[i],
                                  key="gravadas_cliente")
    uso_filtrado = uso_manuais if filtro_cliente is None else uso_manuais[uso_manuais["id_cliente"] == filtro_cliente]
    fazendas_opcoes = (uso_filtrado[["id_fazenda", "nome_fazenda", "razao_social"]].drop_duplicates()
                       .sort_values(["nome_fazenda"]).to_dict("records"))
    nomes_fazendas = {f["id_fazenda"]: (f["nome_fazenda"] if filtro_cliente is not None
                                        else f"{f['nome_fazenda']} ({f['razao_social']})") for f in fazendas_opcoes}
    filtro_fazenda = c2.selectbox("Fazenda", [None] + list(nomes_fazendas),
                                  format_func=lambda i: "Todas as fazendas" if i is None else nomes_fazendas[i],
                                  key=f"gravadas_fazenda_{filtro_cliente}")
    if filtro_fazenda is not None:
        uso_filtrado = uso_filtrado[uso_filtrado["id_fazenda"] == filtro_fazenda]
    if filtro_cliente is not None or filtro_fazenda is not None:
        gravadas = gravadas[gravadas["cod_fonte_manual"].isin(set(uso_filtrado["cod_fonte_manual"]))]
        if gravadas.empty:
            st.info("Nenhuma recomendação gravada para os equipamentos segurados deste cliente ou fazenda. "
                    "Confira se o modelo das máquinas tem manual lido em \"Ler um manual\".")
            render_footer()
            st.stop()

    f1, f2, f3 = st.columns(3)
    filtro_equip = f1.multiselect("Equipamento", sorted(gravadas["equipamento"].unique()),
                                  default=sorted(gravadas["equipamento"].unique()))
    filtro_tipo = f2.multiselect("Tipo", sorted(gravadas["tipo"].unique()), default=sorted(gravadas["tipo"].unique()))
    filtro_sub = f3.multiselect("Subsistema", sorted(gravadas["subsistema"].unique()),
                                default=sorted(gravadas["subsistema"].unique()))
    filtrado = gravadas[gravadas["equipamento"].isin(filtro_equip) & gravadas["tipo"].isin(filtro_tipo)
                        & gravadas["subsistema"].isin(filtro_sub)]

    g1, g2, g3, g4 = st.columns(4)
    g1.metric("Recomendações", len(filtrado))
    g2.metric("Manutenção programada", int((filtrado["tipo_orientacao"] == "MANUTENCAO_PROGRAMADA").sum()))
    g3.metric("Alertas de clima (temperatura e chuva)",
              int(filtrado["tipo_orientacao"].isin(["ALERTA_TEMP_MINIMA", "ALERTA_TEMP_MAXIMA",
                                                    "RISCO_CHUVA_DESLIZE"]).sum()))
    g4.metric("Equipamentos", filtrado["equipamento"].nunique())

    import altair as alt
    AZUL, VERDE = "#1A4A75", "#388e3c"
    st.markdown('<div class="etapa">Recomendações por subsistema</div>', unsafe_allow_html=True)
    por_subsistema = filtrado.groupby("subsistema").size().reset_index(name="qtd").sort_values("qtd", ascending=False)
    if not por_subsistema.empty:
        barras = (alt.Chart(por_subsistema).mark_bar(color=AZUL, cornerRadiusEnd=3)
                  .encode(x=alt.X("qtd:Q", title="Quantidade de recomendações"),
                          y=alt.Y("subsistema:N", sort="-x", title=None), tooltip=["subsistema", "qtd"])
                  .properties(height=28 * len(por_subsistema)))
        st.altair_chart(barras + barras.mark_text(align="left", dx=4, color="#333333").encode(text="qtd:Q"),
                        width="stretch")
    c_equip, c_horas = st.columns(2)
    with c_equip:
        st.caption("Recomendações por equipamento")
        por_equip = filtrado.groupby("equipamento").size().reset_index(name="qtd")
        st.altair_chart(alt.Chart(por_equip).mark_bar(color=VERDE, cornerRadiusEnd=3)
                        .encode(x=alt.X("equipamento:N", title=None), y=alt.Y("qtd:Q", title="Quantidade"),
                                tooltip=["equipamento", "qtd"]), width="stretch")
    with c_horas:
        st.caption("Manutenção programada por intervalo de horas")
        por_horas = filtrado[filtrado["metrica_gatilho"] == "HORIMETRO"]
        if not por_horas.empty:
            por_horas = por_horas.groupby("valor_gatilho").size().reset_index(name="qtd").sort_values("valor_gatilho")
            st.altair_chart(alt.Chart(por_horas).mark_bar(color=AZUL, cornerRadiusEnd=3)
                            .encode(x=alt.X("valor_gatilho:O", title="Horas"), y=alt.Y("qtd:Q", title="Quantidade"),
                                    tooltip=["valor_gatilho", "qtd"]), width="stretch")
        else:
            st.caption("Sem itens por horímetro no filtro atual.")

    st.markdown('<div class="etapa">Detalhamento</div>', unsafe_allow_html=True)
    st.caption(f"Regra ideal: a do manual (com duas opções, o que ocorrer primeiro). Regra mínima: a usada no "
               f"cronograma de comprovação, só a opção de intervalo maior; aqui calculada para uma máquina com uso de "
               f"{USO_REFERENCIA_HORAS} horas por mês (no cronograma, vale o uso de cada máquina). \"-\" = não entra no "
               "cronograma (rotina curta do operador, como lubrificar a cada 10 horas, ou depende de uma condição).")
    filtrado = filtrado.copy()
    filtrado["regra_ideal"] = filtrado.apply(lambda r: regra_ou_traco(r, REGRA_IDEAL), axis=1)
    filtrado["regra_minima"] = filtrado.apply(lambda r: regra_ou_traco(r), axis=1)
    detalhe = filtrado[["equipamento", "tipo", "subsistema", "acao_tecnica", "detalhamento_orientacao",
                        "regra_ideal", "regra_minima", "metrica_gatilho", "valor_gatilho", "unidade_medida",
                        "fator_condicional", "cod_fonte_manual"]].rename(columns={
        "regra_ideal": "Regra ideal (manual)", "regra_minima": "Regra mínima (cronograma)",
        "equipamento": "Equipamento", "tipo": "Tipo", "subsistema": "Subsistema", "acao_tecnica": "Ação",
        "detalhamento_orientacao": "Recomendação", "metrica_gatilho": "Gatilho", "valor_gatilho": "Valor",
        "unidade_medida": "Unidade", "fator_condicional": "Condição", "cod_fonte_manual": "Manual"})
    tabela(detalhe)
    st.download_button("Baixar CSV filtrado", data=detalhe.to_csv(index=False, sep=";").encode("utf-8-sig"),
                       file_name="manutencoes_programadas_recomendacoes.csv", mime="text/csv")
    render_footer()
    st.stop()

try:
    with pool_conexoes().acquire() as conn:
        lista_modelos = mn.modelos(conn)
        tem_revisoes = mn.tabela_existe(conn, "CS_NLP_REVISOES")
        tem_uso_ia = mn.tabela_existe(conn, "CS_NLP_USO_IA")
        lista_rejeicoes = mn.rejeicoes(conn) if tem_revisoes else []
        gasto_total, gasto_mes, ultimas_chamadas = mn.gasto_ia(conn) if tem_uso_ia else (0.0, 0.0, [])
except oracledb.Error as e:
    st.error(f"Não foi possível conectar ao banco: {e}")
    render_footer()
    st.stop()

if not (tem_revisoes and tem_uso_ia):
    st.warning("Rode sql/estrutura_banco.sql no banco: sem ele, a revisão não guarda as rejeições (aprendizado) e a "
               "leitura por IA fica bloqueada, porque o gasto não teria onde ser registrado.")

# ---------------------------------------------------------------------------
# 1. Manual
# ---------------------------------------------------------------------------
st.markdown('<div class="etapa">1. Manual</div>', unsafe_allow_html=True)
if not lista_modelos:
    st.info("Nenhum modelo de equipamento cadastrado.")
    render_footer()
    st.stop()
# Primeiro o manual; depois os modelos a que ele se aplica (um manual pode valer para vários modelos da série)
pdfs_raw = sorted(p.name for p in PASTA_MANUAIS.glob("*.pdf")) if PASTA_MANUAIS.exists() else []
with pool_conexoes().acquire() as conn:
    manuais_banco = mn.manuais_gravados(conn)
fontes = ["Enviar um PDF agora"]
if pdfs_raw:
    fontes.append("Arquivo da pasta data/raw")
if manuais_banco:
    fontes.append("Manual já gravado no banco")
fonte = st.radio("De onde vem o manual", fontes, horizontal=True)

caminho_pdf, conteudo_pdf, nome_arquivo = None, None, None
if fonte == "Manual já gravado no banco":
    gravado = st.selectbox("Manual", manuais_banco,
                           format_func=lambda m: f"{m['COD_FONTE_MANUAL']} — {', '.join(m['MODELOS'])}")
    with pool_conexoes().acquire() as conn:
        dados = conteudo_manual(conn, gravado["ID"])
    if dados:
        nome_arquivo, conteudo_pdf, _ = dados
        caminho_pdf = PASTA_MANUAIS / (re.sub(r"[^a-z0-9]+", "_", gravado["COD_FONTE_MANUAL"].lower()).strip("_") + ".pdf")
        if not caminho_pdf.exists() or caminho_pdf.read_bytes() != conteudo_pdf:
            PASTA_MANUAIS.mkdir(parents=True, exist_ok=True)
            caminho_pdf.write_bytes(conteudo_pdf)
elif fonte == "Arquivo da pasta data/raw":
    nome_arquivo = st.selectbox("Arquivo", pdfs_raw)
    caminho_pdf = PASTA_MANUAIS / nome_arquivo
    conteudo_pdf = caminho_pdf.read_bytes()
else:
    envio = st.file_uploader(f"Manual em PDF (até {TAMANHO_MAXIMO_MB['MANUAL']} MB)", type=["pdf"])
    if envio is not None:
        nome_arquivo, conteudo_pdf = envio.name, envio.getvalue()
        base = re.sub(r"[^a-z0-9]+", "_", Path(envio.name).stem.lower()).strip("_") or "manual"
        caminho_pdf = PASTA_MANUAIS / f"manual_{base.removeprefix('manual_')}.pdf"
        PASTA_MANUAIS.mkdir(parents=True, exist_ok=True)
        caminho_pdf.write_bytes(conteudo_pdf)
        st.caption(f"Cópia de trabalho gravada em data/raw/{caminho_pdf.name}. No banco, o PDF é gravado na etapa 4.")

if caminho_pdf is None:
    st.info("Escolha ou envie o manual para continuar.")
    render_footer()
    st.stop()

hash_pdf = hashlib.sha256(conteudo_pdf).hexdigest()
# Manual digitalizado (páginas só com imagem): o OCR grava uma cópia com o texto, usada daqui em diante
caminho_ocr = PASTA_MANUAIS / "ocr" / f"{Path(caminho_pdf).stem}_{hash_pdf[:12]}.pdf"
if caminho_ocr.exists():
    caminho_pdf, conteudo_pdf = caminho_ocr, caminho_ocr.read_bytes()
    hash_pdf = hashlib.sha256(conteudo_pdf).hexdigest()
    st.caption(f"Usando a versão com OCR deste manual (texto reconhecido das imagens): data/raw/ocr/{caminho_ocr.name}.")
else:
    com_texto, total_paginas = paginas_com_texto(str(caminho_pdf), hash_pdf)
    if com_texto < total_paginas / 2:
        st.warning(f"Este PDF é digitalizado: só {com_texto} de {total_paginas} páginas têm texto (as demais são "
                   "imagem). As leituras precisam do texto: aplique o OCR para reconhecer o texto das imagens. Leva "
                   "alguns segundos por página e é feito uma vez só por manual.")
        if st.button(f"Aplicar OCR ({total_paginas} páginas)", key=f"btn_ocr_{hash_pdf}"):
            barra = st.progress(0.0, text="OCR: começando...")
            try:
                ocr.aplicar_ocr(caminho_pdf, caminho_ocr,
                                ao_progresso=lambda i, n: barra.progress(i / n, text=f"OCR: página {i} de {n}"))
            except ocr.OCRIndisponivel as erro:
                st.error(str(erro))
            else:
                st.rerun()
info = identificar(str(caminho_pdf), hash_pdf)
modelos_por_id = {int(m["ID"]): m for m in lista_modelos}


def nome_modelo(id_modelo):
    m = modelos_por_id[id_modelo]
    return f"{m['FABRICANTE']} {m['MODELO']}"


citados = mn.modelos_citados(lista_modelos, info["texto_inicio"], info["fabricante"])
d1, d2 = st.columns([2, 3])
with d1:
    cod = st.text_input("Código do manual (fonte)", value=sugerir_codigo(info, conteudo_pdf), max_chars=100,
                        key=f"cod_{hash_pdf}",
                        help="Padrão FABRICANTE-MODELO-CODIGO-EDICAO, lido da capa (ex.: JD-CH950-OMCXT31163-B3). "
                             "Confira com a capa do manual. Um manual que vale para vários modelos tem um código só."
                        ).strip().upper()
with d2:
    st.markdown(f"**Lido da capa:** fabricante {info['fabricante'] or 'não identificado'} · modelo "
                f"{info['modelo'] or 'não identificado'} · código {info['codigo'] or 'não identificado'} · edição "
                f"{info['edicao'] or '-'} · idioma {info['idioma']} · {info['paginas']} páginas")
    with pool_conexoes().acquire() as conn:
        ja_gravadas = mn.contar_orientacoes(conn, cod) if cod else {}
        ja_ligados = [i for i in (mn.modelos_do_manual(conn, cod) if cod else []) if i in modelos_por_id]
    if ja_gravadas:
        st.caption("Já gravadas para este manual: " + ", ".join(f"{NOMES_TIPO.get(t, t)}: {q}" for t, q in ja_gravadas.items())
                   + ". Na gravação, só entram as recomendações novas.")

chave_modelos = f"modelos_{hash_pdf}"
# modelo recém-cadastrado no formulário abaixo: entra marcado (só dá para mudar a lista antes de desenhá-la)
novo_marcado = st.session_state.pop("_modelo_cadastrado", None)
if novo_marcado is not None and novo_marcado in modelos_por_id:
    atuais = st.session_state.get(chave_modelos, ja_ligados or citados)
    st.session_state[chave_modelos] = list(dict.fromkeys([i for i in atuais if i in modelos_por_id] + [novo_marcado]))
modelos_escolhidos = st.multiselect(
    "Modelos a que o manual se aplica", list(modelos_por_id),
    default=None if chave_modelos in st.session_state else (ja_ligados or citados), key=chave_modelos,
    format_func=nome_modelo,
    help="Já vêm marcados os modelos cadastrados que aparecem nas primeiras páginas do manual (ou os já ligados a "
         "ele). Um manual pode valer para vários modelos da mesma série.")
if citados:
    st.caption("Encontrados nas primeiras páginas do manual: " + ", ".join(nome_modelo(i) for i in citados) + ".")
else:
    st.caption("Nenhum modelo cadastrado aparece nas primeiras páginas do manual. Escolha os modelos na lista ou "
               "cadastre o modelo abaixo.")
with st.expander("Cadastrar um modelo que não está na lista"):
    with st.form("form_novo_modelo", clear_on_submit=True):
        n1, n2 = st.columns(2)
        novo_fabricante = n1.text_input("Fabricante", value=(info["fabricante"] or "").title())
        novo_modelo = n2.text_input("Modelo", value=info["modelo"] or "")
        n3, n4 = st.columns(2)
        novo_tipo = n3.text_input("Tipo", placeholder="ex.: Trator agrícola de médio porte")
        novo_valor = n4.number_input("Valor estimado de um novo (R$)", min_value=0.0, step=10000.0, format="%.0f")
        novo_descricao = st.text_area("Descrição (opcional)", height=80)
        if st.form_submit_button("Cadastrar modelo"):
            try:
                with pool_conexoes().acquire() as conn:
                    id_novo = mn.cadastrar_modelo(conn, novo_fabricante, novo_modelo, novo_tipo, novo_valor or None,
                                                  novo_descricao.strip() or None)
                    conn.commit()
            except (oracledb.Error, ValueError) as e:
                st.error(f"Modelo não cadastrado: {e}")
            else:
                st.session_state["_modelo_cadastrado"] = id_novo
                st.session_state["_aviso_manut_prog"] = f"Modelo {novo_fabricante} {novo_modelo} cadastrado e marcado."
                st.rerun()

if not modelos_escolhidos:
    st.warning("Escolha ao menos um modelo a que o manual se aplica.")
    render_footer()
    st.stop()
# aviso só quando nenhum dos modelos escolhidos aparece no manual (e a capa indicou algum modelo)
modelo_divergente = bool(info["modelo"] or citados) and not set(modelos_escolhidos) & set(citados)
if modelo_divergente:
    st.warning(f"Nenhum dos modelos escolhidos aparece nas primeiras páginas do manual (a capa indica "
               f"{info['modelo'] or 'outro modelo'}). Confira se escolheu os modelos e o arquivo certos.")
nomes_escolhidos = ", ".join(nome_modelo(i) for i in modelos_escolhidos)
if not cod:
    st.warning("Informe o código do manual.")
    render_footer()
    st.stop()

chave_sessao = f"{hash_pdf}_{cod}"
resultados = st.session_state.setdefault("manuais_resultados", {}).setdefault(chave_sessao, {})

# ---------------------------------------------------------------------------
# 2. Leitura
# ---------------------------------------------------------------------------
st.markdown('<div class="etapa">2. Leitura com IA</div>', unsafe_allow_html=True)
st.markdown("**Claude Sonnet 5** · pago · NPL 1, 2 e 3")
salvo = arquivo_ia(cod)
if salvo.exists():
    dados_salvos = json.loads(salvo.read_text(encoding="utf-8"))
    st.caption(f"Última leitura com IA deste manual: {dados_salvos['data']} · {len(dados_salvos['itens'])} itens · "
               f"custo {moeda_md(dados_salvos['custo_usd'])}.")
    if st.button("Abrir a última leitura com IA (sem custo)", key="btn_ia_salva"):
        resultados["ia"] = dados_salvos
npls = st.multiselect("O que extrair", list(ia.NOMES_NPL), default=list(ia.NOMES_NPL),
                      format_func=lambda n: ia.NOMES_NPL[n])
st.caption("Os temas escolhidos são extraídos juntos, numa leitura só: cada página relevante vai uma vez à IA, "
           "que separa as recomendações por tema.")
with st.expander("Páginas enviadas à IA"):
    max_paginas = st.slider("Máximo de páginas enviadas (limita o custo)", 10, 400, ia.MAX_PAGINAS_PADRAO,
                            step=10, key="maxpag_ia",
                            help="As páginas são escolhidas pelos indícios de cada tema (intervalos em horas, "
                                 "temperatura, chuva...). Se houver mais páginas relevantes que o máximo, vão as "
                                 "com mais indícios.")
ler_ia = False
if npls:
    selecao, lotes = lotes_ia(str(caminho_pdf), hash_pdf, tuple(npls), int(max_paginas))
    estimado = ia.estimativa(lotes)
    relevantes = "; ".join(f"{ia.NOMES_NPL[n].split(' - ')[0]}: {q}" for n, q in selecao["por_npl"].items())
    st.markdown(f"O manual tem **{selecao['total']} páginas**; **{selecao['relevantes']}** têm indícios de algum "
                f"tema ({relevantes}). Serão enviadas **{estimado['paginas']} páginas** em "
                f"**{estimado['lotes']} chamadas**.")
    if selecao["com_texto"] == 0:
        st.warning("Este PDF não tem texto: é um manual digitalizado (cada página é uma imagem). Use o botão "
                   "\"Aplicar OCR\" na etapa 1 para reconhecer o texto antes de ler.")
    elif selecao["com_texto"] < selecao["total"] / 2:
        st.caption(f"Só {selecao['com_texto']} das {selecao['total']} páginas têm texto; as demais são imagem e "
                   "não entram na leitura.")
    if selecao["relevantes"] > len(selecao["paginas"]):
        st.caption(f"{selecao['relevantes'] - len(selecao['paginas'])} página(s) relevante(s) ficaram de fora "
                   "pelo máximo de páginas; aumente o máximo em \"Páginas enviadas à IA\" para incluí-las.")
    e1, e2, e3 = st.columns(3)
    e1.metric("Custo esperado", moeda(estimado["custo_tipico"]))
    e2.metric("Custo máximo possível", moeda(estimado["custo_maximo"]))
    e3.metric("Gasto com IA no mês", moeda(gasto_mes))
    with st.form("form_ia", clear_on_submit=True):
        limite = st.number_input("Limite de gasto desta leitura (US$)", min_value=0.05, max_value=LIMITE_MAXIMO_USD,
                                 value=min(LIMITE_MAXIMO_USD, max(0.05, math.ceil(estimado["custo_maximo"] * 20) / 20)),
                                 step=0.05, format="%.2f")
        chave_api = st.text_input("Chave da API da Anthropic", type="password",
                                  help="Digitada a cada leitura; não é guardada. Crie em console.anthropic.com > "
                                       "Chaves de API > Criar chave, escolhendo um workspace (ex.: Default).")
        autorizo = st.checkbox("Autorizo este gasto com a Anthropic, até o limite informado")
        ler_ia = st.form_submit_button("Ler com IA", disabled=not tem_uso_ia)
    st.caption("A leitura para antes de qualquer chamada que possa passar do limite. Cada chamada fica registrada "
               "com o custo real.")
# Área das mensagens da leitura: é recriada vazia a cada execução, então o erro ou o resultado de uma leitura
# anterior some assim que uma nova leitura começa (em vez de ficar na tela, apagado, até ela terminar).
area_leitura = st.empty()
if npls and ler_ia:
    if not autorizo:
        area_leitura.error("Marque a autorização do gasto.")
    elif not chave_api.strip():
        area_leitura.error("Informe a chave da API da Anthropic.")
    else:
        mensagens = area_leitura.container()
        andamento = mensagens.status("Lendo com o Claude Sonnet 5...", expanded=True)

        def registrar(chamada):
            # chamada que não chegou à IA (chave inválida, rede...): não gera custo, não entra no gasto
            if chamada.tokens_entrada or chamada.tokens_saida:
                with pool_conexoes().acquire() as conn_uso:
                    mn.registrar_uso_ia(conn_uso, cod, ia.MODELO_IA, chamada.npl, chamada.tokens_entrada,
                                        chamada.tokens_saida, chamada.custo_usd, limite, usuario)
                    conn_uso.commit()
            andamento.write(f"Páginas {chamada.paginas[0]} a {chamada.paginas[-1]}"
                            f" · {chamada.itens} itens · {moeda_md(chamada.custo_usd)} · {chamada.situacao}")

        resultado = ia.extrair(chave_api, cod, nome_arquivo or cod, lotes, float(limite),
                               mn.exemplos_negativos(lista_rejeicoes), ao_concluir_chamada=registrar)
        andamento.update(label=f"Leitura com IA concluída · {len(resultado.orientacoes)} itens · "
                               f"custo {moeda_md(resultado.custo_total)}",
                         state="error" if resultado.interrompida and not resultado.orientacoes else "complete")
        if resultado.interrompida:
            mensagens.warning(resultado.interrompida.replace("$", "\\$"))
            if "401" in resultado.interrompida or "authentication" in resultado.interrompida.lower():
                mensagens.info("A Anthropic não reconheceu a chave da API. Confira se colou a chave inteira "
                               "(começa com sk-ant-api03- e é mostrada só uma vez, ao ser criada) ou crie uma "
                               "nova. Nenhum custo foi gerado.")
        dados_ia = {"data": datetime.datetime.now().strftime("%d/%m/%Y %H:%M"), "custo_usd": resultado.custo_total,
                    "itens": [o.as_dict() for o in resultado.orientacoes],
                    "chamadas": [c.__dict__ for c in resultado.chamadas], "npls": npls}
        if resultado.orientacoes:
            PASTA_IA.mkdir(parents=True, exist_ok=True)
            arquivo_ia(cod).write_text(json.dumps(dados_ia, ensure_ascii=False, indent=1), encoding="utf-8")
        resultados["ia"] = dados_ia
        mensagens.success(f"{len(dados_ia['itens'])} recomendações da IA · custo {moeda_md(dados_ia['custo_usd'])}")
elif resultados.get("ia"):
    area_leitura.success(f"{len(resultados['ia']['itens'])} recomendações da IA · custo "
                         f"{moeda_md(resultados['ia']['custo_usd'])}")

if not resultados.get("ia"):
    render_footer()
    st.stop()
candidatos = list(resultados["ia"]["itens"])

# ---------------------------------------------------------------------------
# 3. Revisão e comparação
# ---------------------------------------------------------------------------
st.markdown('<div class="etapa">3. Revisão: o que fica para os modelos</div>', unsafe_allow_html=True)
with pool_conexoes().acquire() as conn:
    gravadas_modelos = mn.orientacoes_gravadas(conn, modelos_escolhidos, cod)
comparacao = mn.comparar_com_gravadas(candidatos, gravadas_modelos)
qtd_situacao = {s: sum(1 for l in comparacao if l["situacao"] == s) for s in mn.ORDEM_SITUACAO}
st.markdown(f"Leitura da IA comparada com as recomendações já gravadas para **{nomes_escolhidos}**: "
            f"**{qtd_situacao[mn.NOVA]}** nova(s), **{qtd_situacao[mn.DIFERENTE]}** nova(s) diferente(s) de uma "
            f"gravada, **{qtd_situacao[mn.IGUAL]}** igual(is) a uma gravada e **{qtd_situacao[mn.SO_GRAVADA]}** "
            "gravada(s) que não vieram nesta leitura.")
st.caption("As regras parecidas (mesmo tipo, subsistema, ação e métrica) aparecem juntas, na ordem das páginas do "
           "manual. Marque em \"Manter\" o que deve ficar para esses modelos. Recomendação nova desmarcada não é gravada e "
           "vira exemplo do que a IA não deve extrair (informe o motivo). Recomendação já gravada desmarcada é "
           "removida (menos as que já têm manutenção comprovada). Tipo, subsistema, ação, recomendação, métrica, "
           "valor, unidade e condição podem ser corrigidos.")

COLUNAS_EDITAVEIS = {"Tipo": "tipo_orientacao", "Subsistema": "subsistema", "Ação": "acao_tecnica",
                     "Recomendação": "detalhamento_orientacao", "Métrica": "metrica_gatilho", "Valor": "valor_gatilho",
                     "Unidade": "unidade_medida", "Condição": "fator_condicional"}
# a tabela é montada uma vez por leitura e modelos escolhidos (as correções do revisor ficam guardadas na sessão)
assinatura = hashlib.sha1(repr((sorted(modelos_escolhidos), resultados["ia"].get("data"),
                                 len(candidatos), len(gravadas_modelos))).encode()).hexdigest()[:10]
chave_base = f"revisao_base_{chave_sessao}_{assinatura}"
chave_versao = f"revisao_versao_{chave_sessao}_{assinatura}"
if chave_base not in st.session_state:
    linhas, indices = [], []
    for n, linha in enumerate(comparacao):
        item = linha["item"]
        parecido = mn.parecido_com_rejeitado(item, lista_rejeicoes) if linha["id"] is None else None
        indices.append(f"g{linha['id']}" if linha["id"] is not None else f"n{n}")
        linhas.append({
            "Manter": parecido is None, "Página": item.get("pagina_origem"), "Situação": linha["situacao"],
            "Manual": item.get("cod_fonte_manual") or cod,
            **{rotulo: item.get(campo) for rotulo, campo in COLUNAS_EDITAVEIS.items()},
            "Gravada hoje": linha["gravada_hoje"],
            "Manutenções": (f"{item['programadas']} programada(s), {item['comprovadas']} comprovada(s)"
                            if linha["id"] is not None else ""),
            "Texto original": item.get("texto_bruto_original"),
            "Motivo (se não manter)": (f"Parecido com item já rejeitado: "
                                       f"{parecido.get('MOTIVO') or parecido['DETALHAMENTO'][:80]}" if parecido else ""),
        })
    st.session_state[chave_base] = pd.DataFrame(linhas, index=indices)
    st.session_state[chave_versao] = 0
chave_editor = f"revisao_{chave_sessao}_{st.session_state[chave_versao]}"


def _marcar_todas():
    """Selecionar tudo: guarda as correções já feitas na tabela e marca (ou desmarca) todas as linhas."""
    base = st.session_state[chave_base].copy()
    for posicao, mudancas in st.session_state.get(chave_editor, {}).get("edited_rows", {}).items():
        for coluna, valor in mudancas.items():
            base.iloc[int(posicao), base.columns.get_loc(coluna)] = valor
    base["Manter"] = bool(st.session_state[f"todas_{chave_sessao}"])
    st.session_state[chave_base] = base
    st.session_state[chave_versao] += 1


base_revisao = st.session_state[chave_base]
st.checkbox("Selecionar tudo", key=f"todas_{chave_sessao}", on_change=_marcar_todas,
            value=bool(len(base_revisao)) and bool(base_revisao["Manter"].all()),
            help="Marca todas as linhas (ou desmarca, ao tirar a marcação). As correções feitas na tabela continuam.")
editada = st.data_editor(
    base_revisao, key=chave_editor, hide_index=True, width="stretch", height=560,
    row_height=ALTURA_LINHA_COM_QUEBRA,               # linhas altas: o texto das células quebra linha
    disabled=["Situação", "Manual", "Gravada hoje", "Manutenções", "Página", "Texto original"],
    column_config={
        "Manter": st.column_config.CheckboxColumn(width="small"),
        "Situação": st.column_config.TextColumn(width="medium"),
        "Tipo": st.column_config.SelectboxColumn(options=list(sch.TIPO_ORIENTACAO), required=True),
        "Subsistema": st.column_config.SelectboxColumn(options=list(sch.SUBSISTEMA), required=True),
        "Ação": st.column_config.SelectboxColumn(options=list(sch.ACAO_TECNICA), required=True),
        "Métrica": st.column_config.SelectboxColumn(options=list(sch.METRICA_GATILHO)),
        "Unidade": st.column_config.SelectboxColumn(options=list(sch.UNIDADE_MEDIDA)),
        "Valor": st.column_config.NumberColumn(format="%g"),
        "Recomendação": st.column_config.TextColumn(width="large"),
        "Gravada hoje": st.column_config.TextColumn(width="medium"),
        "Texto original": st.column_config.TextColumn(width="medium"),
        "Motivo (se não manter)": st.column_config.TextColumn(width="medium"),
        "Página": st.column_config.NumberColumn(format="%d", width="small"),
    },
) if not base_revisao.empty else base_revisao

ids_gravados = {i: int(i[1:]) for i in editada.index if str(i).startswith("g")}
e_gravada = editada.index.isin(list(ids_gravados))
manter = editada["Manter"].fillna(False).astype(bool) if not editada.empty else editada.get("Manter")
novas_gravar = editada[~e_gravada & manter] if not editada.empty else editada
novas_rejeitar = editada[~e_gravada & ~manter] if not editada.empty else editada
gravadas_manter = editada[e_gravada & manter] if not editada.empty else editada
gravadas_remover = editada[e_gravada & ~manter] if not editada.empty else editada
originais = st.session_state[chave_base]


def _valor(v):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) or v == "" else v


def _mudou(indice):
    return any(_valor(editada.at[indice, c]) != _valor(originais.at[indice, c]) for c in COLUNAS_EDITAVEIS)


gravadas_alterar = [i for i in gravadas_manter.index if _mudou(i)]
comprovadas_por_indice = {f"g{g['ID']}": int(g["COMPROVADAS"] or 0) for g in gravadas_modelos}
comprovadas_remover = [i for i in gravadas_remover.index if comprovadas_por_indice.get(i)]
m1, m2, m3, m4 = st.columns(4)
m1.metric("Novas a gravar", len(novas_gravar))
m2.metric("Já gravadas que ficam", len(gravadas_manter))
m3.metric("Já gravadas a remover", len(gravadas_remover))
m4.metric("Novas descartadas", len(novas_rejeitar))

# ---------------------------------------------------------------------------
# 4. Gravação
# ---------------------------------------------------------------------------
st.markdown('<div class="etapa">4. Gravação</div>', unsafe_allow_html=True)
with pool_conexoes().acquire() as conn:
    maquinas = mn.equipamentos_dos_modelos(conn, modelos_escolhidos)
resumo = [f"**{len(novas_gravar)}** recomendação(ões) nova(s) gravada(s) no manual {cod}",
          f"**{len(gravadas_manter)}** já gravada(s) continuam"
          + (f" ({len(gravadas_alterar)} com correção)" if gravadas_alterar else ""),
          f"**{len(gravadas_remover) - len(comprovadas_remover)}** já gravada(s) removida(s)"
          + (f"; outras **{len(comprovadas_remover)}** desmarcadas continuam, porque têm manutenção comprovada e o "
             "comprovante depende delas" if comprovadas_remover else ""),
          f"**{len(novas_rejeitar)}** nova(s) descartada(s), guardada(s) como exemplo do que a IA não deve extrair",
          f"o manual {cod} fica ligado a: {nomes_escolhidos}"]
st.markdown("Ao clicar em **Gravar**:\n" + "\n".join(f"- {r}" for r in resumo))
if maquinas:
    reprogramar = st.checkbox(f"Refazer também a lista de manutenções das {len(maquinas)} máquina(s) segurada(s) "
                              f"desses modelos (as manutenções já comprovadas não mudam)", value=True)
else:
    reprogramar = False
    st.caption("Nenhuma máquina segurada desses modelos por enquanto. Quando uma máquina desses modelos for "
               "cadastrada (página Cadastro), a lista de manutenções dela é gerada com estas recomendações.")
confirmado = True
if modelo_divergente:
    confirmado = st.checkbox(f"Confirmo que o manual {cod} vale para {nomes_escolhidos} "
                             "(nenhum deles aparece nas primeiras páginas do manual)")
if st.button("Gravar", key="btn_gravar", disabled=editada.empty or not confirmado):
    erros = []
    for _, linha in pd.concat([novas_gravar, gravadas_manter]).iterrows():
        if _valor(linha["Valor"]) is not None and not linha["Unidade"]:
            erros.append(f"Informe a unidade de: {str(linha['Recomendação'])[:60]}")
        if not str(linha["Recomendação"] or "").strip():
            erros.append("Há recomendação marcada para ficar sem texto.")
    if erros:
        for erro in erros[:10]:
            st.error(erro)
    else:
        def dados_linha(l):
            return {"cod_fonte_manual": cod, "tipo_orientacao": l["Tipo"], "subsistema": l["Subsistema"],
                    "acao_tecnica": l["Ação"], "detalhamento_orientacao": str(l["Recomendação"]).strip(),
                    "metrica_gatilho": _valor(l["Métrica"]), "valor_gatilho": _valor(l["Valor"]),
                    "unidade_medida": _valor(l["Unidade"]), "fator_condicional": _valor(l["Condição"]),
                    "texto_bruto_original": str(_valor(l["Texto original"]) or l["Recomendação"])}

        itens_rejeitar = [{
            "tipo_orientacao": l["Tipo"], "subsistema": l["Subsistema"], "detalhamento_orientacao": l["Recomendação"],
            "texto_bruto_original": _valor(l["Texto original"]), "motivo": l["Motivo (se não manter)"],
        } for _, l in novas_rejeitar.iterrows()]
        try:
            with pool_conexoes().acquire() as conn:
                vinculos = mn.vincular_manual(conn, modelos_escolhidos, cod, nome_arquivo, conteudo_pdf)
                situacao_vinculo = ", ".join(f"{q} {s}" for s, q in vinculos.items())
                gravadas, repetidas = mn.gravar_orientacoes(conn, [dados_linha(l) for _, l in novas_gravar.iterrows()])
                for indice in gravadas_alterar:
                    mn.atualizar_orientacao(conn, ids_gravados[indice], dados_linha(editada.loc[indice]))
                removidas, mantidas_comprovante = mn.remover_orientacoes(
                    conn, [ids_gravados[i] for i in gravadas_remover.index])
                rejeicoes_gravadas = (mn.registrar_rejeicoes(conn, cod, "IA", itens_rejeitar, usuario)
                                      if tem_revisoes and itens_rejeitar else 0)
                programadas = 0
                if reprogramar:
                    for id_maquina in maquinas:
                        programadas += programar_equipamento(conn, id_maquina)["geradas"]
                auditoria.registrar(conn, "GRAVACAO_MANUAL", auditoria.ENTIDADE_MANUAL, cod,
                                    f"{gravadas} nova(s), {len(gravadas_alterar)} corrigida(s), {removidas} removida(s), "
                                    f"{rejeicoes_gravadas} descartada(s); modelos: {nomes_escolhidos}",
                                    usuario, perfil, "Programação")
                conn.commit()
            partes = [f"{gravadas} recomendação(ões) nova(s) gravada(s)"
                      + (f" ({repetidas} já existiam)" if repetidas else ""),
                      f"{len(gravadas_alterar)} corrigida(s)", f"{removidas} removida(s)"
                      + (f" ({mantidas_comprovante} mantida(s) por ter manutenção comprovada)"
                         if mantidas_comprovante else ""),
                      f"{rejeicoes_gravadas} descartada(s) guardada(s) para o aprendizado",
                      f"vínculos com modelos: {situacao_vinculo}"]
            if reprogramar:
                partes.append(f"{programadas} manutenção(ões) programada(s)")
            st.session_state["_aviso_manut_prog"] = (f"Manual {cod} gravado: " + "; ".join(partes)
                                                     + ". Veja em \"Recomendações gravadas\".")
            st.session_state["manuais_resultados"].pop(chave_sessao, None)
            for chave in (chave_base, chave_versao):
                st.session_state.pop(chave, None)
            st.rerun()
        except (oracledb.Error, ValueError) as e:
            st.error(f"Nada foi gravado: {e}")

# ---------------------------------------------------------------------------
# Gasto com IA
# ---------------------------------------------------------------------------
if tem_uso_ia:
    # consultado de novo aqui (depois da leitura), para já mostrar as chamadas que acabaram de ser feitas
    with pool_conexoes().acquire() as conn:
        gasto_total, gasto_mes, ultimas_chamadas = mn.gasto_ia(conn)
    with st.expander(f"Gasto com IA · mês {moeda_md(gasto_mes)} · total {moeda_md(gasto_total)}"):
        if ultimas_chamadas:
            tabela([{
                "Data": c["DATA_USO"], "Manual": c["COD_FONTE_MANUAL"], "Temas": c["NPL"],
                "Tokens de entrada": c["TOKENS_ENTRADA"], "Tokens de saída": c["TOKENS_SAIDA"],
                "Custo": moeda(float(c["CUSTO_USD"])), "Autorizado por": nome_usuario(c["USUARIO"])} for c in ultimas_chamadas],
                altura=420)
        else:
            st.caption("Nenhuma chamada paga registrada.")
        st.caption("Para uma segunda trava, defina também um limite mensal de gasto na conta da Anthropic "
                   "(console.anthropic.com).")

render_footer()
