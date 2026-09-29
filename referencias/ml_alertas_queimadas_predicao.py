# -*- coding: utf-8 -*-
"""
Gera as REGRAS de previsão de queimadas a partir do histórico -> CS_EVENTOS_PREDICAO (rodar à mão)

Ideia: "condições semelhantes às que antecederam queimadas". Para cada fazenda e cada dia do histórico:
  - clima do dia (CS_FAZENDAS_CLIMA): temperatura máxima, umidade mínima, vento máximo, chuva em 7 dias
    e dias seguidos sem chuva;
  - evento: houve foco do INPE (CS_ALERTAS, risco acima de 40%) a até RAIO_EVENTO_KM da fazenda no dia
    ou nos JANELA_DIAS - 1 dias seguintes.
O programa testa combinações de limites (ex.: umidade até 30% E 10 ou mais dias sem chuva) e guarda as que,
no histórico, tiveram muitos dias observados e chance de foco bem acima do normal. Uma regra por linha em
CS_EVENTOS_PREDICAO (TIPO_RISCO = 'Queimada'), com a chance (PROBABILIDADE), os dias observados (CASOS) e
a explicação (DESCRICAO_REGRA). Também grava a regra 30-30-30 (temperatura > 30 °C, umidade < 30% e
vento > 30 km/h), com nível mínimo Alto.

Validação: as regras são geradas com os dias ANTES de INICIO_TESTE e testadas nos dias a partir dele
(que o programa não viu). O relatório mostra, por nível, quantas vezes o alerta acertou.

Uso:
    python modelos/ml_alertas_queimadas_predicao.py              (gera, valida e grava as regras)
    python modelos/ml_alertas_queimadas_predicao.py --simular    (gera e valida, sem gravar)
As regras de queimada anteriores são substituídas. O serviço servicos/alertas_queimadas_predicao.py
(pipeline) usa essas regras todo dia.
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import math
import time
import argparse
import datetime
import itertools
import numpy as np
import oracledb
from auth import USER, PASSWORD, DSN
from servicos.alertas_queimadas_predicao import (
    TIPO_RISCO, REGRA_30_30_30, NIVEL_MINIMO_30_30_30, ORIGEM_HISTORICO, ORIGEM_30_30_30, UF_REGIAO, NIVEIS,
    VARIAVEIS, AvaliadorRegras, completar_series, nivel_por_probabilidade, nivel_maximo, descrever_condicoes,
    fmt_numero,
)

# --- Definição do evento -------------------------------------------------------------------------
RAIO_EVENTO_KM = 15            # foco a até 15 km da fazenda...
JANELA_DIAS = 3                # ...no dia ou nos 2 dias seguintes
INICIO_TESTE = datetime.date(2026, 1, 1)
HORAS_MINIMAS_DIA = 20         # dia com menos horas de clima registradas é descartado

# --- Busca das regras ----------------------------------------------------------------------------
# Limites testados (None = variável fora da regra). Direção: calor, secura e vento aumentam o risco.
GRADE = {
    "TEMPERATURA": (">=", [None, 30, 33, 36]),
    "UMIDADE": ("<=", [None, 20, 30, 40]),
    "VENTO": (">=", [None, 15, 20, 30]),
    "DIAS": (">=", [None, 5, 10, 20, 30]),
    "PRECIPITACAO": ("<=", [None, 1, 5]),
}
MIN_CASOS_REGRA = 150          # dias (fazenda x dia) mínimos com as condições da regra
LIFT_MINIMO = 1.5              # chance da regra >= 1,5 x a chance normal da região
MAX_REGRAS_POR_REGIAO = 12
COBERTURA_NOVA_MINIMA = 0.2    # regra só entra se ao menos 20% dos seus eventos não forem cobertos pelas já escolhidas
GANHO_AGRAVANTE = 0.10         # regra mais específica entra se tiver chance 10 pontos maior que a regra que a contém
MIN_EVENTOS_UF = 300           # UF com menos eventos no treino usa as regras nacionais


def milhar(numero):
    return f"{numero:,}".replace(",", ".")


def pct(fracao, casas=1):
    return f"{fracao * 100:.{casas}f}%".replace(".", ",")


def log(msg):
    print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)


def distancia_km(lat1, lon1, lat2, lon2):
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


# =============================================================================================
# Leitura do histórico
# =============================================================================================

def carregar_fazendas(cursor):
    cursor.execute("""
        SELECT ID, ESTADO, LATITUDE, LONGITUDE FROM CS_FAZENDAS
        WHERE LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL
    """)
    return {i: {"uf": str(uf or "").strip().upper(), "lat": float(la), "lon": float(lo)}
            for i, uf, la, lo in cursor.fetchall()}


def carregar_clima(cursor, fazendas):
    """Clima diário por fazenda (agregado no banco a partir do horário) com as séries de chuva."""
    log("Lendo o clima diário das fazendas (agregado de CS_FAZENDAS_CLIMA; pode levar 1-2 minutos)...")
    cursor.arraysize = 50000
    cursor.execute("""
        SELECT ID_FAZENDA, TRUNC(DATA_HORA) AS DIA, MAX(TEMPERATURA), MIN(UMIDADE), MAX(VELOCIDADE_VENTO),
               SUM(PRECIPITACAO), COUNT(*)
        FROM CS_FAZENDAS_CLIMA
        GROUP BY ID_FAZENDA, TRUNC(DATA_HORA)
    """)
    por_fazenda = {}
    for id_faz, dia, tmax, umin, vmax, chuva, horas in cursor:
        if id_faz not in fazendas:
            continue
        completo = horas >= HORAS_MINIMAS_DIA
        por_fazenda.setdefault(id_faz, {})[dia.date() if hasattr(dia, "date") else dia] = {
            "temperatura_max": float(tmax) if completo and tmax is not None else None,
            "umidade_min": float(umin) if completo and umin is not None else None,
            "vento_max": float(vmax) if completo and vmax is not None else None,
            "chuva": float(chuva) if completo and chuva is not None else None,
        }
    # Série diária contínua por fazenda (dias faltando entram vazios e zeram a contagem de dias sem chuva)
    series = {}
    for id_faz, dias in por_fazenda.items():
        inicio, fim = min(dias), max(dias)
        lista = []
        atual = inicio
        while atual <= fim:
            registro = dict(dias.get(atual, {"temperatura_max": None, "umidade_min": None,
                                             "vento_max": None, "chuva": None}))
            registro["data"] = atual
            lista.append(registro)
            atual += datetime.timedelta(days=1)
        series[id_faz] = completar_series(lista)
    log(f"   {len(series)} fazenda(s), {milhar(sum(len(s) for s in series.values()))} dias de clima.")
    return series


def carregar_dias_com_foco(cursor, fazendas):
    """{(id_fazenda, dia UTC)} com foco do INPE a até RAIO_EVENTO_KM."""
    log("Lendo os focos do INPE e marcando os dias com foco perto de cada fazenda...")
    # Índice das fazendas em células de 0,5 grau (15 km cabe com folga nas células vizinhas)
    celulas = {}
    for id_faz, f in fazendas.items():
        celulas.setdefault((math.floor(f["lat"] * 2), math.floor(f["lon"] * 2)), []).append((id_faz, f))
    cursor.arraysize = 100000
    # Detecções repetidas (mesmo lugar e dia) viram uma só; DATA_HORA está em Brasília -> dia UTC (+3 h)
    cursor.execute("""
        SELECT DISTINCT ROUND(LATITUDE, 2), ROUND(LONGITUDE, 2), TRUNC(DATA_HORA + 3/24)
        FROM CS_ALERTAS
        WHERE ORIGEM_ALERTA = 'INPE' AND CATEGORIA_RISCO <> 'Aguardando risco'
          AND LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL
    """)
    marcados, lidos = set(), 0
    for lat, lon, dia in cursor:
        lidos += 1
        lat, lon = float(lat), float(lon)
        dia = dia.date() if hasattr(dia, "date") else dia
        base = (math.floor(lat * 2), math.floor(lon * 2))
        for dl in (-1, 0, 1):
            for dc in (-1, 0, 1):
                for id_faz, f in celulas.get((base[0] + dl, base[1] + dc), []):
                    if distancia_km(lat, lon, f["lat"], f["lon"]) <= RAIO_EVENTO_KM:
                        marcados.add((id_faz, dia))
        if lidos % 500000 == 0:
            log(f"   {milhar(lidos)} focos lidos...")
    log(f"   {milhar(lidos)} focos (sem repetição), {milhar(len(marcados))} dias de fazenda com foco a até {RAIO_EVENTO_KM} km.")
    return marcados


def montar_base(fazendas, series, dias_com_foco):
    """Uma linha por fazenda e dia, com o clima e o evento (foco no dia ou nos seguintes)."""
    linhas = []
    for id_faz, dias in series.items():
        if not dias:
            continue
        ultimo = dias[-1]["data"] - datetime.timedelta(days=JANELA_DIAS - 1)  # janela completa
        for dia in dias:
            if dia["data"] > ultimo:
                continue
            if any(dia.get(VARIAVEIS[v][0]) is None for v in VARIAVEIS):
                continue
            evento = any((id_faz, dia["data"] + datetime.timedelta(days=k)) in dias_com_foco
                         for k in range(JANELA_DIAS))
            linhas.append((id_faz, fazendas[id_faz]["uf"], dia["data"], dia["temperatura_max"], dia["umidade_min"],
                           dia["vento_max"], dia["chuva_7d"], dia["dias_sem_chuva"], int(evento)))
    return linhas


# =============================================================================================
# Busca das regras
# =============================================================================================

class Base:
    """Colunas em arrays numpy para testar milhares de combinações rapidamente."""

    def __init__(self, linhas):
        self.linhas = linhas
        self.fazenda = np.array([l[0] for l in linhas])
        self.uf = np.array([l[1] for l in linhas])
        self.data = np.array([l[2] for l in linhas])
        self.valores = {
            "TEMPERATURA": np.array([l[3] for l in linhas], dtype=float),
            "UMIDADE": np.array([l[4] for l in linhas], dtype=float),
            "VENTO": np.array([l[5] for l in linhas], dtype=float),
            "PRECIPITACAO": np.array([l[6] for l in linhas], dtype=float),
            "DIAS": np.array([l[7] for l in linhas], dtype=float),
        }
        self.evento = np.array([l[8] for l in linhas], dtype=bool)

    def filtrar(self, mascara):
        return Base([l for l, m in zip(self.linhas, mascara) if m])


def mascara_condicao(base, variavel, operador, valor):
    x = base.valores[variavel]
    return {">": x > valor, ">=": x >= valor, "<": x < valor, "<=": x <= valor}[operador]


def minerar(base, nome_regiao):
    """Regras da região: combinações da GRADE com muitos casos e chance bem acima do normal."""
    total = len(base.evento)
    if total == 0:
        return []
    taxa_normal = base.evento.mean()
    mascaras = {}
    for variavel, (operador, limites) in GRADE.items():
        for limite in limites:
            if limite is not None:
                mascaras[(variavel, limite)] = mascara_condicao(base, variavel, operador, limite)

    candidatas = []
    for combinacao in itertools.product(*[limites for _, limites in GRADE.values()]):
        condicoes = [(variavel, GRADE[variavel][0], float(limite))
                     for variavel, limite in zip(GRADE, combinacao) if limite is not None]
        if not condicoes:
            continue
        mascara = np.ones(total, dtype=bool)
        for variavel, _, limite in condicoes:
            mascara &= mascaras[(variavel, int(limite))]
        casos = int(mascara.sum())
        if casos < MIN_CASOS_REGRA:
            continue
        eventos = int(base.evento[mascara].sum())
        chance = eventos / casos
        nivel = nivel_por_probabilidade(chance * 100)
        if nivel == "Baixo" or chance < taxa_normal * LIFT_MINIMO:
            continue
        candidatas.append({"condicoes": condicoes, "casos": casos, "eventos": eventos, "chance": chance,
                           "nivel": nivel, "mascara": mascara})

    # Mais graves primeiro; dentro do nível, as que cobrem mais eventos (regras mais amplas e simples).
    # Uma regra entra se (a) traz eventos novos para o seu nível (evita variações quase iguais) ou
    # (b) é um "agravante": fica quase toda dentro de regras já escolhidas, mas com chance bem maior
    # (ex.: umidade até 40% -> 68%; umidade até 20% e 30 dias sem chuva -> 94%).
    candidatas.sort(key=lambda r: (-NIVEIS.index(r["nivel"]), -r["eventos"], len(r["condicoes"]), -r["chance"]))
    escolhidas, cobertos_por_nivel = [], {}
    for regra in candidatas:
        eventos_regra = regra["mascara"] & base.evento
        cobertos = cobertos_por_nivel.setdefault(regra["nivel"], np.zeros(total, dtype=bool))
        novos = int((eventos_regra & ~cobertos).sum())
        traz_novos = regra["eventos"] and novos / regra["eventos"] >= COBERTURA_NOVA_MINIMA
        chance_das_que_contem = [e["chance"] for e in escolhidas
                                 if (regra["mascara"] & e["mascara"]).sum() >= 0.8 * regra["casos"]]
        agravante = bool(chance_das_que_contem) and regra["chance"] >= max(chance_das_que_contem) + GANHO_AGRAVANTE
        if not traz_novos and not agravante:
            continue
        escolhidas.append(regra)
        cobertos |= eventos_regra
        if len(escolhidas) >= MAX_REGRAS_POR_REGIAO:
            break
    log(f"   {nome_regiao}: {milhar(total)} dias, chance normal de foco {pct(taxa_normal)}, "
        f"{len(candidatas)} combinação(ões) acima do normal, {len(escolhidas)} regra(s) escolhida(s).")
    for regra in escolhidas:
        regra["taxa_normal"] = taxa_normal
    return escolhidas


def estatistica_30_30_30(base):
    mascara = np.ones(len(base.evento), dtype=bool)
    for variavel, (operador, valor) in REGRA_30_30_30.items():
        mascara &= mascara_condicao(base, variavel, operador, valor)
    casos = int(mascara.sum())
    eventos = int(base.evento[mascara].sum())
    return casos, eventos, (eventos / casos if casos else None)


def linha_regra(uf, condicoes, nivel, chance, casos, origem, descricao):
    """Registro no formato de CS_EVENTOS_PREDICAO (também usado pelo AvaliadorRegras na validação)."""
    registro = {"TIPO_RISCO": TIPO_RISCO, "UF": uf, "REGIAO": UF_REGIAO.get(uf, "Brasil") if uf else "Brasil",
                "GRAU_RISCO": nivel, "PROBABILIDADE": round(chance * 100, 2) if chance is not None else None,
                "CASOS": casos, "ORIGEM_REGRA": origem, "DESCRICAO_REGRA": descricao}
    for variavel in VARIAVEIS:
        registro[f"{variavel}_CONDICAO"] = None
        registro[f"{variavel}_VALOR"] = None
    for variavel, operador, valor in condicoes:
        registro[f"{variavel}_CONDICAO"] = operador
        registro[f"{variavel}_VALOR"] = valor
    registro["DIAS_TIPO"] = "sem chuva" if registro["DIAS_CONDICAO"] else None
    return registro


def gerar_regras(treino, periodo):
    """Regras por UF (com histórico suficiente) e, para as demais UFs juntas, regras 'nacionais' aprendidas
    só com elas (UFs com poucas queimadas não herdam as chances de MT, GO...). Mais a regra 30-30-30."""
    regras = []
    escopos, sem_regra_propria = [], []
    for uf in sorted(set(treino.uf)):
        base_uf = treino.filtrar(treino.uf == uf)
        if base_uf.evento.sum() >= MIN_EVENTOS_UF:
            escopos.append((uf, base_uf))
        else:
            sem_regra_propria.append(uf)
    if sem_regra_propria:
        demais = treino.filtrar(np.isin(treino.uf, sem_regra_propria))
        if demais.evento.sum() >= MIN_EVENTOS_UF:
            escopos.append((None, demais))
            log(f"   {', '.join(sem_regra_propria)}: poucos focos para regras próprias -> regras aprendidas com essas UFs juntas.")
        else:
            log(f"   {', '.join(sem_regra_propria)}: histórico de focos insuficiente (mesmo juntas) -> só a regra 30-30-30.")
    for uf, base in escopos:
        onde = f"Em {uf}" if uf else f"Nas UFs {', '.join(sem_regra_propria)}"
        for r in minerar(base, uf or "Demais UFs"):
            descricao = (f"{onde}, com {descrever_condicoes(r['condicoes'])}, houve foco a até {RAIO_EVENTO_KM} km "
                         f"da fazenda no dia ou nos {JANELA_DIAS - 1} dias seguintes em "
                         f"{fmt_numero(r['chance'] * 100)}% dos casos ({milhar(r['casos'])} dias observados, {periodo}). "
                         f"A chance normal na região é de {fmt_numero(r['taxa_normal'] * 100, 1)}%.")
            regras.append(linha_regra(uf, r["condicoes"], r["nivel"], r["chance"], r["casos"], ORIGEM_HISTORICO,
                                      descricao))

    casos, eventos, chance = estatistica_30_30_30(treino)
    condicoes = [(v, op, val) for v, (op, val) in REGRA_30_30_30.items()]
    nivel = nivel_maximo(nivel_por_probabilidade((chance or 0) * 100), NIVEL_MINIMO_30_30_30)
    medida = (f"No histórico ({periodo}), houve foco a até {RAIO_EVENTO_KM} km em {fmt_numero(chance * 100)}% "
              f"de {milhar(casos)} dias com essas condições." if casos else "Essas condições não ocorreram no histórico.")
    regras.append(linha_regra(None, condicoes, nivel, chance, casos, ORIGEM_30_30_30,
                              f"Regra 30-30-30: {descrever_condicoes(condicoes)} aumentam muito o risco de incêndio "
                              f"(nível mínimo {NIVEL_MINIMO_30_30_30}). {medida}"))
    log(f"   Regra 30-30-30: {milhar(casos)} dias no treino"
        + (f", foco em {pct(chance)} deles -> nível {nivel}." if casos else f" -> nível {nivel}."))
    return regras


# =============================================================================================
# Validação
# =============================================================================================

def validar(regras, teste):
    """Aplica as regras nos dias de teste (não vistos) e mede o acerto por nível."""
    if not len(teste.evento):
        log("Sem dias de teste para validar.")
        return None
    avaliador = AvaliadorRegras([dict(r, ID=i) for i, r in enumerate(regras)])
    resultado = {n: [0, 0] for n in NIVEIS}  # nível -> [dias, dias com foco]
    for linha in teste.linhas:
        dia = {"temperatura_max": linha[3], "umidade_min": linha[4], "vento_max": linha[5],
               "chuva_7d": linha[6], "dias_sem_chuva": linha[7]}
        _, nivel, _, _ = avaliador.avaliar(linha[1], dia)
        resultado[nivel][0] += 1
        resultado[nivel][1] += linha[8]
    total_eventos = int(teste.evento.sum())
    log("=" * 78)
    log(f" VALIDAÇÃO em dias não usados para gerar as regras: {milhar(len(teste.evento))} dias, "
        f"{milhar(total_eventos)} com foco a até {RAIO_EVENTO_KM} km (chance normal {pct(teste.evento.mean())})")
    log(f" {'Nível previsto':<16}{'Dias':>10}{'Com foco':>12}{'Acerto':>10}{'Focos antecipados':>20}")
    for nivel in reversed(NIVEIS):
        dias, com_foco = resultado[nivel]
        acerto = pct(com_foco / dias) if dias else "-"
        antecipados = pct(com_foco / total_eventos) if total_eventos else "-"
        log(f" {nivel:<16}{milhar(dias):>10}{milhar(com_foco):>12}{acerto:>10}{antecipados:>20}")
    alerta = sum(resultado[n][1] for n in ("Médio", "Alto", "Crítico"))
    dias_alerta = sum(resultado[n][0] for n in ("Médio", "Alto", "Crítico"))
    if total_eventos:
        log(f" Dias com foco que tiveram alerta Médio ou maior antes: {pct(alerta / total_eventos)}")
    log("=" * 78)
    taxa_normal = float(teste.evento.mean())
    precisao = alerta / dias_alerta if dias_alerta else 0.0
    return {
        "dias_teste": len(teste.evento),
        "eventos_teste": total_eventos,
        "taxa_normal": taxa_normal,
        "por_nivel": [{"nivel": n, "dias": resultado[n][0], "com_foco": resultado[n][1],
                       "acerto": (resultado[n][1] / resultado[n][0]) if resultado[n][0] else None,
                       "antecipados": (resultado[n][1] / total_eventos) if total_eventos else None}
                      for n in reversed(NIVEIS)],
        "recall": alerta / total_eventos if total_eventos else 0.0,       # focos antecipados (Médio ou maior)
        "precisao": precisao,                                             # acerto dos alertas Médio ou maior
        "ganho": precisao / taxa_normal if taxa_normal else 0.0,          # quantas vezes melhor que o acaso
    }


# =============================================================================================
# Gravação
# =============================================================================================

COLUNAS_GRAVACAO = ["TIPO_RISCO", "REGIAO", "MUNICIPIO_IBGE", "UF", "GRAU_RISCO",
                    "TEMPERATURA_CONDICAO", "TEMPERATURA_VALOR", "UMIDADE_CONDICAO", "UMIDADE_VALOR",
                    "VENTO_CONDICAO", "VENTO_VALOR", "PRECIPITACAO_CONDICAO", "PRECIPITACAO_VALOR",
                    "DIAS_TIPO", "DIAS_CONDICAO", "DIAS_VALOR", "DESCRICAO_REGRA",
                    "PROBABILIDADE", "CASOS", "ORIGEM_REGRA"]


def gravar(connection, regras):
    cursor = connection.cursor()
    cursor.execute("DELETE FROM CS_EVENTOS_PREDICAO WHERE TIPO_RISCO = :1", [TIPO_RISCO])
    removidas = cursor.rowcount
    cursor.setinputsizes(DESCRICAO_REGRA=oracledb.DB_TYPE_CLOB)
    cursor.executemany(
        f"INSERT INTO CS_EVENTOS_PREDICAO ({', '.join(COLUNAS_GRAVACAO)}, DATA_GERACAO) "
        f"VALUES ({', '.join(':' + c for c in COLUNAS_GRAVACAO)}, SYSDATE)",
        [{c: r.get(c) for c in COLUNAS_GRAVACAO} for r in regras])
    connection.commit()
    log(f"Gravadas {len(regras)} regra(s) em CS_EVENTOS_PREDICAO (substituindo {removidas} regra(s) de queimada anteriores).")


def rodar_treinamento_queimadas(progress_callback=None):
    """Gera e valida as regras SEM gravar (usado pela página de Monitoramento e pelo main).
    progress_callback(porcentagem, etapa, descricao). Devolve um dicionário com as regras e a validação,
    ou None se não houver histórico suficiente. Para gravar, use gravar_regras_queimadas(resultado['regras'])."""
    def progresso(porcentagem, etapa, descricao):
        log(f"[{porcentagem}%] {etapa}: {descricao}")
        if progress_callback:
            progress_callback(porcentagem, etapa, descricao)

    inicio = time.time()
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        cursor = connection.cursor()
        progresso(5, "Fazendas", "Lendo as fazendas com coordenadas...")
        fazendas = carregar_fazendas(cursor)
        progresso(10, "Clima", "Lendo o clima diário das fazendas (pode levar 1-2 minutos)...")
        series = carregar_clima(cursor, fazendas)
        progresso(40, "Focos", "Marcando os dias com foco do INPE perto de cada fazenda...")
        dias_com_foco = carregar_dias_com_foco(cursor, fazendas)
    finally:
        connection.close()

    progresso(65, "Histórico", "Montando a base fazenda x dia...")
    linhas = montar_base(fazendas, series, dias_com_foco)
    if not linhas:
        log("[ERRO] Nenhum dia com clima completo para montar o histórico.")
        return None
    base = Base(linhas)
    treino = base.filtrar(base.data < INICIO_TESTE)
    teste = base.filtrar(base.data >= INICIO_TESTE)
    if not len(treino.evento):
        log("[ERRO] Sem dias de treino antes de INICIO_TESTE.")
        return None
    periodo = f"{min(treino.data):%m/%Y} a {max(treino.data):%m/%Y}"
    log(f"Histórico: {milhar(len(base.evento))} dias de fazenda | treino {milhar(len(treino.evento))} ({periodo}) | "
        f"teste {milhar(len(teste.evento))} (a partir de {INICIO_TESTE:%d/%m/%Y})")

    progresso(75, "Regras", "Buscando as combinações de condições que antecederam queimadas...")
    regras = gerar_regras(treino, periodo)
    progresso(90, "Validação", f"Testando as regras nos dias a partir de {INICIO_TESTE:%d/%m/%Y}...")
    validacao = validar(regras, teste)
    progresso(100, "Concluído", f"{len(regras)} regra(s) geradas em {(time.time() - inicio) / 60:.1f} min.")
    return {
        "regras": regras,
        "validacao": validacao,
        "total_registros": len(base.evento),
        "dias_treino": len(treino.evento),
        "periodo_treino": periodo,
        "inicio_teste": INICIO_TESTE,
        "raio_km": RAIO_EVENTO_KM,
        "janela_dias": JANELA_DIAS,
    }


def gravar_regras_queimadas(regras):
    """Substitui as regras de queimada de CS_EVENTOS_PREDICAO pelas informadas."""
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        gravar(connection, regras)
    finally:
        connection.close()
    return len(regras)


def main():
    parser = argparse.ArgumentParser(description="Gera as regras de previsão de queimadas a partir do histórico.")
    parser.add_argument("--simular", action="store_true", help="gera e valida sem gravar no banco")
    args = parser.parse_args()

    resultado = rodar_treinamento_queimadas()
    if resultado is None:
        return 1
    if args.simular:
        log("Simulação: nenhuma regra gravada. Para gravar, rode sem --simular.")
    else:
        gravar_regras_queimadas(resultado["regras"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
