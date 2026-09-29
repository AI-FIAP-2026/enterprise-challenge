# -*- coding: utf-8 -*-
"""
Risco de chuva por fazenda: HIDROLÓGICO (inundação, enxurrada, alagamento, chuva intensa) e DESLIZAMENTO
(movimento de massa: deslizamento, corrida de massa, queda de blocos).

As regras são aprendidas por modelos/ml_alertas_chuva_predicao.py e gravadas em CS_EVENTOS_PREDICAO, uma por linha:
  chuva acumulada numa janela (do dia, 72 horas, 7 dias ou 30 dias) a partir de um limite (mm) e, no deslizamento,
  a declividade mínima da fazenda. Cada regra guarda a chance de evento no histórico (PROBABILIDADE), os dias
  observados (CASOS), o nível e a explicação (DESCRICAO_REGRA, com o ganho sobre a chance normal).
  Nível pelo ganho: 8 vezes a chance normal ou mais = Crítico; 3 vezes = Alto; 1,5 vez = Médio.

Usado por:
  servicos/alertas_chuva_predicao.py   previsão de amanhã a +3 dias (CS_ALERTAS_PREDICAO, Central de Alertas);
  requisitos/score_risco.py            projeção de 1 ano: % dos dias com risco Alto ou Crítico pelas regras;
  requisitos/score_tendencias.py       o mesmo cálculo, mês a mês.
"""
import datetime

TIPO_HIDROLOGICO, TIPO_DESLIZAMENTO = "Hidrológico", "Deslizamento"
TIPOS = {
    TIPO_HIDROLOGICO: {
        # COBRADE da Defesa Civil (S2iD): inundações, enxurradas, alagamentos e chuvas intensas
        "cobrade": ("12100", "12200", "12300", "13214"),
        "cemaden": "Risco Hidrológico",
        "usa_declividade": False,
        "evento": "inundação, enxurrada, alagamento ou chuva intensa registrados no município (Defesa Civil) "
                  "ou alerta hidrológico do CEMADEN",
    },
    TIPO_DESLIZAMENTO: {
        # quedas, tombamentos e rolamentos (11311-11314), deslizamentos (11321), corridas de massa (11331, 11332)
        "cobrade": ("11311", "11312", "11313", "11314", "11321", "11331", "11332"),
        "cemaden": "Movimentos de Massa",
        "usa_declividade": True,
        "evento": "deslizamento, corrida de massa ou queda de blocos registrados no município (Defesa Civil) "
                  "ou alerta de movimento de massa do CEMADEN",
    },
}
JANELAS = {1: "chuva do dia", 3: "chuva em 72 horas", 7: "chuva em 7 dias", 30: "chuva em 30 dias"}
DIAS_TIPO_JANELA = "JANELA_CHUVA"
FRACAO_MINIMA_DIAS = 0.8          # janela com menos dias de clima conhecidos que isso fica sem valor
NIVEIS = ["Baixo", "Médio", "Alto", "Crítico"]
NIVEIS_RISCO = ("Alto", "Crítico")  # nível que conta como "dia de risco" no score e gera aviso na Central de Alertas
GANHO_NIVEL = [(8.0, "Crítico"), (3.0, "Alto"), (1.5, "Médio")]
GRAU_BANCO = {"Baixo": "BAIXO", "Médio": "MEDIO", "Alto": "ALTO", "Crítico": "CRITICO"}


def nivel_pelo_ganho(ganho):
    for limite, nivel in GANHO_NIVEL:
        if ganho >= limite:
            return nivel
    return "Baixo"


def nivel_do_banco(valor):
    texto = str(valor or "").strip().upper()
    for nivel, no_banco in GRAU_BANCO.items():
        if texto in (no_banco, nivel.upper()):
            return nivel
    return "Baixo"


def _num(valor, casas=0):
    return f"{valor:.{casas}f}".replace(".", ",")


def texto_chance(valor):
    """Chance em %, com casas suficientes para eventos raros (0,05% em vez de 0,0%)."""
    if valor is None:
        return "-"
    valor = float(valor)
    casas = 0 if valor >= 10 else 1 if valor >= 1 else 2 if valor >= 0.01 else 3
    return f"{valor:.{casas}f}%".replace(".", ",")


def acumulados(chuva, dia):
    """{janela: mm acumulados até o dia (inclusive)} a partir de {data: mm}. Janela com poucos dias conhecidos: None."""
    saida = {}
    for janela in JANELAS:
        valores = [chuva.get(dia - datetime.timedelta(days=k)) for k in range(janela)]
        conhecidos = [v for v in valores if v is not None]
        saida[janela] = sum(conhecidos) if len(conhecidos) >= FRACAO_MINIMA_DIAS * janela else None
    return saida


def regra_de_linha(linha):
    """Linha de CS_EVENTOS_PREDICAO -> regra {id, tipo, janela, limite, declividade, chance, casos, nivel}."""
    janela = linha.get("DIAS_VALOR")
    limite = linha.get("PRECIPITACAO_VALOR")
    if janela is None or limite is None:
        return None
    return {"id": linha.get("ID"), "tipo": linha.get("TIPO_RISCO"), "janela": int(janela), "limite": float(limite),
            "declividade": None if linha.get("INCLINACAO_VALOR") is None else float(linha["INCLINACAO_VALOR"]),
            "chance": float(linha.get("PROBABILIDADE") or 0), "casos": linha.get("CASOS"),
            "nivel": nivel_do_banco(linha.get("GRAU_RISCO")), "uf": linha.get("UF")}


def carregar_regras(cursor, tipo):
    """Regras do tipo gravadas em CS_EVENTOS_PREDICAO ([] se a tabela ou as regras não existem)."""
    try:
        cursor.execute("""
            SELECT ID, TIPO_RISCO, UF, GRAU_RISCO, PRECIPITACAO_VALOR, DIAS_VALOR, INCLINACAO_VALOR, PROBABILIDADE,
                   CASOS
            FROM CS_EVENTOS_PREDICAO WHERE TIPO_RISCO = :tipo AND DIAS_TIPO = :janela
        """, {"tipo": tipo, "janela": DIAS_TIPO_JANELA})
        colunas = [c[0] for c in cursor.description]
        linhas = [dict(zip(colunas, l)) for l in cursor.fetchall()]
    except Exception:
        return []
    return [r for r in (regra_de_linha(l) for l in linhas) if r]


def descrever(regra):
    texto = f"{JANELAS[regra['janela']]} a partir de {_num(regra['limite'])} mm"
    if regra.get("declividade"):
        texto += f" e declividade a partir de {_num(regra['declividade'])}%"
    return texto


def regra_vale(regra, acum, declividade):
    valor = acum.get(regra["janela"])
    if valor is None or valor < regra["limite"]:
        return False
    if regra.get("declividade") is not None:
        return declividade is not None and float(declividade) >= regra["declividade"]
    return True


def avaliar(regras, acum, declividade, uf=None):
    """Regra mais grave (e de maior chance) que vale no dia: (nível, chance %, regra, motivo). Sem regra: Baixo."""
    candidatas = [r for r in regras if not r.get("uf") or str(r["uf"]).upper() == str(uf or "").upper()]
    melhor = None
    for regra in candidatas:
        if not regra_vale(regra, acum, declividade):
            continue
        chave = (NIVEIS.index(regra["nivel"]), regra["chance"])
        if melhor is None or chave > melhor[0]:
            melhor = (chave, regra)
    if melhor is None:
        return "Baixo", 0.0, None, "Chuva abaixo das combinações que antecederam eventos no histórico."
    regra = melhor[1]
    motivo = (f"{JANELAS[regra['janela']]} {_num(acum[regra['janela']])} mm (regra: a partir de "
              f"{_num(regra['limite'])} mm)")
    if regra.get("declividade") is not None:
        motivo += f" | declividade {_num(float(declividade))}% (regra: a partir de {_num(regra['declividade'])}%)"
    return regra["nivel"], regra["chance"], regra, motivo


def dias_de_risco(regras, chuva, declividade, dias, uf=None):
    """Dias (da lista) em que alguma regra dá nível Alto ou Crítico."""
    if not regras:
        return set()
    return {d for d in dias if avaliar(regras, acumulados(chuva, d), declividade, uf)[0] in NIVEIS_RISCO}


def chuva_diaria(cursor, id_fazenda, inicio, fim, consultar):
    """{data: mm} da fazenda entre inicio e fim (datas), somando a chuva horária de CS_FAZENDAS_CLIMA.
    consultar(cursor, sql, parametros) -> lista de dicts (score_risco._consultar)."""
    from requisitos.programacao_manutencao import para_data
    linhas = consultar(cursor, """
        SELECT TRUNC(DATA_HORA) AS DIA, SUM(NVL(PRECIPITACAO, 0)) AS CHUVA FROM CS_FAZENDAS_CLIMA
        WHERE ID_FAZENDA = :fazenda AND DATA_HORA >= :inicio AND DATA_HORA < :fim
        GROUP BY TRUNC(DATA_HORA)
    """, {"fazenda": int(id_fazenda), "inicio": datetime.datetime.combine(inicio, datetime.time()),
          "fim": datetime.datetime.combine(fim + datetime.timedelta(days=1), datetime.time())})
    saida = {}
    for l in linhas:
        dia = l["DIA"]
        dia = datetime.date.fromisoformat(dia[:10]) if isinstance(dia, str) else para_data(dia)
        saida[dia] = float(l["CHUVA"] or 0)
    return saida
