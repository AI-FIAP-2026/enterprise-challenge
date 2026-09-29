# -*- coding: utf-8 -*-
"""
Central de Alertas

Acesso (somente usuários logados):
  - Administrador: todos os alertas de todas as fazendas (ou os de um cliente escolhido).
  - Sompo (analista de subscrição): alertas de todas as fazendas monitoradas.
  - Produtor (operador): apenas as fazendas do cliente vinculado ao seu CNPJ.

Quais alertas pertencem a uma fazenda:
  - CEMADEN (risco hidrológico): alertas do mesmo município (código IBGE) da fazenda.
  - Demais origens (focos do INPE etc.): alertas a até RAIO_KM da fazenda.

O que é mostrado:
  - Por padrão, só as últimas 24 horas. Alertas mais antigos: filtro "Consultar histórico".
  - Cada alerta aparece uma vez, com o risco mais recente: o fogo em volta de cada fazenda é resumido por direção
    (norte, leste, sul e oeste) e cada hidrológico é o último alerta do CEMADEN para o município e tipo.

Desempenho: todos os filtros (período, fazenda, origem, nível) vão para o SQL, a lista é paginada
e as consultas ficam em cache por alguns minutos. Nada é carregado "inteiro" para filtrar no Python.
"""
import sys
import os
import re
import math
import unicodedata
import html
import datetime

import numpy as np
import pydeck as pdk

import streamlit as st
import oracledb

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from auth import USER, PASSWORD, DSN
from components import render_header, render_footer, exigir_login, PERFIL_PRODUTOR, PERFIL_ADMIN
from servicos.alertas_queimadas import orientacao_foco_pendente, CATEGORIA_PENDENTE

st.set_page_config(page_title="Campo Seguro - Central de Alertas", layout="wide")

perfil = exigir_login()
render_header()

# ---------------------------------------------------------------------------
# Configurações
# ---------------------------------------------------------------------------
RAIO_KM = 50                 # mesmo raio usado na carga dos focos (servicos/alertas_queimadas.py)
HORAS_JANELA = 24            # padrão da página: só os alertas das últimas 24 horas, com o risco mais recente
DIAS_PADRAO = 7              # período inicial sugerido quando o usuário escolhe consultar o histórico
DIAS_MAXIMO = 90             # maior período permitido numa consulta
ALERTAS_POR_PAGINA = 20
MAX_HIDROLOGICOS = 500       # teto de segurança: os alertas hidrológicos são sempre listados todos
FAZENDAS_POR_PAGINA = 10     # cards por página (mesmo padrão em todas as seções de alertas)
MAX_FOCOS_AGRUPAR = 5000     # focos lidos para montar os cards (os mais graves e recentes primeiro)
# Fogo ATIVO = com detecção nas últimas 6 horas (o GOES-19 passa a cada 10 minutos: sem detecção em 6 horas, o fogo
# naquele ponto provavelmente apagou). O nível atual de cada direção usa as detecções desse período.
HORAS_ATIVIDADE_RECENTE = 6
CACHE_SEG = 300              # 5 minutos (o pipeline não atualiza mais rápido que isso)

NIVEIS = ["Crítico", "Alto", "Médio", "Baixo", "Mínimo", "Aguardando risco"]
# Só são gravados focos com risco acima de 40% (Médio para cima). Baixo/Mínimo ficam no filtro para
# alertas antigos ou de outras origens.
NIVEIS_PADRAO = ["Crítico", "Alto", "Médio", "Aguardando risco"]
CORES_CARD = {
    "Mínimo": "#C8E6C9",
    "Baixo": "#FFF9C4",
    "Médio": "#FFE0B2",
    "Alto": "#FFCCBC",
    "Crítico": "#FFCDD2",
    "Aguardando risco": "#ECEFF1",
}
CORES_MAPA = {
    "Crítico": "#C62828",
    "Alto": "#EF6C00",
    "Médio": "#F9A825",
    "Baixo": "#7CB342",
    "Mínimo": "#2E7D32",
    "Aguardando risco": "#90A4AE",
}
COR_FAZENDA = "#1565C0"
ORIGENS = {
    "INPE": "Risco de Queimadas",
    "CEMADEN": "Risco hidrológico",
}


# ---------------------------------------------------------------------------
# Banco
# ---------------------------------------------------------------------------
@st.cache_resource
def pool_conexoes():
    """Pool compartilhado: evita abrir uma conexão nova com o Oracle a cada clique."""
    return oracledb.create_pool(user=USER, password=PASSWORD, dsn=DSN, min=1, max=4, increment=1)


def consultar(sql, parametros=None):
    with pool_conexoes().acquire() as conn:
        with conn.cursor() as cursor:
            cursor.execute(sql, parametros or [])
            colunas = [c[0] for c in cursor.description]
            return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


@st.cache_data(ttl=CACHE_SEG, show_spinner=False)
def carregar_fazendas(somente_cnpj):
    """Fazendas que o usuário pode ver. somente_cnpj=None -> todas (admin/Sompo)."""
    sql = """
        SELECT f.ID, f.NOME_FAZENDA, f.MUNICIPIO, f.ESTADO, f.CODIGO_IBGE, f.LATITUDE, f.LONGITUDE, f.ID_CLIENTE
        FROM CS_FAZENDAS f
    """
    parametros = []
    if somente_cnpj is not None:
        # Compara só os dígitos: o CNPJ pode estar gravado com ou sem pontuação
        sql += """
        JOIN CS_CLIENTES c ON c.ID = f.ID_CLIENTE
        WHERE REGEXP_REPLACE(c.CNPJ, '[^0-9]', '') = :1
        """
        parametros = [somente_cnpj]
    sql += " ORDER BY f.NOME_FAZENDA"
    fazendas = []
    for f in consultar(sql, parametros):
        if f["LATITUDE"] is None or f["LONGITUDE"] is None:
            continue
        f["LATITUDE"], f["LONGITUDE"] = float(f["LATITUDE"]), float(f["LONGITUDE"])
        fazendas.append(f)
    return fazendas


COLUNAS_NOME_CLIENTE = ["NOME", "NOME_CLIENTE", "RAZAO_SOCIAL", "NOME_FANTASIA", "NOME_EMPRESA"]


@st.cache_data(ttl=CACHE_SEG, show_spinner=False)
def carregar_clientes(ids_clientes):
    """{ID: nome para exibir} dos clientes informados. Usa a primeira coluna de nome que existir em
    CS_CLIENTES (NOME, RAZAO_SOCIAL...); sem nenhuma, mostra o CNPJ."""
    if not ids_clientes:
        return {}
    nomes_bind = ", ".join(f":{i + 1}" for i in range(len(ids_clientes)))
    linhas = consultar(f"SELECT * FROM CS_CLIENTES WHERE ID IN ({nomes_bind})", list(ids_clientes))
    clientes = {}
    for c in linhas:
        coluna = next((col for col in COLUNAS_NOME_CLIENTE if c.get(col)), None)
        nome = str(c[coluna]).strip() if coluna else f"Cliente {c['ID']}"
        if c.get("CNPJ"):
            nome += f" (CNPJ {c['CNPJ']})"
        clientes[c["ID"]] = nome
    return clientes


def montar_filtro(fazendas_ids, filtrar_por_fazenda, origens, inicio, fim, fazendas_por_id,
                  sem_filtro_fazenda=False):
    """WHERE com binds nomeados (nunca concatena valores digitados pelo usuário).
    O nível de risco NÃO entra aqui: ele é filtrado depois de consolidar cada alerta no risco mais recente
    (senão um fogo que baixou de Crítico para Médio continuaria aparecendo como Crítico).
    sem_filtro_fazenda=True (administrador): não restringe por fazenda nem por município."""
    condicoes = ["a.DATA_HORA >= :data_ini", "a.DATA_HORA < :data_fim"]
    params = {"data_ini": inicio, "data_fim": fim}

    if origens:
        partes = []
        for i, origem in enumerate(origens):
            if origem == "OUTRAS":
                partes.append("a.ORIGEM_ALERTA NOT IN ('INPE', 'CEMADEN')")
            else:
                partes.append(f"a.ORIGEM_ALERTA = :origem{i}")
                params[f"origem{i}"] = origem
        condicoes.append("(" + " OR ".join(partes) + ")")

    if sem_filtro_fazenda:
        return " AND ".join(condicoes), params

    # CEMADEN chega para o Brasil inteiro: sempre só os municípios (código IBGE) das fazendas
    codigos_ibge = sorted({str(fazendas_por_id[i]["CODIGO_IBGE"]).strip()
                           for i in fazendas_ids if fazendas_por_id[i].get("CODIGO_IBGE")})
    if codigos_ibge:
        nomes = []
        for j, codigo in enumerate(codigos_ibge[:1000]):
            nomes.append(f":ibge{j}")
            params[f"ibge{j}"] = codigo
        cemaden = ("(a.ORIGEM_ALERTA = 'CEMADEN' AND REGEXP_SUBSTR(a.DETALHAMENTO_1, 'IBGE: ([0-9]+)', 1, 1, NULL, 1) "
                   f"IN ({', '.join(nomes)}))")
    else:
        cemaden = "1 = 0"

    # Demais origens (focos do INPE etc.): já são gravadas só perto de alguma fazenda. Quando o acesso é
    # restrito (produtor) ou uma fazenda foi escolhida, filtra por um retângulo de RAIO_KM em volta de cada
    # fazenda (a distância exata é calculada na exibição).
    if filtrar_por_fazenda:
        retangulos = []
        for j, id_faz in enumerate(fazendas_ids):
            f = fazendas_por_id[id_faz]
            dlat = RAIO_KM / 111.0
            dlon = RAIO_KM / (111.0 * max(math.cos(math.radians(f["LATITUDE"])), 0.1))
            params.update({f"lat_min{j}": f["LATITUDE"] - dlat, f"lat_max{j}": f["LATITUDE"] + dlat,
                           f"lon_min{j}": f["LONGITUDE"] - dlon, f"lon_max{j}": f["LONGITUDE"] + dlon})
            retangulos.append(f"(a.LATITUDE BETWEEN :lat_min{j} AND :lat_max{j} "
                              f"AND a.LONGITUDE BETWEEN :lon_min{j} AND :lon_max{j})")
        demais = f"(a.ORIGEM_ALERTA <> 'CEMADEN' AND ({' OR '.join(retangulos)}))"
    else:
        demais = "a.ORIGEM_ALERTA <> 'CEMADEN'"
    condicoes.append(f"({cemaden} OR {demais})")

    return " AND ".join(condicoes), params


ORDEM_GRAVIDADE = """
    CASE a.CATEGORIA_RISCO WHEN 'Crítico' THEN 1 WHEN 'Alto' THEN 2 WHEN 'Médio' THEN 3
                           WHEN 'Aguardando risco' THEN 4 WHEN 'Baixo' THEN 5 ELSE 6 END"""
COLUNAS_CARD = """a.ID, a.TIPO_ALERTA, a.ORIGEM_ALERTA, a.DATA_HORA, a.CATEGORIA_RISCO, a.LATITUDE, a.LONGITUDE,
               a.ORIENTACAO, a.DETALHAMENTO_1, a.DETALHAMENTO_2"""
SO_HIDROLOGICO = " AND a.ORIGEM_ALERTA = 'CEMADEN'"
SEM_HIDROLOGICO = " AND a.ORIGEM_ALERTA <> 'CEMADEN'"
POSICAO_GRAVIDADE = {nivel: i for i, nivel in enumerate(["Crítico", "Alto", "Médio", "Aguardando risco", "Baixo"])}


@st.cache_data(ttl=CACHE_SEG, show_spinner=False)
def alertas_hidrologicos(where, params):
    """Alertas do CEMADEN do filtro, UM por município e tipo de alerta: o mais recente (quando o CEMADEN muda o
    nível, o alerta é gravado de novo; vale o último). Do mais grave para o mais leve."""
    p = dict(params)
    p["limite"] = MAX_HIDROLOGICOS
    return consultar(f"""
        SELECT * FROM (
            SELECT {COLUNAS_CARD},
                   ROW_NUMBER() OVER (
                       PARTITION BY NVL(REGEXP_SUBSTR(a.DETALHAMENTO_1, 'IBGE: ([0-9]+)', 1, 1, NULL, 1),
                                        a.DETALHAMENTO_1),
                                    a.TIPO_ALERTA
                       ORDER BY a.DATA_HORA DESC, a.ID DESC) AS ORDEM_RECENTE
            FROM CS_ALERTAS a WHERE {where}{SO_HIDROLOGICO}
        ) WHERE ORDEM_RECENTE = 1
        ORDER BY CASE CATEGORIA_RISCO WHEN 'Crítico' THEN 1 WHEN 'Alto' THEN 2 WHEN 'Médio' THEN 3
                                      WHEN 'Aguardando risco' THEN 4 WHEN 'Baixo' THEN 5 ELSE 6 END,
                 DATA_HORA DESC, ID DESC
        FETCH FIRST :limite ROWS ONLY
    """, p)


@st.cache_data(ttl=CACHE_SEG, show_spinner=False)
def focos_para_agrupar(where, params):
    """Focos de queimada para montar os cards: até MAX_FOCOS_AGRUPAR, dos mais recentes para os mais antigos
    (o nível atual de cada direção vem das detecções mais recentes)."""
    p = dict(params)
    p["limite"] = MAX_FOCOS_AGRUPAR
    return consultar(f"""
        SELECT {COLUNAS_CARD}
        FROM CS_ALERTAS a WHERE {where}{SEM_HIDROLOGICO} AND a.LATITUDE IS NOT NULL AND a.LONGITUDE IS NOT NULL
        ORDER BY a.DATA_HORA DESC, a.ID DESC
        FETCH FIRST :limite ROWS ONLY
    """, p)


TIPO_PREVISAO_REGRAS = "Queimada"                    # regras aprendidas (chance de foco perto da fazenda)
NIVEIS_AVISO_PREVISAO = ("Alto", "Crítico")           # só esses níveis geram aviso de risco previsto
# Previsão de amanhã a +3 dias. Hoje não entra: o dia de hoje já vem dos focos detectados pelo INPE.
PRIMEIRO_DIA_PREVISAO, ULTIMO_DIA_PREVISAO = 1, 3


TIPOS_PREVISAO_CHUVA = ("Hidrológico", "Deslizamento")   # regras de chuva (modelos/ml_alertas_chuva_predicao.py)
TITULO_PREVISAO = {"Queimada": "Risco de incêndio previsto", "Hidrológico": "Risco hidrológico previsto",
                   "Deslizamento": "Risco de deslizamento previsto"}
TEXTO_CHANCE = {"Queimada": "chance de foco a até 15 km da fazenda",
                "Hidrológico": "chance de inundação, enxurrada ou alagamento no município (histórico)",
                "Deslizamento": "chance de deslizamento no município (histórico)"}


@st.cache_data(ttl=CACHE_SEG, show_spinner=False)
def previsoes_incendio(ids_fazendas, tipo=TIPO_PREVISAO_REGRAS):
    """Previsões de amanhã a +3 dias (regras) de CS_ALERTAS_PREDICAO para as fazendas informadas, do tipo de risco
    (Queimada, Hidrológico ou Deslizamento)."""
    params = {"tipo_regras": tipo, "inicio": PRIMEIRO_DIA_PREVISAO, "fim": ULTIMO_DIA_PREVISAO + 1}
    filtro = ""
    if ids_fazendas is not None:
        nomes = []
        for i, id_faz in enumerate(ids_fazendas[:1000]):
            nomes.append(f":faz{i}")
            params[f"faz{i}"] = id_faz
        filtro = f" AND p.ID_FAZENDA IN ({', '.join(nomes)})" if nomes else " AND 1 = 0"
    return consultar(f"""
        SELECT p.ID_FAZENDA, p.TIPO_RISCO, p.DATA_REFERENCIA, p.VALOR, p.NIVEL, p.MOTIVOS, p.DATA_CALCULO
        FROM CS_ALERTAS_PREDICAO p
        WHERE p.TIPO_RISCO = :tipo_regras
          AND p.DATA_REFERENCIA >= TRUNC(SYSDATE) + :inicio AND p.DATA_REFERENCIA < TRUNC(SYSDATE) + :fim{filtro}
    """, params)


# ---------------------------------------------------------------------------
# Utilitários de exibição
# ---------------------------------------------------------------------------
def distancia_km(lat1, lon1, lat2, lon2):
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def fazenda_mais_proxima(alerta, fazendas):
    """Fazenda do USUÁRIO mais próxima do alerta (nunca mostra fazendas de outros clientes)."""
    if alerta["LATITUDE"] is None or alerta["LONGITUDE"] is None or not fazendas:
        return None, None
    lat, lon = float(alerta["LATITUDE"]), float(alerta["LONGITUDE"])
    melhor = min(fazendas, key=lambda f: distancia_km(lat, lon, f["LATITUDE"], f["LONGITUDE"]))
    return melhor, distancia_km(lat, lon, melhor["LATITUDE"], melhor["LONGITUDE"])


def fazendas_mais_proximas(lats, lons, fazendas):
    """Versão em lote (numpy) para o mapa: índice da fazenda mais próxima e distância em km."""
    lat1, lon1 = np.radians(np.asarray(lats, dtype=float))[:, None], np.radians(np.asarray(lons, dtype=float))[:, None]
    lat2 = np.radians(np.array([f["LATITUDE"] for f in fazendas]))[None, :]
    lon2 = np.radians(np.array([f["LONGITUDE"] for f in fazendas]))[None, :]
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    dist = 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))
    indice = dist.argmin(axis=1)
    return indice, dist[np.arange(len(indice)), indice]


def sem_emoji(texto):
    """Tira emojis/símbolos (ex.: os que ficaram gravados nas orientações antigas)."""
    limpo = "".join(c for c in str(texto) if unicodedata.category(c) != "So" and c not in "\ufe0f\u200d")
    return re.sub(r"\s{2,}", " ", limpo).strip()


def texto_seguro(valor):
    return html.escape(sem_emoji(valor)) if valor else ""


def titulo_alerta(tipo):
    """'Foco de Incêndio / Queimada (GOES-19)' -> 'Foco de Incêndio'."""
    titulo = str(tipo or "Alerta").split(" / ")[0]
    return re.sub(r"\s*\([^)]*\)\s*$", "", titulo).strip() or "Alerta"


def detalhes_exibicao(alerta):
    """Detalhes do card sem satélite, sem 'aguardando arquivo', sem bioma vazio e sem a 'Fazenda mais próxima'
    gravada (que é calculada entre TODAS as fazendas). Risco de fogo calculado aparece em %."""
    partes = [str(alerta["DETALHAMENTO_1"] or "")] + str(alerta["DETALHAMENTO_2"] or "").split("|")
    mantidas = []
    for parte in (p.strip() for p in partes):
        if not parte or parte.startswith(("Satélite", "Fazenda mais próxima", "Sem fazendas cadastradas")):
            continue
        if parte.startswith("Risco de Fogo:"):
            try:
                mantidas.append(f"Risco de fogo: {float(parte.split(':', 1)[1]) * 100:.0f}%")
            except ValueError:
                pass
            continue
        if parte.startswith("Bioma:") and not parte.split(":", 1)[1].strip():
            continue
        mantidas.append(parte)
    return " | ".join(mantidas)


def municipio_do_texto(det1):
    """'Município: Sorriso/MT (IBGE: 5107925) - Lat: ...' -> 'Sorriso/MT'."""
    texto = str(det1 or "").replace("Município: ", "")
    return texto.split(" (IBGE")[0].split(" - Lat:")[0].strip() or "Município não informado"


def risco_do_texto(det2):
    """Risco de fogo (0 a 1) gravado no texto, ou None se ainda não calculado."""
    encontrado = re.search(r"Risco de Fogo: ([0-9]+\.[0-9]+)", str(det2 or ""))
    return float(encontrado.group(1)) if encontrado else None


def km(valor):
    return f"{valor:.1f}".replace(".", ",")


def distancias_km(lat0, lon0, lats, lons):
    """Distância (km) de um ponto a vários pontos (numpy)."""
    lat0, lon0 = math.radians(lat0), math.radians(lon0)
    lats, lons = np.radians(lats), np.radians(lons)
    a = np.sin((lats - lat0) / 2) ** 2 + math.cos(lat0) * np.cos(lats) * np.sin((lons - lon0) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


def contorno(lats, lons, margem_km=0.8):
    """Contorno (envoltória convexa) das detecções, com uma margem para aparecer no mapa: [[lon, lat], ...].
    None se há menos de 3 pontos distintos (no mapa, fica só o marcador)."""
    pontos = sorted(set(zip(np.round(lons, 5), np.round(lats, 5))))
    if len(pontos) < 3:
        return None

    def cruz(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
    baixo, cima = [], []
    for p in pontos:
        while len(baixo) >= 2 and cruz(baixo[-2], baixo[-1], p) <= 0:
            baixo.pop()
        baixo.append(p)
    for p in reversed(pontos):
        while len(cima) >= 2 and cruz(cima[-2], cima[-1], p) <= 0:
            cima.pop()
        cima.append(p)
    casca = baixo[:-1] + cima[:-1]
    if len(casca) < 3:
        return None
    clon, clat = sum(p[0] for p in casca) / len(casca), sum(p[1] for p in casca) / len(casca)
    grau_lat = margem_km / 111.0
    grau_lon = margem_km / (111.0 * max(math.cos(math.radians(clat)), 0.1))
    resultado = []
    for lon, lat in casca:
        dx, dy = lon - clon, lat - clat
        norma = math.hypot(dx / grau_lon, dy / grau_lat) or 1.0
        resultado.append([float(lon + dx / norma), float(lat + dy / norma)])
    return resultado


SETORES = ["norte", "leste", "sul", "oeste"]     # quadrantes de 90° centrados em cada direção
AO_SETOR = {"norte": "ao norte", "leste": "a leste", "sul": "ao sul", "oeste": "a oeste"}


def rumos_graus(lat0, lon0, lats, lons):
    """Rumo (0° = norte, 90° = leste) de vários pontos vistos a partir de (lat0, lon0)."""
    phi1, phi2 = math.radians(lat0), np.radians(lats)
    dlon = np.radians(lons - lon0)
    angulo = np.degrees(np.arctan2(np.sin(dlon) * np.cos(phi2),
                                   math.cos(phi1) * np.sin(phi2) - math.sin(phi1) * np.cos(phi2) * np.cos(dlon)))
    return (angulo + 360) % 360


def data_foco(f):
    return f["DATA_HORA"] if isinstance(f["DATA_HORA"], datetime.datetime) else None


def resumo_setor(fazenda, nome, lista, dist, lats, lons, limite_recente):
    """Resumo do fogo num setor (norte/leste/sul/oeste) da fazenda.
    Nível atual: o pior entre as detecções das últimas HORAS_ATIVIDADE_RECENTE horas; sem detecção recente,
    o da última detecção com risco divulgado pelo INPE."""
    datas = [d for d in (data_foco(f) for f in lista) if d]
    recentes = [f for f in lista if data_foco(f) and data_foco(f) >= limite_recente
                and f["CATEGORIA_RISCO"] != CATEGORIA_PENDENTE]
    if recentes:
        atual = min(recentes, key=lambda f: (POSICAO_GRAVIDADE.get(f["CATEGORIA_RISCO"], 9),
                                             -(risco_do_texto(f["DETALHAMENTO_2"]) or 0)))
    else:
        com_risco = [f for f in lista if f["CATEGORIA_RISCO"] != CATEGORIA_PENDENTE]
        candidatos = com_risco or lista
        atual = max(candidatos, key=lambda f: data_foco(f) or datetime.datetime.min)
    municipios = {}
    for f in lista:
        nome_mun = municipio_do_texto(f["DETALHAMENTO_1"])
        municipios[nome_mun] = municipios.get(nome_mun, 0) + 1
    k = int(dist.argmin())
    ultima = max(datas) if datas else None
    return {
        "id": f"{fazenda['ID']}-{nome}", "setor": nome, "focos": lista,
        "lat": float(lats[k]), "lon": float(lons[k]),           # marcador no ponto de fogo mais próximo
        "distancia": float(dist[k]), "distancia_max": float(dist.max()),
        "categoria": atual["CATEGORIA_RISCO"], "orientacao": atual["ORIENTACAO"],
        "risco": risco_do_texto(atual["DETALHAMENTO_2"]),
        "primeira": min(datas) if datas else None, "ultima": ultima,
        "ativo": bool(ultima and ultima >= limite_recente),
        "municipios": sorted(municipios, key=municipios.get, reverse=True),
        "contorno": contorno(lats, lons),
    }


def agrupar_por_fazenda(focos, fazendas, niveis=None):
    """Fogo perto de cada fazenda (a até RAIO_KM), resumido por SETOR: norte, leste, sul e oeste. As detecções
    repetidas do satélite (o GOES-19 passa a cada 10 minutos) e os vários pontos da mesma frente de fogo viram
    uma linha só por direção. niveis: só os setores cujo nível ATUAL está entre esses níveis.
    Devolve os grupos (um por fazenda), das fazendas com fogo ativo e mais grave para as demais."""
    if not focos or not fazendas:
        return []
    lats = np.array([float(f["LATITUDE"]) for f in focos])
    lons = np.array([float(f["LONGITUDE"]) for f in focos])
    limite_recente = datetime.datetime.now() - datetime.timedelta(hours=HORAS_ATIVIDADE_RECENTE)
    grau = RAIO_KM / 111.0

    def gravidade(categoria):
        return POSICAO_GRAVIDADE.get(categoria, 9)

    grupos = []
    for fazenda in fazendas:
        flat, flon = float(fazenda["LATITUDE"]), float(fazenda["LONGITUDE"])
        grau_lon = RAIO_KM / (111.0 * max(math.cos(math.radians(flat)), 0.1))
        idx = np.nonzero((np.abs(lats - flat) <= grau) & (np.abs(lons - flon) <= grau_lon))[0]
        if not len(idx):
            continue
        dist = distancias_km(flat, flon, lats[idx], lons[idx])
        dentro = dist <= RAIO_KM
        idx, dist = idx[dentro], dist[dentro]
        if not len(idx):
            continue
        quadrante = (((rumos_graus(flat, flon, lats[idx], lons[idx]) + 45) % 360) // 90).astype(int)
        setores = []
        for q, nome in enumerate(SETORES):
            sel = quadrante == q
            if not sel.any():
                continue
            membros = idx[sel]
            setor = resumo_setor(fazenda, nome, [focos[i] for i in membros], dist[sel], lats[membros],
                                 lons[membros], limite_recente)
            if niveis is None or setor["categoria"] in niveis:
                setores.append(setor)
        if not setores:
            continue
        # ativos primeiro; depois do mais grave para o mais leve e do mais perto para o mais longe
        setores.sort(key=lambda s: (not s["ativo"], gravidade(s["categoria"]), s["distancia"]))
        principal = setores[0]
        if principal["categoria"] == CATEGORIA_PENDENTE:
            # nenhum foco com risco calculado: orientação provisória pela distância do fogo mais próximo
            orientacao = orientacao_foco_pendente(principal["distancia"])
        else:
            orientacao = principal["orientacao"]
        mais_proximo = min(setores, key=lambda s: s["distancia"])
        grupos.append({
            "fazenda": fazenda,
            "setores": setores,
            "ativos": sum(1 for s in setores if s["ativo"]),
            "categoria": principal["categoria"],
            "orientacao": orientacao,
            "mais_proximo": mais_proximo,
            "ultima": max((s["ultima"] for s in setores if s["ultima"]), default=None),
        })
    # fazendas com fogo ativo primeiro; depois pela gravidade e pela distância
    grupos.sort(key=lambda g: (g["ativos"] == 0, gravidade(g["categoria"]), g["mais_proximo"]["distancia"]))
    return grupos


def html_compacto(texto):
    """Junta o HTML numa linha só. Linhas recuadas depois de uma linha em branco viram "bloco de código"
    no markdown do Streamlit (era o que fazia o HTML aparecer como texto no card)."""
    return " ".join(linha.strip() for linha in texto.splitlines() if linha.strip())


def etiqueta_nivel(categoria):
    """Etiqueta colorida com o nível (ex.: CRÍTICO)."""
    cor = CORES_MAPA.get(categoria, "#90A4AE")
    nome = "AGUARDANDO RISCO" if categoria == CATEGORIA_PENDENTE else str(categoria or "").upper()
    return (f"<span style='background: {cor}; color: white; border-radius: 10px; padding: 1px 9px; "
            f"font-size: 12px; font-weight: 700; white-space: nowrap;'>{texto_seguro(nome)}</span>")


def cor_rgb(hexa, alfa=220):
    hexa = hexa.lstrip("#")
    return [int(hexa[0:2], 16), int(hexa[2:4], 16), int(hexa[4:6], 16), alfa]


# ---------------------------------------------------------------------------
# Fazendas visíveis para o usuário
# ---------------------------------------------------------------------------
st.markdown("<h1 style='color: #1A4A75;'>Central de Alertas</h1>", unsafe_allow_html=True)

try:
    if perfil == PERFIL_PRODUTOR:
        cnpj = "".join(ch for ch in str(st.session_state.get("cliente_cnpj") or "") if ch.isdigit())
        if not cnpj:
            st.error("Seu usuário não está vinculado a um CNPJ. Solicite o vínculo ao administrador.")
            render_footer()
            st.stop()
        fazendas = carregar_fazendas(cnpj)
    else:
        fazendas = carregar_fazendas(None)
except oracledb.Error as e:
    st.error(f"Não foi possível conectar ao banco de dados: {e}")
    render_footer()
    st.stop()

if not fazendas:
    st.info("Nenhuma fazenda com localização cadastrada para o seu acesso.")
    render_footer()
    st.stop()

fazendas_por_id = {f["ID"]: f for f in fazendas}

if perfil == PERFIL_ADMIN:
    pass  # a mensagem do administrador depende do cliente escolhido (mostrada depois dos filtros)
elif perfil == PERFIL_PRODUTOR:
    st.info(f"Visualizando os alertas das suas {len(fazendas)} fazenda(s) "
            f"(CNPJ: {st.session_state.get('cliente_cnpj')}).")
else:
    st.markdown("Feed consolidado de monitoramento de ameaças para a segurança da frota e da lavoura "
                f"— **{len(fazendas)} fazendas** monitoradas.")

# ---------------------------------------------------------------------------
# Filtros (aplicados só ao clicar no botão, para não consultar o banco a cada mudança)
# ---------------------------------------------------------------------------
hoje = datetime.date.today()
TODOS_CLIENTES, TODAS_FAZENDAS = "Todos os clientes", "Todas as fazendas"

# Cliente e fazenda ficam fora do formulário: a lista de fazendas muda na hora conforme o cliente escolhido
st.sidebar.markdown("### Filtros")
escolha_cliente = TODOS_CLIENTES
if perfil != PERFIL_PRODUTOR:
    try:
        clientes = carregar_clientes(tuple(sorted({f["ID_CLIENTE"] for f in fazendas if f.get("ID_CLIENTE") is not None})))
    except oracledb.Error as e:
        clientes = {}
        st.sidebar.warning(f"Não foi possível carregar os clientes: {e}")
    escolha_cliente = st.sidebar.selectbox(
        "Cliente", [TODOS_CLIENTES] + sorted(clientes, key=lambda i: clientes[i].lower()),
        format_func=lambda v: v if v == TODOS_CLIENTES else clientes.get(v, f"Cliente {v}"), key="filtro_cliente")

fazendas_do_cliente = (fazendas if escolha_cliente == TODOS_CLIENTES
                       else [f for f in fazendas if f.get("ID_CLIENTE") == escolha_cliente])
# Administrador sem cliente escolhido: visão geral, sem lista de fazendas (seriam centenas)
if perfil == PERFIL_ADMIN and escolha_cliente == TODOS_CLIENTES:
    escolha_fazenda = TODAS_FAZENDAS
else:
    escolha_fazenda = st.sidebar.selectbox(
        "Fazenda" + ("" if escolha_cliente == TODOS_CLIENTES else f" do cliente ({len(fazendas_do_cliente)})"),
        [TODAS_FAZENDAS] + [f["ID"] for f in fazendas_do_cliente],
        format_func=lambda v: v if v == TODAS_FAZENDAS else
        f"{fazendas_por_id[v]['NOME_FAZENDA']} ({fazendas_por_id[v]['MUNICIPIO'] or ''}/{fazendas_por_id[v]['ESTADO'] or ''})",
        key=f"filtro_fazenda_{escolha_cliente}")

# Padrão: só as últimas HORAS_JANELA horas. Alertas mais antigos só com o filtro de histórico ligado.
historico = st.sidebar.toggle("Consultar histórico (alertas mais antigos)", key="filtro_historico")
with st.sidebar.form("filtros_alertas"):
    periodo = None
    if historico:
        periodo = st.date_input("Período", (hoje - datetime.timedelta(days=DIAS_PADRAO - 1), hoje),
                                max_value=hoje, format="DD/MM/YYYY")
    origens = st.multiselect("Origem", list(ORIGENS) + ["OUTRAS"], default=list(ORIGENS),
                             format_func=lambda o: ORIGENS.get(o, "Outras origens"))
    niveis = st.multiselect("Nível de risco", NIVEIS, default=NIVEIS_PADRAO)
    st.form_submit_button("Aplicar filtros", use_container_width=True)

if historico:
    if not isinstance(periodo, (list, tuple)) or len(periodo) != 2:
        st.warning("Selecione a data inicial e a data final do período.")
        st.stop()
    data_ini, data_fim = periodo
    if (data_fim - data_ini).days + 1 > DIAS_MAXIMO:
        data_ini = data_fim - datetime.timedelta(days=DIAS_MAXIMO - 1)
        st.sidebar.warning(f"Período limitado a {DIAS_MAXIMO} dias: de {data_ini:%d/%m/%Y} a {data_fim:%d/%m/%Y}.")
    inicio = datetime.datetime.combine(data_ini, datetime.time.min)
    fim = datetime.datetime.combine(data_fim + datetime.timedelta(days=1), datetime.time.min)
    texto_periodo = f"Período: {data_ini:%d/%m/%Y} a {data_fim:%d/%m/%Y}"
else:
    # arredonda para 5 minutos: a consulta fica em cache entre um clique e outro
    agora = datetime.datetime.now().replace(second=0, microsecond=0)
    agora -= datetime.timedelta(minutes=agora.minute % 5)
    fim = agora + datetime.timedelta(minutes=5)
    inicio = agora - datetime.timedelta(hours=HORAS_JANELA)
    texto_periodo = f"Últimas {HORAS_JANELA} horas (desde {inicio:%d/%m às %H:%M})"
if not origens or not niveis:
    st.warning("Selecione ao menos uma origem e um nível de risco.")
    st.stop()

if escolha_fazenda != TODAS_FAZENDAS:
    fazendas_exibicao = [fazendas_por_id[escolha_fazenda]]
else:
    fazendas_exibicao = fazendas_do_cliente
ids_filtro = [f["ID"] for f in fazendas_exibicao]
if not ids_filtro:
    st.info("O cliente escolhido não tem fazendas com localização cadastrada.")
    render_footer()
    st.stop()

# Visão geral do administrador: nenhum cliente/fazenda escolhido -> sem filtro por fazenda
visao_geral_admin = perfil == PERFIL_ADMIN and escolha_cliente == TODOS_CLIENTES
# Sompo com todos os clientes: os focos gravados já são de perto de alguma fazenda -> sem o filtro por raio
filtrar_por_fazenda = (perfil == PERFIL_PRODUTOR or escolha_cliente != TODOS_CLIENTES
                       or escolha_fazenda != TODAS_FAZENDAS)
where, params = montar_filtro(ids_filtro, filtrar_por_fazenda, origens, inicio, fim, fazendas_por_id,
                              sem_filtro_fazenda=visao_geral_admin)
if visao_geral_admin:
    st.markdown("Visão do administrador: sem cliente escolhido, mostra os alertas de **todas as fazendas**.")
if escolha_cliente != TODOS_CLIENTES or escolha_fazenda != TODAS_FAZENDAS:
    selecao = (fazendas_por_id[escolha_fazenda]["NOME_FAZENDA"] if escolha_fazenda != TODAS_FAZENDAS
               else f"{len(fazendas_exibicao)} fazenda(s)")
    if escolha_cliente != TODOS_CLIENTES:
        selecao = f"{clientes.get(escolha_cliente, escolha_cliente)} — {selecao}"
    st.markdown(f"**Filtro:** {texto_seguro(selecao)}", unsafe_allow_html=True)
municipios_com_fazenda = {str(f["CODIGO_IBGE"]).strip() for f in fazendas if f.get("CODIGO_IBGE")}

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
# Tudo já consolidado: hidrológicos = o alerta mais recente de cada município/tipo; queimadas = uma frente
# por direção em volta da fazenda, com o risco atual. O nível escolhido vale para esse risco atual.
try:
    with st.spinner("Consultando alertas..."):
        hidrologicos = alertas_hidrologicos(where, params) if "CEMADEN" in origens else []
        focos = focos_para_agrupar(where, params) if any(o != "CEMADEN" for o in origens) else []
except oracledb.Error as e:
    st.error(f"Erro ao consultar os alertas: {e}")
    st.stop()
hidrologicos = [h for h in hidrologicos if h["CATEGORIA_RISCO"] in niveis]
grupos = agrupar_por_fazenda(focos, fazendas_exibicao, niveis)
# frentes de fogo = direções (norte/leste/sul/oeste) com fogo em volta de cada fazenda
frentes = [(grupo["fazenda"], setor) for grupo in grupos for setor in grupo["setores"]]
total_hidro, total_frentes = len(hidrologicos), len(frentes)
focos_limitados = len(focos) >= MAX_FOCOS_AGRUPAR

resumo = {}
for h in hidrologicos:
    resumo[h["CATEGORIA_RISCO"]] = resumo.get(h["CATEGORIA_RISCO"], 0) + 1
for _, setor in frentes:
    resumo[setor["categoria"]] = resumo.get(setor["categoria"], 0) + 1
total = total_hidro + total_frentes


def numero(valor):
    return f"{valor:,}".replace(",", ".")


st.caption(f"{texto_periodo} | Níveis: {', '.join(niveis)} | Cada alerta aparece uma vez, com o risco mais "
           "recente (queimadas = uma frente por direção em volta da fazenda: norte, leste, sul e oeste; "
           "hidrológico = último alerta de cada município e tipo)")
# Painel: 4 indicadores dentro dos filtros (período, cliente, fazenda, origem e nível)
ids_visiveis = {f["ID"] for f in fazendas_exibicao}
fazendas_com_alerta = {grupo["fazenda"]["ID"] for grupo in grupos if grupo["setores"]}
ibge_hidro = {m.group(1) for h in hidrologicos
              for m in [re.search(r"IBGE: (\d+)", str(h.get("DETALHAMENTO_1") or ""))] if m}
fazendas_com_alerta |= {f["ID"] for f in fazendas_exibicao if str(f.get("CODIGO_IBGE") or "").strip() in ibge_hidro}
previsao_painel = []
tipos_painel = ([TIPO_PREVISAO_REGRAS] if "INPE" in origens else []) + \
    (list(TIPOS_PREVISAO_CHUVA) if "CEMADEN" in origens else [])
for tipo_painel in tipos_painel:
    try:
        previsao_painel += previsoes_incendio(None if visao_geral_admin else tuple(ids_filtro), tipo_painel)
    except oracledb.Error:
        pass
# Previsão: o nível de cada fazenda é o pior dos próximos 3 dias
pior_previsto = {}
for p in previsao_painel:
    if p["NIVEL"] in NIVEIS_AVISO_PREVISAO and (visao_geral_admin or p["ID_FAZENDA"] in ids_visiveis):
        if pior_previsto.get(p["ID_FAZENDA"]) != "Crítico":
            pior_previsto[p["ID_FAZENDA"]] = p["NIVEL"]
graves_agora = resumo.get("Crítico", 0) + resumo.get("Alto", 0)
colunas = st.columns(4)
colunas[0].metric("Alertas ativos agora", numero(total),
                  help=f"Alertas detectados no período (focos do INPE e alertas do CEMADEN). Crítico "
                       f"{resumo.get('Crítico', 0)} · Alto {resumo.get('Alto', 0)} · Médio {resumo.get('Médio', 0)} · "
                       f"Aguardando risco {resumo.get('Aguardando risco', 0)}.")
colunas[1].metric("Fazendas com alerta agora", f"{numero(len(fazendas_com_alerta & ids_visiveis))} de "
                                               f"{numero(len(ids_visiveis))}",
                  help=f"Dos alertas detectados agora, {numero(graves_agora)} são Crítico ou Alto.")
por_tipo_previsto = {}
for p in previsao_painel:
    if p["NIVEL"] in NIVEIS_AVISO_PREVISAO and (visao_geral_admin or p["ID_FAZENDA"] in ids_visiveis):
        por_tipo_previsto.setdefault(p.get("TIPO_RISCO") or TIPO_PREVISAO_REGRAS, set()).add(p["ID_FAZENDA"])
detalhe_tipos = " · ".join(f"{TITULO_PREVISAO.get(t, t).replace('Risco ', '').replace(' previsto', '')}: "
                           f"{len(ids)}" for t, ids in por_tipo_previsto.items()) or "nenhum"
colunas[2].metric("Risco previsto Crítico", f"{numero(sum(1 for n in pior_previsto.values() if n == 'Crítico'))} "
                                            "fazenda(s)",
                  help="Fazendas com risco Crítico previsto em algum dos próximos 3 dias (incêndio, hidrológico ou "
                       f"deslizamento). Fazendas com aviso Alto ou Crítico por tipo: {detalhe_tipos}.")
colunas[3].metric("Risco previsto Alto", f"{numero(sum(1 for n in pior_previsto.values() if n == 'Alto'))} fazenda(s)",
                  help="Fazendas com risco Alto (e nenhum dia Crítico) previsto nos próximos 3 dias.")

if total == 0:
    st.success("Nenhum alerta no período e filtros selecionados. Suas fazendas estão em segurança.")
    render_footer()
    st.stop()

# ---------------------------------------------------------------------------
# Escolha da visualização (lista ou mapa), em destaque
# ---------------------------------------------------------------------------
# Botões no padrão do app (azul #1A4A75, alinhados à esquerda): o da visualização ativa fica preenchido
st.markdown("""
    <style>
    div.stButton > button[kind="primary"], button[data-testid="stBaseButton-primary"] {
        background-color: #1A4A75 !important; color: white !important; border: 1px solid #1A4A75 !important;
        border-radius: 4px !important; font-weight: 600 !important;
    }
    div.stButton > button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
        background-color: #133557 !important; border-color: #133557 !important; color: white !important;
    }
    div.stButton > button[kind="secondary"], button[data-testid="stBaseButton-secondary"] {
        background-color: white !important; color: #1A4A75 !important; border: 1px solid #1A4A75 !important;
        border-radius: 4px !important; font-weight: 600 !important;
    }
    div.stButton > button[kind="secondary"]:hover, button[data-testid="stBaseButton-secondary"]:hover {
        background-color: #EEF3F8 !important; color: #133557 !important;
    }
    </style>
""", unsafe_allow_html=True)
VISAO_LISTA, VISAO_MAPA = "Lista de alertas", "Ver no mapa"
if st.session_state.get("alertas_visao") not in (VISAO_LISTA, VISAO_MAPA):
    st.session_state["alertas_visao"] = VISAO_LISTA
visao = st.session_state["alertas_visao"]


def trocar_visao(nova):
    st.session_state["alertas_visao"] = nova


st.markdown("<div style='height: 8px;'></div>", unsafe_allow_html=True)
col_lista, col_mapa, _ = st.columns([1.4, 1.4, 5])
with col_lista:
    st.button(VISAO_LISTA, key="botao_lista", use_container_width=True,
              type="primary" if visao == VISAO_LISTA else "secondary", on_click=trocar_visao, args=(VISAO_LISTA,))
with col_mapa:
    st.button(VISAO_MAPA, key="botao_mapa", use_container_width=True,
              type="primary" if visao == VISAO_MAPA else "secondary", on_click=trocar_visao, args=(VISAO_MAPA,))


def mostrar_card(alerta):
    categoria = alerta["CATEGORIA_RISCO"] or "Sem categoria"
    data_hora = alerta["DATA_HORA"]
    data_txt = data_hora.strftime("%d/%m/%Y às %H:%M") if isinstance(data_hora, datetime.datetime) else str(data_hora)

    if alerta["ORIGEM_ALERTA"] == "CEMADEN":
        ibge = (alerta["DETALHAMENTO_1"] or "").rpartition("IBGE: ")[2].rstrip(")").strip()
        proximidade = ("Município com fazenda monitorada" if ibge in municipios_com_fazenda
                       else "Município sem fazenda monitorada")
    else:
        fazenda, dist = fazenda_mais_proxima(alerta, fazendas_exibicao)
        proximidade = (f"{texto_seguro(fazenda['NOME_FAZENDA'])} a {dist:.1f} km"
                       if fazenda is not None and dist <= RAIO_KM else "—")

    st.markdown(html_compacto(
        f"""
        <div style="background-color: {CORES_CARD.get(categoria, '#FCFCFC')}; padding: 18px; border-radius: 8px;
                    margin-bottom: 14px; border-left: 6px solid {CORES_MAPA.get(categoria, '#1A4A75')};">
            <div style="font-size: 1.45rem; font-weight: 700; color: #1A4A75;">
                {texto_seguro(titulo_alerta(alerta['TIPO_ALERTA']))}</div>
            <p style="margin: 5px 0; font-size: 14px;">
                <b>Origem:</b> {texto_seguro(alerta['ORIGEM_ALERTA'])} |
                <b>Data/Hora:</b> {data_txt} |
                <b>Fazenda:</b> {proximidade}
            </p>
            <p style="margin: 8px 0; font-size: 16px;"><b>NÍVEL DO RISCO:</b> {etiqueta_nivel(categoria)}</p>
            <hr style="border: 0; border-top: 1px solid rgba(0,0,0,0.1);">
            <p style="margin: 0; font-size: 15px;"><b>Orientação operacional:</b>
                {texto_seguro(alerta['ORIENTACAO']) or 'Sem orientações específicas.'}</p>
            <p style="margin: 5px 0 0 0; font-size: 13px; color: #333;"><b>Detalhes:</b>
                {texto_seguro(detalhes_exibicao(alerta))}</p>
        </div>
        """), unsafe_allow_html=True)


def plural(n, singular, plural_):
    return singular if n == 1 else plural_


def tempo_desde(momento):
    """Texto curto do tempo desde a última detecção (ex.: 30 h, 3 dias)."""
    if not momento:
        return "—"
    horas = (datetime.datetime.now() - momento).total_seconds() / 3600
    if horas < 48:
        return f"{max(1, round(horas))} h"
    return f"{round(horas / 24)} dias"


def mostrar_card_fazenda(grupo):
    """Um card por fazenda com o fogo em volta dela resumido por direção (norte, leste, sul e oeste): de que lado
    está, a que distância, se ainda está queimando e o risco atual. O nível do card é o da direção mais grave."""
    fazenda, setores = grupo["fazenda"], grupo["setores"]
    categoria = grupo["categoria"] or "Sem categoria"
    ultima = grupo["ultima"].strftime("%d/%m/%Y às %H:%M") if grupo["ultima"] else "—"
    ativos, mais_proximo = grupo["ativos"], grupo["mais_proximo"]

    if categoria == CATEGORIA_PENDENTE:
        nivel = (f"{etiqueta_nivel(categoria)} <span style='font-size: 14px; color: #555;'>foco detectado; "
                 f"o INPE ainda não divulgou o risco de fogo</span>")
    else:
        nivel = etiqueta_nivel(categoria)

    lados = [s["setor"] for s in setores]
    if len(lados) == 1:
        resumo = f"fogo {AO_SETOR[lados[0]]} da fazenda, a até {RAIO_KM} km — "
    else:
        resumo = (f"fogo em {len(lados)} direções ({', '.join(lados[:-1])} e {lados[-1]}), "
                  f"a até {RAIO_KM} km da fazenda — ")
    if ativos:
        resumo += (f"<b>ativo em {ativos} {plural(ativos, 'direção', 'direções')}</b> (detecção nas últimas "
                   f"{HORAS_ATIVIDADE_RECENTE} horas)")
    else:
        resumo += f"sem detecção nas últimas {HORAS_ATIVIDADE_RECENTE} horas"
    situacao = f"<p style='margin: 6px 0 0 0; font-size: 14px;'><b>Situação atual:</b> {resumo}</p>"

    itens = []
    for setor in setores:
        cor = CORES_MAPA.get(setor["categoria"], "#90A4AE")
        if setor["risco"] is not None:
            risco = f"risco de fogo atual {setor['risco'] * 100:.0f}%"
        else:
            risco = "risco de fogo em cálculo pelo INPE"
        if setor["ativo"]:
            estado = (f"<span style='color: #B71C1C; font-weight: 700;'>ATIVO</span> "
                      f"<span style='color: #555;'>(última detecção há {tempo_desde(setor['ultima'])})</span>")
        else:
            estado = f"<span style='color: #555;'>sem detecção há {tempo_desde(setor['ultima'])}</span>"
        municipios = setor["municipios"]
        local = texto_seguro(municipios[0]) if municipios else ""
        if len(municipios) > 1:
            outros = len(municipios) - 1
            local += f" (+{outros} {plural(outros, 'município', 'municípios')})"
        if setor["distancia_max"] - setor["distancia"] >= 1:
            faixa = f"fogo de {km(setor['distancia'])} a {km(setor['distancia_max'])} km da fazenda"
        else:
            faixa = f"fogo a {km(setor['distancia'])} km da fazenda"
        itens.append(
            f"<div style='display: flex; align-items: flex-start; gap: 10px; margin: 10px 0;'>"
            f"<span style='flex: 0 0 26px; height: 26px; border-radius: 50%; background: {cor}; color: white; "
            f"font-weight: 700; text-align: center; line-height: 26px;'>{setor['setor'][0].upper()}</span>"
            f"<span style='font-size: 14px;'><b>{setor['setor'].upper()}</b> {etiqueta_nivel(setor['categoria'])} "
            f"{estado}<br>{faixa} — {local}"
            f"<br><span style='color: #555;'>{risco}</span></span></div>"
        )

    st.markdown(html_compacto(f"""
        <div style="background-color: {CORES_CARD.get(categoria, '#FCFCFC')}; padding: 18px; border-radius: 8px;
                    margin-bottom: 14px; border-left: 6px solid {CORES_MAPA.get(categoria, '#1A4A75')};">
            <div style="font-size: 1.45rem; font-weight: 700; color: #1A4A75;">
                Fogo perto da fazenda — {texto_seguro(fazenda['NOME_FAZENDA'])}</div>
            <p style="margin: 5px 0; font-size: 14px;">
                <b>Fazenda em:</b> {texto_seguro(fazenda['MUNICIPIO'])}/{texto_seguro(fazenda['ESTADO'])} |
                <b>Origem:</b> INPE |
                <b>Fogo mais próximo:</b> {km(mais_proximo['distancia'])} km {AO_SETOR[mais_proximo['setor']]} |
                <b>Última detecção:</b> {ultima}
            </p>
            <p style="margin: 8px 0; font-size: 16px;"><b>NÍVEL DO RISCO:</b> {nivel}</p>
            {situacao}
            <hr style="border: 0; border-top: 1px solid rgba(0,0,0,0.1);">
            <p style="margin: 0; font-size: 15px;"><b>Orientação operacional:</b>
                {texto_seguro(grupo['orientacao']) or 'Sem orientações específicas.'}</p>
            <p style="margin: 12px 0 0 0; font-size: 14px;"><b>Onde está o fogo</b>
                <span style="color: #555;">— por direção a partir da fazenda (as detecções repetidas do satélite
                ficam juntas em cada direção)</span></p>
            {''.join(itens)}
        </div>
    """), unsafe_allow_html=True)


# ---------------------------------------------------------------------------
# Lista: hidrológicos (todos) e depois queimadas, do mais grave para o mais leve
# ---------------------------------------------------------------------------
ORIENTACAO_PREVISTA = {
    "Crítico": ("Risco CRÍTICO de incêndio previsto: reforce e limpe os aceiros, deixe a brigada e o caminhão-pipa "
                "de prontidão, não faça queimadas nem use máquinas que gerem faíscas perto da vegetação seca e "
                "planeje a retirada de máquinas e animais das áreas mais expostas."),
    "Alto": ("Risco ALTO de incêndio previsto: confira aceiros e equipamentos de combate, evite queimadas e trabalho "
             "com máquinas perto de palhada ou vegetação seca e acompanhe os boletins diariamente."),
}


ORIENTACAO_PREVISTA_CHUVA = {
    "Hidrológico": {
        "Crítico": ("Risco CRÍTICO de inundação ou enxurrada previsto: retire máquinas e insumos de áreas baixas e "
                    "próximas de rios e córregos, suspenda operações em várzeas e não atravesse áreas alagadas."),
        "Alto": ("Risco ALTO de inundação ou enxurrada previsto: planeje a retirada de máquinas das áreas baixas, "
                 "evite operar perto de cursos d'água e acompanhe os alertas do CEMADEN e da Defesa Civil."),
    },
    "Deslizamento": {
        "Crítico": ("Risco CRÍTICO de deslizamento previsto: suspenda operações em encostas e taludes, retire as "
                    "máquinas de áreas de declive e de pé de encosta e evite estradas de terra em morros."),
        "Alto": ("Risco ALTO de deslizamento previsto: evite trabalhar com máquinas em encostas e taludes, guarde-as "
                 "em área plana e segura e observe trincas, estalos e água barrenta no terreno."),
    },
}


def rotulo_dia(data, hoje):
    diferenca = (data - hoje).days
    nome = {0: "Hoje", 1: "Amanhã"}.get(diferenca, f"Em {diferenca} dias")
    return f"{nome} ({data:%d/%m})"


def agrupar_previsoes(linhas, fazendas_por_id_visiveis):
    """Uma entrada por fazenda com risco Alto/Crítico (pelas regras) em algum dos próximos dias."""
    por_fazenda = {}
    for linha in linhas:
        if linha["ID_FAZENDA"] not in fazendas_por_id_visiveis:
            continue
        data = linha["DATA_REFERENCIA"].date() if hasattr(linha["DATA_REFERENCIA"], "date") else linha["DATA_REFERENCIA"]
        por_fazenda.setdefault(linha["ID_FAZENDA"], {}).setdefault(data, {})["regras"] = linha
    grupos = []
    for id_faz, dias in por_fazenda.items():
        alertas = [(data, d["regras"]) for data, d in sorted(dias.items())
                   if d.get("regras") and d["regras"]["NIVEL"] in NIVEIS_AVISO_PREVISAO]
        if not alertas:
            continue
        pior_data, pior = max(alertas, key=lambda a: (POSICAO_GRAVIDADE_INV.get(a[1]["NIVEL"], 0),
                                                      float(a[1]["VALOR"] or 0), -a[0].toordinal()))
        grupos.append({"fazenda": fazendas_por_id_visiveis[id_faz], "dias": dias, "primeiro": alertas[0][0],
                       "pior_data": pior_data, "pior": pior})
    grupos.sort(key=lambda g: (-POSICAO_GRAVIDADE_INV.get(g["pior"]["NIVEL"], 0), g["primeiro"],
                               -float(g["pior"]["VALOR"] or 0)))
    return grupos


POSICAO_GRAVIDADE_INV = {"Baixo": 0, "Médio": 1, "Alto": 2, "Crítico": 3}
# Cards de previsão em tons de cinza (para não confundir com alertas já detectados); só as etiquetas são coloridas
COR_FUNDO_PREVISAO, COR_BORDA_PREVISAO = "#F2F3F5", "#6B7280"


def mostrar_card_previsao(grupo, hoje):
    fazenda, pior = grupo["fazenda"], grupo["pior"]
    nivel = pior["NIVEL"]
    tipo = pior.get("TIPO_RISCO") or TIPO_PREVISAO_REGRAS
    orientacao = ORIENTACAO_PREVISTA_CHUVA[tipo][nivel] if tipo in ORIENTACAO_PREVISTA_CHUVA \
        else ORIENTACAO_PREVISTA[nivel]
    dias_txt = []
    for k in range(PRIMEIRO_DIA_PREVISAO, ULTIMO_DIA_PREVISAO + 1):
        data = hoje + datetime.timedelta(days=k)
        regra = grupo["dias"].get(data, {}).get("regras")
        nivel_dia = regra["NIVEL"] if regra else None
        if nivel_dia in NIVEIS_AVISO_PREVISAO:
            chance = f" · {fmt_pct_simples(regra['VALOR'])}" if regra.get("VALOR") is not None else ""
            conteudo = f"{etiqueta_nivel(nivel_dia)}<span style='font-size: 12px; color: #333;'>{chance}</span>"
        else:
            conteudo = "<span style='font-size: 12px; color: #777;'>sem alerta</span>"
        dias_txt.append(
            f"<div style='flex: 1 1 0; min-width: 120px; background: white; border: 1px solid #D5D8DC; "
            f"border-radius: 6px; padding: 8px 10px;'>"
            f"<div style='font-size: 12px; color: #555; margin-bottom: 4px;'>{rotulo_dia(data, hoje)}</div>"
            f"{conteudo}</div>")
    primeiro = grupo["primeiro"]
    diferenca = (primeiro - hoje).days
    if diferenca <= 1:
        antecedencia = f"a partir de amanhã ({primeiro:%d/%m})"
    else:
        antecedencia = f"a partir de {primeiro:%d/%m} (em {diferenca} dias)"
    st.markdown(html_compacto(f"""
        <div style="background-color: {COR_FUNDO_PREVISAO}; padding: 18px; border-radius: 8px;
                    margin-bottom: 14px; border: 1px solid #D5D8DC; border-left: 6px solid {COR_BORDA_PREVISAO};">
            <div style="font-size: 1.45rem; font-weight: 700; color: #1A4A75;">
                {TITULO_PREVISAO.get(tipo, TITULO_PREVISAO["Queimada"])} — {texto_seguro(fazenda['NOME_FAZENDA'])}</div>
            <p style="margin: 5px 0; font-size: 14px;">
                <b>Fazenda em:</b> {texto_seguro(fazenda['MUNICIPIO'])}/{texto_seguro(fazenda['ESTADO'])} |
                <b>Risco alto ou crítico {antecedencia}</b> |
                <b>Pior dia:</b> {grupo['pior_data']:%d/%m}
            </p>
            <p style="margin: 8px 0; font-size: 16px;"><b>NÍVEL PREVISTO:</b> {etiqueta_nivel(nivel)}
                <span style="font-size: 14px; color: #555;">{TEXTO_CHANCE.get(tipo, TEXTO_CHANCE["Queimada"])}:
                {fmt_pct_simples(pior['VALOR'])}</span></p>
            <div style="display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0;">{''.join(dias_txt)}</div>
            <hr style="border: 0; border-top: 1px solid rgba(0,0,0,0.1);">
            <p style="margin: 0; font-size: 15px;"><b>Orientação preventiva:</b> {orientacao}</p>
            <p style="margin: 8px 0 0 0; font-size: 13px; color: #333;"><b>Por que:</b>
                {texto_seguro(pior.get('MOTIVOS'))}</p>
        </div>
    """), unsafe_allow_html=True)


def fmt_pct_simples(valor):
    """Chance em %: sem casas a partir de 10%; com mais casas nos eventos raros (0,05% em vez de 0%)."""
    if valor is None:
        return "-"
    valor = float(valor)
    casas = 0 if valor >= 10 else 1 if valor >= 1 else 2 if valor >= 0.01 else 3
    return f"{valor:.{casas}f}%".replace(".", ",")


def ir_para_pagina(chave, pagina):
    st.session_state[chave] = pagina


def controles_paginacao(chave, total_paginas, posicao):
    """Início / Anterior / Página X de N / Próxima / Fim (mesmo padrão em todas as seções)."""
    pagina = st.session_state.get(chave, 1)
    col_ini, col_ant, col_txt, col_prox, col_fim = st.columns([1.1, 1.1, 2.2, 1.1, 1.1])
    col_ini.button("« Início", key=f"{chave}_inicio_{posicao}", use_container_width=True, type="secondary",
                   disabled=pagina <= 1, on_click=ir_para_pagina, args=(chave, 1))
    col_ant.button("‹ Anterior", key=f"{chave}_anterior_{posicao}", use_container_width=True, type="secondary",
                   disabled=pagina <= 1, on_click=ir_para_pagina, args=(chave, pagina - 1))
    col_txt.markdown(f"<div style='text-align:center;padding-top:7px;color:#1A4A75;font-weight:600;'>"
                     f"Página {pagina} de {total_paginas}</div>", unsafe_allow_html=True)
    col_prox.button("Próxima ›", key=f"{chave}_proxima_{posicao}", use_container_width=True, type="secondary",
                    disabled=pagina >= total_paginas, on_click=ir_para_pagina, args=(chave, pagina + 1))
    col_fim.button("Fim »", key=f"{chave}_fim_{posicao}", use_container_width=True, type="secondary",
                   disabled=pagina >= total_paginas, on_click=ir_para_pagina, args=(chave, total_paginas))


def mostrar_paginado(itens, chave, mostrar, descricao):
    """Mostra os itens em páginas de FAZENDAS_POR_PAGINA, com os controles em cima e embaixo.
    Volta para a página 1 quando os filtros mudam (assinatura = filtro atual)."""
    assinatura = (where, tuple(sorted((k, str(v)) for k, v in params.items())), len(itens))
    if st.session_state.get(f"{chave}_assinatura") != assinatura:
        st.session_state[f"{chave}_assinatura"] = assinatura
        st.session_state[chave] = 1
    total_paginas = max(1, math.ceil(len(itens) / FAZENDAS_POR_PAGINA))
    pagina = min(max(1, st.session_state.get(chave, 1)), total_paginas)
    st.session_state[chave] = pagina
    inicio = (pagina - 1) * FAZENDAS_POR_PAGINA
    fim = min(inicio + FAZENDAS_POR_PAGINA, len(itens))
    if total_paginas > 1:
        st.caption(f"{descricao} {inicio + 1} a {fim} de {numero(len(itens))}.")
        controles_paginacao(chave, total_paginas, "topo")
    for item in itens[inicio:fim]:
        mostrar(item)
    if total_paginas > 1:
        controles_paginacao(chave, total_paginas, "rodape")


def titulo_secao(titulo, subtitulo):
    st.markdown(html_compacto(f"""
        <div style="margin: 28px 0 12px 0;">
            <h2 style="color: #1A4A75; margin: 0; padding: 0;">{titulo}</h2>
            <p style="color: #555; font-size: 15px; margin: 4px 0 0 0;">{subtitulo}</p>
        </div>
    """), unsafe_allow_html=True)


if visao == VISAO_LISTA:
    # Previsão (hoje a +3 dias): só fazendas com risco Alto ou Crítico
    if "INPE" in origens:
        hoje_previsao = datetime.date.today()
        try:
            linhas_previsao = previsoes_incendio(None if visao_geral_admin else tuple(ids_filtro))
        except oracledb.Error:
            linhas_previsao = []
        visiveis = fazendas_por_id if visao_geral_admin else {f["ID"]: f for f in fazendas_exibicao}
        previstos = agrupar_previsoes(linhas_previsao, visiveis)
        criticos = sum(1 for g in previstos if g["pior"]["NIVEL"] == "Crítico")
        if not linhas_previsao:
            titulo_secao("Risco de incêndio previsto (próximos 3 dias)",
                         "A previsão ainda não foi calculada. Ela é gerada pelo pipeline depois que as regras são "
                         "gravadas.")
        elif not previstos:
            titulo_secao("Risco de incêndio previsto (próximos 3 dias)",
                         "Nenhuma das fazendas selecionadas tem risco alto ou crítico de incêndio previsto até "
                         f"{hoje_previsao + datetime.timedelta(days=ULTIMO_DIA_PREVISAO):%d/%m}.")
        else:
            titulo_secao("Risco de incêndio previsto (próximos 3 dias)",
                         f"{len(previstos)} fazenda(s) com risco alto ou crítico previsto ({criticos} crítico(s)), "
                         "da mais grave e mais próxima no tempo para a menos grave.")
            mostrar_paginado(previstos, "pagina_previsao", lambda g: mostrar_card_previsao(g, hoje_previsao),
                             "Fazendas")

    if "CEMADEN" in origens:
        hoje_previsao = datetime.date.today()
        visiveis = fazendas_por_id if visao_geral_admin else {f["ID"]: f for f in fazendas_exibicao}
        linhas_chuva, previstos_chuva = [], []
        for tipo_chuva in TIPOS_PREVISAO_CHUVA:
            try:
                linhas_tipo = previsoes_incendio(None if visao_geral_admin else tuple(ids_filtro), tipo_chuva)
            except oracledb.Error:
                linhas_tipo = []
            linhas_chuva += linhas_tipo
            previstos_chuva += agrupar_previsoes(linhas_tipo, visiveis)
        previstos_chuva.sort(key=lambda g: (-POSICAO_GRAVIDADE_INV.get(g["pior"]["NIVEL"], 0), g["primeiro"],
                                            -float(g["pior"]["VALOR"] or 0)))
        titulo_chuva = "Risco hidrológico e de deslizamento previsto (próximos 3 dias)"
        if not linhas_chuva:
            titulo_secao(titulo_chuva, "A previsão ainda não foi calculada. Ela é gerada pelo pipeline depois que as "
                                       "regras de chuva são gravadas (página Monitoramento).")
        elif not previstos_chuva:
            titulo_secao(titulo_chuva, "Nenhuma das fazendas selecionadas tem risco alto ou crítico de inundação ou "
                                       f"deslizamento previsto até "
                                       f"{hoje_previsao + datetime.timedelta(days=ULTIMO_DIA_PREVISAO):%d/%m}.")
        else:
            titulo_secao(titulo_chuva, f"{len(previstos_chuva)} aviso(s) de risco alto ou crítico previsto pela chuva "
                                       "acumulada, do mais grave e mais próximo no tempo para o menos grave.")
            mostrar_paginado(previstos_chuva, "pagina_previsao_chuva",
                             lambda g: mostrar_card_previsao(g, hoje_previsao), "Avisos")

        if total_hidro == 0:
            titulo_secao("Risco hidrológico",
                         "Não há alertas de risco hidrológico (inundações, enxurradas ou deslizamentos) para os "
                         "municípios das fazendas selecionadas no período e níveis escolhidos.")
        else:
            titulo_secao("Risco hidrológico",
                         f"{numero(total_hidro)} alerta{'' if total_hidro == 1 else 's'} para os municípios das "
                         "fazendas selecionadas (o mais recente de cada município e tipo), do mais grave para o "
                         "mais leve.")
            mostrar_paginado(hidrologicos, "pagina_hidrologico", mostrar_card, "Alertas")

    if any(o != "CEMADEN" for o in origens):
        if not grupos:
            titulo_secao("Risco de Queimadas",
                         f"Não há fogo a até {RAIO_KM} km das fazendas selecionadas no período e níveis escolhidos.")
        else:
            titulo_secao("Risco de Queimadas",
                         f"Fogo a até {RAIO_KM} km de {numero(len(grupos))} fazenda{'' if len(grupos) == 1 else 's'}, "
                         "resumido por direção (norte, leste, sul e oeste) com o risco atual, da fazenda mais exposta "
                         "para a menos exposta.")
            if focos_limitados:
                st.caption(f"Considerando as {numero(len(focos))} detecções mais recentes do período.")
            mostrar_paginado(grupos, "pagina_queimadas", mostrar_card_fazenda, "Fazendas")

# ---------------------------------------------------------------------------
# Mapa com rótulo (tooltip) ao passar o mouse
# ---------------------------------------------------------------------------
else:
    # Mesmos dados e mesma consolidação da lista: para cada fazenda, uma MANCHA por direção com fogo (norte, leste,
    # sul, oeste: a área das detecções) e um marcador no ponto de fogo mais próximo da fazenda; e um ponto por
    # alerta hidrológico.
    alertas_por_fazenda = {grupo["fazenda"]["ID"]: [s["setor"] for s in grupo["setores"]] for grupo in grupos}
    st.caption(f"{numero(total_frentes)} frente(s) de fogo em volta de {numero(len(grupos))} fazenda(s) e "
               f"{numero(len(hidrologicos))} alerta(s) hidrológico(s), com o risco mais recente. A mancha mostra onde "
               "há fogo em cada direção; o marcador fica no fogo mais próximo da fazenda (maior = fogo ativo)."
               + (f" Considerando as {numero(len(focos))} detecções mais recentes do período."
                  if focos_limitados else ""))

    registros_alertas = []
    registros_areas = []
    for p in hidrologicos:
        ibge = (p["DETALHAMENTO_1"] or "").rpartition("IBGE: ")[2].rstrip(")").strip()
        data_hora = p["DATA_HORA"]
        if p["LATITUDE"] is None or p["LONGITUDE"] is None:
            continue
        registros_alertas.append({
            "lat": float(p["LATITUDE"]), "lon": float(p["LONGITUDE"]), "raio": 7,
            "cor": cor_rgb(CORES_MAPA.get(p["CATEGORIA_RISCO"], "#90A4AE")),
            "titulo": html.escape("Risco hidrológico: " + titulo_alerta(p["TIPO_ALERTA"])),
            "linha1": html.escape(f"Nível: {p['CATEGORIA_RISCO']}"),
            "linha2": html.escape(data_hora.strftime("%d/%m/%Y %H:%M") if isinstance(data_hora, datetime.datetime) else ""),
            "linha3": html.escape((p["DETALHAMENTO_1"] or "").split(" - Lat:")[0].replace("Município: ", "")),
            "linha4": html.escape("Município com fazenda monitorada" if ibge in municipios_com_fazenda
                                  else "Município sem fazenda monitorada"),
        })
    for fazenda, setor in frentes:
        risco = (f"risco de fogo atual {setor['risco'] * 100:.0f}%" if setor["risco"] is not None
                 else "risco de fogo em cálculo pelo INPE")
        if setor["ativo"]:
            estado = f"ATIVO: última detecção há {tempo_desde(setor['ultima'])}"
        else:
            estado = f"sem detecção há {tempo_desde(setor['ultima'])}"
        municipios = ", ".join(setor["municipios"][:3]) + (" e outros" if len(setor["municipios"]) > 3 else "")
        rotulo = {
            "cor": cor_rgb(CORES_MAPA.get(setor["categoria"], "#90A4AE")),
            "titulo": html.escape(f"Fogo {AO_SETOR[setor['setor']]} da {fazenda['NOME_FAZENDA']}"),
            "linha1": html.escape(f"Nível: {setor['categoria']} | {risco}"),
            "linha2": html.escape(estado),
            "linha3": html.escape(municipios),
            "linha4": html.escape(f"mais próximo a {km(setor['distancia'])} km da fazenda"),
        }
        registros_alertas.append(dict(rotulo, lat=setor["lat"], lon=setor["lon"], raio=11 if setor["ativo"] else 7))
        if setor["contorno"]:
            registros_areas.append(dict(rotulo, contorno=setor["contorno"],
                                        cor_area=cor_rgb(CORES_MAPA.get(setor["categoria"], "#90A4AE"), 60)))

    registros_fazendas = [{
        "lat": f["LATITUDE"], "lon": f["LONGITUDE"], "cor": cor_rgb(COR_FAZENDA, 255),
        "titulo": html.escape(f['NOME_FAZENDA']),
        "linha1": "Fazenda monitorada",
        "linha2": html.escape(f"{f['MUNICIPIO'] or ''}/{f['ESTADO'] or ''}"),
        "linha3": html.escape(("Fogo a até " + str(RAIO_KM) + " km: " + ", ".join(alertas_por_fazenda[f["ID"]]))
                              if f["ID"] in alertas_por_fazenda else f"Sem fogo a até {RAIO_KM} km"),
        "linha4": "",
    } for f in fazendas_exibicao]

    camadas = [
        # Áreas com fogo no fundo, fazendas no meio e marcadores por cima (o anel azul da fazenda continua
        # visível)
        pdk.Layer("PolygonLayer", data=registros_areas, get_polygon="contorno", get_fill_color="cor_area",
                  get_line_color="cor", line_width_min_pixels=1, stroked=True, filled=True, pickable=True),
        pdk.Layer("ScatterplotLayer", data=registros_fazendas, get_position="[lon, lat]", get_fill_color="cor",
                  get_radius=9, radius_units="'pixels'", stroked=True, get_line_color=[255, 255, 255, 255],
                  line_width_min_pixels=3, pickable=True),
        pdk.Layer("ScatterplotLayer", data=registros_alertas, get_position="[lon, lat]", get_fill_color="cor",
                  get_radius="raio", radius_units="'pixels'", stroked=True, get_line_color=[255, 255, 255, 200],
                  line_width_min_pixels=1, pickable=True),
    ]
    centro_lat = sum(f["LATITUDE"] for f in fazendas_exibicao) / len(fazendas_exibicao)
    centro_lon = sum(f["LONGITUDE"] for f in fazendas_exibicao) / len(fazendas_exibicao)
    zoom = 7 if len(fazendas_exibicao) == 1 else (5 if perfil == PERFIL_PRODUTOR else 3.8)
    st.pydeck_chart(pdk.Deck(
        layers=camadas,
        initial_view_state=pdk.ViewState(latitude=centro_lat, longitude=centro_lon, zoom=zoom),
        map_style="light",
        tooltip={
            "html": "<b>{titulo}</b><br/>{linha1}<br/>{linha2}<br/>{linha3}<br/><i>{linha4}</i>",
            "style": {"backgroundColor": "#1A4A75", "color": "white", "fontSize": "13px", "padding": "8px"},
        },
    ), height=620)

    legenda = [("Fazenda", COR_FAZENDA)] + [(n, CORES_MAPA[n]) for n in NIVEIS if n in niveis]
    st.markdown(
        " ".join(f"<span style='display:inline-block;width:12px;height:12px;border-radius:50%;background:{cor};"
                 f"margin:0 4px 0 14px;vertical-align:middle;'></span>{nome}" for nome, cor in legenda)
        + "<br><span style='color:#666;font-size:13px;'>Passe o mouse sobre o fogo ou sobre uma fazenda para ver os detalhes.</span>",
        unsafe_allow_html=True,
    )

render_footer()
