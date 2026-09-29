# -*- coding: utf-8 -*-
"""
Comprovação das manutenções: revisões de cada equipamento segurado, envio dos comprovantes, leitura mensal do
horímetro e validação da Sompo. É a página de prestação de contas do cliente.

- O usuário escolhe cliente, fazenda e EQUIPAMENTO: a manutenção é feita por máquina.
- Revisões do equipamento (requisitos/comprovacao.py): como na tabela de manutenção do manual, os serviços da mesma
  periodicidade que vencem na mesma data formam uma revisão (a de 200 horas não se mistura com a de 400 horas),
  com o título nas duas opções do manual quando houver (ex.: "Revisão de 200 horas / mensal") e a lista
  Sistema | Serviço. Um comprovante (nota fiscal, ordem de serviço ou fotos) vale para a revisão inteira (CS_ANEXOS,
  ligado pelo ID_COMPROVANTE). Prazo de 90 dias depois da data prevista para o comprovante; depois, "comprovante não
  enviado".
- Para estimular o envio: nível do equipamento (Ouro, Prata, Bronze), barra de realização, quanto falta para o
  próximo nível e revisões seguidas em dia. Em destaque só a missão de agora (a revisão vencida mais recente sem
  comprovante e a próxima dos 30 dias); as demais ficam recolhidas.
- Revisão feita de novo antes da próxima (regra ideal do manual, ex.: a cada 400 horas quando o cronograma pede a
  semestral): o comprovante é enviado no card da revisão comprovada (REGRA = IDEAL) e dá bônus no Score.
- Título e periodicidade com as duas opções do manual quando houver (ex.: "400 horas / Semestral").
- Validação Sompo, por revisão (todos os serviços da revisão juntos, com os comprovantes enviados): Atende (100), Atende na maior parte (75), Atende na menor parte (25) ou Não atende
  (0, volta a pedir comprovante). Motivo obrigatório para menor parte e não atende.
- Registro de ações (CS_LOG_ACOES, requisitos/auditoria.py): envio de comprovante, revisão feita a mais e validação
  da Sompo ficam gravados com quem fez e quando (o histórico não é sobrescrito); cada card mostra o seu registro.
- Métricas do Score de Risco (docs/Score_risco_matriz.md): realização do cronograma e atendimento (média das notas
  da validação); o atraso não conta.

Acesso: Sompo e administrador (todos os clientes, com a validação); cliente/produtor (perfil Operador) só vê e
preenche os equipamentos do próprio cliente (CNPJ ligado ao usuário), sem a validação Sompo.
Antes de usar, rode sql/estrutura_banco.sql.
"""
import sys
import os
import re
import html
import math
import datetime

import pandas as pd
import streamlit as st
import oracledb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

st.set_page_config(page_title="Campo Seguro - Comprovação", layout="wide")

from components import (exigir_login, render_footer, tabela, nome_usuario, cliente_do_usuario, PERFIL_ADMIN,
                        PERFIL_SOMPO, PERFIL_PRODUTOR)
from auth import USER, PASSWORD, DSN
from requisitos.anexos import (salvar_anexo, _inserir_com_id, listar_anexos, conteudo_anexo, AnexoInvalido,
                               TIPOS_DOCUMENTO, ENTIDADE_MANUTENCAO, ANEXO_COMPROVANTE)
from requisitos.recomendacoes import eh_manutencao
from requisitos import comprovacao as cp
from requisitos import auditoria
from requisitos.programacao_manutencao import REGRA_IDEAL
from requisitos.anexos import ENTIDADE_LEITURA
from requisitos.leituras import (registrar_leitura, leituras, ultimas_leituras, situacao_leitura, LeituraInvalida,
                                 PRAZO_LEITURA_DIAS)

PERFIS_COM_ACESSO = [PERFIL_ADMIN, PERFIL_SOMPO, PERFIL_PRODUTOR]
perfil = exigir_login(PERFIS_COM_ACESSO)
# quem fez fica gravado como referência do usuário ("USR-5"), sem o nome (requisitos/usuarios.py)
usuario = st.session_state.get("usuario_ref") or st.session_state.get("usuario_nome") or ""

VALIDACOES_POR_PAGINA = 10
from requisitos.programacao_manutencao import REALIZACAO_PADRAO, valor_realizacao
ATENDE_SIM, ATENDE_NAO = 100, 0          # ATENDE_CRITERIOS vai de 0 a 100 no banco (percentual)

# Aviso depois de salvar (a página recarrega)
aviso_salvo = st.session_state.pop("_aviso_manutencao", None)


@st.cache_resource(show_spinner=False)
def pool_conexoes():
    return oracledb.create_pool(user=USER, password=PASSWORD, dsn=DSN, min=1, max=4, increment=1)


def consultar(sql, parametros=None):
    with pool_conexoes().acquire() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, parametros or {})
            colunas = [c[0] for c in cursor.description]
            return [{c: (v.read() if hasattr(v, "read") else v) for c, v in zip(colunas, linha)}
                    for linha in cursor.fetchall()]


@st.cache_data(ttl=60, show_spinner=False)
def tem_tabela_leituras():
    return bool(consultar("SELECT COUNT(*) AS N FROM USER_TABLES WHERE TABLE_NAME = 'CS_LEITURAS_MEDIDOR'")[0]["N"])


@st.cache_data(ttl=600, show_spinner=False)
def valores_realizacao():
    """Valores aceitos em REALIZACAO (restrição CHECK), ou o padrão."""
    try:
        for linha in consultar("SELECT SEARCH_CONDITION FROM USER_CONSTRAINTS "
                               "WHERE TABLE_NAME = 'CS_MANUTENCOES_REALIZADAS' AND CONSTRAINT_TYPE = 'C'"):
            achado = re.search(r"\bREALIZACAO\b\s+IN\s*\(([^)]*)\)", str(linha["SEARCH_CONDITION"] or ""), re.I)
            if achado:
                return re.findall(r"'([^']*)'", achado.group(1))
    except oracledb.Error:
        pass
    return REALIZACAO_PADRAO


def texto(valor):
    return "" if valor is None else str(valor)


def para_data(valor):
    return valor.date() if isinstance(valor, datetime.datetime) else valor




def nome_equipamento(e):
    return " | ".join(p for p in [f"{texto(e['FABRICANTE'])} {texto(e['MODELO'])}".strip(),
                                  texto(e["IDENTIFICACAO_INTERNA"])] if p) or f"Equipamento {e.get('ID', '')}"


@st.cache_data(ttl=60, show_spinner=False)
def colunas_da_tabela(tabela_banco):
    return {c["COLUMN_NAME"] for c in consultar("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :t",
                                                {"t": tabela_banco})}


def tem_coluna(coluna):
    """Colunas novas de CS_MANUTENCOES_REALIZADAS (sql/estrutura_banco.sql): a página funciona sem elas."""
    return coluna in colunas_da_tabela("CS_MANUTENCOES_REALIZADAS")


COLUNAS_NOVAS = ("REGRA", "ID_COMPROVANTE", "AVALIADO_POR", "DATA_AVALIACAO", "MOTIVO_AVALIACAO")
COLUNAS_USO_MAQUINA = ("DATA_AQUISICAO", "HORIMETRO_ATUAL", "HODOMETRO_ATUAL", "DATA_LEITURA", "USO_MEDIO_HORAS_MES",
                       "USO_MEDIO_KM_MES", "TIPO_TRACAO", "DATA_INICIO_VIGENCIA")


def carregar_atividades(filtro, parametros):
    """Manutenções (só as de tipo manutenção) com a recomendação e os dados de uso da máquina."""
    novas = [c for c in COLUNAS_NOVAS if tem_coluna(c)]
    uso = [c for c in COLUNAS_USO_MAQUINA if c in colunas_da_tabela("CS_EQUIPAMENTOS_SEGURADOS")]
    linhas = consultar(f"""
        SELECT m.ID, m.ID_EQUIPAMENTO_SEGURADO, m.ID_ORIENTACAO, m.DATA_PREVISTA, m.DATA_REALIZADA, m.REALIZACAO,
               m.COMPROVANTE_ENTREGUE, m.ATENDE_CRITERIOS{''.join(', m.' + c for c in novas)},
               o.TIPO_ORIENTACAO, o.SUBSISTEMA, o.ACAO_TECNICA, o.DETALHAMENTO_ORIENTACAO, o.METRICA_GATILHO,
               o.VALOR_GATILHO, o.UNIDADE_MEDIDA, o.FATOR_CONDICIONAL,
               e.IDENTIFICACAO_INTERNA, e.NUMERO_SERIE, e.ID_FAZENDA, f.NOME_FAZENDA,
               mo.FABRICANTE, mo.MODELO{''.join(', e.' + c for c in uso)}
        FROM CS_MANUTENCOES_REALIZADAS m
        JOIN CS_EQUIPAMENTOS_SEGURADOS e ON e.ID = m.ID_EQUIPAMENTO_SEGURADO
        JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
        LEFT JOIN CS_EQUIPAMENTOS_ORIENTACOES o ON o.ID = m.ID_ORIENTACAO
        LEFT JOIN CS_EQUIPAMENTOS_MODELOS mo ON mo.ID = e.ID_EQUIPAMENTO_MODELO
        WHERE {filtro}
        ORDER BY m.DATA_PREVISTA, m.ID
    """, parametros)
    atividades = []
    for a in linhas:
        if not eh_manutencao(a["TIPO_ORIENTACAO"]):
            continue                 # orientações de operação, segurança etc. não pedem comprovante
        for coluna in COLUNAS_NOVAS:
            a.setdefault(coluna, None)
        a["DATA_PREVISTA"] = para_data(a["DATA_PREVISTA"])
        a["DATA_REALIZADA"] = para_data(a["DATA_REALIZADA"])
        a["tem_comprovante"] = str(a["COMPROVANTE_ENTREGUE"] or "").upper().startswith("S")
        a["equipamento"] = nome_equipamento(a)
        atividades.append(a)
    return atividades


def situacao_item(a):
    if not a["tem_comprovante"]:
        return "A fazer"
    if cp.reprovada(a):
        return "Não atende (Sompo)" + (f": {a['MOTIVO_AVALIACAO']}" if a.get("MOTIVO_AVALIACAO") else "")
    if cp.aguardando_validacao(a):
        return "Enviado, aguardando validação"
    return f"Validado: {cp.rotulo_validacao(a['ATENDE_CRITERIOS']).lower()}"


CORES_NIVEL = {"Ouro": "#B8860B", "Prata": "#6B7C8C", "Bronze": "#A0522D", "Em risco": "#C62828", None: "#607D8B"}


def placar(nivel_atual, percentual, realizadas, previstas, em_sequencia, indicador):
    """Quadro do equipamento: nível, barra de realização, revisões seguidas, regra ideal e validação."""
    cor = CORES_NIVEL.get(nivel_atual, "#607D8B")
    largura = 0 if percentual is None else max(0, min(100, percentual))
    proximo = ""
    if percentual is not None and previstas:
        for limite, nome in sorted(((l, n) for l, n in cp.NIVEIS if l > 0), key=lambda x: x[0]):
            if percentual < limite:
                faltam = math.ceil(limite / 100 * previstas) - realizadas
                proximo = f"Faltam {faltam} manutenção(ões) comprovada(s) para o nível {nome}."
                break
        else:
            proximo = "Nível máximo. Continue assim!"
    ideal = ("" if indicador["ideal"] is None else
             f"<div>Regra ideal do manual: <b>{indicador['ideal']:.0f}%</b>"
             + (f" (bônus de {indicador['bonus']:g} pontos no Score de Risco)".replace(".", ",") if indicador["bonus"]
                else "") + "</div>")
    st.markdown(f"""
        <div style="border:1px solid #d8dee4; border-radius:10px; padding:16px 20px; margin:8px 0 4px 0;
                    display:flex; flex-wrap:wrap; gap:18px 36px; align-items:center;">
          <div style="min-width:150px; text-align:center;">
            <div style="font-size:0.8rem; color:#555;">Nível do equipamento</div>
            <div style="font-size:1.7rem; font-weight:800; color:{cor};">{nivel_atual or 'Começando'}</div>
          </div>
          <div style="flex:1; min-width:260px;">
            <div style="font-weight:700; color:#1A4A75;">Realização do cronograma:
              {'-' if percentual is None else f'{percentual:.0f}%'}
              <span style="font-weight:400; color:#555;">({realizadas} de {previstas} manutenções vencidas; as que
              ainda estão no prazo de {cp.PRAZO_COMPROVANTE_DIAS} dias não contam)</span></div>
            <div style="background:#e9ecef; border-radius:6px; height:14px; margin:6px 0;">
              <div style="background:{cor}; width:{largura:.0f}%; height:14px; border-radius:6px;"></div></div>
            <div style="font-size:0.9rem; color:#555;">{proximo}</div>
          </div>
          <div style="min-width:240px; font-size:0.95rem;">
            <div>Revisões seguidas em dia: <b>{em_sequencia}</b></div>
            {ideal}
            <div>Aguardando validação da Sompo: <b>{indicador['aguardando']}</b></div>
          </div>
        </div>""", unsafe_allow_html=True)


ESTILO_STATUS = {cp.PENDENTE: ("#FDECEA", "#B71C1C", "Comprovante pendente"),
                 cp.NAO_ENVIADO: ("#ECEFF1", "#616161", "Comprovante não enviado"),
                 cp.PROXIMA: ("#FFF8E1", "#8D6E00", "Próxima"),
                 cp.FUTURA: ("#ECEFF1", "#455A64", "Programada")}


def gravar_comprovante(ids_atividades, arquivo, data_realizada, conn=None, chave_revisao=None,
                       acao="ENVIO_COMPROVANTE"):
    """Um arquivo para todos os serviços da revisão: grava o anexo uma vez e marca as manutenções como realizadas
    (reenvio depois de uma reprovação limpa a validação anterior). Com conn, não faz commit."""
    if conn is None:
        with pool_conexoes().acquire() as nova:
            gravar_comprovante(ids_atividades, arquivo, data_realizada, nova, chave_revisao, acao)
            nova.commit()
        return
    realizada = valor_realizacao(valores_realizacao(), "realizada")
    id_anexo = salvar_anexo(conn, ENTIDADE_MANUTENCAO, ids_atividades[0], ANEXO_COMPROVANTE, arquivo.name,
                            arquivo.getvalue(), arquivo.type, usuario)
    campos = "REALIZACAO = :r, DATA_REALIZADA = :d, COMPROVANTE_ENTREGUE = 'SIM', ATENDE_CRITERIOS = NULL"
    valores = {"r": realizada, "d": data_realizada}
    if tem_coluna("ID_COMPROVANTE"):
        campos += ", ID_COMPROVANTE = :anexo"
        valores["anexo"] = id_anexo
    if tem_coluna("AVALIADO_POR"):
        campos += ", AVALIADO_POR = NULL, DATA_AVALIACAO = NULL, MOTIVO_AVALIACAO = NULL"
    with conn.cursor() as cursor:
        for id_atividade in ids_atividades:
            cursor.execute(f"UPDATE CS_MANUTENCOES_REALIZADAS SET {campos} WHERE ID = :id",
                           dict(valores, id=id_atividade))
    auditoria.registrar(conn, acao, auditoria.ENTIDADE_REVISAO, chave_revisao,
                        f"Arquivo {arquivo.name} (anexo {id_anexo}); feita em {data_realizada:%d/%m/%Y}; "
                        f"{len(ids_atividades)} serviço(s)", usuario, perfil, "Comprovação")


def cabecalho_revisao(revisao, hoje, rotulo_status=None):
    fundo, cor, rotulo = ESTILO_STATUS.get(revisao["status"], ("#E8F5E9", "#1B5E20", "Comprovada"))
    rotulo = rotulo_status or rotulo
    medidor = f" · por volta de {revisao['medidor']:,.0f} horas".replace(",", ".") if revisao["medidor"] else ""
    prazo = f" · {cp.situacao_prazo(revisao, hoje)}" if revisao["abertos"] else ""
    st.markdown(f"""
        <div style="display:flex; flex-wrap:wrap; justify-content:space-between; gap:8px; align-items:center;">
          <div style="font-size:1.2rem; font-weight:800; color:#1A4A75;">{html.escape(revisao['titulo'])}</div>
          <div style="background:{fundo}; color:{cor}; border-radius:12px; padding:2px 12px; font-weight:700;
                      font-size:0.85rem;">{html.escape(rotulo)}</div>
        </div>
        <div style="color:#555; margin:2px 0 8px 0;">Prevista para {revisao['data']:%d/%m/%Y}{medidor}{prazo}</div>
        """, unsafe_allow_html=True)


def chave_revisao(revisao):
    a0 = revisao["itens"][0]["atividade"]
    return f"{a0['ID_EQUIPAMENTO_SEGURADO']}_{revisao['data']:%Y%m%d}_{a0['ID']}"


def tabela_servicos(revisao):
    """Serviços da revisão; a coluna Periodicidade só aparece quando há mais de uma no card (o título já a mostra)."""
    tabela([({"Periodicidade": i["periodicidade"]} if revisao["varias_periodicidades"] else {})
            | {"Sistema": i["sistema"], "Serviço": i["servico"]} for i in revisao["itens"]], altura=None)


def cartao_revisao(revisao, hoje):
    """Revisão a comprovar: os serviços (como na tabela do manual) e o envio de UM comprovante para a revisão."""
    with st.container(border=True):
        cabecalho_revisao(revisao, hoje)
        registro_de_acoes(revisao["chave"])
        if revisao["validacao"] == "Não atende":
            st.error("A Sompo não aceitou o comprovante enviado" + (f": {revisao['motivo']}" if revisao["motivo"]
                                                                    else ".") + " Envie um novo comprovante.")
        tabela_servicos(revisao)
        chave = f"rev_{chave_revisao(revisao)}"
        with st.form(f"form_{chave}", clear_on_submit=True):
            f1, f2 = st.columns([3, 1])
            arquivo = f1.file_uploader("Comprovante da revisão: nota fiscal, ordem de serviço ou fotos (PDF, JPG ou "
                                       "PNG, até 10 MB)", type=TIPOS_DOCUMENTO, key=f"arquivo_{chave}")
            data_realizada = f2.date_input("Feita em", value=min(hoje, revisao["data"]), max_value=hoje,
                                           format="DD/MM/YYYY")
            enviar = st.form_submit_button("Enviar comprovante da revisão")
        if enviar:
            if arquivo is None:
                st.error("Anexe o comprovante.")
            else:
                try:
                    gravar_comprovante([i["atividade"]["ID"] for i in revisao["abertos"]], arquivo, data_realizada,
                                       chave_revisao=revisao["chave"])
                except AnexoInvalido as e:
                    st.error(str(e))
                except oracledb.Error as e:
                    st.error(f"Erro ao salvar o comprovante: {e}")
                else:
                    st.session_state["_aviso_manutencao"] = (f"{revisao['titulo']}: comprovante enviado. A Sompo vai "
                                                             "validar e o nível do equipamento já foi atualizado.")
                    st.rerun()


def cartao_concluida(revisao, hoje):
    """Revisão já comprovada: validação da Sompo e envio de mais comprovantes da mesma revisão (feita de novo antes
    da próxima, seguindo a regra ideal do manual)."""
    a0 = revisao["itens"][0]["atividade"]
    rotulo = (f"Validada: {revisao['validacao'].lower()}" if revisao["validacao"] and not revisao["aguardando"]
              else "Aguardando validação")
    with st.container(border=True):
        cabecalho_revisao(revisao, hoje, rotulo)
        st.caption(f"Feita em {revisao['realizada_em']:%d/%m/%Y}" if revisao["realizada_em"] else "")
        registro_de_acoes(revisao["chave"])
        if revisao["extras"]:
            datas = sorted({a["DATA_REALIZADA"] for a in revisao["extras"] if a["DATA_REALIZADA"]})
            st.markdown(f"**Feita a mais {len(datas)} vez(es)** (regra ideal): "
                        + ", ".join(f"{d:%d/%m/%Y}" for d in datas) + ".")
        tabela_servicos(revisao)
        if not tem_coluna("REGRA"):
            return
        st.markdown("**Fez esta revisão de novo antes da próxima?** Envie o comprovante: conta para a regra ideal "
                    "do manual (ex.: a cada 400 horas, mesmo quando o cronograma pede a semestral) e dá bônus no "
                    "Score de Risco.")
        chave = f"mais_{chave_revisao(revisao)}"
        with st.form(f"form_{chave}", clear_on_submit=True):
            f1, f2 = st.columns([3, 1])
            arquivo = f1.file_uploader("Comprovante (PDF, JPG ou PNG, até 10 MB)", type=TIPOS_DOCUMENTO,
                                       key=f"arquivo_{chave}")
            data_realizada = f2.date_input("Feita em", value=hoje, max_value=hoje, format="DD/MM/YYYY")
            enviar = st.form_submit_button("Enviar mais um comprovante desta revisão")
        if enviar:
            if arquivo is None:
                st.error("Anexe o comprovante.")
                return
            try:
                realizada = valor_realizacao(valores_realizacao(), "realizada")
                with pool_conexoes().acquire() as conn:
                    with conn.cursor() as cursor:
                        novos = [_inserir_com_id(cursor, "CS_MANUTENCOES_REALIZADAS", {
                            "ID_EQUIPAMENTO_SEGURADO": a0["ID_EQUIPAMENTO_SEGURADO"],
                            "ID_ORIENTACAO": i["atividade"]["ID_ORIENTACAO"], "DATA_PREVISTA": revisao["data"],
                            "DATA_REALIZADA": data_realizada, "REALIZACAO": realizada, "COMPROVANTE_ENTREGUE": "NAO",
                            "REGRA": REGRA_IDEAL}) for i in revisao["itens"]]
                    gravar_comprovante(novos, arquivo, data_realizada, conn, revisao["chave"], "COMPROVANTE_A_MAIS")
                    conn.commit()
            except AnexoInvalido as e:
                st.error(str(e))
            except oracledb.Error as e:
                st.error(f"Erro ao registrar: {e}")
            else:
                st.session_state["_aviso_manutencao"] = (f"{revisao['titulo']}: mais um comprovante enviado. Conta "
                                                         "para a regra ideal do manual.")
                st.rerun()


def grafico_mes(atividades, hoje):
    meses = pd.DataFrame(cp.por_mes(atividades, hoje))
    if not meses["quantidade"].sum():
        return
    import altair as alt
    grafico = (alt.Chart(meses).mark_bar(cornerRadiusEnd=2)
               .encode(x=alt.X("yearmonth(mes):O", title=None, axis=alt.Axis(format="%m/%Y", labelAngle=-45)),
                       xOffset=alt.XOffset("serie:N"),
                       y=alt.Y("quantidade:Q", title="Serviços"),
                       color=alt.Color("serie:N", title=None,
                                       scale=alt.Scale(domain=["Previstas", "Realizadas"], range=["#90A4AE", "#388e3c"]),
                                       legend=alt.Legend(orient="top")),
                       tooltip=[alt.Tooltip("yearmonth(mes):O", title="Mês", format="%m/%Y"),
                                alt.Tooltip("serie:N", title="Série"), alt.Tooltip("quantidade:Q", title="Serviços")])
               .properties(height=240))
    st.altair_chart(grafico, width="stretch")
    st.caption("Serviços previstos no cronograma (pela data prevista) e realizados (pela data informada no "
               "comprovante), 12 meses para trás e 3 para a frente.")


def chave_validacao(a):
    """Itens da validação: a revisão inteira (máquina, data prevista e periodicidade); a revisão feita a mais (regra
    ideal) é validada à parte, por data de realização."""
    return (cp.chave_da_atividade(a), cp.eh_ideal(a), a["DATA_REALIZADA"] if cp.eh_ideal(a) else None)


def anexos_do_comprovante(itens):
    """Arquivos dos comprovantes dos itens (sem repetir): pelo ID_COMPROVANTE ou pelos anexos de cada manutenção."""
    ids_anexo = sorted({int(a["ID_COMPROVANTE"]) for a in itens if a.get("ID_COMPROVANTE")})
    with pool_conexoes().acquire() as conn:
        anexos = []
        if ids_anexo:
            binds = {f"a{n}": v for n, v in enumerate(ids_anexo)}
            with conn.cursor() as cursor:
                cursor.execute("SELECT ID, NOME_ARQUIVO, TIPO_CONTEUDO, USUARIO_ENVIO, DATA_ENVIO FROM CS_ANEXOS "
                               f"WHERE ID IN ({', '.join(':' + b for b in binds)})", binds)
                colunas = [c[0] for c in cursor.description]
                anexos = [dict(zip(colunas, linha)) for linha in cursor.fetchall()]
        sem_ligacao = [a["ID"] for a in itens if not a.get("ID_COMPROVANTE")]
        for lista in listar_anexos(conn, ENTIDADE_MANUTENCAO, sem_ligacao).values():
            anexos += lista
    vistos, unicos = set(), []
    for anexo in sorted(anexos, key=lambda x: x.get("DATA_ENVIO") or datetime.datetime.min, reverse=True):
        if anexo["ID"] not in vistos:
            vistos.add(anexo["ID"])
            unicos.append(anexo)
    return unicos


def registro_de_acoes(chave):
    """Quem fez o quê e quando nesta revisão (CS_LOG_ACOES)."""
    with pool_conexoes().acquire() as conn:
        linhas = auditoria.historico(conn, auditoria.ENTIDADE_REVISAO, chave)
    if linhas:
        # popover (não expander): os cards também aparecem dentro de listas recolhidas, e o Streamlit não aninha expanders
        with st.popover(f"Registro de ações ({len(linhas)})"):
            tabela([{"Quando": l["DATA_HORA"], "Quem": nome_usuario(l["USUARIO"]), "Perfil": l["PERFIL"],
                     "Ação": NOMES_ACAO.get(l["ACAO"], l["ACAO"]), "Detalhe": l["DETALHE"]} for l in linhas],
                   altura=260)


NOMES_ACAO = {"ENVIO_COMPROVANTE": "Envio do comprovante", "COMPROVANTE_A_MAIS": "Revisão feita a mais",
              "VALIDACAO": "Validação Sompo"}


@st.cache_data(show_spinner=False, max_entries=50)
def paginas_em_imagem(id_anexo, max_paginas=2):
    """Primeiras páginas do PDF como imagem, para a Sompo conferir sem baixar."""
    import pymupdf
    with pool_conexoes().acquire() as conn:
        _, conteudo, _ = conteudo_anexo(conn, id_anexo)
    with pymupdf.open(stream=conteudo, filetype="pdf") as doc:
        return [pagina.get_pixmap(dpi=110).tobytes("png") for pagina in list(doc)[:max_paginas]]


def mostrar_documento(anexo):
    try:
        with pool_conexoes().acquire() as conn:
            arquivo = conteudo_anexo(conn, anexo["ID"])
    except AnexoInvalido as e:
        st.error(str(e))
        return
    if not arquivo:
        return
    nome, conteudo, tipo = arquivo
    if nome.lower().endswith(".pdf"):
        for imagem in paginas_em_imagem(anexo["ID"]):
            st.image(imagem, width="stretch")
    else:
        st.image(conteudo, width="stretch")
    st.download_button(f"Baixar {nome}", conteudo, file_name=nome, mime=tipo or "application/octet-stream",
                       key=f"baixar_{anexo['ID']}")


def cartao_validacao(itens):
    a0 = itens[0]
    anexos = anexos_do_comprovante(itens)
    chave_rev = cp.chave_da_atividade(a0)
    chave = "val_" + chave_rev.replace("|", "_") + ("_ideal_" + f"{a0['DATA_REALIZADA']:%Y%m%d}"
                                                   if cp.eh_ideal(a0) and a0["DATA_REALIZADA"] else "")
    with st.container(border=True):
        enviado = (f" · enviado por {nome_usuario(anexos[0]['USUARIO_ENVIO']) or '-'} em {anexos[0]['DATA_ENVIO']:%d/%m/%Y}"
                   if anexos and anexos[0].get("DATA_ENVIO") else "")
        st.markdown(f"**{cp.titulo_da_atividade(a0)}** — {a0['equipamento']} (série "
                    f"{texto(a0['NUMERO_SERIE']) or '-'}) — {a0['NOME_FAZENDA']}")
        st.caption(("Feita a mais (regra ideal) · " if cp.eh_ideal(a0) else "")
                   + f"Prevista para {a0['DATA_PREVISTA']:%d/%m/%Y}"
                   + (f" · feita em {a0['DATA_REALIZADA']:%d/%m/%Y}" if a0["DATA_REALIZADA"] else "")
                   + f" · {len(itens)} serviço(s)" + enviado)
        registro_de_acoes(chave_rev)
        c_doc, c_val = st.columns([3, 2])
        with c_doc:
            if anexos:
                for anexo in anexos:
                    mostrar_documento(anexo)
            else:
                st.warning("Arquivo do comprovante não encontrado.")
        with c_val:
            st.markdown("**Serviços da revisão**")
            tabela([{"Sistema": cp._sistema(a), "Serviço": cp._servico(a)} for a in itens], altura=None)
            atual = cp.rotulo_validacao(min((a["ATENDE_CRITERIOS"] for a in itens
                                             if a["ATENDE_CRITERIOS"] is not None), default=None))
            with st.form(f"form_{chave}"):
                opcoes = list(cp.NOTAS_VALIDACAO)
                resultado = st.radio("O comprovante atende à revisão?", opcoes, key=f"{chave}_nota",
                                     index=opcoes.index(atual) if atual else None)
                motivo = st.text_input("Motivo (obrigatório para \"menor parte\" e \"não atende\"; aparece para o "
                                       "cliente)", value=next((texto(a["MOTIVO_AVALIACAO"]) for a in itens
                                                               if a.get("MOTIVO_AVALIACAO")), ""), max_chars=500)
                salvar = st.form_submit_button("Salvar validação")
            if salvar:
                if resultado is None:
                    st.error("Escolha o resultado da validação.")
                    return
                nota = cp.NOTAS_VALIDACAO[resultado]
                if nota <= 25 and not motivo.strip():
                    st.error("Informe o motivo: ele aparece para o cliente.")
                    return
                campos = "ATENDE_CRITERIOS = :v"
                valores = {"v": nota}
                if tem_coluna("AVALIADO_POR"):
                    campos += ", AVALIADO_POR = :quem, DATA_AVALIACAO = :quando, MOTIVO_AVALIACAO = :motivo"
                    valores.update(quem=usuario[:150] or None, quando=datetime.date.today(),
                                   motivo=motivo.strip()[:500] or None)
                try:
                    with pool_conexoes().acquire() as conn:
                        with conn.cursor() as cursor:
                            for a in itens:
                                cursor.execute(f"UPDATE CS_MANUTENCOES_REALIZADAS SET {campos} WHERE ID = :id",
                                               dict(valores, id=a["ID"]))
                        auditoria.registrar(conn, "VALIDACAO", auditoria.ENTIDADE_REVISAO, chave_rev,
                                            f"{resultado} ({nota})" + (f"; motivo: {motivo.strip()}" if motivo.strip()
                                                                       else "") + f"; {len(itens)} serviço(s)"
                                            + ("; revisão feita a mais" if cp.eh_ideal(a0) else ""),
                                            usuario, perfil, "Comprovação")
                        conn.commit()
                except oracledb.Error as e:
                    st.error(f"Erro ao salvar a validação: {e}")
                    return
                st.session_state["_aviso_manutencao"] = f"Validação salva: {resultado.lower()}."
                st.rerun()


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
st.markdown("<h1 style='color: #1A4A75; margin-bottom: 0;'>Comprovação</h1>", unsafe_allow_html=True)
st.markdown("Escolha o equipamento e acompanhe as revisões recomendadas pelo manual. Cada revisão comprovada sobe o "
            "nível do equipamento e melhora o Score de Risco do cliente.")
if aviso_salvo:
    st.success(aviso_salvo)

# ---------------------------------------------------------------------------
# Cliente, fazenda e equipamento (a manutenção é feita por equipamento)
# ---------------------------------------------------------------------------
try:
    clientes = consultar("""
        SELECT c.ID, c.RAZAO_SOCIAL, c.CNPJ,
               (SELECT COUNT(*) FROM CS_EQUIPAMENTOS_SEGURADOS e JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
                WHERE f.ID_CLIENTE = c.ID) AS EQUIPAMENTOS
        FROM CS_CLIENTES c ORDER BY c.RAZAO_SOCIAL
    """)
except oracledb.Error as e:
    st.error(f"Não foi possível conectar ao banco de dados: {e}")
    st.stop()
if perfil == PERFIL_PRODUTOR:
    # cliente só vê os próprios equipamentos
    clientes = cliente_do_usuario(clientes)
    if not clientes:
        st.warning("Seu usuário não está ligado a um cliente. Peça à Sompo para vincular o CNPJ do seu cadastro.")
        render_footer()
        st.stop()
if not clientes:
    st.info("Nenhum cliente cadastrado.")
    st.stop()
nomes_clientes = {c["ID"]: f"{c['RAZAO_SOCIAL']} ({texto(c['CNPJ'])})"
                  + ("" if c["EQUIPAMENTOS"] else " · sem equipamentos") for c in clientes}
# abre no primeiro cliente com equipamento segurado (os sem equipamento não têm revisões a mostrar)
if "manutencao_cliente" not in st.session_state or st.session_state["manutencao_cliente"] not in nomes_clientes:
    st.session_state["manutencao_cliente"] = next((c["ID"] for c in clientes if c["EQUIPAMENTOS"]), clientes[0]["ID"])
s1, s2, s3 = st.columns(3)
id_cliente = s1.selectbox("Cliente", list(nomes_clientes), format_func=lambda i: nomes_clientes[i],
                          key="manutencao_cliente", disabled=perfil == PERFIL_PRODUTOR)
equipamentos_cliente = consultar("""
    SELECT e.ID, e.ID_FAZENDA, f.NOME_FAZENDA, e.IDENTIFICACAO_INTERNA, e.NUMERO_SERIE, mo.FABRICANTE, mo.MODELO
    FROM CS_EQUIPAMENTOS_SEGURADOS e
    JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
    LEFT JOIN CS_EQUIPAMENTOS_MODELOS mo ON mo.ID = e.ID_EQUIPAMENTO_MODELO
    WHERE f.ID_CLIENTE = :cliente
    ORDER BY f.NOME_FAZENDA, mo.FABRICANTE, mo.MODELO, e.IDENTIFICACAO_INTERNA
""", {"cliente": id_cliente})
fazendas_cliente = sorted({(e["ID_FAZENDA"], e["NOME_FAZENDA"]) for e in equipamentos_cliente}, key=lambda f: f[1])
id_fazenda = s2.selectbox("Fazenda", [None] + [f[0] for f in fazendas_cliente],
                          format_func=lambda i: "Todas" if i is None else dict(fazendas_cliente)[i],
                          key=f"manutencao_fazenda_{id_cliente}")
nomes_equipamentos = {e["ID"]: nome_equipamento(e) + ("" if id_fazenda else f" | {e['NOME_FAZENDA']}")
                      for e in equipamentos_cliente if id_fazenda is None or e["ID_FAZENDA"] == id_fazenda}
id_equipamento = s3.selectbox("Equipamento", list(nomes_equipamentos), format_func=lambda i: nomes_equipamentos[i],
                              key=f"manutencao_equipamento_{id_cliente}_{id_fazenda}",
                              placeholder="Nenhum equipamento segurado")

# Painel: 4 indicadores do cliente (ou da fazenda escolhida), todas as máquinas. Consulta leve (sem os textos longos
# das recomendações) e guardada por 2 minutos: a lista completa só é carregada para o equipamento escolhido.
@st.cache_data(ttl=120, show_spinner=False)
def indicadores_painel(id_cliente, id_fazenda, hoje):
    novas = [c for c in ("REGRA", "MOTIVO_AVALIACAO") if tem_coluna(c)]
    uso = [c for c in COLUNAS_USO_MAQUINA if c in colunas_da_tabela("CS_EQUIPAMENTOS_SEGURADOS")]
    filtro, parametros = "f.ID_CLIENTE = :cliente", {"cliente": id_cliente}
    if id_fazenda is not None:
        filtro, parametros = "e.ID_FAZENDA = :fazenda", {"fazenda": id_fazenda}
    linhas = consultar(f"""
        SELECT m.ID, m.ID_EQUIPAMENTO_SEGURADO, m.ID_ORIENTACAO, m.DATA_PREVISTA, m.DATA_REALIZADA,
               m.COMPROVANTE_ENTREGUE, m.ATENDE_CRITERIOS{''.join(', m.' + c for c in novas)},
               o.TIPO_ORIENTACAO, o.METRICA_GATILHO, o.VALOR_GATILHO, o.UNIDADE_MEDIDA, o.FATOR_CONDICIONAL
               {''.join(', e.' + c for c in uso)}
        FROM CS_MANUTENCOES_REALIZADAS m
        JOIN CS_EQUIPAMENTOS_SEGURADOS e ON e.ID = m.ID_EQUIPAMENTO_SEGURADO
        JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
        LEFT JOIN CS_EQUIPAMENTOS_ORIENTACOES o ON o.ID = m.ID_ORIENTACAO
        WHERE {filtro}
    """, parametros)
    lista = []
    for a in linhas:
        if not eh_manutencao(a["TIPO_ORIENTACAO"]):
            continue
        for coluna in COLUNAS_NOVAS:
            a.setdefault(coluna, None)
        a["DATA_PREVISTA"], a["DATA_REALIZADA"] = para_data(a["DATA_PREVISTA"]), para_data(a["DATA_REALIZADA"])
        a["tem_comprovante"] = str(a["COMPROVANTE_ENTREGUE"] or "").upper().startswith("S")
        lista.append(a)
    ind = cp.indicador_cumprimento(lista, hoje)
    limite = hoje - datetime.timedelta(days=cp.PRAZO_COMPROVANTE_DIAS)
    aguardando = len({(a["ID_EQUIPAMENTO_SEGURADO"], a["DATA_PREVISTA"], a["DATA_REALIZADA"]) for a in lista
                      if cp.aguardando_validacao(a)})
    nao_enviadas = len({(a["ID_EQUIPAMENTO_SEGURADO"], a["DATA_PREVISTA"]) for a in lista
                        if not a["tem_comprovante"] and not cp.eh_ideal(a) and a["DATA_PREVISTA"]
                        and a["DATA_PREVISTA"] < limite})
    return ind["realizacao"], ind["atende"], aguardando, nao_enviadas


if equipamentos_cliente:
    try:
        _realizacao, _atende, _aguardando, _nao_enviadas = indicadores_painel(id_cliente, id_fazenda,
                                                                             datetime.date.today())
    except oracledb.Error as e:
        st.warning(f"Não foi possível calcular os indicadores: {e}")
    else:
        _k1, _k2, _k3, _k4 = st.columns(4)
        _k1.metric("Realização do cronograma", "—" if _realizacao is None else f"{_realizacao:.0f}%",
                   help="Revisões com comprovante entre as que já passaram do prazo de "
                   f"{cp.PRAZO_COMPROVANTE_DIAS} dias. Meta do Score Risk: 80%.")
        _k2.metric("Aprovação na validação Sompo", "—" if _atende is None else f"{_atende:.0f}%",
                   help="Média da validação da Sompo (Atende 100, maior parte 75, menor parte 25, não atende 0).")
        _k3.metric("Comprovantes aguardando validação", _aguardando)
        _k4.metric("Revisões sem comprovante no prazo", _nao_enviadas,
                   help=f"Revisões que passaram {cp.PRAZO_COMPROVANTE_DIAS} dias da data prevista sem comprovante.")

# Seletor no lugar de abas: a escolha continua a mesma depois de enviar um formulário (a página recarrega)
VISTA_REVISOES, VISTA_LEITURAS, VISTA_VALIDACAO = ("Revisões do equipamento", "Leitura mensal do horímetro",
                                                   "Validação Sompo")
vistas = [VISTA_REVISOES, VISTA_LEITURAS] + ([VISTA_VALIDACAO] if perfil in (PERFIL_ADMIN, PERFIL_SOMPO) else [])
vista = st.radio("Ver", vistas, horizontal=True, key="manutencao_vista", label_visibility="collapsed")

# ---------------------------------------------------------------------------
# Leitura mensal do horímetro (com evidência): atualiza o horímetro e o uso médio e refaz as datas das manutenções
# ---------------------------------------------------------------------------
if vista == VISTA_LEITURAS:
    if not tem_tabela_leituras():
        st.warning("Rode sql/estrutura_banco.sql no banco para usar a leitura mensal do horímetro.")
    else:
        maquinas = consultar("""
            SELECT e.ID, e.IDENTIFICACAO_INTERNA, e.NUMERO_SERIE, e.HORIMETRO_ATUAL, e.HODOMETRO_ATUAL, e.DATA_LEITURA,
                   e.USO_MEDIO_HORAS_MES, e.USO_MEDIO_KM_MES, f.NOME_FAZENDA, mo.FABRICANTE, mo.MODELO
            FROM CS_EQUIPAMENTOS_SEGURADOS e
            JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
            LEFT JOIN CS_EQUIPAMENTOS_MODELOS mo ON mo.ID = e.ID_EQUIPAMENTO_MODELO
            WHERE f.ID_CLIENTE = :cliente
            ORDER BY f.NOME_FAZENDA, mo.FABRICANTE, mo.MODELO
        """, {"cliente": id_cliente})
        if not maquinas:
            st.info("Nenhum equipamento segurado para este cliente.")
        else:
            hoje_leitura = datetime.date.today()
            with pool_conexoes().acquire() as conn:
                ultimas = ultimas_leituras(conn, [m["ID"] for m in maquinas])
            nomes_maquinas = {}
            quadro = []
            for m in maquinas:
                nome = " | ".join(p for p in [f"{texto(m['FABRICANTE'])} {texto(m['MODELO'])}".strip(),
                                              texto(m["IDENTIFICACAO_INTERNA"]), texto(m["NOME_FAZENDA"])] if p)
                nomes_maquinas[m["ID"]] = nome
                ultima = ultimas.get(m["ID"])
                data_ultima = ultima["DATA_LEITURA"] if ultima else None
                situacao_m = situacao_leitura(data_ultima, hoje_leitura)
                quadro.append({
                    "Equipamento": nome,
                    "Horímetro atual": f"{float(m['HORIMETRO_ATUAL']):,.0f} h".replace(",", ".")
                    if m["HORIMETRO_ATUAL"] is not None else "-",
                    "Última leitura mensal": f"{data_ultima:%d/%m/%Y}" if data_ultima else "-",
                    "Uso médio": f"{float(m['USO_MEDIO_HORAS_MES']):,.0f} h/mês".replace(",", ".")
                    if m["USO_MEDIO_HORAS_MES"] is not None else "-",
                    "Situação": {"em dia": "Em dia", "pendente": f"Pendente (mais de {PRAZO_LEITURA_DIAS} dias)",
                                 "sem leitura": "Sem leitura mensal"}[situacao_m],
                })
            em_dia = sum(1 for q in quadro if q["Situação"] == "Em dia")
            l1, l2 = st.columns(2)
            l1.metric("Leituras em dia", f"{em_dia} de {len(quadro)}")
            l2.metric("Pendentes", len(quadro) - em_dia)
            st.caption(f"Todo mês, informe as horas do horímetro de cada equipamento com a foto do horímetro. As "
                       f"manutenções \"a cada N horas\" passam a vencer pelas horas reais da máquina.")
            tabela(quadro)

            # Em erro, os dados digitados continuam no formulário; depois de registrar, o formulário volta vazio
            numero_form = st.session_state.setdefault("leitura_form_n", 0)
            with st.form(f"form_leitura_{numero_form}"):
                id_maquina = st.selectbox("Equipamento", list(nomes_maquinas), format_func=nomes_maquinas.get)
                r1, r2, r3 = st.columns(3)
                data_nova = r1.date_input("Data da leitura", value=hoje_leitura, max_value=hoje_leitura,
                                          format="DD/MM/YYYY")
                horas_novas = r2.number_input("Horímetro (horas)", min_value=0.0, step=1.0, value=None,
                                              format="%.1f", placeholder="Horas que o horímetro marca")
                km_novos = r3.number_input("Hodômetro (km, se houver)", min_value=0.0, step=10.0, value=None,
                                           format="%.1f")
                foto = st.file_uploader("Foto ou PDF do horímetro (JPG, PNG ou PDF, até 10 MB)", type=TIPOS_DOCUMENTO)
                enviar_leitura = st.form_submit_button("Registrar leitura")
            if enviar_leitura:
                try:
                    with pool_conexoes().acquire() as conn:
                        resultado_leitura = registrar_leitura(
                            conn, id_maquina, data_nova, horas_novas, km_novos,
                            (foto.name, foto.getvalue(), foto.type) if foto is not None else None, usuario)
                        conn.commit()
                    auditoria.registrar_avulso("LEITURA", "EQUIPAMENTO", str(id_maquina),
                                               f"leitura de {data_nova:%d/%m/%Y}", usuario, perfil, "Comprovação")
                    st.session_state["leitura_form_n"] = numero_form + 1
                    uso_novo = resultado_leitura["uso_medio_horas"]
                    st.session_state["_aviso_manutencao"] = (
                        f"Leitura de {nomes_maquinas[id_maquina]} registrada"
                        + (f"; uso médio atualizado para {uso_novo:,.0f} horas por mês".replace(",", ".") if uso_novo else "")
                        + f"; datas das manutenções recalculadas ({resultado_leitura['programacao']['geradas']} "
                          f"pendentes).")
                    st.rerun()
                except (LeituraInvalida, AnexoInvalido) as e:
                    st.error(str(e))
                except oracledb.Error as e:
                    st.error(f"Erro ao registrar a leitura: {e}")

            with st.expander("Histórico de leituras"):
                id_hist = st.selectbox("Equipamento ", list(nomes_maquinas), format_func=nomes_maquinas.get,
                                       key="hist_leituras")
                with pool_conexoes().acquire() as conn:
                    historico = leituras(conn, id_hist)
                    fotos = listar_anexos(conn, ENTIDADE_LEITURA, [h["ID"] for h in historico])
                if not historico:
                    st.caption("Nenhuma leitura registrada.")
                for h in reversed(historico):
                    c_txt, c_btn = st.columns([5, 1])
                    c_txt.markdown(
                        f"{h['DATA_LEITURA']:%d/%m/%Y} · "
                        + (f"{float(h['HORIMETRO']):,.1f} h".replace(",", "X").replace(".", ",").replace("X", ".")
                           if h["HORIMETRO"] is not None else "")
                        + (f" · {float(h['HODOMETRO']):,.0f} km".replace(",", ".") if h["HODOMETRO"] is not None else "")
                        + (" · informada no cadastro" if h["ORIGEM"] == "CADASTRO" else f" · {nome_usuario(h['USUARIO']) or '-'}"))
                    for anexo in fotos.get(h["ID"], [])[:1]:
                        try:
                            with pool_conexoes().acquire() as conn:
                                arquivo_foto = conteudo_anexo(conn, anexo["ID"])
                            if arquivo_foto:
                                c_btn.download_button("Evidência", arquivo_foto[1], file_name=arquivo_foto[0],
                                                      mime=arquivo_foto[2] or "application/octet-stream",
                                                      key=f"foto_{anexo['ID']}", use_container_width=True)
                        except AnexoInvalido as e:
                            c_btn.error(str(e))

# ---------------------------------------------------------------------------
# Revisões do equipamento: missões (pendentes e próximas), envio do comprovante e histórico
# ---------------------------------------------------------------------------
if vista == VISTA_REVISOES:
    if id_equipamento is None:
        st.info("Nenhum equipamento segurado para este cliente ou fazenda.")
        render_footer()
        st.stop()
    hoje = datetime.date.today()
    try:
        atividades = carregar_atividades("m.ID_EQUIPAMENTO_SEGURADO = :equip", {"equip": id_equipamento})
    except oracledb.Error as e:
        st.error(f"Erro ao ler as manutenções: {e}")
        st.stop()
    if not atividades:
        st.info("Nenhuma manutenção prevista para este equipamento. A lista é gerada a partir das recomendações do "
                "manual do modelo (página Programação) ao salvar o equipamento no Cadastro.")
        render_footer()
        st.stop()

    lista_revisoes = cp.revisoes(atividades, hoje)
    realizadas, previstas, percentual = cp.percentual_realizacao(atividades, hoje)
    indicador = cp.indicador_cumprimento(atividades, hoje)
    nivel_atual = cp.nivel(percentual)
    em_sequencia = cp.sequencia(lista_revisoes, hoje)
    placar(nivel_atual, percentual, realizadas, previstas, em_sequencia, indicador)

    # Em destaque só o necessário agora: a revisão vencida mais recente sem comprovante e a próxima dos próximos
    # 30 dias. As mais antigas sem comprovante, as já comprovadas e as futuras ficam recolhidas.
    pendentes = [r for r in lista_revisoes if r["status"] == cp.PENDENTE]
    proximas = [r for r in lista_revisoes if r["status"] == cp.PROXIMA]
    futuras = [r for r in lista_revisoes if r["status"] == cp.FUTURA]
    # revisões da mesma data (ex.: 200 horas e 400 horas) aparecem juntas
    agora = ([r for r in pendentes if r["data"] == pendentes[-1]["data"]] if pendentes else []) + \
        ([r for r in proximas if r["data"] == proximas[0]["data"]] if proximas else [])
    if not agora and futuras:
        agora = [r for r in futuras if r["data"] == futuras[0]["data"]]
    anteriores = [r for r in pendentes if r not in agora]
    nao_enviadas = [r for r in lista_revisoes if r["status"] == cp.NAO_ENVIADO]
    st.markdown('<div class="etapa">Sua missão agora</div>', unsafe_allow_html=True)
    if not agora:
        st.success("Todas as revisões previstas estão comprovadas. Parabéns!")
    else:
        st.caption("Anexe a nota fiscal, a ordem de serviço ou as fotos da revisão e envie: um comprovante vale para "
                   f"a revisão inteira. O prazo é de {cp.PRAZO_COMPROVANTE_DIAS} dias depois da data prevista. A "
                   "Sompo valida o comprovante.")
    for revisao in agora:
        cartao_revisao(revisao, hoje)
    if anteriores:
        with st.expander(f"Revisões anteriores ainda sem comprovante ({len(anteriores)})"):
            for revisao in reversed(anteriores):
                cartao_revisao(revisao, hoje)

    if nao_enviadas:
        with st.expander(f"Comprovante não enviado no prazo ({len(nao_enviadas)})"):
            st.caption(f"Revisões vencidas há mais de {cp.PRAZO_COMPROVANTE_DIAS} dias sem comprovante: contam como não "
                       "realizadas no Score de Risco.")
            tabela([{"Revisão": r["titulo"], "Prevista para": r["data"], "Prazo encerrado em": r["prazo"],
                     "Serviços": r["total"]} for r in reversed(nao_enviadas)], altura=300)

    concluidas = [r for r in lista_revisoes if r["status"] in (cp.CONCLUIDA, cp.AGUARDANDO)]
    with st.expander(f"Revisões comprovadas ({len(concluidas)}): validação e comprovantes a mais"):
        if concluidas:
            opcoes = list(reversed(concluidas))
            escolhida = st.selectbox("Revisão", range(len(opcoes)),
                                     format_func=lambda n: f"{opcoes[n]['titulo']} · prevista para "
                                                           f"{opcoes[n]['data']:%d/%m/%Y}",
                                     key=f"concluida_{id_equipamento}")
            cartao_concluida(opcoes[escolhida], hoje)
            st.markdown("**Todas as revisões comprovadas**")
            tabela([{"Revisão": r["titulo"], "Prevista para": r["data"], "Feita em": r["realizada_em"],
                     "Feita a mais": len({a["DATA_REALIZADA"] for a in r["extras"]}) or "",
                     "Validação Sompo": ("Aguardando" if r["aguardando"] else r["validacao"] or "")}
                    for r in opcoes], altura=360)
        else:
            st.caption("Nenhuma revisão comprovada ainda.")
        grafico_mes(atividades, hoje)
    restantes = [r for r in futuras if r not in agora] + [r for r in proximas if r not in agora]
    if restantes:
        with st.expander(f"Próximas revisões do cronograma ({len(restantes)})"):
            tabela([{"Revisão": r["titulo"], "Prevista para": r["data"],
                     "Por volta de": f"{r['medidor']:,.0f} horas".replace(",", ".") if r["medidor"] else "",
                     "Serviços": r["total"]} for r in sorted(restantes, key=lambda r: r["data"])], altura=300)

# ---------------------------------------------------------------------------
# Validação Sompo: a equipe vê cada comprovante e diz se atende às recomendações
# ---------------------------------------------------------------------------
if vista == VISTA_VALIDACAO:
    filtro = "f.ID_CLIENTE = :cliente AND m.COMPROVANTE_ENTREGUE = 'SIM'"
    parametros = {"cliente": id_cliente}
    if id_fazenda is not None:
        filtro += " AND e.ID_FAZENDA = :fazenda"
        parametros["fazenda"] = id_fazenda
    so_equipamento = st.toggle("Só o equipamento escolhido", value=False)
    if so_equipamento and id_equipamento is not None:
        filtro += " AND m.ID_EQUIPAMENTO_SEGURADO = :equip"
        parametros["equip"] = id_equipamento
    mostrar_validados = st.toggle("Mostrar também os já validados (para corrigir)", value=False)
    atividades = carregar_atividades(filtro, parametros)
    comprovantes = {}
    for a in atividades:
        if mostrar_validados or a["ATENDE_CRITERIOS"] is None:
            comprovantes.setdefault(chave_validacao(a), []).append(a)
    aguardando = sum(1 for itens in comprovantes.values() if any(i["ATENDE_CRITERIOS"] is None for i in itens))
    st.markdown(f"**{aguardando} comprovante(s) aguardando validação** para os filtros escolhidos.")
    st.caption("Confira no documento: (1) o equipamento (modelo, número de série ou identificação); (2) a data, "
               "compatível com a informada; (3) os serviços da revisão foram feitos (peças, filtros, óleos e fluidos "
               "indicados no manual); (4) quem fez (oficina, concessionária ou equipe própria). Avalie a revisão como "
               "um todo: Atende (100), Atende na maior parte (75), Atende na menor parte (25) ou Não atende (0). "
               "\"Não atende\" volta a pedir comprovante ao cliente e não conta como realizada no Score de Risco.")
    ordem = sorted(comprovantes.values(), key=lambda itens: min(i["DATA_REALIZADA"] or i["DATA_PREVISTA"]
                                                               for i in itens))
    if not ordem:
        st.success("Nenhum comprovante aguardando validação.")
    for itens in ordem[:VALIDACOES_POR_PAGINA]:
        cartao_validacao(itens)
    if len(ordem) > VALIDACOES_POR_PAGINA:
        st.caption(f"Mostrando {VALIDACOES_POR_PAGINA} de {len(ordem)}. Valide estes para ver os próximos.")

render_footer()
