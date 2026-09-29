# -*- coding: utf-8 -*-
"""
Previsão de risco de queimadas por fazenda -> CS_ALERTAS_PREDICAO (roda no pipeline)

Todo dia, para cada fazenda:
  1) busca na Open-Meteo o clima dos últimos 60 dias e a previsão até +3 dias
     (temperatura máxima, umidade mínima, vento máximo, chuva e umidade/vento das 13h);
  2) calcula os dias seguidos sem chuva e a chuva acumulada em 7 dias;
  3) compara cada dia (amanhã a +3; hoje já vem dos focos detectados) com as regras de CS_EVENTOS_PREDICAO (TIPO_RISCO = 'Queimada'):
     as regras aprendidas do histórico para a UF da fazenda (ou as nacionais, se a UF não tiver regras
     próprias), a regra 30-30-30 e o foco recente (INPE a até 50 km nos últimos 2 dias). A regra mais grave
     que "bate" define o nível, o valor (0 a 100 =
     chance de foco perto da fazenda) e os motivos;
  4) grava/atualiza uma linha por fazenda e dia em CS_ALERTAS_PREDICAO (TIPO_RISCO = 'Queimada');
  5) calcula também o PERIGO METEOROLÓGICO de incêndio (índice FMA+, só clima) e grava com
     TIPO_RISCO = 'Perigo meteorológico' (VALOR = índice FMA+; nível pela classe do índice).

As regras são geradas por modelos/ml_alertas_queimadas_predicao.py (rodar à mão, por exemplo uma vez por mês).
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import math
import logging
import datetime
import oracledb
from auth import USER, PASSWORD, DSN
from servicos.fazendas_clima import buscar_open_meteo, URL_PREVISAO

logger = logging.getLogger(__name__)

TIPO_RISCO = "Queimada"
DIAS_PASSADOS = 60            # histórico recente: dias sem chuva, chuva de 7 dias e a soma do índice FMA+
DIAS_PREVISAO = 4             # a Open-Meteo devolve hoje + 3 dias...
PRIMEIRO_HORIZONTE = 1        # ...mas só amanhã a +3 são gravados: hoje já vem dos focos detectados, e assim a
                              # previsão guardada de cada dia é a feita com antecedência (base do acompanhamento)
FAZENDAS_POR_LOTE = 50
CHUVA_DIA_SECO_MM = 1.0       # dia com menos de 1 mm conta como "sem chuva"
DIARIO_OPEN_METEO = "temperature_2m_max,relative_humidity_2m_min,wind_speed_10m_max,precipitation_sum"

# Níveis pela chance de foco a até 15 km da fazenda no dia ou nos 2 dias seguintes.
# A chance normal fica perto de 10%: Médio ~1,5x, Alto ~3x e Crítico ~5x o normal.
NIVEIS = ["Baixo", "Médio", "Alto", "Crítico"]
# CS_EVENTOS_PREDICAO.GRAU_RISCO só aceita estes valores (restrição CK_PREDICAO_GRAU)
GRAU_BANCO = {"Baixo": "BAIXO", "Médio": "MEDIO", "Alto": "ALTO", "Crítico": "CRITICO"}
LIMIARES_NIVEL = [(50.0, "Crítico"), (30.0, "Alto"), (15.0, "Médio")]

# Regra de ouro: temperatura acima de 30 °C, umidade abaixo de 30% e vento acima de 30 km/h
REGRA_30_30_30 = {"TEMPERATURA": (">", 30.0), "UMIDADE": ("<", 30.0), "VENTO": (">", 30.0)}
NIVEL_MINIMO_30_30_30 = "Alto"
ORIGEM_HISTORICO = "Histórico"
ORIGEM_30_30_30 = "Regra 30-30-30"

# Foco recente: fogo costuma se espalhar e reaparecer na mesma região. Se o INPE detectou foco a até
# RAIO_FOCO_RECENTE_KM da fazenda no dia do cálculo ou no anterior, os próximos dias ficam no mínimo em Alto.
ORIGEM_FOCO_RECENTE = "Foco recente"
RAIO_FOCO_RECENTE_KM = 50
DIAS_FOCO_RECENTE = 2
NIVEL_MINIMO_FOCO_RECENTE = "Alto"
VARIAVEL_FOCO_RECENTE = "FOCO_RECENTE"   # condição interna (não é coluna de CS_EVENTOS_PREDICAO)
TEXTO_FOCO_RECENTE = f"foco do INPE a até {RAIO_FOCO_RECENTE_KM} km nos últimos {DIAS_FOCO_RECENTE} dias"
# Regras gerais (valem para todas as UFs) e o nível mínimo de cada uma
NIVEL_MINIMO_ORIGEM = {ORIGEM_30_30_30: NIVEL_MINIMO_30_30_30, ORIGEM_FOCO_RECENTE: NIVEL_MINIMO_FOCO_RECENTE}

# --- Perigo meteorológico de incêndio: Fórmula de Monte Alegre Alterada (FMA+) ---------------------------
# Índice brasileiro de perigo de incêndio (Nunes, Soares e Batista, 2006). Soma, dia a dia desde a última chuva
# forte, (100 / umidade às 13h) x e^(0,04 x vento às 13h em m/s). A chuva do dia desconta parte da soma.
# Não depende de regras aprendidas: só do clima (observado e previsto).
TIPO_PERIGO_METEOROLOGICO = "Perigo meteorológico"
HORA_13H_UTC = 16                                   # 13h de Brasília
DESCONTO_CHUVA_FMA = [(2.4, 0.0), (4.9, 0.3), (9.9, 0.6), (12.9, 0.8)]   # chuva do dia (mm) -> % abatido da soma
CLASSES_FMA = [(3.0, "Nulo"), (8.0, "Pequeno"), (14.0, "Médio"), (24.0, "Alto"), (float("inf"), "Muito alto")]
NIVEL_DA_CLASSE_FMA = {"Nulo": "Baixo", "Pequeno": "Baixo", "Médio": "Médio", "Alto": "Alto", "Muito alto": "Crítico"}


def calcular_fma(dias):
    """Acrescenta 'fma' e 'classe_fma' a cada dia (em ordem). Usa 'umidade_13h', 'vento_13h' (km/h) e 'chuva'.
    Dia sem umidade/vento das 13h ou sem chuva fica sem índice e a soma recomeça (não dá para saber)."""
    soma = None
    for dia in dias:
        umidade, vento, chuva = dia.get("umidade_13h"), dia.get("vento_13h"), dia.get("chuva")
        if umidade is None or vento is None or chuva is None:
            soma = None
        elif chuva > DESCONTO_CHUVA_FMA[-1][0]:
            soma = 0.0                                  # chuva forte: o índice zera e recomeça no dia seguinte
        else:
            desconto = next(d for limite, d in DESCONTO_CHUVA_FMA if chuva <= limite)
            parcela = 100.0 / max(umidade, 1.0) * math.exp(0.04 * vento / 3.6)
            soma = (soma or 0.0) * (1 - desconto) + parcela
        dia["fma"] = soma
        dia["classe_fma"] = classe_fma(soma)
    return dias


def classe_fma(valor):
    if valor is None:
        return None
    return next(classe for limite, classe in CLASSES_FMA if valor <= limite)


# Variáveis das regras: coluna de CS_EVENTOS_PREDICAO -> (campo do dia, rótulo, unidade)
VARIAVEIS = {
    "TEMPERATURA": ("temperatura_max", "temperatura máxima", "°C"),
    "UMIDADE": ("umidade_min", "umidade mínima", "%"),
    "VENTO": ("vento_max", "vento máximo", "km/h"),
    "PRECIPITACAO": ("chuva_7d", "chuva em 7 dias", "mm"),
    "DIAS": ("dias_sem_chuva", "dias seguidos sem chuva", "dias"),
}
OPERADORES = {
    ">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
}
TEXTO_OPERADOR = {">": "acima de", ">=": "a partir de", "<": "abaixo de", "<=": "até"}

UF_REGIAO = {
    "AC": "Norte", "AM": "Norte", "AP": "Norte", "PA": "Norte", "RO": "Norte", "RR": "Norte", "TO": "Norte",
    "AL": "Nordeste", "BA": "Nordeste", "CE": "Nordeste", "MA": "Nordeste", "PB": "Nordeste", "PE": "Nordeste",
    "PI": "Nordeste", "RN": "Nordeste", "SE": "Nordeste",
    "DF": "Centro-Oeste", "GO": "Centro-Oeste", "MS": "Centro-Oeste", "MT": "Centro-Oeste",
    "ES": "Sudeste", "MG": "Sudeste", "RJ": "Sudeste", "SP": "Sudeste",
    "PR": "Sul", "RS": "Sul", "SC": "Sul",
}


# =============================================================================================
# Funções comuns (também usadas por modelos/ml_alertas_queimadas_predicao.py)
# =============================================================================================

def nivel_por_probabilidade(probabilidade):
    """Probabilidade em % -> nível."""
    for limite, nivel in LIMIARES_NIVEL:
        if probabilidade >= limite:
            return nivel
    return "Baixo"


def nivel_do_banco(valor):
    """'MEDIO' (como está em CS_EVENTOS_PREDICAO) -> 'Médio'. Aceita também o nome já no formato da tela."""
    texto = str(valor or "").strip()
    for nivel, no_banco in GRAU_BANCO.items():
        if texto.upper() in (no_banco, nivel.upper()):
            return nivel
    return None


def nivel_maximo(*niveis):
    return max(niveis, key=lambda n: NIVEIS.index(n) if n in NIVEIS else -1)


def fmt_numero(valor, casas=0):
    return f"{valor:.{casas}f}".replace(".", ",")


def com_unidade(valor, unidade):
    """'30%' (sem espaço) e '33 °C', '20 km/h', '10 dias'."""
    return f"{fmt_numero(valor)}{'' if unidade == '%' else ' '}{unidade}"


def condicoes_da_regra(regra):
    """[(variavel, operador, valor)] das condições preenchidas numa linha de CS_EVENTOS_PREDICAO."""
    condicoes = []
    for variavel in VARIAVEIS:
        operador = regra.get(f"{variavel}_CONDICAO")
        valor = regra.get(f"{variavel}_VALOR")
        if operador in OPERADORES and valor is not None:
            condicoes.append((variavel, operador, float(valor)))
    return condicoes


def descrever_condicoes(condicoes):
    partes = []
    for variavel, operador, valor in condicoes:
        if variavel == VARIAVEL_FOCO_RECENTE:
            partes.append(TEXTO_FOCO_RECENTE)
            continue
        _, rotulo, unidade = VARIAVEIS[variavel]
        partes.append(f"{rotulo} {TEXTO_OPERADOR[operador]} {com_unidade(valor, unidade)}")
    return ", ".join(partes)


def regra_bate(condicoes, dia):
    """True se todas as condições valem para o dia (dict com os campos de VARIAVEIS)."""
    for variavel, operador, valor in condicoes:
        if variavel == VARIAVEL_FOCO_RECENTE:
            if not dia.get("foco_recente"):
                return False
            continue
        atual = dia.get(VARIAVEIS[variavel][0])
        if atual is None or not OPERADORES[operador](atual, valor):
            return False
    return bool(condicoes)


def motivos(condicoes, dia):
    """'umidade mínima 22% (regra: até 30%) | 14 dias seguidos sem chuva (regra: a partir de 10 dias)'."""
    partes = []
    for variavel, operador, valor in condicoes:
        if variavel == VARIAVEL_FOCO_RECENTE:
            partes.append(TEXTO_FOCO_RECENTE[0].upper() + TEXTO_FOCO_RECENTE[1:])
            continue
        campo, rotulo, unidade = VARIAVEIS[variavel]
        partes.append(f"{rotulo} {com_unidade(dia[campo], unidade)} "
                      f"(regra: {TEXTO_OPERADOR[operador]} {com_unidade(valor, unidade)})")
    return " | ".join(partes)


def completar_series(dias):
    """Recebe [{data, temperatura_max, umidade_min, vento_max, chuva}] em ordem de data e acrescenta
    'dias_sem_chuva' (seguidos, até o dia) e 'chuva_7d' (soma do dia e dos 6 anteriores).
    Dia sem informação de chuva zera a contagem (não dá para saber se choveu)."""
    seguidos = 0
    janela = []
    for dia in dias:
        chuva = dia.get("chuva")
        if chuva is None:
            seguidos = 0
        elif chuva < CHUVA_DIA_SECO_MM:
            seguidos += 1
        else:
            seguidos = 0
        dia["dias_sem_chuva"] = seguidos if chuva is not None else None
        janela.append(chuva)
        janela = janela[-7:]
        conhecidos = [c for c in janela if c is not None]
        dia["chuva_7d"] = sum(conhecidos) if len(conhecidos) >= 5 else None
    return dias


class AvaliadorRegras:
    """Escolhe as regras de cada UF e avalia um dia: devolve (valor, nível, regra, motivos)."""

    def __init__(self, regras):
        self.por_uf, self.nacionais, self.ouro = {}, [], []
        for regra in regras:
            regra["condicoes"] = condicoes_da_regra(regra)
            if regra.get("ORIGEM_REGRA") == ORIGEM_FOCO_RECENTE:
                regra["condicoes"] = [(VARIAVEL_FOCO_RECENTE, ">=", 1)]
            if not regra["condicoes"]:
                continue
            if regra.get("ORIGEM_REGRA") in NIVEL_MINIMO_ORIGEM:
                self.ouro.append(regra)   # regras gerais: 30-30-30 e foco recente
            elif regra.get("UF"):
                self.por_uf.setdefault(str(regra["UF"]).strip().upper(), []).append(regra)
            else:
                self.nacionais.append(regra)

    @property
    def tem_foco_recente(self):
        return any(r.get("ORIGEM_REGRA") == ORIGEM_FOCO_RECENTE for r in self.ouro)

    def regras_para(self, uf):
        return self.por_uf.get(str(uf or "").strip().upper()) or self.nacionais

    def avaliar(self, uf, dia):
        melhor = None
        for regra in self.regras_para(uf) + self.ouro:
            if not regra_bate(regra["condicoes"], dia):
                continue
            probabilidade = float(regra.get("PROBABILIDADE") or 0)
            nivel = nivel_do_banco(regra.get("GRAU_RISCO")) or nivel_por_probabilidade(probabilidade)
            chave = (NIVEIS.index(nivel) if nivel in NIVEIS else 0, probabilidade)
            if melhor is None or chave > melhor[0]:
                melhor = (chave, probabilidade, nivel, regra)
        if melhor is None:
            return 0.0, "Baixo", None, "Nenhuma combinação de condições que antecedeu queimadas no histórico."
        _, probabilidade, nivel, regra = melhor
        texto = motivos(regra["condicoes"], dia)
        if regra.get("ORIGEM_REGRA") == ORIGEM_30_30_30:
            texto = "Regra 30-30-30: " + texto
        return probabilidade, nivel, regra, texto

    def chance_maxima(self, uf, dia):
        """(maior chance entre as regras que valem no dia, True se alguma regra geral com nível mínimo vale).
        Usado pelo gerador para achar o limite do Alto que atinge a meta de detecção."""
        chance, piso = 0.0, False
        for regra in self.regras_para(uf) + self.ouro:
            if regra_bate(regra["condicoes"], dia):
                chance = max(chance, float(regra.get("PROBABILIDADE") or 0))
                piso = piso or regra.get("ORIGEM_REGRA") in NIVEL_MINIMO_ORIGEM
        return chance, piso


def distancia_km(lat1, lon1, lat2, lon2):
    dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2)
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def fazendas_com_foco_recente(cursor, fazendas, hoje):
    """IDs das fazendas com foco do INPE a até RAIO_FOCO_RECENTE_KM no dia do cálculo ou nos anteriores
    (DIAS_FOCO_RECENTE dias UTC; inclui os focos ainda 'Aguardando risco', que são os mais novos)."""
    inicio = datetime.datetime.combine(hoje - datetime.timedelta(days=DIAS_FOCO_RECENTE - 1), datetime.time.min)
    cursor.execute("""
        SELECT DISTINCT ROUND(LATITUDE, 2), ROUND(LONGITUDE, 2)
        FROM CS_ALERTAS
        WHERE ORIGEM_ALERTA = 'INPE' AND DATA_HORA >= :inicio - 3/24
          AND LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL
    """, {"inicio": inicio})
    focos = [(float(la), float(lo)) for la, lo in cursor.fetchall()]
    margem = RAIO_FOCO_RECENTE_KM / 111.0 + 0.05
    com_foco = set()
    for f in fazendas:
        for la, lo in focos:
            if (abs(la - f["lat"]) <= margem and abs(lo - f["lon"]) <= margem * 1.2
                    and distancia_km(la, lo, f["lat"], f["lon"]) <= RAIO_FOCO_RECENTE_KM):
                com_foco.add(f["id"])
                break
    return com_foco


# =============================================================================================
# Execução diária (pipeline)
# =============================================================================================

def carregar_regras(cursor):
    cursor.execute("""
        SELECT ID, UF, GRAU_RISCO, TEMPERATURA_CONDICAO, TEMPERATURA_VALOR, UMIDADE_CONDICAO, UMIDADE_VALOR,
               VENTO_CONDICAO, VENTO_VALOR, PRECIPITACAO_CONDICAO, PRECIPITACAO_VALOR,
               DIAS_CONDICAO, DIAS_VALOR, PROBABILIDADE, ORIGEM_REGRA
        FROM CS_EVENTOS_PREDICAO
        WHERE TIPO_RISCO = :1
    """, [TIPO_RISCO])
    colunas = [c[0] for c in cursor.description]
    return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def clima_diario(resposta):
    """Resposta da Open-Meteo -> lista de dias em ordem (valores ausentes ficam None), com as séries de chuva,
    a umidade e o vento das 13h de Brasília e o índice FMA+."""
    horario = (resposta or {}).get("hourly", {})
    as_13h = {}
    for i, momento in enumerate(horario.get("time", [])):
        if momento.endswith(f"T{HORA_13H_UTC:02d}:00"):
            umidades, ventos = horario.get("relative_humidity_2m", []), horario.get("wind_speed_10m", [])
            as_13h[momento[:10]] = (umidades[i] if i < len(umidades) else None,
                                    ventos[i] if i < len(ventos) else None)
    diario = (resposta or {}).get("daily", {})
    datas = diario.get("time", [])
    colunas = {campo: diario.get(nome, []) for campo, nome in (
        ("temperatura_max", "temperature_2m_max"), ("umidade_min", "relative_humidity_2m_min"),
        ("vento_max", "wind_speed_10m_max"), ("chuva", "precipitation_sum"))}
    dias = []
    for i, data in enumerate(datas):
        dia = {"data": datetime.date.fromisoformat(data)}
        for campo, valores in colunas.items():
            dia[campo] = valores[i] if i < len(valores) and valores[i] is not None else None
        dia["umidade_13h"], dia["vento_13h"] = as_13h.get(data, (None, None))
        dias.append(dia)
    return calcular_fma(completar_series(dias))


SQL_MERGE = """
    MERGE INTO CS_ALERTAS_PREDICAO d
    USING (SELECT :id_fazenda AS ID_FAZENDA, :tipo AS TIPO_RISCO, :data_ref AS DATA_REFERENCIA FROM DUAL) s
    ON (d.ID_FAZENDA = s.ID_FAZENDA AND d.TIPO_RISCO = s.TIPO_RISCO AND d.DATA_REFERENCIA = s.DATA_REFERENCIA)
    WHEN MATCHED THEN UPDATE SET
        HORIZONTE_DIAS = :horizonte, VALOR = :valor, NIVEL = :nivel, ID_REGRA = :id_regra, MOTIVOS = :motivos,
        TEMPERATURA_MAX = :tmax, UMIDADE_MIN = :umin, VENTO_MAX = :vmax, CHUVA_7D = :chuva7,
        DIAS_SEM_CHUVA = :dias_seco, DATA_CALCULO = SYSTIMESTAMP
    WHEN NOT MATCHED THEN INSERT
        (ID_FAZENDA, TIPO_RISCO, DATA_REFERENCIA, HORIZONTE_DIAS, VALOR, NIVEL, ID_REGRA, MOTIVOS,
         TEMPERATURA_MAX, UMIDADE_MIN, VENTO_MAX, CHUVA_7D, DIAS_SEM_CHUVA, DATA_CALCULO)
    VALUES (:id_fazenda, :tipo, :data_ref, :horizonte, :valor, :nivel, :id_regra, :motivos,
            :tmax, :umin, :vmax, :chuva7, :dias_seco, SYSTIMESTAMP)
"""


def motivos_fma(dia):
    """'Índice FMA+ 27,4 (Muito alto): umidade às 13h 18%, vento às 13h 22 km/h, chuva do dia 0 mm...'."""
    return (f"Índice FMA+ {fmt_numero(dia['fma'], 1)} ({dia['classe_fma']}): umidade às 13h "
            f"{fmt_numero(dia['umidade_13h'])}%, vento às 13h {fmt_numero(dia['vento_13h'])} km/h, "
            f"chuva do dia {fmt_numero(dia['chuva'] or 0, 1)} mm, {dia['dias_sem_chuva']} dia(s) seguido(s) sem chuva.")


def arredondar(valor, casas=1):
    return round(float(valor), casas) if valor is not None else None


def executar():
    logger.info("[PREVISÃO - QUEIMADAS] Calculando o risco de queimadas das fazendas (próximos 3 dias)...")
    connection = None
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()

        regras = carregar_regras(cursor)
        if not regras:
            msg = ("Nenhuma regra de queimada em CS_EVENTOS_PREDICAO. "
                   "Gere as regras com: python modelos/ml_alertas_queimadas_predicao.py")
            logger.warning(f"   -> [AVISO] {msg}")
            return "ALERTA", msg, 0
        avaliador = AvaliadorRegras(regras)
        logger.info(f"   -> {len(regras)} regra(s) carregada(s): {len(avaliador.por_uf)} UF(s) com regras próprias, "
                    f"{len(avaliador.nacionais)} nacional(is), {len(avaliador.ouro)} regra(s) geral(is) (30-30-30 e foco recente).")

        cursor.execute("""
            SELECT ID, NOME_FAZENDA, ESTADO, LATITUDE, LONGITUDE FROM CS_FAZENDAS
            WHERE LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL ORDER BY ID
        """)
        fazendas = [{"id": i, "nome": n, "uf": uf, "lat": float(la), "lon": float(lo)}
                    for i, n, uf, la, lo in cursor.fetchall()]
        if not fazendas:
            return "ALERTA", "Nenhuma fazenda com coordenadas em CS_FAZENDAS.", 0

        hoje = datetime.datetime.now(datetime.timezone.utc).date()
        com_foco_recente = fazendas_com_foco_recente(cursor, fazendas, hoje) if avaliador.tem_foco_recente else set()
        if avaliador.tem_foco_recente:
            logger.info(f"   -> {len(com_foco_recente)} fazenda(s) com {TEXTO_FOCO_RECENTE} (nível mínimo "
                        f"{NIVEL_MINIMO_FOCO_RECENTE} nos próximos dias).")
        registros, sem_clima = [], []
        contagem, contagem_fma = {}, {}
        for inicio in range(0, len(fazendas), FAZENDAS_POR_LOTE):
            lote = fazendas[inicio:inicio + FAZENDAS_POR_LOTE]
            respostas = buscar_open_meteo(URL_PREVISAO, {
                "latitude": ",".join(f"{f['lat']:.4f}" for f in lote),
                "longitude": ",".join(f"{f['lon']:.4f}" for f in lote),
                "daily": DIARIO_OPEN_METEO,
                "hourly": "relative_humidity_2m,wind_speed_10m",
                "past_days": DIAS_PASSADOS,
                "forecast_days": DIAS_PREVISAO,
            }, f"previsão diária de {len(lote)} fazenda(s)")
            if respostas is None or len(respostas) != len(lote):
                sem_clima.extend(f["nome"] for f in lote)
                continue
            for fazenda, resposta in zip(lote, respostas):
                for dia in clima_diario(resposta):
                    horizonte = (dia["data"] - hoje).days
                    if horizonte < PRIMEIRO_HORIZONTE or horizonte >= DIAS_PREVISAO:
                        continue
                    dia["foco_recente"] = fazenda["id"] in com_foco_recente
                    valor, nivel, regra, texto = avaliador.avaliar(fazenda["uf"], dia)
                    contagem[(horizonte, nivel)] = contagem.get((horizonte, nivel), 0) + 1
                    if dia.get("fma") is not None:
                        # Perigo meteorológico (FMA+): só clima, sem regras aprendidas
                        nivel_fma = NIVEL_DA_CLASSE_FMA[dia["classe_fma"]]
                        contagem_fma[(horizonte, nivel_fma)] = contagem_fma.get((horizonte, nivel_fma), 0) + 1
                        registros.append({
                            "id_fazenda": fazenda["id"], "tipo": TIPO_PERIGO_METEOROLOGICO,
                            "data_ref": datetime.datetime.combine(dia["data"], datetime.time.min),
                            "horizonte": horizonte, "valor": arredondar(dia["fma"]), "nivel": nivel_fma,
                            "id_regra": None, "motivos": motivos_fma(dia)[:1000],
                            "tmax": arredondar(dia["temperatura_max"]), "umin": arredondar(dia["umidade_min"]),
                            "vmax": arredondar(dia["vento_max"]), "chuva7": arredondar(dia["chuva_7d"]),
                            "dias_seco": dia["dias_sem_chuva"],
                        })
                    registros.append({
                        "id_fazenda": fazenda["id"], "tipo": TIPO_RISCO,
                        "data_ref": datetime.datetime.combine(dia["data"], datetime.time.min),
                        "horizonte": horizonte, "valor": arredondar(valor), "nivel": nivel,
                        "id_regra": regra["ID"] if regra else None, "motivos": texto[:1000],
                        "tmax": arredondar(dia["temperatura_max"]), "umin": arredondar(dia["umidade_min"]),
                        "vmax": arredondar(dia["vento_max"]), "chuva7": arredondar(dia["chuva_7d"]),
                        "dias_seco": dia["dias_sem_chuva"],
                    })
            logger.info(f"   -> Clima e regras avaliados: {min(inicio + FAZENDAS_POR_LOTE, len(fazendas))}/{len(fazendas)} fazendas")

        if registros:
            cursor.executemany(SQL_MERGE, registros)
            connection.commit()

        resumo = []
        for horizonte in range(PRIMEIRO_HORIZONTE, DIAS_PREVISAO):
            data = hoje + datetime.timedelta(days=horizonte)
            partes = [f"{contagem[(horizonte, n)]} {n}" for n in reversed(NIVEIS) if contagem.get((horizonte, n))]
            resumo.append(f"{data:%d/%m}: {', '.join(partes) or 'sem dados'}")
            partes_fma = [f"{contagem_fma[(horizonte, n)]} {n}" for n in reversed(NIVEIS) if contagem_fma.get((horizonte, n))]
            logger.info(f"   -> {data:%d/%m/%Y}: regras de queimada: {', '.join(partes) or 'sem dados'} | "
                        f"perigo meteorológico (FMA+): {', '.join(partes_fma) or 'sem dados'}")
        msg = (f"Risco de queimadas calculado para {len(fazendas) - len(sem_clima)} fazenda(s), próximos 3 dias. "
               + " | ".join(resumo))
        if sem_clima:
            msg += f" | Sem clima da Open-Meteo: {len(sem_clima)} fazenda(s)."
            logger.warning(f"   -> [AVISO] Sem clima para {len(sem_clima)} fazenda(s): {', '.join(sem_clima[:5])}...")
        status = "SUCESSO" if len(sem_clima) < len(fazendas) else "ERRO"
        return status, msg[:4000], len(registros)

    except Exception as e:
        if connection:
            connection.rollback()
        msg = f"Falha no cálculo do risco de queimadas: {e}"
        logger.error(f"[ERRO GERAL] {msg}")
        return "ERRO", msg, 0
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    status, mensagem, qtd = executar()
    print(f"\n{status}: {mensagem}")
