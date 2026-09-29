# -*- coding: utf-8 -*-
"""
Tendências de risco (página Score Risk): evolução no tempo e cortes por região, equipamento e tipo de operação.

1. Evolução do score: histórico gravado em CS_SCORE_GESTAO (um score por cliente e dia; o serviço
   servicos/score_risco_diario.py calcula uma vez por dia pelo pipeline). Dá a variação desde o cálculo anterior
   e os alertas de piora (subiu de classe ou o score aumentou SUBIDA_ALERTA ou mais).
2. Risco ambiental mês a mês por região: para cada fazenda e mês dos últimos MESES_TENDENCIA meses, a % dos dias
   com foco do INPE a até 15 km (queimadas), com alerta do CEMADEN no município (hidrológico) e com chuva em
   72 horas acima do limite do relevo (climático) — as bases dos itens ambientais do Score, mês a mês. A região
   (estado) é a média das suas fazendas.
3. Equipamentos e operação: por máquina, a realização e o atendimento das revisões (comprovacao.py), os sinistros
   de 5 anos e o valor segurado, para agrupar por tipo de equipamento, modelo, tipo de operação ou estado.
"""
import math
import datetime

from requisitos import score_risco as sr
from requisitos import risco_chuva as rc
from requisitos import comprovacao as cp
from requisitos.recomendacoes import eh_manutencao
from requisitos.programacao_manutencao import para_data

MESES_TENDENCIA = 12
SUBIDA_ALERTA = 7.5              # aumento do score (pontos, 0 a 100) que gera alerta, mesmo sem mudar de classe
MINUTOS_MESMO_CALCULO = 10       # linhas do mesmo cliente gravadas neste intervalo são o mesmo cálculo
DIAS_MINIMOS_MES_CLIMA = 15      # mês com menos dias de clima fica sem valor no climático
ORDEM_CLASSE = {"Baixo": 1, "Médio": 2, "Alto": 3}


def _dt(valor):
    if isinstance(valor, str):
        return datetime.datetime.fromisoformat(valor[:19])
    if isinstance(valor, datetime.datetime):
        return valor
    return datetime.datetime.combine(valor, datetime.time())


# ---------------------------------------------------------------------------
# 1. Evolução do score
# ---------------------------------------------------------------------------
def historico_scores(conn):
    """Um score por cliente e dia (o último cálculo do dia): [{"id_cliente", "dia", "data", "score", "classe",
    "notas" {chave: nota média das fazendas}}], em ordem de data."""
    colunas = list(sr.COLUNAS_GESTAO.items())
    with conn.cursor() as cursor:
        filtro, binds = sr.filtro_versao_atual(cursor)
        linhas = sr._consultar(cursor, f"""
            SELECT f.ID_CLIENTE, g.DATA_CALCULO, g.SCORE_RISCO_TOTAL, g.CLASSIFICACAO_RISCO,
                   {', '.join('g.' + c for _, c in colunas)}
            FROM CS_SCORE_GESTAO g JOIN CS_FAZENDAS f ON f.ID = g.ID_FAZENDA
            WHERE 1 = 1{filtro}
            ORDER BY f.ID_CLIENTE, g.DATA_CALCULO
        """, binds)
    por_dia = {}
    for l in linhas:
        l["DATA_CALCULO"] = _dt(l["DATA_CALCULO"])
        por_dia.setdefault((l["ID_CLIENTE"], l["DATA_CALCULO"].date()), []).append(l)
    resultado = []
    for (cliente, dia), grupo in por_dia.items():
        ultimo = max(l["DATA_CALCULO"] for l in grupo)
        grupo = [l for l in grupo if (ultimo - l["DATA_CALCULO"]).total_seconds() <= MINUTOS_MESMO_CALCULO * 60]
        notas = {}
        for chave, coluna in colunas:
            valores = [float(l[coluna]) for l in grupo if l[coluna] is not None]
            notas[chave] = sum(valores) / len(valores) if valores else None
        resultado.append({"id_cliente": cliente, "dia": dia, "data": ultimo,
                          "score": float(grupo[-1]["SCORE_RISCO_TOTAL"]), "classe": grupo[-1]["CLASSIFICACAO_RISCO"],
                          "notas": notas})
    return sorted(resultado, key=lambda r: (r["dia"], r["id_cliente"]))


def situacao_atual(historico):
    """Último e penúltimo score de cada cliente: {id_cliente: {"atual": registro, "anterior": registro ou None,
    "variacao": float ou None, "alerta": texto ou None}}."""
    por_cliente = {}
    for r in historico:
        por_cliente.setdefault(r["id_cliente"], []).append(r)
    saida = {}
    for cliente, registros in por_cliente.items():
        atual = registros[-1]
        anterior = registros[-2] if len(registros) > 1 else None
        variacao = atual["score"] - anterior["score"] if anterior else None
        alerta = None
        if anterior and ORDEM_CLASSE.get(atual["classe"], 0) > ORDEM_CLASSE.get(anterior["classe"], 0):
            alerta = f"subiu de {anterior['classe']} para {atual['classe']}"
        elif variacao is not None and variacao >= SUBIDA_ALERTA:
            alerta = f"score subiu {variacao:.1f} pontos".replace(".", ",")
        saida[cliente] = {"atual": atual, "anterior": anterior, "variacao": variacao, "alerta": alerta}
    return saida


# ---------------------------------------------------------------------------
# 2. Risco ambiental mês a mês
# ---------------------------------------------------------------------------
def _meses(hoje, quantidade):
    """Primeiro dia dos últimos `quantidade` meses (o atual incluído), do mais antigo ao mais novo."""
    ano, mes = hoje.year, hoje.month
    lista = []
    for _ in range(quantidade):
        lista.append(datetime.date(ano, mes, 1))
        ano, mes = (ano - 1, 12) if mes == 1 else (ano, mes - 1)
    return lista[::-1]


def _dias_no_mes(inicio_mes, hoje):
    proximo = (inicio_mes.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)
    return (min(proximo, hoje + datetime.timedelta(days=1)) - inicio_mes).days


def risco_mensal_fazenda(cursor, fazenda, hoje, meses=MESES_TENDENCIA, regras_chuva=None):
    """[{"mes", "queimadas", "hidrologico", "climatico"}]: % dos dias do mês com cada condição (None = sem dados).
    Hidrológico e climático usam as regras de chuva (requisitos/risco_chuva.py), como o score; sem regras, os alertas
    do CEMADEN e a regra fixa de chuva em 72 horas pelo relevo."""
    regras_chuva = regras_chuva or {}
    regras_hidro = regras_chuva.get(rc.TIPO_HIDROLOGICO) or []
    lista_meses = _meses(hoje, meses)
    inicio = lista_meses[0]
    inicio_dt = datetime.datetime.combine(inicio, datetime.time())
    focos, alertas, chuva = set(), set(), {}

    lat, lon = fazenda.get("LATITUDE"), fazenda.get("LONGITUDE")
    if lat is not None and lon is not None:
        lat, lon = float(lat), float(lon)
        dlat = sr.RAIO_QUEIMADA_KM / 111.32
        dlon = sr.RAIO_QUEIMADA_KM / (111.32 * max(math.cos(math.radians(lat)), 0.1))
        for f in sr._consultar(cursor, """
                SELECT DATA_HORA, LATITUDE, LONGITUDE FROM CS_ALERTAS
                WHERE ORIGEM_ALERTA = 'INPE' AND DATA_HORA >= :inicio
                  AND LATITUDE BETWEEN :lat1 AND :lat2 AND LONGITUDE BETWEEN :lon1 AND :lon2
                """, {"inicio": inicio_dt, "lat1": lat - dlat, "lat2": lat + dlat, "lon1": lon - dlon,
                      "lon2": lon + dlon}):
            if sr._distancia_km(lat, lon, float(f["LATITUDE"]), float(f["LONGITUDE"])) <= sr.RAIO_QUEIMADA_KM:
                focos.add(sr._data(f["DATA_HORA"]))

    ibge = str(fazenda.get("CODIGO_IBGE") or "").strip()
    if ibge and not regras_hidro:
        for l in sr._consultar(cursor, """
                SELECT DISTINCT TRUNC(DATA_HORA) AS DIA FROM CS_ALERTAS
                WHERE ORIGEM_ALERTA = 'CEMADEN' AND DATA_HORA >= :inicio AND DETALHAMENTO_1 LIKE :padrao
                """, {"inicio": inicio_dt, "padrao": f"%IBGE: {ibge}%"}):
            alertas.add(sr._data(l["DIA"]))

    for l in sr._consultar(cursor, """
            SELECT TRUNC(DATA_HORA) AS DIA, SUM(NVL(PRECIPITACAO, 0)) AS CHUVA FROM CS_FAZENDAS_CLIMA
            WHERE ID_FAZENDA = :fazenda AND DATA_HORA >= :inicio AND DATA_HORA < :fim
            GROUP BY TRUNC(DATA_HORA)
            """, {"fazenda": int(fazenda["ID"]), "inicio": inicio_dt - datetime.timedelta(days=30),
                  "fim": datetime.datetime.combine(hoje + datetime.timedelta(days=1), datetime.time())}):
        chuva[sr._data(l["DIA"])] = float(l["CHUVA"] or 0)
    dias_clima = sorted(d for d in chuva if d >= inicio)
    risco_clima = sr.dias_climaticos(chuva, dias_clima, fazenda, regras_chuva.get(rc.TIPO_DESLIZAMENTO))[0]
    risco_hidro = rc.dias_de_risco(regras_hidro, chuva, None, dias_clima, fazenda.get("ESTADO")) if regras_hidro \
        else None

    saida = []
    for mes in lista_meses:
        total = _dias_no_mes(mes, hoje)
        dias = [mes + datetime.timedelta(days=n) for n in range(total)]
        com_clima = [d for d in dias if d in chuva]
        clima_ok = len(com_clima) >= min(DIAS_MINIMOS_MES_CLIMA, total)
        if risco_hidro is not None:
            hidro = sum(1 for d in com_clima if d in risco_hidro) / len(com_clima) * 100 if clima_ok else None
        else:
            hidro = None if not ibge else sum(1 for d in dias if d in alertas) / total * 100
        saida.append({
            "mes": mes,
            "queimadas": None if lat is None or lon is None else sum(1 for d in dias if d in focos) / total * 100,
            "hidrologico": hidro,
            "climatico": sum(1 for d in com_clima if d in risco_clima) / len(com_clima) * 100 if clima_ok else None,
        })
    return saida


def risco_mensal_por_estado(conn, ids_fazendas, hoje=None, meses=MESES_TENDENCIA):
    """[{"mes", "estado", "risco", "percentual", "fazendas"}]: média das fazendas de cada estado, mês a mês."""
    hoje = hoje or datetime.date.today()
    if not ids_fazendas:
        return []
    with conn.cursor() as cursor:
        extras = [c for c in ("CODIGO_IBGE", "DECLIVIDADE_PCT") if c in sr._colunas(cursor, "CS_FAZENDAS")]
        binds = {f"f{n}": int(v) for n, v in enumerate(ids_fazendas)}
        fazendas = sr._consultar(cursor, f"""
            SELECT ID, ESTADO, LATITUDE, LONGITUDE{''.join(', ' + c for c in extras)} FROM CS_FAZENDAS
            WHERE ID IN ({', '.join(':' + b for b in binds)})
        """, binds)
        regras_chuva = {tipo: rc.carregar_regras(cursor, tipo) for tipo in rc.TIPOS}
        acumulado = {}
        for f in fazendas:
            for linha in risco_mensal_fazenda(cursor, f, hoje, meses, regras_chuva):
                for risco in ("queimadas", "hidrologico", "climatico"):
                    if linha[risco] is not None:
                        acumulado.setdefault((linha["mes"], f["ESTADO"] or "-", risco), []).append(linha[risco])
    nomes = {"queimadas": "Queimadas", "hidrologico": "Hidrológico", "climatico": "Climático"}
    return [{"mes": mes, "estado": estado, "risco": nomes[risco], "percentual": sum(v) / len(v), "fazendas": len(v)}
            for (mes, estado, risco), v in sorted(acumulado.items())]


# ---------------------------------------------------------------------------
# 3. Equipamentos e tipo de operação
# ---------------------------------------------------------------------------
def indicadores_equipamentos(conn, ids_clientes, hoje=None):
    """Uma linha por máquina ativa dos clientes: tipo, modelo, operação, estado, valor segurado, realização,
    atendimento, nota do cumprimento, sinistros e indenização de 5 anos."""
    hoje = hoje or datetime.date.today()
    if not ids_clientes:
        return []
    with conn.cursor() as cursor:
        col_equip = sr._colunas(cursor, "CS_EQUIPAMENTOS_SEGURADOS")
        tem_operacao = "TIPO_OPERACAO" in col_equip
        uso = [c for c in ("DATA_AQUISICAO", "HORIMETRO_ATUAL", "HODOMETRO_ATUAL", "DATA_LEITURA",
                           "USO_MEDIO_HORAS_MES", "USO_MEDIO_KM_MES", "TIPO_TRACAO", "DATA_INICIO_VIGENCIA")
               if c in col_equip]
        binds = {f"c{n}": int(v) for n, v in enumerate(ids_clientes)}
        equipamentos = sr._consultar(cursor, f"""
            SELECT e.ID, e.VALOR_SEGURADO, e.STATUS, e.ID_FAZENDA, f.ID_CLIENTE, f.NOME_FAZENDA, f.ESTADO,
                   mo.TIPO, mo.FABRICANTE, mo.MODELO{', e.TIPO_OPERACAO' if tem_operacao else ''}
            FROM CS_EQUIPAMENTOS_SEGURADOS e
            JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
            JOIN CS_EQUIPAMENTOS_MODELOS mo ON mo.ID = e.ID_EQUIPAMENTO_MODELO
            WHERE f.ID_CLIENTE IN ({', '.join(':' + b for b in binds)})
        """, binds)
        equipamentos = [e for e in equipamentos if str(e.get("STATUS") or "ATIVO").upper().startswith("ATIV")]
        if not equipamentos:
            return []
        novas = [c for c in ("REGRA", "ID_COMPROVANTE", "MOTIVO_AVALIACAO")
                 if c in sr._colunas(cursor, "CS_MANUTENCOES_REALIZADAS")]
        atividades = sr._consultar(cursor, f"""
            SELECT m.ID, m.ID_EQUIPAMENTO_SEGURADO, m.ID_ORIENTACAO, m.DATA_PREVISTA, m.DATA_REALIZADA,
                   m.COMPROVANTE_ENTREGUE, m.ATENDE_CRITERIOS{''.join(', m.' + c for c in novas)},
                   o.TIPO_ORIENTACAO, o.METRICA_GATILHO, o.VALOR_GATILHO, o.UNIDADE_MEDIDA, o.FATOR_CONDICIONAL
                   {''.join(', e.' + c for c in uso)}
            FROM CS_MANUTENCOES_REALIZADAS m
            JOIN CS_EQUIPAMENTOS_SEGURADOS e ON e.ID = m.ID_EQUIPAMENTO_SEGURADO
            JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
            LEFT JOIN CS_EQUIPAMENTOS_ORIENTACOES o ON o.ID = m.ID_ORIENTACAO
            WHERE f.ID_CLIENTE IN ({', '.join(':' + b for b in binds)})
        """, binds)
        sinistros = sr._consultar(cursor, f"""
            SELECT s.ID_EQUIPAMENTO_SEGURADO, COUNT(*) AS QTD, NVL(SUM(s.VALOR_INDENIZACAO), 0) AS TOTAL
            FROM CS_SINISTROS s
            JOIN CS_EQUIPAMENTOS_SEGURADOS e ON e.ID = s.ID_EQUIPAMENTO_SEGURADO
            JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
            WHERE f.ID_CLIENTE IN ({', '.join(':' + b for b in binds)}) AND s.DATA_OCORRENCIA >= :desde
            GROUP BY s.ID_EQUIPAMENTO_SEGURADO
        """, dict(binds, desde=datetime.datetime.combine(
            hoje - datetime.timedelta(days=365 * sr.ANOS_SINISTROS), datetime.time())))

    por_equip = {}
    for a in atividades:
        if not eh_manutencao(a["TIPO_ORIENTACAO"]):
            continue
        for coluna in ("REGRA", "ID_COMPROVANTE", "MOTIVO_AVALIACAO"):
            a.setdefault(coluna, None)
        a["DATA_PREVISTA"], a["DATA_REALIZADA"] = para_data(a["DATA_PREVISTA"]), para_data(a["DATA_REALIZADA"])
        a["tem_comprovante"] = str(a["COMPROVANTE_ENTREGUE"] or "").upper().startswith("S")
        por_equip.setdefault(a["ID_EQUIPAMENTO_SEGURADO"], []).append(a)
    sin = {s["ID_EQUIPAMENTO_SEGURADO"]: s for s in sinistros}

    linhas = []
    for e in equipamentos:
        ind = cp.indicador_cumprimento(por_equip.get(e["ID"], []), hoje)
        s = sin.get(e["ID"], {})
        linhas.append({"id": e["ID"], "id_cliente": e["ID_CLIENTE"], "id_fazenda": e["ID_FAZENDA"],
                       "fazenda": e["NOME_FAZENDA"],
                       "estado": e["ESTADO"] or "-", "tipo": (e["TIPO"] or "-").strip().title(),
                       "modelo": f"{e['FABRICANTE']} {e['MODELO']}",
                       "operacao": (e.get("TIPO_OPERACAO") or "Não informada") if tem_operacao else "Não informada",
                       "valor": float(e["VALOR_SEGURADO"] or 0), "realizacao": ind["realizacao"],
                       "atende": ind["atende"], "nota": ind["nota"], "sinistros": int(s.get("QTD") or 0),
                       "indenizado": float(s.get("TOTAL") or 0)})
    return linhas


def agrupar_equipamentos(linhas, dimensao):
    """Agrupa as máquinas por dimensao ("tipo", "modelo", "operacao" ou "estado"):
    [{"grupo", "maquinas", "valor", "realizacao", "atende", "nota_media", "sinistros", "indenizado",
      "sinistralidade"}]. Realização e atendimento são médias das máquinas com dado; sinistralidade = indenizado /
    valor segurado (%)."""
    grupos = {}
    for l in linhas:
        grupos.setdefault(l[dimensao], []).append(l)

    def media(valores):
        valores = [v for v in valores if v is not None]
        return sum(valores) / len(valores) if valores else None

    saida = []
    for nome, itens in grupos.items():
        valor = sum(i["valor"] for i in itens)
        indenizado = sum(i["indenizado"] for i in itens)
        saida.append({"grupo": nome, "maquinas": len(itens), "valor": valor,
                      "realizacao": media(i["realizacao"] for i in itens), "atende": media(i["atende"] for i in itens),
                      "nota_media": media(i["nota"] for i in itens),
                      "sinistros": sum(i["sinistros"] for i in itens), "indenizado": indenizado,
                      "sinistralidade": indenizado / valor * 100 if valor else None})
    return sorted(saida, key=lambda g: -g["valor"])
