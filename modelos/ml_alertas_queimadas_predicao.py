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

Anos extremos: ano com taxa de focos (agosto a outubro) muito acima dos demais (ex.: 2024, seca recorde)
fica fora do aprendizado, para não exagerar as chances.
Chances: as condições vêm do histórico, mas a chance gravada é a dos últimos 12 meses (momento atual).
Validação: regras aprendidas com os dias ANTES de INICIO_TESTE, chances calibradas com os 12 meses
anteriores ao teste e testadas nos dias a partir de INICIO_TESTE (que o programa não viu).

Uso:
    python modelos/ml_alertas_queimadas_predicao.py              (gera, valida e grava as regras)
    python modelos/ml_alertas_queimadas_predicao.py --simular    (gera e valida, sem gravar)
    python modelos/ml_alertas_queimadas_predicao.py --simular --excluir-anos 2024   (escolhe os anos à mão)
    python modelos/ml_alertas_queimadas_predicao.py --simular --excluir-anos        (não exclui nenhum)
    python modelos/ml_alertas_queimadas_predicao.py --simular --so-altos            (só Alto e Crítico, foco no acerto)
    python modelos/ml_alertas_queimadas_predicao.py --simular --raio 50 --janela 7  (evento: foco a até 50 km em 7 dias)
    python modelos/ml_alertas_queimadas_predicao.py --simular --meta 0.9    (90% dos focos com aviso Alto/Crítico)
    python modelos/ml_alertas_queimadas_predicao.py --simular --meta 0      (Alto fixo a partir de 30% de chance)
Meta de detecção (padrão 85%): o limite de chance do Alto é escolhido para que a maioria dos focos tenha aviso
Alto ou Crítico antes, aceitando mais alarmes falsos. Foco recente: foco do INPE a até 50 km nos 2 dias antes
deixa o dia no mínimo em Alto.
O relatório também mostra o PERIGO METEOROLÓGICO (índice FMA+): em que classe caíram os dias com foco.
Aprendizado: as previsões já feitas (CS_ALERTAS_PREDICAO) são conferidas com os focos que ocorreram; a regra com
previsões conferidas suficientes tem a chance ajustada pelo acerto real (pode subir, descer de nível ou sair).
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
    VARIAVEIS, GRAU_BANCO, LIMIARES_NIVEL, AvaliadorRegras, calcular_fma, CLASSES_FMA, NIVEL_DA_CLASSE_FMA,
    HORA_13H_UTC, completar_series, nivel_por_probabilidade, nivel_maximo, descrever_condicoes,
    fmt_numero, nivel_do_banco, ORIGEM_FOCO_RECENTE, RAIO_FOCO_RECENTE_KM, DIAS_FOCO_RECENTE,
    VARIAVEL_FOCO_RECENTE, NIVEL_MINIMO_ORIGEM, TEXTO_FOCO_RECENTE,
)

# --- Definição do evento -------------------------------------------------------------------------
RAIO_EVENTO_KM = 15            # foco a até 15 km da fazenda... (--raio; no máximo 50: o banco só guarda focos até
JANELA_DIAS = 3                # ...no dia ou nos 2 dias seguintes   50 km das fazendas)  (--janela)
RAIO_MAXIMO_KM = 50
RAIO_PADRAO_KM, JANELA_PADRAO_DIAS = RAIO_EVENTO_KM, JANELA_DIAS   # evento das regras gravadas (padrão)
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

# --- Modo "só Alto e Crítico" (--so-altos) -------------------------------------------------------
# Prioriza ACERTAR os alertas graves: só combinações com chance >= 30% (nível Alto), escolhidas pela maior
# chance, e que se mantêm em TODOS os anos do aprendizado (não só na média). Regras de nível Médio ficam de fora.
NIVEL_MINIMO_SO_ALTOS = "Alto"
MIN_CASOS_POR_ANO = 50         # ano com menos casos da combinação não entra na checagem de estabilidade

# --- Anos extremos (outliers) --------------------------------------------------------------------
# Ano cuja taxa de focos de agosto a outubro (auge da seca, período presente em todos os anos) passa de
# FATOR_ANO_EXTREMO x a mediana dos anos fica fora do aprendizado das regras (ex.: 2024, seca recorde).
MESES_COMPARACAO_ANOS = (8, 9, 10)
FATOR_ANO_EXTREMO = 1.5

# --- Calibração das chances ----------------------------------------------------------------------
# As regras (condições) vêm do histórico; a chance gravada vem dos últimos 12 meses, para refletir o
# momento atual. Regra com poucos casos recentes usa a chance do histórico ajustada à taxa atual da região.
DIAS_CALIBRACAO = 365
MIN_CASOS_CALIBRACAO = 100

# --- Meta de detecção dos avisos Alto e Crítico ----------------------------------------------------
# Objetivo: perder poucos focos, mesmo com mais alarmes falsos. Em vez de Alto = chance a partir de 30%, o limite
# do Alto é o maior valor de chance que, no período de calibração, deixa ao menos META_DETECCAO dos focos com
# aviso Alto ou Crítico antes (nunca acima de 30% nem abaixo de LIMITE_ALTO_MINIMO). O Crítico continua a
# partir de 50%. --meta 0 volta ao limite fixo de 30%.
META_DETECCAO = 0.85
LIMITE_ALTO_PADRAO = 30.0
LIMITE_ALTO_MINIMO = 10.0
LIMITE_MEDIO = 15.0
LIMITE_CRITICO = 50.0

# --- Acompanhamento e aprendizado com as previsões já feitas --------------------------------------
# As previsões gravadas em CS_ALERTAS_PREDICAO (com o clima PREVISTO usado) são conferidas com os focos que
# de fato ocorreram. Ao gerar as regras de novo, cada regra que já tiver MIN_CASOS_APRENDIZADO previsões
# conferidas mistura a chance calibrada com o acerto real: chance = (focos reais + chance x PESO) /
# (previsões + PESO). Com poucas previsões pesa mais a calibração; com muitas, o resultado real.
NIVEIS_AVISO = ("Alto", "Crítico")   # níveis que viram aviso na Central de Alertas (previsão "positiva")
DIAS_APRENDIZADO = 365
MIN_CASOS_APRENDIZADO = 30
PESO_CALIBRACAO = 50
DIAS_ACOMPANHAMENTO = 90       # período padrão da conferência na página de Monitoramento
MIN_ALERTAS_VEREDITO = 30      # menos avisos Alto/Crítico conferidos que isso -> "poucos dados"
MAX_EXEMPLOS = 10


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
    # Umidade e vento às 13h de Brasília (16h UTC), para o índice FMA+
    cursor.execute(f"""
        SELECT ID_FAZENDA, TRUNC(DATA_HORA), UMIDADE, VELOCIDADE_VENTO
        FROM CS_FAZENDAS_CLIMA
        WHERE TO_CHAR(DATA_HORA, 'HH24') = '{HORA_13H_UTC:02d}'
    """)
    for id_faz, dia, umidade, vento in cursor:
        registro = por_fazenda.get(id_faz, {}).get(dia.date() if hasattr(dia, "date") else dia)
        if registro is not None:
            registro["umidade_13h"] = float(umidade) if umidade is not None else None
            registro["vento_13h"] = float(vento) if vento is not None else None
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
        series[id_faz] = calcular_fma(completar_series(lista))
    log(f"   {len(series)} fazenda(s), {milhar(sum(len(s) for s in series.values()))} dias de clima.")
    return series


def carregar_dias_com_foco(cursor, fazendas, desde=None, ate=None, recentes=None):
    """{(id_fazenda, dia UTC)} com foco do INPE a até RAIO_EVENTO_KM.
    desde / ate (datas UTC, opcionais): lê só os focos desse período (conferência das previsões).
    recentes (set, opcional): recebe os (id_fazenda, dia) com foco a até RAIO_FOCO_RECENTE_KM (foco recente)."""
    log("Lendo os focos do INPE e marcando os dias com foco perto de cada fazenda...")
    # Índice das fazendas em células de 0,5 grau (15 km cabe com folga nas células vizinhas)
    celulas = {}
    for id_faz, f in fazendas.items():
        celulas.setdefault((math.floor(f["lat"] * 2), math.floor(f["lon"] * 2)), []).append((id_faz, f))
    maior_raio = max(RAIO_EVENTO_KM, RAIO_FOCO_RECENTE_KM if recentes is not None else 0)
    alcance = 1 if maior_raio <= 40 else 2        # células vizinhas a verificar (0,5 grau ~ 55 km)
    cursor.arraysize = 100000
    # Detecções repetidas (mesmo lugar e dia) viram uma só; DATA_HORA está em Brasília -> dia UTC (+3 h)
    filtro, parametros = "", {}
    if desde is not None:   # dia UTC começa às 21h do dia anterior em Brasília
        filtro += " AND DATA_HORA >= :desde - 3/24"
        parametros["desde"] = datetime.datetime.combine(desde, datetime.time.min)
    if ate is not None:
        filtro += " AND DATA_HORA < :ate + 1 - 3/24"
        parametros["ate"] = datetime.datetime.combine(ate, datetime.time.min)
    cursor.execute(f"""
        SELECT DISTINCT ROUND(LATITUDE, 2), ROUND(LONGITUDE, 2), TRUNC(DATA_HORA + 3/24)
        FROM CS_ALERTAS
        WHERE ORIGEM_ALERTA = 'INPE' AND CATEGORIA_RISCO <> 'Aguardando risco'
          AND LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL{filtro}
    """, parametros)
    marcados, lidos = set(), 0
    for lat, lon, dia in cursor:
        lidos += 1
        lat, lon = float(lat), float(lon)
        dia = dia.date() if hasattr(dia, "date") else dia
        base = (math.floor(lat * 2), math.floor(lon * 2))
        for dl in range(-alcance, alcance + 1):
            for dc in range(-alcance, alcance + 1):
                for id_faz, f in celulas.get((base[0] + dl, base[1] + dc), []):
                    distancia = distancia_km(lat, lon, f["lat"], f["lon"])
                    if distancia <= RAIO_EVENTO_KM:
                        marcados.add((id_faz, dia))
                    if recentes is not None and distancia <= RAIO_FOCO_RECENTE_KM:
                        recentes.add((id_faz, dia))
        if lidos % 500000 == 0:
            log(f"   {milhar(lidos)} focos lidos...")
    log(f"   {milhar(lidos)} focos (sem repetição), {milhar(len(marcados))} dias de fazenda com foco a até {RAIO_EVENTO_KM} km.")
    return marcados


def teve_foco_recente(recentes, id_faz, data):
    """Foco a até RAIO_FOCO_RECENTE_KM nos DIAS_FOCO_RECENTE dias ANTES do dia previsto (o que se sabe no
    cálculo de véspera)."""
    return int(bool(recentes) and any((id_faz, data - datetime.timedelta(days=k)) in recentes
                                      for k in range(1, DIAS_FOCO_RECENTE + 1)))


def montar_base(fazendas, series, dias_com_foco, recentes=None):
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
                           dia["vento_max"], dia["chuva_7d"], dia["dias_sem_chuva"], int(evento), dia.get("fma"),
                           teve_foco_recente(recentes, id_faz, dia["data"])))
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
            VARIAVEL_FOCO_RECENTE: np.array([l[10] if len(l) > 10 else 0 for l in linhas], dtype=float),
        }
        self.evento = np.array([l[8] for l in linhas], dtype=bool)
        self.fma = np.array([l[9] if len(l) > 9 and l[9] is not None else np.nan for l in linhas], dtype=float)

    def filtrar(self, mascara):
        return Base([l for l, m in zip(self.linhas, mascara) if m])


def mascara_condicao(base, variavel, operador, valor):
    x = base.valores[variavel]
    return {">": x > valor, ">=": x >= valor, "<": x < valor, "<=": x <= valor}[operador]


def minerar(base, nome_regiao, so_altos=False):
    """Regras da região: combinações da GRADE com muitos casos e chance bem acima do normal.
    so_altos: só combinações de nível Alto/Crítico, estáveis em todos os anos, escolhidas pela maior chance."""
    total = len(base.evento)
    if total == 0:
        return []
    taxa_normal = base.evento.mean()
    anos = np.array([d.year for d in base.data])
    lista_anos = sorted(set(anos))
    minimo_nivel = NIVEIS.index(NIVEL_MINIMO_SO_ALTOS) if so_altos else 1
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
        if NIVEIS.index(nivel) < minimo_nivel or chance < taxa_normal * LIFT_MINIMO:
            continue
        if so_altos:
            # Estabilidade: a combinação precisa chegar ao nível mínimo em cada ano com casos suficientes
            limite_nivel = next(l for l, n in LIMIARES_NIVEL if n == NIVEL_MINIMO_SO_ALTOS) / 100
            estavel = True
            for ano in lista_anos:
                no_ano = mascara & (anos == ano)
                casos_ano = int(no_ano.sum())
                if casos_ano >= MIN_CASOS_POR_ANO and base.evento[no_ano].mean() < limite_nivel:
                    estavel = False
                    break
            if not estavel:
                continue
        candidatas.append({"condicoes": condicoes, "casos": casos, "eventos": eventos, "chance": chance,
                           "nivel": nivel, "mascara": mascara})

    # Mais graves primeiro; dentro do nível, as que cobrem mais eventos (regras mais amplas e simples).
    # Uma regra entra se (a) traz eventos novos para o seu nível (evita variações quase iguais) ou
    # (b) é um "agravante": fica quase toda dentro de regras já escolhidas, mas com chance bem maior
    # (ex.: umidade até 40% -> 68%; umidade até 20% e 30 dias sem chuva -> 94%).
    if so_altos:  # acerto primeiro: as de maior chance
        candidatas.sort(key=lambda r: (-NIVEIS.index(r["nivel"]), -r["chance"], len(r["condicoes"]), -r["casos"]))
    else:
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


def anos_extremos(base, forcar=None):
    """Taxa de focos por ano (agosto a outubro) e os anos a excluir do aprendizado.
    forcar: lista de anos informada à mão ([] = não excluir nenhum; None = detectar automaticamente)."""
    meses = np.array([d.month for d in base.data])
    anos = np.array([d.year for d in base.data])
    taxas = {}
    for ano in sorted(set(anos)):
        mascara = (anos == ano) & np.isin(meses, MESES_COMPARACAO_ANOS)
        if mascara.sum() >= MIN_CASOS_REGRA:
            taxas[ano] = float(base.evento[mascara].mean())
    if forcar is not None:
        excluidos = sorted(a for a in forcar if a in set(anos))
    elif len(taxas) >= 3:
        mediana = float(np.median(list(taxas.values())))
        excluidos = sorted(a for a, t in taxas.items() if mediana and t > FATOR_ANO_EXTREMO * mediana)
    else:
        excluidos = []
    for ano, taxa in taxas.items():
        log(f"   {ano}: foco em {pct(taxa)} dos dias de agosto a outubro"
            + ("  <- ano extremo, fora do aprendizado das regras" if ano in excluidos else ""))
    return taxas, excluidos


def gerar_regras(treino, so_altos=False):
    """Condições das regras (sem chance final): por UF com histórico suficiente e, para as demais UFs
    juntas, regras aprendidas só com elas. Mais a regra 30-30-30 (vale para todas as UFs)."""
    brutas = []
    escopos, sem_regra_propria = [], []
    for uf in sorted(set(treino.uf)):
        base_uf = treino.filtrar(treino.uf == uf)
        if base_uf.evento.sum() >= MIN_EVENTOS_UF:
            escopos.append((uf, [uf], base_uf))
        else:
            sem_regra_propria.append(uf)
    if sem_regra_propria:
        demais = treino.filtrar(np.isin(treino.uf, sem_regra_propria))
        if demais.evento.sum() >= MIN_EVENTOS_UF:
            escopos.append((None, sem_regra_propria, demais))
            log(f"   {', '.join(sem_regra_propria)}: poucos focos para regras próprias -> regras aprendidas com essas UFs juntas.")
        else:
            log(f"   {', '.join(sem_regra_propria)}: histórico de focos insuficiente (mesmo juntas) -> só a regra 30-30-30.")
    for uf, ufs, base in escopos:
        for r in minerar(base, uf or "Demais UFs", so_altos):
            brutas.append({"uf": uf, "ufs": ufs, "condicoes": r["condicoes"], "chance_treino": r["chance"],
                           "casos_treino": r["casos"], "taxa_treino": r["taxa_normal"], "origem": ORIGEM_HISTORICO})
    brutas.append({"uf": None, "ufs": None, "condicoes": [(v, op, val) for v, (op, val) in REGRA_30_30_30.items()],
                   "chance_treino": None, "casos_treino": 0, "taxa_treino": float(treino.evento.mean()),
                   "origem": ORIGEM_30_30_30})
    if treino.valores[VARIAVEL_FOCO_RECENTE].any():
        brutas.append({"uf": None, "ufs": None, "condicoes": [(VARIAVEL_FOCO_RECENTE, ">=", 1)],
                       "chance_treino": None, "casos_treino": 0, "taxa_treino": float(treino.evento.mean()),
                       "origem": ORIGEM_FOCO_RECENTE})
    return brutas, sem_regra_propria


def nivel_pela_chance(chance_pct, limite_alto=LIMITE_ALTO_PADRAO):
    """Nível pela chance (%), com o limite do Alto ajustável (meta de detecção)."""
    if chance_pct >= LIMITE_CRITICO:
        return "Crítico"
    if chance_pct >= limite_alto:
        return "Alto"
    if chance_pct >= min(LIMITE_MEDIO, limite_alto):
        return "Médio" if limite_alto > LIMITE_MEDIO else "Baixo"
    return "Baixo"


def calibrar(brutas, calibracao, treino, periodo_treino, periodo_calibracao, sem_regra_propria, excluidos,
             so_altos=False, producao=None, aprendizado=None, limite_alto=LIMITE_ALTO_PADRAO, manter_todas=False):
    """Transforma as condições em regras gravadas, com a chance medida no período de calibração.
    producao (Base das previsões já conferidas, com o clima previsto): a regra com MIN_CASOS_APRENDIZADO
    previsões conferidas aprende com o resultado real. aprendizado (lista): recebe o que mudou em cada regra."""
    regras = []
    for b in brutas:
        base_escopo = calibracao if b["ufs"] is None else calibracao.filtrar(np.isin(calibracao.uf, b["ufs"]))
        treino_escopo = treino if b["ufs"] is None else treino.filtrar(np.isin(treino.uf, b["ufs"]))
        mascara = np.ones(len(base_escopo.evento), dtype=bool)
        mascara_treino = np.ones(len(treino_escopo.evento), dtype=bool)
        for variavel, operador, valor in b["condicoes"]:
            mascara &= mascara_condicao(base_escopo, variavel, operador, valor)
            mascara_treino &= mascara_condicao(treino_escopo, variavel, operador, valor)
        casos_treino = int(mascara_treino.sum())
        chance_treino = float(treino_escopo.evento[mascara_treino].mean()) if casos_treino else None
        casos = int(mascara.sum())
        taxa_atual = float(base_escopo.evento.mean()) if len(base_escopo.evento) else None
        if casos >= MIN_CASOS_CALIBRACAO:
            chance, forma = float(base_escopo.evento[mascara].mean()), "medida"
        elif chance_treino is not None and taxa_atual is not None and b["taxa_treino"]:
            chance, forma = min(chance_treino * taxa_atual / b["taxa_treino"], 0.95), "ajustada"
        else:
            chance, forma = chance_treino, "histórico"

        # Aprendizado: acerto real das previsões feitas com o clima previsto (o que o produtor viu)
        real = None
        if producao is not None and len(producao.evento) and chance is not None:
            prod_escopo = producao if b["ufs"] is None else producao.filtrar(np.isin(producao.uf, b["ufs"]))
            mascara_prod = np.ones(len(prod_escopo.evento), dtype=bool)
            for variavel, operador, valor in b["condicoes"]:
                mascara_prod &= mascara_condicao(prod_escopo, variavel, operador, valor)
            casos_prod = int(mascara_prod.sum())
            if casos_prod >= MIN_CASOS_APRENDIZADO:
                eventos_prod = int(prod_escopo.evento[mascara_prod].sum())
                real = {"casos": casos_prod, "eventos": eventos_prod, "acerto": eventos_prod / casos_prod,
                        "chance_antes": chance,
                        "nivel_antes": nivel_pela_chance(chance * 100, limite_alto)}
                chance = (eventos_prod + chance * PESO_CALIBRACAO) / (casos_prod + PESO_CALIBRACAO)

        nivel = nivel_pela_chance((chance or 0) * 100, limite_alto)
        minimo = NIVEL_MINIMO_ORIGEM.get(b["origem"])
        if minimo:
            nivel = nivel_maximo(nivel, minimo)
            if real:
                real["nivel_antes"] = nivel_maximo(real["nivel_antes"], minimo)
        removida = not manter_todas and not minimo and (
            nivel == "Baixo" or (so_altos and NIVEIS.index(nivel) < NIVEIS.index(NIVEL_MINIMO_SO_ALTOS)))
        if real is not None and aprendizado is not None:
            aprendizado.append({
                "regra": (f"{b['uf'] or 'Demais UFs'}: " if not minimo else f"{b['origem']}: ")
                         + descrever_condicoes(b["condicoes"]),
                "previsoes": real["casos"], "com_foco": real["eventos"], "acerto_real": float(real["acerto"]),
                "chance_antes": float(real["chance_antes"]), "chance_nova": float(chance),
                "nivel_antes": real["nivel_antes"], "nivel_novo": "Removida" if removida else nivel,
            })
        if removida:
            continue  # no momento atual essas condições não aumentam o risco o suficiente

        condicoes_txt = descrever_condicoes(b["condicoes"])
        sem_anos = f", sem {', '.join(map(str, excluidos))}" if excluidos else ""
        if b["origem"] == ORIGEM_30_30_30:
            onde = "Regra 30-30-30 (todas as UFs)"
        elif b["origem"] == ORIGEM_FOCO_RECENTE:
            onde = "Foco recente (todas as UFs)"
        elif b["uf"]:
            onde = f"Em {b['uf']}"
        else:
            onde = f"Nas UFs {', '.join(sem_regra_propria)}"
        if forma == "medida":
            atual = (f"nos últimos 12 meses ({periodo_calibracao}) houve foco a até {RAIO_EVENTO_KM} km da fazenda "
                     f"no dia ou nos {JANELA_DIAS - 1} dias seguintes em {fmt_numero(chance * 100)}% dos casos "
                     f"({milhar(casos)} dias)")
        elif forma == "ajustada":
            atual = (f"chance estimada de {fmt_numero(chance * 100)}% (poucos casos nos últimos 12 meses: histórico "
                     f"ajustado à taxa atual da região)")
        else:
            atual = "chance do histórico" + (f" de {fmt_numero(chance * 100)}%" if chance is not None else "")
        historico = (f"No período usado para achar a regra ({periodo_treino}{sem_anos}): "
                     f"{fmt_numero(chance_treino * 100)}% de {milhar(casos_treino)} dias." if casos_treino else "")
        normal = (f" Chance normal na região nos últimos 12 meses: {fmt_numero(taxa_atual * 100, 1)}%."
                  if taxa_atual is not None else "")
        descricao = f"{onde}, com {condicoes_txt}: {atual}. {historico}{normal}".replace(": .", ".")
        if real:
            descricao += (f" Resultado real das previsões conferidas: foco em {fmt_numero(real['acerto'] * 100)}% de "
                          f"{milhar(real['casos'])} dias previstos com essas condições; chance ajustada de "
                          f"{fmt_numero(real['chance_antes'] * 100)}% para {fmt_numero(chance * 100)}%.")
        if b["origem"] == ORIGEM_30_30_30:
            descricao += f" Nível mínimo {NIVEL_MINIMO_30_30_30}: condições de referência para incêndios."
        elif b["origem"] == ORIGEM_FOCO_RECENTE:
            descricao += (f" Nível mínimo {minimo}: o fogo costuma se espalhar e reaparecer na mesma região "
                          f"(foco detectado no dia do cálculo ou no anterior).")
        regras.append(linha_regra(b["uf"], b["condicoes"], nivel, chance, casos if forma == "medida" else casos_treino,
                                  b["origem"], descricao))
    return regras


# =============================================================================================
# Validação
# =============================================================================================

def dia_da_linha(linha):
    return {"temperatura_max": linha[3], "umidade_min": linha[4], "vento_max": linha[5], "chuva_7d": linha[6],
            "dias_sem_chuva": linha[7], "foco_recente": bool(linha[10]) if len(linha) > 10 else False}


def chances_por_dia(regras, base):
    """[(maior chance da regra que vale no dia, regra geral com nível mínimo vale?, houve foco)] por dia."""
    avaliador = AvaliadorRegras([dict(r, ID=i) for i, r in enumerate(regras)])
    return [(*avaliador.chance_maxima(linha[1], dia_da_linha(linha)), linha[8]) for linha in base.linhas]


def limite_para_meta(regras, base, meta):
    """Maior limite de chance (%) para o Alto que deixa ao menos `meta` dos focos com aviso Alto ou Crítico
    no período `base`. Devolve (limite, detecção atingida)."""
    dias = chances_por_dia(regras, base)
    eventos = sum(e for _, _, e in dias)
    if not eventos:
        return LIMITE_ALTO_PADRAO, None
    cobertos_por_piso = sum(e for c, piso, e in dias if piso)
    por_chance = {}
    for chance, piso, evento in dias:
        if not piso and evento:
            por_chance[chance] = por_chance.get(chance, 0) + 1
    cobertos, limite = cobertos_por_piso, LIMITE_ALTO_PADRAO
    # focos já cobertos com o limite padrão
    cobertos += sum(q for c, q in por_chance.items() if c >= LIMITE_ALTO_PADRAO)
    for chance in sorted((c for c in por_chance if 0 < c < LIMITE_ALTO_PADRAO), reverse=True):
        if cobertos / eventos >= meta or chance < LIMITE_ALTO_MINIMO:
            break
        cobertos += por_chance[chance]
        limite = chance
    return math.floor(limite * 10) / 10, cobertos / eventos


def curva_deteccao(regras, base, limites=(10, 12.5, 15, 17.5, 20, 22.5, 25, 27.5, 30)):
    """Para cada limite do Alto: % dos focos com aviso antes (POD), % de alarmes falsos (FAR) e avisos por foco."""
    dias = chances_por_dia(regras, base)
    eventos = sum(e for _, _, e in dias)
    curva = []
    for limite in limites:
        avisos = [(e) for c, piso, e in dias if piso or c >= limite]
        acertos = sum(avisos)
        curva.append({"limite": limite, "pod": acertos / eventos if eventos else None,
                      "far": 1 - acertos / len(avisos) if avisos else None,
                      "avisos_por_foco": len(avisos) / eventos if eventos else None,
                      "dias_com_aviso": len(avisos) / len(dias) if dias else None})
    return curva


def metricas_classificacao(contagem, eh_aviso):
    """Métricas típicas de modelos preditivos com "aviso" = previsão positiva.
    contagem: {pontuação ordenável: [dias, dias com foco]}; eh_aviso(pontuação) -> bool.
    AUC-ROC calculada com a pontuação (nível e chance): chance de um dia com foco ter pontuação maior que um sem."""
    vp = sum(c for k, (d, c) in contagem.items() if eh_aviso(k))        # aviso e houve foco
    fp = sum(d - c for k, (d, c) in contagem.items() if eh_aviso(k))    # aviso sem foco (alarme falso)
    fn = sum(c for k, (d, c) in contagem.items() if not eh_aviso(k))    # foco sem aviso
    vn = sum(d - c for k, (d, c) in contagem.items() if not eh_aviso(k))
    total, positivos, negativos = vp + fp + fn + vn, vp + fn, fp + vn
    precisao = vp / (vp + fp) if vp + fp else None
    recall = vp / positivos if positivos else None
    auc, negativos_abaixo = 0.0, 0
    for k in sorted(contagem):
        dias, com_foco = contagem[k]
        auc += com_foco * (negativos_abaixo + 0.5 * (dias - com_foco))
        negativos_abaixo += dias - com_foco
    # Métricas de verificação de previsão meteorológica (tabela de contingência 2x2, padrão OMM/WWRP)
    alertas = vp + fp
    far = fp / alertas if alertas else None                       # razão de alarmes falsos
    pofd = fp / negativos if negativos else None                  # prob. de alarme em dia sem evento
    csi = vp / (vp + fp + fn) if vp + fp + fn else None           # índice de sucesso crítico (threat score)
    vies = alertas / positivos if positivos else None             # 1 = avisa na mesma frequência do evento
    tss = recall - pofd if recall is not None and pofd is not None else None   # Peirce / Hanssen-Kuipers
    denominador_hss = (vp + fn) * (fn + vn) + (vp + fp) * (fp + vn)
    hss = 2 * (vp * vn - fp * fn) / denominador_hss if denominador_hss else None   # Heidke
    acaso = alertas * positivos / total if total else 0.0
    ets = (vp - acaso) / (vp + fp + fn - acaso) if (vp + fp + fn - acaso) else None  # Gilbert / ETS
    return {
        "pod": recall, "far": far, "pofd": pofd, "csi": csi, "vies": vies, "tss": tss, "hss": hss, "ets": ets,
        "taxa_evento": positivos / total if total else None,
        "vp": vp, "fp": fp, "fn": fn, "vn": vn,
        "acuracia": (vp + vn) / total if total else None,
        "acuracia_sempre_nao": negativos / total if total else None,
        "precisao": precisao, "recall": recall,
        "especificidade": vn / negativos if negativos else None,
        "f1": 2 * precisao * recall / (precisao + recall) if precisao and recall else None,
        "auc": auc / (positivos * negativos) if positivos and negativos else None,
    }


def validar(regras, teste):
    """Aplica as regras nos dias de teste (não vistos) e mede o acerto por nível."""
    if not len(teste.evento):
        log("Sem dias de teste para validar.")
        return None
    avaliador = AvaliadorRegras([dict(r, ID=i) for i, r in enumerate(regras)])
    resultado = {n: [0, 0] for n in NIVEIS}  # nível -> [dias, dias com foco]
    pontuacao = {}                            # (nível, chance) -> [dias, dias com foco], para a AUC
    diario = {}                               # data -> avisos Alto/Crítico, avisos com foco, dias com foco
    for linha in teste.linhas:
        chance, nivel, _, _ = avaliador.avaliar(linha[1], dia_da_linha(linha))
        resultado[nivel][0] += 1
        resultado[nivel][1] += linha[8]
        chave = (NIVEIS.index(nivel), round(float(chance or 0), 1))
        pontuacao.setdefault(chave, [0, 0])
        pontuacao[chave][0] += 1
        pontuacao[chave][1] += linha[8]
        d = diario.setdefault(linha[2], {"Data": linha[2], "Avisos": 0, "Avisos com foco": 0, "Dias com foco": 0})
        aviso = nivel in NIVEIS_AVISO
        d["Avisos"] += int(aviso)
        d["Avisos com foco"] += int(aviso and bool(linha[8]))
        d["Dias com foco"] += int(linha[8])
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
        graves = sum(resultado[n][1] for n in ("Alto", "Crítico"))
        dias_graves = sum(resultado[n][0] for n in ("Alto", "Crítico"))
        log(f" Dias com foco que tiveram alerta Médio ou maior antes: {pct(alerta / total_eventos)}")
        log(f" Dias com foco que tiveram aviso ALTO ou CRÍTICO antes: {pct(graves / total_eventos)} "
            f"(alarmes falsos: {pct(1 - graves / dias_graves) if dias_graves else '-'})")
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
        "metricas": metricas_classificacao(pontuacao, lambda k: NIVEIS[k[0]] in NIVEIS_AVISO),
        "diario": [diario[d] for d in sorted(diario)],
    }


def validar_fma(base, titulo):
    """Perigo meteorológico (FMA+): em que classe caíram os dias com foco. O índice não é aprendido dos dados
    (é uma fórmula de clima), então pode ser avaliado em todo o histórico."""
    com_indice = ~np.isnan(base.fma)
    if not com_indice.any():
        log("Sem umidade/vento das 13h para calcular o FMA+.")
        return None
    fma, evento = base.fma[com_indice], base.evento[com_indice]
    total_eventos = int(evento.sum())
    classes = []
    for i, (limite, classe) in enumerate(CLASSES_FMA):
        inferior = CLASSES_FMA[i - 1][0] if i else -1.0
        mascara = (fma > inferior) & (fma <= limite)
        dias = int(mascara.sum())
        com_foco = int(evento[mascara].sum())
        classes.append({"classe": classe, "nivel": NIVEL_DA_CLASSE_FMA[classe], "dias": dias, "com_foco": com_foco,
                        "acerto": com_foco / dias if dias else None,
                        "focos": com_foco / total_eventos if total_eventos else None})
    graves = [c for c in classes if c["classe"] in ("Alto", "Muito alto")]
    tranquilas = [c for c in classes if c["classe"] in ("Nulo", "Pequeno")]
    focos_graves = sum(c["com_foco"] for c in graves) / total_eventos if total_eventos else 0.0
    dias_tranquilos = sum(c["dias"] for c in tranquilas)
    sem_foco_tranquilos = (1 - sum(c["com_foco"] for c in tranquilas) / dias_tranquilos) if dias_tranquilos else None
    log("=" * 78)
    log(f" PERIGO METEOROLÓGICO (FMA+) - {titulo}: {milhar(len(fma))} dias, {milhar(total_eventos)} com foco")
    log(f" {'Classe FMA+':<14}{'Nível':<10}{'Dias':>10}{'Com foco':>11}{'% com foco':>12}{'% dos focos':>13}")
    for c in classes:
        log(f" {c['classe']:<14}{c['nivel']:<10}{milhar(c['dias']):>10}{milhar(c['com_foco']):>11}"
            f"{pct(c['acerto']) if c['acerto'] is not None else '-':>12}"
            f"{pct(c['focos']) if c['focos'] is not None else '-':>13}")
    log(f" Focos em dias de perigo Alto ou Muito alto: {pct(focos_graves)}")
    if sem_foco_tranquilos is not None:
        log(f" Dias de perigo Nulo ou Pequeno sem foco: {pct(sem_foco_tranquilos)}")
    log("=" * 78)
    return {"titulo": titulo, "dias": int(len(fma)), "eventos": total_eventos, "classes": classes,
            "focos_em_alto_ou_muito_alto": focos_graves, "sem_foco_em_nulo_ou_pequeno": sem_foco_tranquilos}


# =============================================================================================
# Acompanhamento das previsões já feitas (validação em produção)
# =============================================================================================

PROMESSA_NIVEL = {nivel: limite / 100 for limite, nivel in LIMIARES_NIVEL}   # chance mínima que o nível promete


def dia_utc_hoje():
    return datetime.datetime.now(datetime.timezone.utc).date()


def carregar_previsoes(cursor, desde, ate):
    """Previsões de queimada gravadas pelo serviço diário (uma por fazenda e dia: a mais recente, de véspera)."""
    cursor.execute("""
        SELECT p.ID_FAZENDA, f.NOME_FAZENDA, f.ESTADO, p.DATA_REFERENCIA, p.HORIZONTE_DIAS, p.VALOR, p.NIVEL,
               p.MOTIVOS, p.TEMPERATURA_MAX, p.UMIDADE_MIN, p.VENTO_MAX, p.CHUVA_7D, p.DIAS_SEM_CHUVA
        FROM CS_ALERTAS_PREDICAO p
        JOIN CS_FAZENDAS f ON f.ID = p.ID_FAZENDA
        WHERE p.TIPO_RISCO = :tipo AND p.DATA_REFERENCIA >= :desde AND p.DATA_REFERENCIA < :ate + 1
    """, {"tipo": TIPO_RISCO, "desde": datetime.datetime.combine(desde, datetime.time.min),
          "ate": datetime.datetime.combine(ate, datetime.time.min)})
    previsoes = []
    for (id_faz, nome, uf, data, horizonte, valor, nivel, motivos_txt, tmax, umin, vmax, chuva7,
         dias_seco) in cursor.fetchall():
        previsoes.append({
            "id_fazenda": id_faz, "fazenda": nome, "uf": str(uf or "").strip().upper(),
            "data": data.date() if hasattr(data, "date") else data, "horizonte": horizonte,
            "chance": float(valor) if valor is not None else 0.0,
            "nivel": nivel_do_banco(nivel) or "Baixo", "motivos": motivos_txt or "",
            "temperatura_max": tmax, "umidade_min": umin, "vento_max": vmax, "chuva_7d": chuva7,
            "dias_sem_chuva": dias_seco,
        })
    return previsoes


def conferir(previsoes, dias_com_foco):
    """Marca em cada previsão se houve foco a até RAIO_EVENTO_KM no dia ou nos JANELA_DIAS - 1 seguintes."""
    for p in previsoes:
        p["evento"] = int(any((p["id_fazenda"], p["data"] + datetime.timedelta(days=k)) in dias_com_foco
                              for k in range(JANELA_DIAS)))
    return previsoes


def base_de_producao(conferidas, recentes=None):
    """Base (mesmo formato do histórico) com o clima PREVISTO e o resultado real, para o aprendizado."""
    linhas = []
    for p in conferidas:
        clima = [p["temperatura_max"], p["umidade_min"], p["vento_max"], p["chuva_7d"], p["dias_sem_chuva"]]
        if any(v is None for v in clima):
            continue
        linhas.append((p["id_fazenda"], p["uf"], p["data"], *[float(v) for v in clima], p["evento"], None,
                       teve_foco_recente(recentes, p["id_fazenda"], p["data"])))
    return Base(linhas) if linhas else None


def veredito(valor, bom, atencao):
    if valor is None:
        return "Sem dados"
    return "Bom" if valor >= bom else ("Atenção" if valor >= atencao else "Ruim")


def exemplo(p):
    clima = []
    if p["temperatura_max"] is not None:
        clima.append(f"máx. {fmt_numero(float(p['temperatura_max']))} °C")
    if p["umidade_min"] is not None:
        clima.append(f"umidade mín. {fmt_numero(float(p['umidade_min']))}%")
    if p["vento_max"] is not None:
        clima.append(f"vento {fmt_numero(float(p['vento_max']))} km/h")
    if p["dias_sem_chuva"] is not None:
        clima.append(f"{int(p['dias_sem_chuva'])} dia(s) sem chuva")
    return {"Data": p["data"], "Fazenda": p["fazenda"], "UF": p["uf"], "Nível previsto": p["nivel"],
            "Chance prevista": f"{fmt_numero(p['chance'], 1)}%", "Houve foco": "Sim" if p["evento"] else "Não",
            "Clima previsto": ", ".join(clima), "Motivo do aviso": p["motivos"]}


def resumir_conferencia(conferidas, desde, ate):
    """Estatísticas por nível, vereditos qualitativos, evolução diária, UFs e exemplos."""
    total = len(conferidas)
    eventos = sum(p["evento"] for p in conferidas)
    taxa_normal = eventos / total if total else 0.0
    por_nivel = []
    for nivel in reversed(NIVEIS):
        doN = [p for p in conferidas if p["nivel"] == nivel]
        com_foco = sum(p["evento"] for p in doN)
        acerto = com_foco / len(doN) if doN else None
        promessa = PROMESSA_NIVEL.get(nivel)
        if acerto is None or promessa is None:
            cumpre = "-" if promessa is None else "Sem dados"
        else:
            cumpre = "Cumpre" if acerto >= promessa else ("Perto" if acerto >= 0.75 * promessa else "Abaixo")
        por_nivel.append({"nivel": nivel, "dias": len(doN), "com_foco": com_foco, "acerto": acerto,
                          "antecipados": com_foco / eventos if eventos else None,
                          "promessa": promessa, "cumpre": cumpre})

    avisos = [p for p in conferidas if p["nivel"] in NIVEIS_AVISO]
    tranquilos = [p for p in conferidas if p["nivel"] not in NIVEIS_AVISO]
    acertos = sum(p["evento"] for p in avisos)
    precisao = acertos / len(avisos) if avisos else None
    recall = acertos / eventos if eventos else None
    sem_foco_tranquilo = 1 - sum(p["evento"] for p in tranquilos) / len(tranquilos) if tranquilos else None
    ganho = precisao / taxa_normal if precisao is not None and taxa_normal else None
    # Diferença entre dias com e sem aviso (não depende de quantos dias tiveram aviso, como no auge da seca)
    foco_sem_aviso = 1 - sem_foco_tranquilo if sem_foco_tranquilo is not None else None
    separacao = (precisao / foco_sem_aviso if precisao is not None and foco_sem_aviso
                 else (None if precisao is None or foco_sem_aviso is None else float("inf")))

    def pct_txt(v):
        return pct(v) if v is not None else "-"

    indicadores = [
        {"Indicador": "Acerto dos avisos Alto e Crítico", "Valor": pct_txt(precisao),
         "Veredito": veredito(precisao, 0.30, 0.20),
         "O que significa": (f"De cada 10 avisos Alto ou Crítico, {fmt_numero(precisao * 10, 1)} tiveram foco a até "
                             f"{RAIO_EVENTO_KM} km em {JANELA_DIAS} dias. Referência: 3 em 10 (validação com o histórico)."
                             if precisao is not None else "Nenhum aviso Alto ou Crítico no período.")},
        {"Indicador": "Focos avisados com antecedência", "Valor": pct_txt(recall),
         "Veredito": veredito(recall, 0.50, 0.30),
         "O que significa": (f"Dos {milhar(eventos)} dias de fazenda com foco perto, {pct_txt(recall)} tinham aviso "
                             f"Alto ou Crítico na véspera." if eventos else "Nenhum foco perto das fazendas no período.")},
        {"Indicador": "Confiança quando não há aviso", "Valor": pct_txt(sem_foco_tranquilo),
         "Veredito": veredito(sem_foco_tranquilo, 0.95, 0.90),
         "O que significa": (f"Quando o modelo não avisou (Baixo ou Médio), {pct_txt(sem_foco_tranquilo)} dos dias "
                             f"ficaram sem foco perto." if tranquilos else "Todos os dias tiveram aviso.")},
        {"Indicador": "Diferença entre dias com e sem aviso",
         "Valor": (f"{fmt_numero(separacao, 1)}x" if separacao not in (None, float("inf"))
                   else ("sem focos sem aviso" if separacao else "-")),
         "Veredito": veredito(separacao, 3.0, 2.0),
         "O que significa": (f"Com aviso, houve foco em {pct_txt(precisao)} dos dias; sem aviso, em "
                             f"{pct_txt(foco_sem_aviso)}. Quanto maior a diferença, melhor o modelo separa os dias "
                             f"de risco." if separacao is not None else "Sem dias com e sem aviso para comparar.")},
    ]

    if len(avisos) < MIN_ALERTAS_VEREDITO:
        geral = "Poucos dados"
        frase = (f"Só {len(avisos)} aviso(s) Alto ou Crítico conferido(s) (mínimo {MIN_ALERTAS_VEREDITO}). "
                 "Os números abaixo ainda variam muito; a avaliação fica mais firme com mais dias de previsão.")
    else:
        vereditos = [i["Veredito"] for i in indicadores if i["Veredito"] != "Sem dados"]
        geral = "Ruim" if "Ruim" in vereditos else ("Atenção" if "Atenção" in vereditos else "Bom")
        fracos = [i["Indicador"].lower() for i in indicadores if i["Veredito"] in ("Ruim", "Atenção")]
        frase = {"Bom": "As previsões cumprem o que prometem: os avisos acertam no nível esperado e os dias sem "
                        "aviso são confiáveis.",
                 "Atenção": "O modelo funciona, mas com pontos a acompanhar: " + ", ".join(fracos) + ".",
                 "Ruim": "O desempenho real está abaixo do esperado em: " + ", ".join(fracos)
                         + ". Gere as regras de novo para o modelo aprender com esses resultados."}[geral]

    diario = {}
    for p in conferidas:
        d = diario.setdefault(p["data"], {"Data": p["data"], "Avisos": 0, "Avisos com foco": 0,
                                          "Dias com foco": 0})
        aviso = p["nivel"] in NIVEIS_AVISO
        d["Avisos"] += aviso
        d["Avisos com foco"] += aviso and p["evento"]
        d["Dias com foco"] += p["evento"]

    por_uf = []
    for uf in sorted({p["uf"] for p in conferidas}):
        do_uf = [p for p in conferidas if p["uf"] == uf]
        av = [p for p in do_uf if p["nivel"] in NIVEIS_AVISO]
        ev = sum(p["evento"] for p in do_uf)
        por_uf.append({"UF": uf, "Previsões": len(do_uf), "Dias com foco": ev, "Avisos": len(av),
                       "Acerto dos avisos": (sum(p["evento"] for p in av) / len(av)) if av else None,
                       "Focos avisados": (sum(p["evento"] for p in av) / ev) if ev else None})

    def ordenar(lista):
        return [exemplo(p) for p in sorted(lista, key=lambda p: (p["data"], p["chance"]), reverse=True)[:MAX_EXEMPLOS]]

    pontuacao = {}
    for p in conferidas:
        chave = (NIVEIS.index(p["nivel"]), round(p["chance"], 1))
        pontuacao.setdefault(chave, [0, 0])
        pontuacao[chave][0] += 1
        pontuacao[chave][1] += p["evento"]

    return {
        "metricas": metricas_classificacao(pontuacao, lambda k: NIVEIS[k[0]] in NIVEIS_AVISO),
        "desde": desde, "ate": ate, "previsoes": total, "eventos": eventos, "taxa_normal": taxa_normal,
        "fazendas": len({p["id_fazenda"] for p in conferidas}),
        "avisos": len(avisos), "acertos": acertos, "precisao": precisao, "recall": recall,
        "sem_foco_tranquilo": sem_foco_tranquilo, "ganho": ganho, "separacao": separacao,
        "por_nivel": por_nivel, "indicadores": indicadores, "veredito": geral, "frase": frase,
        "diario": [diario[d] for d in sorted(diario)], "por_uf": por_uf,
        "exemplos": {
            "acertos": ordenar([p for p in avisos if p["evento"]]),
            "alarmes": ordenar([p for p in avisos if not p["evento"]]),
            "sem_aviso": ordenar([p for p in tranquilos if p["evento"]]),
        },
        "raio_km": RAIO_EVENTO_KM, "janela_dias": JANELA_DIAS,
    }


def carregar_regras_em_uso():
    """Regras de queimada gravadas em CS_EVENTOS_PREDICAO (as que o pipeline usa hoje)."""
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        cursor = connection.cursor()
        cursor.execute("""
            SELECT ID, UF, GRAU_RISCO, PROBABILIDADE, CASOS, ORIGEM_REGRA, DATA_GERACAO,
                   TEMPERATURA_CONDICAO, TEMPERATURA_VALOR, UMIDADE_CONDICAO, UMIDADE_VALOR,
                   VENTO_CONDICAO, VENTO_VALOR, PRECIPITACAO_CONDICAO, PRECIPITACAO_VALOR,
                   DIAS_CONDICAO, DIAS_VALOR, DESCRICAO_REGRA
            FROM CS_EVENTOS_PREDICAO
            WHERE TIPO_RISCO = :1
        """, [TIPO_RISCO])
        colunas = [c[0] for c in cursor.description]
        regras = []
        for linha in cursor.fetchall():
            regra = dict(zip(colunas, linha))
            if hasattr(regra.get("DESCRICAO_REGRA"), "read"):
                regra["DESCRICAO_REGRA"] = regra["DESCRICAO_REGRA"].read()
            regra["GRAU_RISCO"] = nivel_do_banco(regra["GRAU_RISCO"]) or regra["GRAU_RISCO"]
            regras.append(regra)
    finally:
        connection.close()
    ordem = {n: i for i, n in enumerate(reversed(NIVEIS))}
    return sorted(regras, key=lambda r: (ordem.get(r["GRAU_RISCO"], 9), -(float(r["PROBABILIDADE"] or 0))))


def avaliar_previsoes_realizadas(dias=DIAS_ACOMPANHAMENTO, raio_km=None, janela_dias=None):
    """Confere as previsões dos últimos `dias` cujo resultado já é conhecido (a janela do evento já passou)
    com os focos do INPE. Evento: o das regras gravadas (padrão 15 km em 3 dias).
    Devolve o resumo (resumir_conferencia) ou um dicionário com 'previsoes' = 0."""
    global RAIO_EVENTO_KM, JANELA_DIAS
    RAIO_EVENTO_KM = max(1, min(int(raio_km or RAIO_PADRAO_KM), RAIO_MAXIMO_KM))
    JANELA_DIAS = max(1, int(janela_dias or JANELA_PADRAO_DIAS))
    ate = dia_utc_hoje() - datetime.timedelta(days=JANELA_DIAS)
    desde = ate - datetime.timedelta(days=int(dias) - 1)
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        cursor = connection.cursor()
        previsoes = carregar_previsoes(cursor, desde, ate)
        if not previsoes:
            return {"desde": desde, "ate": ate, "previsoes": 0}
        fazendas = carregar_fazendas(cursor)
        dias_com_foco = carregar_dias_com_foco(cursor, fazendas, min(p["data"] for p in previsoes),
                                               ate + datetime.timedelta(days=JANELA_DIAS - 1))
    finally:
        connection.close()
    return resumir_conferencia(conferir(previsoes, dias_com_foco), min(p["data"] for p in previsoes), ate)


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
        [{c: (GRAU_BANCO.get(r.get(c), r.get(c)) if c == "GRAU_RISCO" else r.get(c)) for c in COLUNAS_GRAVACAO}
         for r in regras])
    connection.commit()
    log(f"Gravadas {len(regras)} regra(s) em CS_EVENTOS_PREDICAO (substituindo {removidas} regra(s) de queimada anteriores).")


def rodar_treinamento_queimadas(progress_callback=None, excluir_anos=None, so_altos=False, raio_km=None,
                                janela_dias=None, meta_deteccao=META_DETECCAO):
    """Gera e valida as regras SEM gravar (usado pela página de Monitoramento e pelo main).
    progress_callback(porcentagem, etapa, descricao). excluir_anos: None = detecta os anos extremos
    sozinho; [] = não exclui nenhum; [2024] = exclui os anos informados.
    meta_deteccao: fração dos focos que deve ter aviso Alto ou Crítico antes (define o limite do Alto);
    None ou 0 = limite fixo de 30%.
    Devolve um dicionário com as regras e a validação, ou None se não houver histórico suficiente.
    Para gravar, use gravar_regras_queimadas(resultado['regras'])."""
    def progresso(porcentagem, etapa, descricao):
        log(f"[{porcentagem}%] {etapa}: {descricao}")
        if progress_callback:
            progress_callback(porcentagem, etapa, descricao)

    global RAIO_EVENTO_KM, JANELA_DIAS
    if raio_km is not None:
        RAIO_EVENTO_KM = max(1, min(int(raio_km), RAIO_MAXIMO_KM))
    if janela_dias is not None:
        JANELA_DIAS = max(1, int(janela_dias))
    log(f"Evento: foco a até {RAIO_EVENTO_KM} km da fazenda no dia ou nos {JANELA_DIAS - 1} dias seguintes.")

    inicio = time.time()
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        cursor = connection.cursor()
        progresso(5, "Fazendas", "Lendo as fazendas com coordenadas...")
        fazendas = carregar_fazendas(cursor)
        progresso(10, "Clima", "Lendo o clima diário das fazendas (pode levar 1-2 minutos)...")
        series = carregar_clima(cursor, fazendas)
        progresso(40, "Focos", "Marcando os dias com foco do INPE perto de cada fazenda...")
        recentes = set()
        dias_com_foco = carregar_dias_com_foco(cursor, fazendas, recentes=recentes)
        progresso(60, "Previsões feitas", "Conferindo as previsões já gravadas com os focos que ocorreram...")
        ate_producao = dia_utc_hoje() - datetime.timedelta(days=JANELA_DIAS)
        conferidas = conferir(carregar_previsoes(cursor, ate_producao - datetime.timedelta(days=DIAS_APRENDIZADO - 1),
                                                 ate_producao), dias_com_foco)
    finally:
        connection.close()
    producao = base_de_producao(conferidas, recentes)
    log(f"   {milhar(len(conferidas))} previsão(ões) conferida(s) para o aprendizado"
        + (f" ({milhar(int(producao.evento.sum()))} com foco)." if producao else "."))

    progresso(65, "Histórico", "Montando a base fazenda x dia e verificando anos extremos...")
    linhas = montar_base(fazendas, series, dias_com_foco, recentes)
    if not linhas:
        log("[ERRO] Nenhum dia com clima completo para montar o histórico.")
        return None
    base = Base(linhas)
    # Dias antes do primeiro mês com focos carregados (INPE a partir de 01/2023) não têm como ter "evento":
    # entrariam como se não houvesse fogo e distorceriam as regras. Ficam fora.
    if dias_com_foco:
        primeiro = min(dia for _, dia in dias_com_foco)
        inicio_focos = datetime.date(primeiro.year, primeiro.month, 1)
        antes = int((base.data < inicio_focos).sum())
        if antes:
            base = base.filtrar(base.data >= inicio_focos)
            log(f"   {milhar(antes)} dias de fazenda antes de {inicio_focos:%m/%Y} (sem dados de focos do INPE) ficam fora.")
    antes_teste = base.filtrar(base.data < INICIO_TESTE)
    teste = base.filtrar(base.data >= INICIO_TESTE)
    taxas_ano, excluidos = anos_extremos(antes_teste, excluir_anos)
    anos = np.array([d.year for d in antes_teste.data])
    treino = antes_teste.filtrar(~np.isin(anos, excluidos))
    if not len(treino.evento):
        log("[ERRO] Sem dias de treino antes de INICIO_TESTE.")
        return None

    def periodo(b):
        return f"{min(b.data):%m/%Y} a {max(b.data):%m/%Y}" if len(b.data) else "sem dados"

    # Calibração da validação: 12 meses antes do teste (o teste continua sem ser visto)
    calib_validacao = antes_teste.filtrar(antes_teste.data >= INICIO_TESTE - datetime.timedelta(days=DIAS_CALIBRACAO))
    # Calibração final (regras gravadas): os 12 meses mais recentes de dados
    ultimo_dia = max(base.data)
    calib_final = base.filtrar(base.data > ultimo_dia - datetime.timedelta(days=DIAS_CALIBRACAO))
    log(f"Histórico: {milhar(len(base.evento))} dias de fazenda | regras aprendidas com {milhar(len(treino.evento))} "
        f"({periodo(treino)}{', sem ' + ', '.join(map(str, excluidos)) if excluidos else ''}) | "
        f"teste {milhar(len(teste.evento))} (a partir de {INICIO_TESTE:%d/%m/%Y})")

    progresso(75, "Regras", "Buscando as combinações de condições que antecederam queimadas...")
    if so_altos:
        log("Modo só Alto e Crítico: regras com chance >= 30%, estáveis em todos os anos, priorizando o acerto.")
    brutas, sem_regra_propria = gerar_regras(treino, so_altos)
    progresso(85, "Validação", f"Calibrando com {periodo(calib_validacao)} e testando nos dias a partir de "
                               f"{INICIO_TESTE:%d/%m/%Y}...")
    argumentos = (treino, periodo(treino), periodo(calib_validacao), sem_regra_propria, excluidos, so_altos)
    limite_validacao, deteccao_validacao, curva = LIMITE_ALTO_PADRAO, None, None
    if meta_deteccao:
        todas_validacao = calibrar(brutas, calib_validacao, *argumentos, manter_todas=True)
        limite_validacao, deteccao_validacao = limite_para_meta(todas_validacao, calib_validacao, meta_deteccao)
        curva = curva_deteccao(todas_validacao, teste) if len(teste.evento) else None
        log(f"Meta de detecção {pct(meta_deteccao, 0)}: Alto a partir de {fmt_numero(limite_validacao, 1)}% de chance "
            f"(calibração {periodo(calib_validacao)}: {pct(deteccao_validacao or 0)} dos focos com aviso Alto ou Crítico).")
    regras_validacao = calibrar(brutas, calib_validacao, *argumentos, limite_alto=limite_validacao)
    validacao = validar(regras_validacao, teste)
    validacao_fma = validar_fma(base, f"desde {min(base.data):%m/%Y}")
    validacao_fma_teste = validar_fma(teste, f"a partir de {INICIO_TESTE:%m/%Y}")
    progresso(95, "Calibração final", f"Calculando as chances com os últimos 12 meses ({periodo(calib_final)})...")
    aprendizado = []
    argumentos = (treino, periodo(treino), periodo(calib_final), sem_regra_propria, excluidos, so_altos)
    limite_alto, deteccao_final = LIMITE_ALTO_PADRAO, None
    if meta_deteccao:
        todas = calibrar(brutas, calib_final, *argumentos, producao=producao, manter_todas=True)
        limite_alto, deteccao_final = limite_para_meta(todas, calib_final, meta_deteccao)
        log(f"Regras gravadas: Alto a partir de {fmt_numero(limite_alto, 1)}% de chance ({periodo(calib_final)}: "
            f"{pct(deteccao_final or 0)} dos focos com aviso Alto ou Crítico).")
    regras = calibrar(brutas, calib_final, *argumentos, producao=producao, aprendizado=aprendizado,
                      limite_alto=limite_alto)
    if aprendizado:
        log(f"Aprendizado com as previsões conferidas: {len(aprendizado)} regra(s) ajustada(s) pelo resultado real.")
        for a in aprendizado:
            if a["nivel_novo"] != a["nivel_antes"]:
                log(f"   {a['regra']}: {a['nivel_antes']} -> {a['nivel_novo']} "
                    f"(acerto real {pct(a['acerto_real'])} em {milhar(a['previsoes'])} previsões)")
    else:
        log(f"Aprendizado: nenhuma regra com {MIN_CASOS_APRENDIZADO} ou mais previsões conferidas ainda.")
    for nivel in reversed(NIVEIS):
        qtd = sum(1 for r in regras if r["GRAU_RISCO"] == nivel)
        if qtd:
            log(f"   Regras gravadas como {nivel}: {qtd}")
    progresso(100, "Concluído", f"{len(regras)} regra(s) geradas em {(time.time() - inicio) / 60:.1f} min.")
    return {
        "regras": regras,
        "validacao": validacao,
        "total_registros": len(base.evento),
        "dias_treino": len(treino.evento),
        "periodo_treino": periodo(treino),
        "anos_excluidos": excluidos,
        "taxas_por_ano": taxas_ano,
        "periodo_calibracao": periodo(calib_final),
        "so_altos": so_altos,
        "validacao_fma": validacao_fma,
        "validacao_fma_teste": validacao_fma_teste,
        "inicio_teste": INICIO_TESTE,
        "raio_km": RAIO_EVENTO_KM,
        "janela_dias": JANELA_DIAS,
        "aprendizado": aprendizado,
        "meta_deteccao": meta_deteccao or None,
        "limite_alto": limite_alto,
        "deteccao_calibracao": deteccao_final,
        "limite_alto_validacao": limite_validacao,
        "deteccao_calibracao_validacao": deteccao_validacao,
        "curva_deteccao": curva,
        "foco_recente": {"raio_km": RAIO_FOCO_RECENTE_KM, "dias": DIAS_FOCO_RECENTE},
        "previsoes_conferidas": len(conferidas),
        "min_casos_aprendizado": MIN_CASOS_APRENDIZADO,
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
    parser.add_argument("--raio", type=int, default=None,
                        help=f"raio do evento em km (padrão {RAIO_EVENTO_KM}; máximo {RAIO_MAXIMO_KM})")
    parser.add_argument("--janela", type=int, default=None,
                        help=f"janela do evento em dias, contando o próprio dia (padrão {JANELA_DIAS})")
    parser.add_argument("--meta", type=float, default=META_DETECCAO,
                        help=f"fração dos focos com aviso Alto ou Crítico antes (padrão {META_DETECCAO}); "
                             "0 = Alto fixo a partir de 30%% de chance")
    parser.add_argument("--so-altos", action="store_true",
                        help="só regras de nível Alto e Crítico, estáveis em todos os anos, priorizando o acerto")
    parser.add_argument("--excluir-anos", nargs="*", type=int, default=None,
                        help="anos fora do aprendizado (ex.: --excluir-anos 2024). Sem a opção, detecta sozinho; "
                             "'--excluir-anos' vazio não exclui nenhum")
    args = parser.parse_args()

    resultado = rodar_treinamento_queimadas(excluir_anos=args.excluir_anos, so_altos=args.so_altos,
                                            raio_km=args.raio, janela_dias=args.janela, meta_deteccao=args.meta)
    if resultado is None:
        return 1
    if args.simular:
        log("Simulação: nenhuma regra gravada. Para gravar, rode sem --simular.")
    else:
        gravar_regras_queimadas(resultado["regras"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
