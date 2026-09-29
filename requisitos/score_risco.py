# -*- coding: utf-8 -*-
"""
Score de Risco do cliente (docs/Score_risco_matriz.md), de 0 a 100 pontos (quanto maior, maior o risco).

Cada um dos 8 itens recebe nota 1 (baixo risco), 2 (médio) ou 3 (alto) e soma pontos conforme o seu peso:
nota 1 = 0 ponto, nota 2 = metade do peso, nota 3 = o peso inteiro (pontos = peso x (nota - 1) / 2). Como os pesos
somam 100, o score vai de 0 a 100. O bônus da regra ideal desconta pontos (5 ou 2,5). Classificação: abaixo de 30
Baixo; de 30 a 50 Médio; acima de 50 Alto.

Itens e fontes:
  Cliente      Exposição (5)                    soma do valor segurado dos equipamentos ativos
               Cumprimento de orientações (20)  requisitos/comprovacao.indicador_cumprimento (realização e validação)
               Sinistros (7,5)                  indenizações dos últimos 5 anos / valor segurado
  Ambiental    Queimadas (11,25)                % dos dias do último ano com foco do INPE a até 15 km da fazenda
               Hidrológico (11,25)              projeção: % dos dias do último ano com chuva de risco hidrológico
                                                Alto/Crítico pelas regras (requisitos/risco_chuva.py)
               (Eventos extremos saiu na versão v5: o histórico do município não media o risco da fazenda; os 2,5
               pontos foram divididos entre Queimadas e Hidrológico.)
  Operacional  Climático: atolamento e deslizamento (5)
                                                % dos dias do último ano com risco de deslizamento (regras de
                                                chuva e declividade) ou de atolamento (chuva em 72 h >= 80 mm)
               Complexidade da manutenção (20)  revisões previstas no cronograma dos próximos 5 anos, média por máquina
               Procedimentos de manutenção (20) pontos do questionário de maturidade (0 a 15) respondido pelo cliente

Itens da fazenda (ambientais, climático e procedimentos) viram a nota do cliente pela média das fazendas ponderada
pelo valor segurado de cada uma (fazenda sem equipamento entra com o peso médio das demais).
Sem dados (cliente novo ou fonte vazia): nota 2 (neutra), sinalizada.
As regras (VERSAO_MATRIZ, CRITERIOS) são gravadas em cada linha de CS_SCORE_GESTAO (REGRAS_SCORE e DETALHE_CALCULO).

Uso:
    python requisitos/score_risco.py                (calcula e mostra, sem gravar)
    python requisitos/score_risco.py --gravar       (grava em CS_SCORE_GESTAO)
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import math
import argparse
import datetime

from requisitos import comprovacao as cp
from requisitos import risco_chuva as rc
from requisitos.recomendacoes import eh_manutencao
from requisitos.programacao_manutencao import (orientacoes_do_modelo, aplica_ao_equipamento, vencimentos,
                                               para_data)

# Peso de cada item = pontos máximos que ele soma (nota 3). Somam 100.
PESOS = {"exposicao": 5.0, "cumprimento": 20.0, "sinistros": 7.5, "queimadas": 11.25, "hidrologico": 11.25,
         "climatico": 5.0, "complexidade": 20.0, "procedimentos": 20.0}
NOMES = {"exposicao": "Exposição da seguradora", "cumprimento": "Histórico de cumprimento de orientações",
         "sinistros": "Histórico de sinistros", "queimadas": "Queimadas", "hidrologico": "Hidrológico",
         "climatico": "Climático (atolamento e deslizamento)",
         "complexidade": "Complexidade da manutenção", "procedimentos": "Procedimentos de manutenção"}
CATEGORIAS = {"exposicao": "Cliente", "cumprimento": "Cliente", "sinistros": "Cliente", "queimadas": "Ambiental",
              "hidrologico": "Ambiental", "climatico": "Operacional",
              "complexidade": "Operacional", "procedimentos": "Operacional"}
ITENS_FAZENDA = ("queimadas", "hidrologico", "climatico", "procedimentos")
NOTA_NEUTRA = 2
SCORE_MAXIMO = 100
LIMITE_BAIXO, LIMITE_MEDIO = 30.0, 50.0           # abaixo de 30 Baixo; de 30 a 50 Médio; acima de 50 Alto

EXPOSICAO_FAIXAS = (20_000_000, 60_000_000)
ANOS_SINISTROS = 5
RAIO_QUEIMADA_KM = 15
ANOS_COMPLEXIDADE = 5
DIAS_MINIMOS_CLIMA = 180                          # menos dias de clima no último ano: sem dados (nota neutra)
# chuva acumulada em 72 horas (mm) a partir da qual o dia é de risco, pela declividade da fazenda (%)
LIMITES_CHUVA_72H = [(20.0, 30.0), (8.0, 50.0), (0.0, 80.0)]
LIMITE_CHUVA_SEM_RELEVO = 50.0
LIMITE_ATOLAMENTO_72H = 80.0                      # atolamento: chuva em 72 horas (mm), em qualquer relevo

# Versão da matriz: mude sempre que alterar pesos ou faixas. Cada linha de CS_SCORE_GESTAO grava a versão, a matriz
# completa (REGRAS_SCORE) e a medida de cada item (DETALHE_CALCULO), para o score continuar explicável depois.
VERSAO_MATRIZ = "2026-09 v5 (0 a 100)"            # até 30 caracteres (coluna VERSAO_MATRIZ)
# Critério de cada nota e como é medido: (nota 1, nota 2, nota 3, medida). Textos da tela: sem nomes de tabelas.
_PCT = ("até 10%", "mais de 10% e menos de 30%", "30% ou mais")
CRITERIOS = {
    "exposicao": ("até R$ 20 milhões", "de R$ 20 milhões a R$ 60 milhões", "mais de R$ 60 milhões",
                  "Soma do valor segurado das máquinas ativas do cliente."),
    "cumprimento": ("realização e atendimento de 80% ou mais", "realização e atendimento de 50% ou mais, "
                    "algum abaixo de 80%", "realização ou atendimento abaixo de 50%",
                    "Realização: revisões do cronograma com comprovante, entre as que já passaram do prazo de 90 dias "
                    "para enviar. Atendimento: média da validação da Sompo (Atende 100, maior parte 75, menor parte "
                    "25, não atende 0). Vale o menor dos dois."),
    "sinistros": ("sem sinistros em 5 anos", "indenizações até 20% do valor segurado",
                  "indenizações acima de 20% do valor segurado",
                  "Soma das indenizações dos últimos 5 anos dividida pelo valor segurado. Cliente com menos de 1 ano "
                  "de seguro fica com nota neutra."),
    "queimadas": _PCT + ("Percentual dos dias do último ano com pelo menos um foco de queimada detectado pelo INPE "
                         "a até 15 km da fazenda.",),
    "hidrologico": _PCT + ("Projeção de um ano: percentual dos dias do último ano em que a chuva da fazenda atingiu "
                           "uma regra de risco hidrológico Alto ou Crítico, isto é, chuva acumulada igual à que "
                           "antecedeu inundações, enxurradas, alagamentos e chuvas intensas no histórico da Defesa "
                           "Civil e do CEMADEN (modelo preditivo de chuva). Não depende do histórico curto de alertas "
                           "do CEMADEN.",),
    "climatico": _PCT + ("Percentual dos dias do último ano com risco de deslizamento ou de atolamento: "
                         "deslizamento quando a chuva e a declividade da fazenda atingem uma regra Alto ou Crítico do "
                         "modelo preditivo (chuva que antecedeu deslizamentos no histórico); atolamento quando a chuva "
                         "em 72 horas chega a 80 mm. Menos de 180 dias de clima: nota neutra.",),
    "complexidade": ("até 20 revisões", "mais de 20 e menos de 40 revisões", "40 revisões ou mais",
                     "Para cada máquina, o sistema projeta o cronograma de manutenção dos próximos 5 anos (pelo "
                     "manual e pelo uso da máquina) e conta as datas de revisão. O item é a média por máquina: quanto "
                     "mais revisões, mais complexa a manutenção e maior a chance de falha."),
    "procedimentos": ("80% ou mais", "mais de 50% e menos de 80%", "50% ou menos",
                      "Pontos obtidos no questionário de maturidade da manutenção preenchido pelo cliente (15 "
                      "perguntas, de 0 a 15 pontos), em percentual da nota máxima. Vale a última resposta de cada "
                      "fazenda."),
}


def _virgula(valor, casas=None):
    texto = f"{valor:g}" if casas is None else f"{valor:.{casas}f}"
    return texto.replace(".", ",")


def pontos(chave, nota):
    """Pontos que o item soma no score: 0 (nota 1) até o peso inteiro (nota 3)."""
    return PESOS[chave] * (float(nota) - 1) / 2


def matriz():
    """Regras do score, uma linha por item (para mostrar na tela)."""
    return [{"Categoria": CATEGORIAS[k], "Item": NOMES[k], "Peso": _virgula(PESOS[k]),
             "Nota 1 (0 ponto)": CRITERIOS[k][0],
             "Nota 2 (metade do peso)": CRITERIOS[k][1],
             "Nota 3 (peso inteiro)": CRITERIOS[k][2], "Como é medido": CRITERIOS[k][3]} for k in PESOS]


def texto_classificacao():
    return (f"abaixo de {_virgula(LIMITE_BAIXO)} pontos: Baixo; de {_virgula(LIMITE_BAIXO)} a "
            f"{_virgula(LIMITE_MEDIO)}: Médio; acima de {_virgula(LIMITE_MEDIO)}: Alto")


def texto_bonus():
    return "; ".join(f"{_virgula(b)} pontos com {_virgula(f)}% ou mais" for f, b in
                     sorted(cp.BONUS_IDEAL.items(), reverse=True))


def regras_texto():
    """Matriz completa em texto (coluna REGRAS_SCORE de CS_SCORE_GESTAO)."""
    linhas = [f"Matriz do Score de Risco, versão {VERSAO_MATRIZ}",
              "Score de 0 a 100 pontos. Cada item tem nota 1 (baixo), 2 (médio) ou 3 (alto) e soma pontos pelo peso: "
              "nota 1 = 0, nota 2 = metade do peso, nota 3 = peso inteiro. O bônus da regra ideal desconta pontos. "
              "Item sem dados: nota 2 (neutra). Itens da fazenda (queimadas, hidrológico, climático, "
              "procedimentos): média das fazendas pelo valor segurado.", ""]
    for k in PESOS:
        c = CRITERIOS[k]
        linhas.append(f"{CATEGORIAS[k]} | {NOMES[k]} | peso {_virgula(PESOS[k])} pontos | nota 1: {c[0]} | "
                      f"nota 2: {c[1]} | nota 3: {c[2]} | medida: {c[3]}")
    linhas += ["", f"Classificação: {texto_classificacao()}.",
               f"Bônus da regra ideal (revisões feitas pela regra ideal do manual): {texto_bonus()}."]
    return "\n".join(linhas)


def detalhe_calculo(resultado, fazenda):
    """Medida, nota e pontos de cada item de uma fazenda (coluna DETALHE_CALCULO de CS_SCORE_GESTAO)."""
    linhas = []
    for k in PESOS:
        item = fazenda["itens"][k] if k in ITENS_FAZENDA else resultado["itens"][k]
        origem = "fazenda" if k in ITENS_FAZENDA else "cliente"
        linhas.append(f"{NOMES[k]} ({origem}): nota {_virgula(item['nota'])}, {_virgula(pontos(k, item['nota']), 1)} "
                      f"de {_virgula(PESOS[k])} pontos" + (" (sem dados, neutra)" if item["sem_dados"] else "")
                      + f" | {item['valor']} | {item['detalhe']}")
    linhas.append(f"Bônus da regra ideal: {_virgula(resultado['bonus'] or 0)} pontos")
    linhas.append(f"Score da fazenda: {_virgula(fazenda['score'], 1)} | score do cliente: "
                  f"{_virgula(resultado['score'], 1)} ({resultado['classe']})")
    return "\n".join(linhas)


def classificar(score):
    if score is None:
        return None
    return "Baixo" if score < LIMITE_BAIXO else "Médio" if score <= LIMITE_MEDIO else "Alto"


def _faixa_percentual(pct):
    """Itens em %: até 10% nota 1; de 10% a menos de 30% nota 2; 30% ou mais nota 3."""
    if pct is None:
        return None
    return 1 if pct <= 10 else 2 if pct < 30 else 3


def _consultar(cursor, sql, parametros=None):
    cursor.execute(sql, parametros or {})
    colunas = [c[0] for c in cursor.description]
    return [{c: (v.read() if hasattr(v, "read") else v) for c, v in zip(colunas, linha)} for linha in cursor.fetchall()]


def _colunas(cursor, tabela):
    return {r["COLUMN_NAME"] for r in _consultar(cursor, "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS "
                                                         "WHERE TABLE_NAME = :t", {"t": tabela})}


def _item(nota, valor, detalhe=""):
    """{"nota": 1..3 (neutra se sem dados), "sem_dados", "valor" (texto), "detalhe"}."""
    return {"nota": NOTA_NEUTRA if nota is None else nota, "sem_dados": nota is None, "valor": valor,
            "detalhe": detalhe}


def _data(valor):
    """date de um valor do banco (datetime, date ou texto 'AAAA-MM-DD...')."""
    if isinstance(valor, str):
        return datetime.date.fromisoformat(valor[:10])
    return para_data(valor)


def _pct(valor):
    return "-" if valor is None else f"{valor:.1f}%".replace(".", ",")


def _distancia_km(lat1, lon1, lat2, lon2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


# ---------------------------------------------------------------------------
# Itens da fazenda
# ---------------------------------------------------------------------------
def queimadas(cursor, fazenda, hoje):
    lat, lon = fazenda.get("LATITUDE"), fazenda.get("LONGITUDE")
    if lat is None or lon is None:
        return _item(None, "-", "fazenda sem coordenadas")
    lat, lon = float(lat), float(lon)
    inicio = hoje - datetime.timedelta(days=365)
    dlat = RAIO_QUEIMADA_KM / 111.32
    dlon = RAIO_QUEIMADA_KM / (111.32 * max(math.cos(math.radians(lat)), 0.1))
    focos = _consultar(cursor, """
        SELECT DATA_HORA, LATITUDE, LONGITUDE FROM CS_ALERTAS
        WHERE ORIGEM_ALERTA = 'INPE' AND DATA_HORA >= :inicio
          AND LATITUDE BETWEEN :lat1 AND :lat2 AND LONGITUDE BETWEEN :lon1 AND :lon2
    """, {"inicio": datetime.datetime.combine(inicio, datetime.time()), "lat1": lat - dlat, "lat2": lat + dlat,
          "lon1": lon - dlon, "lon2": lon + dlon})
    dias_foco = {_data(f["DATA_HORA"]) for f in focos
                 if _distancia_km(lat, lon, float(f["LATITUDE"]), float(f["LONGITUDE"])) <= RAIO_QUEIMADA_KM}
    dias_foco = {d for d in dias_foco if inicio <= d <= hoje}
    pct = len(dias_foco) / 365 * 100
    return _item(_faixa_percentual(pct), _pct(pct),
                 f"{len(dias_foco)} dia(s) com foco a até {RAIO_QUEIMADA_KM} km no último ano")


def chuva_do_ano(cursor, fazenda, hoje):
    """{data: mm} do último ano da fazenda, com os 30 dias anteriores (para as chuvas acumuladas das regras)."""
    return rc.chuva_diaria(cursor, fazenda["ID"], hoje - datetime.timedelta(days=365 + 30), hoje, _consultar)


def dias_do_ano(chuva, hoje):
    inicio = hoje - datetime.timedelta(days=365)
    return sorted(d for d in chuva if inicio <= d <= hoje)


def hidrologico(cursor, fazenda, hoje, regras=None, chuva=None):
    """Projeção de 1 ano: % dos dias do último ano em que a chuva da fazenda atingiu uma regra de risco hidrológico
    Alto ou Crítico (requisitos/risco_chuva.py). Sem regras geradas: dias com alerta do CEMADEN no município."""
    if regras:
        chuva = chuva if chuva is not None else chuva_do_ano(cursor, fazenda, hoje)
        dias = dias_do_ano(chuva, hoje)
        if len(dias) < DIAS_MINIMOS_CLIMA:
            return _item(None, "-", f"só {len(dias)} dia(s) de clima no último ano")
        risco = rc.dias_de_risco(regras, chuva, None, dias, fazenda.get("ESTADO"))
        pct = len(risco) / len(dias) * 100
        return _item(_faixa_percentual(pct), _pct(pct),
                     f"{len(risco)} de {len(dias)} dia(s) do último ano com chuva de risco hidrológico Alto ou Crítico")
    ibge = str(fazenda.get("CODIGO_IBGE") or "").strip()
    if not ibge:
        return _item(None, "-", "fazenda sem código IBGE")
    inicio = hoje - datetime.timedelta(days=365)
    linhas = _consultar(cursor, """
        SELECT DISTINCT TRUNC(DATA_HORA) AS DIA FROM CS_ALERTAS
        WHERE ORIGEM_ALERTA = 'CEMADEN' AND DATA_HORA >= :inicio AND DETALHAMENTO_1 LIKE :padrao
    """, {"inicio": datetime.datetime.combine(inicio, datetime.time()), "padrao": f"%IBGE: {ibge}%"})
    pct = len(linhas) / 365 * 100
    return _item(_faixa_percentual(pct), _pct(pct), f"{len(linhas)} dia(s) com alerta do CEMADEN no último ano "
                                                    "(regras de risco hidrológico ainda não geradas)")


def limite_chuva(declividade):
    if declividade is None:
        return LIMITE_CHUVA_SEM_RELEVO
    return next(limite for minimo, limite in LIMITES_CHUVA_72H if float(declividade) >= minimo)


def dias_climaticos(chuva, dias, fazenda, regras_deslizamento):
    """(dias de risco, dias de deslizamento, dias de atolamento). Com regras de deslizamento: deslizamento pelas regras
    (chuva e declividade) ou atolamento (chuva em 72 h a partir de LIMITE_ATOLAMENTO_72H). Sem regras: a regra fixa
    de chuva em 72 h pelo relevo."""
    def chuva_72h(d):
        return sum(chuva.get(d - datetime.timedelta(days=k), 0) for k in range(3))
    if regras_deslizamento:
        deslizamento = rc.dias_de_risco(regras_deslizamento, chuva, fazenda.get("DECLIVIDADE_PCT"), dias,
                                        fazenda.get("ESTADO"))
        atolamento = {d for d in dias if chuva_72h(d) >= LIMITE_ATOLAMENTO_72H}
        return deslizamento | atolamento, deslizamento, atolamento
    limite = limite_chuva(fazenda.get("DECLIVIDADE_PCT"))
    return {d for d in dias if chuva_72h(d) >= limite}, None, None


def climatico(cursor, fazenda, hoje, regras_deslizamento=None, chuva=None):
    """Atolamento e deslizamento: % dos dias do último ano com risco (ver dias_climaticos)."""
    chuva = chuva if chuva is not None else chuva_do_ano(cursor, fazenda, hoje)
    dias = dias_do_ano(chuva, hoje)
    if len(dias) < DIAS_MINIMOS_CLIMA:
        return _item(None, "-", f"só {len(dias)} dia(s) de clima no último ano")
    risco, deslizamento, atolamento = dias_climaticos(chuva, dias, fazenda, regras_deslizamento)
    pct = len(risco) / len(dias) * 100
    relevo = ("sem relevo calculado" if fazenda.get("DECLIVIDADE_PCT") is None
              else f"declividade {float(fazenda['DECLIVIDADE_PCT']):.1f}%".replace(".", ","))
    if deslizamento is not None:
        detalhe = (f"{len(risco)} de {len(dias)} dia(s): {len(deslizamento)} com risco de deslizamento Alto ou "
                   f"Crítico e {len(atolamento)} com chuva em 72 h de {LIMITE_ATOLAMENTO_72H:.0f} mm ou mais ({relevo})")
    else:
        detalhe = (f"{len(risco)} de {len(dias)} dia(s) com chuva em 72 h de "
                   f"{limite_chuva(fazenda.get('DECLIVIDADE_PCT')):.0f} mm ou mais ({relevo}; regras de deslizamento "
                   "ainda não geradas)")
    return _item(_faixa_percentual(pct), _pct(pct), detalhe)


def procedimentos(cursor, fazenda):
    linhas = _consultar(cursor, """
        SELECT SCORE_FINAL FROM CS_SCORE_MANUTENCAO_PROCEDIMENTOS WHERE ID_FAZENDA = :f
        ORDER BY DATA_AVALIACAO DESC FETCH FIRST 1 ROWS ONLY
    """, {"f": int(fazenda["ID"])})
    if not linhas or linhas[0]["SCORE_FINAL"] is None:
        return _item(None, "-", "sem avaliação de maturidade")
    pct = float(linhas[0]["SCORE_FINAL"]) / 15 * 100
    nota = 1 if pct >= 80 else 2 if pct > 50 else 3
    return _item(nota, _pct(pct), f"nota {float(linhas[0]['SCORE_FINAL']):.1f} de 15".replace(".", ","))


# ---------------------------------------------------------------------------
# Itens do cliente
# ---------------------------------------------------------------------------
def exposicao(valor_total):
    nota = 1 if valor_total <= EXPOSICAO_FAIXAS[0] else 2 if valor_total <= EXPOSICAO_FAIXAS[1] else 3
    return _item(nota, f"R$ {valor_total:,.0f}".replace(",", "."), "valor segurado dos equipamentos ativos")


def sinistros(cursor, ids_equipamentos, valor_total, inicio_cobertura, hoje):
    if not ids_equipamentos or not valor_total:
        return _item(None, "-", "sem equipamentos segurados")
    binds = {f"e{n}": v for n, v in enumerate(ids_equipamentos)}
    binds["desde"] = datetime.datetime.combine(hoje - datetime.timedelta(days=365 * ANOS_SINISTROS), datetime.time())
    linhas = _consultar(cursor, f"""
        SELECT COUNT(*) AS QTD, NVL(SUM(VALOR_INDENIZACAO), 0) AS TOTAL FROM CS_SINISTROS
        WHERE ID_EQUIPAMENTO_SEGURADO IN ({', '.join(':' + b for b in binds if b.startswith('e'))})
          AND DATA_OCORRENCIA >= :desde
    """, binds)
    qtd, total = int(linhas[0]["QTD"] or 0), float(linhas[0]["TOTAL"] or 0)
    if qtd == 0:
        # cliente novo (menos de 1 ano de seguro): nota neutra; senão, sem sinistro = nota 1
        novo = inicio_cobertura is None or (hoje - inicio_cobertura).days < 365
        return _item(None if novo else 1, "0%", "cliente novo, sem histórico" if novo else "sem sinistros em 5 anos")
    razao = total / valor_total * 100
    return _item(2 if razao <= 20 else 3, _pct(razao),
                 f"{qtd} sinistro(s) em {ANOS_SINISTROS} anos, R$ " + f"{total:,.0f}".replace(",", ".") + " indenizados")


def cumprimento(cursor, ids_equipamentos, hoje):
    """Manutenções do cliente -> indicador de cumprimento (realização, validação e bônus da regra ideal)."""
    if not ids_equipamentos:
        return _item(None, "-", "sem equipamentos"), 0.0
    novas = [c for c in ("REGRA", "ID_COMPROVANTE", "MOTIVO_AVALIACAO") if c in _colunas(cursor,
                                                                                        "CS_MANUTENCOES_REALIZADAS")]
    uso = [c for c in ("DATA_AQUISICAO", "HORIMETRO_ATUAL", "HODOMETRO_ATUAL", "DATA_LEITURA", "USO_MEDIO_HORAS_MES",
                       "USO_MEDIO_KM_MES", "TIPO_TRACAO", "DATA_INICIO_VIGENCIA")
           if c in _colunas(cursor, "CS_EQUIPAMENTOS_SEGURADOS")]
    binds = {f"e{n}": v for n, v in enumerate(ids_equipamentos)}
    atividades = _consultar(cursor, f"""
        SELECT m.ID, m.ID_EQUIPAMENTO_SEGURADO, m.ID_ORIENTACAO, m.DATA_PREVISTA, m.DATA_REALIZADA,
               m.COMPROVANTE_ENTREGUE, m.ATENDE_CRITERIOS{''.join(', m.' + c for c in novas)},
               o.TIPO_ORIENTACAO, o.METRICA_GATILHO, o.VALOR_GATILHO, o.UNIDADE_MEDIDA, o.FATOR_CONDICIONAL
               {''.join(', e.' + c for c in uso)}
        FROM CS_MANUTENCOES_REALIZADAS m
        JOIN CS_EQUIPAMENTOS_SEGURADOS e ON e.ID = m.ID_EQUIPAMENTO_SEGURADO
        LEFT JOIN CS_EQUIPAMENTOS_ORIENTACOES o ON o.ID = m.ID_ORIENTACAO
        WHERE m.ID_EQUIPAMENTO_SEGURADO IN ({', '.join(':' + b for b in binds)})
    """, binds)
    lista = []
    for a in atividades:
        if not eh_manutencao(a["TIPO_ORIENTACAO"]):
            continue
        for coluna in ("REGRA", "ID_COMPROVANTE", "MOTIVO_AVALIACAO"):
            a.setdefault(coluna, None)
        a["DATA_PREVISTA"], a["DATA_REALIZADA"] = para_data(a["DATA_PREVISTA"]), para_data(a["DATA_REALIZADA"])
        a["tem_comprovante"] = str(a["COMPROVANTE_ENTREGUE"] or "").upper().startswith("S")
        lista.append(a)
    ind = cp.indicador_cumprimento(lista, hoje)
    valor = (f"realização {_pct(ind['realizacao'])}; atendimento {_pct(ind['atende'])}"
             + (f"; regra ideal {_pct(ind['ideal'])}" if ind["ideal"] is not None else ""))
    return _item(ind["nota"], valor, f"{ind['validados']} serviço(s) validado(s) pela Sompo"), ind["bonus"]


def complexidade(cursor, equipamentos, hoje):
    """Revisões (datas distintas do cronograma) nos próximos 5 anos, média por equipamento."""
    if not equipamentos:
        return _item(None, "-", "sem equipamentos")
    fim = hoje + datetime.timedelta(days=365 * ANOS_COMPLEXIDADE)
    cache, totais = {}, []
    for e in equipamentos:
        modelo = e["ID_EQUIPAMENTO_MODELO"]
        if modelo not in cache:
            cache[modelo] = orientacoes_do_modelo(cursor, modelo)
        datas = set()
        for o in cache[modelo]:
            if aplica_ao_equipamento(o, e):
                datas.update(vencimentos(o, e, hoje, fim))
        totais.append(len(datas))
    media = sum(totais) / len(totais)
    nota = 1 if media <= 20 else 2 if media < 40 else 3
    return _item(nota, f"{media:.0f} revisões".replace(".", ","),
                 f"média de {len(totais)} equipamento(s) nos próximos {ANOS_COMPLEXIDADE} anos")


# ---------------------------------------------------------------------------
# Cliente
# ---------------------------------------------------------------------------
def calcular_cliente(conn, id_cliente, hoje=None):
    """Score do cliente: {"id_cliente", "nome", "itens" {chave: item}, "bonus", "score", "classe", "fazendas"
    [{"ID", "NOME_FAZENDA", "peso", "itens", "score"}]}."""
    hoje = hoje or datetime.date.today()
    with conn.cursor() as cursor:
        cliente = _consultar(cursor, "SELECT ID, RAZAO_SOCIAL FROM CS_CLIENTES WHERE ID = :c", {"c": int(id_cliente)})
        colunas_faz = _colunas(cursor, "CS_FAZENDAS")
        extras_faz = [c for c in ("CODIGO_IBGE", "DECLIVIDADE_PCT") if c in colunas_faz]
        fazendas = _consultar(cursor, f"""
            SELECT ID, NOME_FAZENDA, LATITUDE, LONGITUDE, MUNICIPIO, ESTADO{''.join(', ' + c for c in extras_faz)}
            FROM CS_FAZENDAS WHERE ID_CLIENTE = :c ORDER BY NOME_FAZENDA
        """, {"c": int(id_cliente)})
        uso = [c for c in ("DATA_AQUISICAO", "HORIMETRO_ATUAL", "HODOMETRO_ATUAL", "DATA_LEITURA",
                           "USO_MEDIO_HORAS_MES", "USO_MEDIO_KM_MES", "TIPO_TRACAO")
               if c in _colunas(cursor, "CS_EQUIPAMENTOS_SEGURADOS")]
        equipamentos = _consultar(cursor, f"""
            SELECT e.ID, e.ID_FAZENDA, e.ID_EQUIPAMENTO_MODELO, e.VALOR_SEGURADO, e.STATUS, e.DATA_INICIO_VIGENCIA
                   {''.join(', e.' + c for c in uso)}
            FROM CS_EQUIPAMENTOS_SEGURADOS e JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
            WHERE f.ID_CLIENTE = :c
        """, {"c": int(id_cliente)})
        ativos = [e for e in equipamentos if str(e.get("STATUS") or "ATIVO").upper().startswith("ATIV")]
        for e in ativos:
            for c in ("DATA_AQUISICAO", "DATA_LEITURA", "DATA_INICIO_VIGENCIA"):
                e[c] = para_data(e.get(c))
        valor_total = sum(float(e["VALOR_SEGURADO"] or 0) for e in ativos)
        inicios = [e["DATA_INICIO_VIGENCIA"] for e in ativos if e["DATA_INICIO_VIGENCIA"]]
        regras_chuva = {tipo: rc.carregar_regras(cursor, tipo) for tipo in rc.TIPOS}

        itens = {"exposicao": exposicao(valor_total)}
        itens["cumprimento"], bonus = cumprimento(cursor, [e["ID"] for e in ativos], hoje)
        itens["sinistros"] = sinistros(cursor, [e["ID"] for e in ativos], valor_total,
                                       min(inicios) if inicios else None, hoje)
        itens["complexidade"] = complexidade(cursor, ativos, hoje)

        # itens da fazenda, combinados pelo valor segurado de cada fazenda
        valor_fazenda = {}
        for e in ativos:
            valor_fazenda[e["ID_FAZENDA"]] = valor_fazenda.get(e["ID_FAZENDA"], 0) + float(e["VALOR_SEGURADO"] or 0)
        pesos_com_valor = [v for v in valor_fazenda.values() if v > 0]
        peso_padrao = sum(pesos_com_valor) / len(pesos_com_valor) if pesos_com_valor else 1.0
        resultado_fazendas = []
        for f in fazendas:
            chuva_f = chuva_do_ano(cursor, f, hoje)
            itens_f = {"queimadas": queimadas(cursor, f, hoje),
                       "hidrologico": hidrologico(cursor, f, hoje, regras_chuva[rc.TIPO_HIDROLOGICO], chuva_f),
                       "climatico": climatico(cursor, f, hoje, regras_chuva[rc.TIPO_DESLIZAMENTO], chuva_f),
                       "procedimentos": procedimentos(cursor, f)}
            resultado_fazendas.append({"ID": f["ID"], "NOME_FAZENDA": f["NOME_FAZENDA"],
                                       "MUNICIPIO": f"{f.get('MUNICIPIO') or ''}/{f.get('ESTADO') or ''}",
                                       "peso": valor_fazenda.get(f["ID"]) or peso_padrao, "itens": itens_f})
        soma_pesos = sum(f["peso"] for f in resultado_fazendas)
        for chave in ITENS_FAZENDA:
            if not resultado_fazendas:
                itens[chave] = _item(None, "-", "cliente sem fazendas")
                continue
            nota = sum(f["itens"][chave]["nota"] * f["peso"] for f in resultado_fazendas) / soma_pesos
            todas_sem = all(f["itens"][chave]["sem_dados"] for f in resultado_fazendas)
            itens[chave] = {"nota": round(nota, 2), "sem_dados": todas_sem,
                            "valor": "; ".join(f"{f['NOME_FAZENDA']}: {f['itens'][chave]['valor']}"
                                               for f in resultado_fazendas[:4])
                            + (" ..." if len(resultado_fazendas) > 4 else ""),
                            "detalhe": f"média de {len(resultado_fazendas)} fazenda(s) pelo valor segurado"}
        for f in resultado_fazendas:
            notas_f = {**{k: itens[k]["nota"] for k in PESOS if k not in ITENS_FAZENDA},
                       **{k: f["itens"][k]["nota"] for k in ITENS_FAZENDA}}
            f["score"] = _total(notas_f, bonus)
        score = _total({k: itens[k]["nota"] for k in PESOS}, bonus)
    return {"id_cliente": int(id_cliente), "nome": cliente[0]["RAZAO_SOCIAL"] if cliente else f"Cliente {id_cliente}",
            "itens": itens, "bonus": bonus, "score": score, "classe": classificar(score),
            "valor_segurado": valor_total, "equipamentos": len(ativos), "fazendas": resultado_fazendas}


def _total(notas, bonus):
    """Score de 0 a 100: soma dos pontos dos itens menos o bônus da regra ideal."""
    bruto = sum(pontos(k, notas[k]) for k in PESOS) * SCORE_MAXIMO / sum(PESOS.values())
    return round(max(0.0, bruto - (bonus or 0)), 1)


def clientes_com_equipamentos(conn):
    with conn.cursor() as cursor:
        return _consultar(cursor, """
            SELECT DISTINCT c.ID, c.RAZAO_SOCIAL FROM CS_CLIENTES c
            JOIN CS_FAZENDAS f ON f.ID_CLIENTE = c.ID
            JOIN CS_EQUIPAMENTOS_SEGURADOS e ON e.ID_FAZENDA = f.ID
            ORDER BY c.RAZAO_SOCIAL
        """)


COLUNAS_GESTAO = {"exposicao": "SCORE_CLIENTE_EXPOSICAO", "cumprimento": "SCORE_CLIENTE_ADERENCIA_MANUTENCAO",
                  "sinistros": "SCORE_CLIENTE_HISTORICO_SINISTROS", "queimadas": "SCORE_AMBIENTAL_QUEIMADAS",
                  "hidrologico": "SCORE_AMBIENTAL_HIDROLOGICO",
                  "climatico": "SCORE_OPERACIONAL_CLIMATICO", "complexidade": "SCORE_OPERACIONAL_MANUTENCAO",
                  "procedimentos": "SCORE_OPERACIONAL_PROCEDIMENTOS"}


def gravar(conn, resultado):
    """Uma linha por fazenda em CS_SCORE_GESTAO: notas da fazenda (itens ambientais, climático e procedimentos) e do
    cliente (demais itens). SCORE_RISCO_TOTAL e CLASSIFICACAO_RISCO são do CLIENTE; SCORE_FAZENDA é o da fazenda.
    Não faz commit."""
    with conn.cursor() as cursor:
        extras = _colunas(cursor, "CS_SCORE_GESTAO")
        for f in resultado["fazendas"]:
            dados = {col: (f["itens"][k]["nota"] if k in ITENS_FAZENDA else resultado["itens"][k]["nota"])
                     for k, col in COLUNAS_GESTAO.items()}
            dados.update(ID_FAZENDA=f["ID"], SCORE_RISCO_TOTAL=resultado["score"],
                         CLASSIFICACAO_RISCO=resultado["classe"])
            if "SCORE_FAZENDA" in extras:
                dados["SCORE_FAZENDA"] = f["score"]
            if "BONUS_REGRA_IDEAL" in extras:
                dados["BONUS_REGRA_IDEAL"] = resultado["bonus"]
            if "ID_CLIENTE" in extras:
                dados["ID_CLIENTE"] = resultado["id_cliente"]
            if "VERSAO_MATRIZ" in extras:
                dados["VERSAO_MATRIZ"] = VERSAO_MATRIZ
            if "REGRAS_SCORE" in extras:
                dados["REGRAS_SCORE"] = regras_texto()
            if "DETALHE_CALCULO" in extras:
                dados["DETALHE_CALCULO"] = detalhe_calculo(resultado, f)
            cursor.execute(f"INSERT INTO CS_SCORE_GESTAO ({', '.join(dados)}) "
                           f"VALUES ({', '.join(':' + c for c in dados)})", dados)


def valor_segurado_por_cliente(conn):
    """{id_cliente: soma do valor segurado das máquinas ativas}."""
    with conn.cursor() as cursor:
        linhas = _consultar(cursor, """
            SELECT f.ID_CLIENTE, e.VALOR_SEGURADO, e.STATUS FROM CS_EQUIPAMENTOS_SEGURADOS e
            JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA
        """)
    total = {}
    for l in linhas:
        if str(l.get("STATUS") or "ATIVO").upper().startswith("ATIV"):
            total[l["ID_CLIENTE"]] = total.get(l["ID_CLIENTE"], 0.0) + float(l["VALOR_SEGURADO"] or 0)
    return total


def valor_segurado_por_fazenda(conn):
    """{id_fazenda: soma do valor segurado das máquinas ativas}."""
    with conn.cursor() as cursor:
        linhas = _consultar(cursor, "SELECT ID_FAZENDA, VALOR_SEGURADO, STATUS FROM CS_EQUIPAMENTOS_SEGURADOS")
    total = {}
    for l in linhas:
        if str(l.get("STATUS") or "ATIVO").upper().startswith("ATIV"):
            total[l["ID_FAZENDA"]] = total.get(l["ID_FAZENDA"], 0.0) + float(l["VALOR_SEGURADO"] or 0)
    return total


def filtro_versao_atual(cursor):
    """Trecho de WHERE que deixa só os cálculos da matriz atual (a escala mudou de 1-3 para 0-100 na v3)."""
    if "VERSAO_MATRIZ" not in _colunas(cursor, "CS_SCORE_GESTAO"):
        return "", {}
    return " AND g.VERSAO_MATRIZ = :versao", {"versao": VERSAO_MATRIZ}


def fazendas_dos_clientes(conn):
    """Fazendas dos clientes com equipamentos (para os filtros por cliente e estado)."""
    with conn.cursor() as cursor:
        return _consultar(cursor, """
            SELECT f.ID, f.NOME_FAZENDA, f.ID_CLIENTE, f.MUNICIPIO, f.ESTADO FROM CS_FAZENDAS f
            WHERE f.ID_CLIENTE IN (SELECT f2.ID_CLIENTE FROM CS_FAZENDAS f2
                                   JOIN CS_EQUIPAMENTOS_SEGURADOS e ON e.ID_FAZENDA = f2.ID)
            ORDER BY f.NOME_FAZENDA
        """)


def ultimos_scores_fazendas(conn):
    """Último cálculo gravado de cada fazenda: {id_fazenda: {"score" (da fazenda), "classe" (da fazenda), "data",
    "versao"}}."""
    with conn.cursor() as cursor:
        extras = _colunas(cursor, "CS_SCORE_GESTAO")
        colunas = [c for c in ("SCORE_FAZENDA", "VERSAO_MATRIZ") if c in extras] + list(COLUNAS_GESTAO.values())
        filtro, binds = filtro_versao_atual(cursor)
        linhas = _consultar(cursor, f"""
            SELECT g.ID_FAZENDA, g.SCORE_RISCO_TOTAL, g.DATA_CALCULO{''.join(', g.' + c for c in colunas)}
            FROM CS_SCORE_GESTAO g WHERE 1 = 1{filtro} ORDER BY g.DATA_CALCULO, g.ID
        """, binds)
    resultado = {}
    for l in linhas:
        score = l.get("SCORE_FAZENDA")
        score = float(score if score is not None else l["SCORE_RISCO_TOTAL"])
        resultado[l["ID_FAZENDA"]] = {"score": score, "classe": classificar(score), "data": l["DATA_CALCULO"],
                                      "versao": l.get("VERSAO_MATRIZ"),
                                      "notas": {k: (float(l[c]) if l.get(c) is not None else None)
                                                for k, c in COLUNAS_GESTAO.items()}}
    return resultado


def main():
    parser = argparse.ArgumentParser(description="Calcula o Score de Risco de todos os clientes com equipamentos.")
    parser.add_argument("--gravar", action="store_true", help="grava em CS_SCORE_GESTAO")
    args = parser.parse_args()
    import oracledb
    from auth import USER, PASSWORD, DSN
    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        for c in clientes_com_equipamentos(conn):
            r = calcular_cliente(conn, c["ID"])
            print(f"{r['score']:5.1f} {r['classe']:<6} {r['nome']} | "
                  + " ".join(f"{k[:5]}={r['itens'][k]['nota']:g}" for k in PESOS)
                  + (f" | bônus {r['bonus']:g}" if r["bonus"] else ""))
            if args.gravar:
                gravar(conn, r)
        if args.gravar:
            conn.commit()
            print("Gravado em CS_SCORE_GESTAO.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
