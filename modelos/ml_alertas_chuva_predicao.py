# -*- coding: utf-8 -*-
"""
Gera as REGRAS de risco de chuva (hidrológico e deslizamento) a partir do histórico -> CS_EVENTOS_PREDICAO
(rodar à mão ou pela página Monitoramento; por exemplo, uma vez por mês).

Ideia: "chuva parecida com a que antecedeu inundações e deslizamentos". Para cada fazenda e cada dia com clima
(CS_FAZENDAS_CLIMA, agregado por dia):
  - chuva acumulada do dia, de 72 horas, de 7 dias e de 30 dias (solo encharcado) e a declividade da fazenda;
  - evento: desastre do tipo registrado pela Defesa Civil (CS_EVENTOS, COBRADE) no município da fazenda, ou alerta
    do CEMADEN para o município, no dia ou nos DIAS_EVENTO - 1 dias seguintes.
O programa testa limites de chuva em cada janela (e, no deslizamento, declividades mínimas) e guarda as combinações
com casos suficientes e chance de evento bem acima do normal. O nível vem do ganho sobre a chance normal
(requisitos/risco_chuva.GANHO_NIVEL). Poucos eventos por estado: as regras são nacionais.

Validação: regras aprendidas com os dias ANTES de INICIO_TESTE e testadas nos dias a partir dele (que o programa não
viu): detecção (eventos com aviso Alto ou Crítico), acerto dos avisos, ganho sobre o acaso e matriz de confusão.
As regras gravadas usam todo o histórico. As regras anteriores do tipo são substituídas.

Uso:
    python modelos/ml_alertas_chuva_predicao.py              (gera, valida e grava as regras dos dois tipos)
    python modelos/ml_alertas_chuva_predicao.py --simular    (gera e valida, sem gravar)
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import re
import argparse
import datetime

from requisitos import risco_chuva as rc

INICIO_TESTE = datetime.date(2025, 1, 1)
DIAS_EVENTO = 3                   # evento no dia ou nos 2 dias seguintes
MIN_CASOS = 60                    # dias de fazenda em que a condição ocorreu (mínimo para confiar na chance)
MIN_EVENTOS = 4                   # dias com evento entre os casos
GANHO_MINIMO = 1.5                # chance pelo menos 1,5 vez a normal
LIMITES = {1: [30, 50, 80, 100, 130], 3: [50, 80, 120, 150, 200], 7: [80, 120, 160, 200, 250],
           30: [150, 250, 350, 450, 600]}
DECLIVIDADES = [8.0, 20.0]        # deslizamento: declividade mínima testada (além de "qualquer")


def log(msg):
    print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)


def _consultar(cursor, sql, parametros=None):
    cursor.execute(sql, parametros or {})
    colunas = [c[0] for c in cursor.description]
    return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def _data(valor):
    if isinstance(valor, str):
        return datetime.date.fromisoformat(valor[:10])
    return valor.date() if isinstance(valor, datetime.datetime) else valor


def _colunas(cursor, tabela):
    return {l["COLUMN_NAME"] for l in _consultar(cursor, "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE "
                                                          "TABLE_NAME = :t", {"t": tabela})}


# ---------------------------------------------------------------------------
# Dados
# ---------------------------------------------------------------------------
def carregar_fazendas(cursor):
    extras = [c for c in ("CODIGO_IBGE", "DECLIVIDADE_PCT") if c in _colunas(cursor, "CS_FAZENDAS")]
    fazendas = _consultar(cursor, f"SELECT ID, ESTADO{''.join(', ' + c for c in extras)} FROM CS_FAZENDAS ORDER BY ID")
    for f in fazendas:
        f.setdefault("CODIGO_IBGE", None)
        f.setdefault("DECLIVIDADE_PCT", None)
        f["ibge"] = str(f["CODIGO_IBGE"]).strip() if f["CODIGO_IBGE"] is not None else None
    return fazendas


def carregar_chuva(cursor):
    """{id_fazenda: {data: mm}} de todo o clima das fazendas."""
    chuva = {}
    for l in _consultar(cursor, """
            SELECT ID_FAZENDA, TRUNC(DATA_HORA) AS DIA, SUM(NVL(PRECIPITACAO, 0)) AS CHUVA
            FROM CS_FAZENDAS_CLIMA GROUP BY ID_FAZENDA, TRUNC(DATA_HORA)"""):
        chuva.setdefault(l["ID_FAZENDA"], {})[_data(l["DIA"])] = float(l["CHUVA"] or 0)
    return chuva


def carregar_eventos(cursor, tipo, ibges):
    """{codigo_ibge: {datas}} dos eventos do tipo: Defesa Civil (COBRADE) e alertas do CEMADEN."""
    config = rc.TIPOS[tipo]
    eventos = {}
    codigos = {f"c{n}": c for n, c in enumerate(config["cobrade"])}
    for l in _consultar(cursor, f"""
            SELECT CODIGO_IBGE, DATA_OCORRENCIA FROM CS_EVENTOS
            WHERE DATA_OCORRENCIA IS NOT NULL AND SUBSTR(COBRADE, 1, 5) IN ({', '.join(':' + c for c in codigos)})
            """, codigos):
        ibge = str(l["CODIGO_IBGE"]).strip()
        if ibge in ibges:
            eventos.setdefault(ibge, set()).add(_data(l["DATA_OCORRENCIA"]))
    for l in _consultar(cursor, """
            SELECT DATA_HORA, DETALHAMENTO_1 FROM CS_ALERTAS
            WHERE ORIGEM_ALERTA = 'CEMADEN' AND TIPO_ALERTA LIKE :tipo""", {"tipo": config["cemaden"] + "%"}):
        m = re.search(r"IBGE: (\d+)", str(l["DETALHAMENTO_1"] or ""))
        if m and m.group(1) in ibges:
            eventos.setdefault(m.group(1), set()).add(_data(l["DATA_HORA"]))
    return eventos


def montar_base(fazendas, chuva, eventos):
    """Um registro por fazenda e dia com as chuvas acumuladas, a declividade e se houve evento."""
    base = []
    janela_evento = [datetime.timedelta(days=k) for k in range(DIAS_EVENTO)]
    for f in fazendas:
        serie = chuva.get(f["ID"])
        if not serie or not f["ibge"]:
            continue
        datas_evento = eventos.get(f["ibge"], set())
        for dia in sorted(serie):
            acum = rc.acumulados(serie, dia)
            if acum[30] is None:
                continue
            base.append({"fazenda": f["ID"], "dia": dia, "acum": acum, "declividade": f["DECLIVIDADE_PCT"],
                         "uf": f["ESTADO"], "evento": any(dia + k in datas_evento for k in janela_evento)})
    return base


# ---------------------------------------------------------------------------
# Regras e validação
# ---------------------------------------------------------------------------
def minerar(base, tipo):
    """Regras (janela, limite, declividade) com casos, eventos, chance e ganho suficientes. Para a mesma janela e
    declividade, um limite maior só entra se tiver ganho maior que o do limite anterior guardado."""
    total, eventos = len(base), sum(1 for b in base if b["evento"])
    if not total or not eventos:
        return [], 0.0
    taxa = eventos / total
    declividades = [None] + (DECLIVIDADES if rc.TIPOS[tipo]["usa_declividade"] else [])
    regras, vistas = [], set()
    for janela, limites in LIMITES.items():
        for declividade in declividades:
            ganho_anterior = 0.0
            for limite in limites:
                casos = [b for b in base if b["acum"][janela] is not None and b["acum"][janela] >= limite
                         and (declividade is None or (b["declividade"] is not None
                                                      and float(b["declividade"]) >= declividade))]
                com_evento = sum(1 for b in casos if b["evento"])
                if len(casos) < MIN_CASOS or com_evento < MIN_EVENTOS:
                    break
                chance = com_evento / len(casos)
                ganho = chance / taxa
                if ganho < GANHO_MINIMO or ganho <= ganho_anterior:
                    continue
                ganho_anterior = ganho
                if (janela, limite, len(casos), com_evento) in vistas:
                    continue          # mesma regra com declividade maior que não muda os casos: não repete
                vistas.add((janela, limite, len(casos), com_evento))
                regras.append({"tipo": tipo, "janela": janela, "limite": float(limite), "declividade": declividade,
                               "chance": chance * 100, "casos": len(casos), "eventos": com_evento, "ganho": ganho,
                               "nivel": rc.nivel_pelo_ganho(ganho), "uf": None})
    return regras, taxa


def validar(regras, teste):
    """Detecção e acerto dos avisos Alto/Crítico nos dias de teste, matriz de confusão e resultado por nível."""
    vp = fp = fn = vn = 0
    medio_ou_mais = 0
    por_nivel = {n: [0, 0] for n in rc.NIVEIS}          # dias, dias com evento
    for b in teste:
        nivel = rc.avaliar(regras, b["acum"], b["declividade"])[0]
        aviso = nivel in rc.NIVEIS_RISCO
        por_nivel[nivel][0] += 1
        por_nivel[nivel][1] += 1 if b["evento"] else 0
        medio_ou_mais += 1 if b["evento"] and nivel != "Baixo" else 0
        if aviso and b["evento"]:
            vp += 1
        elif aviso:
            fp += 1
        elif b["evento"]:
            fn += 1
        else:
            vn += 1
    total, eventos = len(teste), vp + fn
    taxa = eventos / total if total else None
    precisao = vp / (vp + fp) if vp + fp else None
    recall = vp / eventos if eventos else None
    return {"dias": total, "eventos": eventos, "taxa_normal": taxa, "vp": vp, "fp": fp, "fn": fn, "vn": vn,
            "recall": recall, "precisao": precisao,
            "recall_medio": medio_ou_mais / eventos if eventos else None,
            "ganho": precisao / taxa if precisao is not None and taxa else None,
            "f1": 2 * precisao * recall / (precisao + recall) if precisao and recall else None,
            "especificidade": vn / (vn + fp) if vn + fp else None,
            "por_nivel": [{"nivel": n, "dias": d, "com_evento": e, "acerto": e / d if d else None}
                          for n, (d, e) in reversed(list(por_nivel.items()))]}


def rodar_treinamento_chuva(progress_callback=None):
    """{tipo: {"regras", "validacao", "taxa_normal", "dias", "eventos", "periodo"}} para os dois tipos."""
    import oracledb
    from auth import USER, PASSWORD, DSN

    def progresso(pct, etapa, descricao):
        log(f"[{pct}%] {etapa}: {descricao}")
        if progress_callback:
            progress_callback(pct, etapa, descricao)

    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        with conn.cursor() as cursor:
            progresso(5, "Fazendas", "carregando fazendas, municípios e declividade")
            fazendas = carregar_fazendas(cursor)
            progresso(15, "Clima", "somando a chuva horária por fazenda e dia")
            chuva = carregar_chuva(cursor)
            ibges = {f["ibge"] for f in fazendas if f["ibge"]}
            resultado = {}
            for n, tipo in enumerate(rc.TIPOS):
                progresso(35 + n * 30, tipo, "eventos da Defesa Civil e do CEMADEN nos municípios das fazendas")
                base = montar_base(fazendas, chuva, carregar_eventos(cursor, tipo, ibges))
                treino = [b for b in base if b["dia"] < INICIO_TESTE]
                teste = [b for b in base if b["dia"] >= INICIO_TESTE]
                regras_treino, _ = minerar(treino, tipo)
                validacao = validar(regras_treino, teste) if regras_treino and teste else None
                progresso(50 + n * 30, tipo, "regras finais com todo o histórico")
                regras, taxa = minerar(base, tipo)
                datas = [b["dia"] for b in base]
                resultado[tipo] = {"regras": regras, "validacao": validacao, "taxa_normal": taxa, "dias": len(base),
                                   "eventos": sum(1 for b in base if b["evento"]),
                                   "periodo": (min(datas), max(datas)) if datas else None,
                                   "treino_eventos": sum(1 for b in treino if b["evento"]),
                                   "teste_eventos": sum(1 for b in teste if b["evento"])}
        progresso(100, "Concluído", "regras geradas e validadas")
        return resultado
    finally:
        conn.close()


def gravar_regras_chuva(resultado):
    """Substitui as regras de cada tipo em CS_EVENTOS_PREDICAO. Devolve o total gravado."""
    import oracledb
    from auth import USER, PASSWORD, DSN
    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    total = 0
    try:
        with conn.cursor() as cursor:
            for tipo, r in resultado.items():
                cursor.execute("DELETE FROM CS_EVENTOS_PREDICAO WHERE TIPO_RISCO = :t", {"t": tipo})
                for regra in r["regras"]:
                    descricao = (f"{rc.descrever(regra).capitalize()}: chance de "
                                 f"{rc.texto_chance(regra['chance'])}" + f" de {rc.TIPOS[tipo]['evento']} (no dia "
                                 f"ou nos 2 seguintes), " + f"{regra['ganho']:.1f}".replace(".", ",") + " vezes a "
                                 f"chance normal; {regra['casos']} dias observados, {regra['eventos']} com evento.")
                    cursor.execute("""
                        INSERT INTO CS_EVENTOS_PREDICAO (TIPO_RISCO, REGIAO, UF, GRAU_RISCO, PRECIPITACAO_CONDICAO,
                            PRECIPITACAO_VALOR, DIAS_TIPO, DIAS_CONDICAO, DIAS_VALOR, INCLINACAO_CONDICAO,
                            INCLINACAO_VALOR, DESCRICAO_REGRA, PROBABILIDADE, CASOS, ORIGEM_REGRA, DATA_GERACAO)
                        VALUES (:tipo, 'Brasil', NULL, :grau, '>=', :limite, :dias_tipo, '=', :janela, :inc_cond,
                            :declividade, :descricao, :chance, :casos, 'Histórico', :gerado)
                    """, {"tipo": tipo, "grau": rc.GRAU_BANCO[regra["nivel"]], "limite": regra["limite"],
                          "dias_tipo": rc.DIAS_TIPO_JANELA, "janela": regra["janela"],
                          "inc_cond": ">=" if regra["declividade"] is not None else None,
                          "declividade": regra["declividade"], "descricao": descricao,
                          "chance": round(regra["chance"], 4), "casos": regra["casos"],
                          "gerado": datetime.datetime.now()})
                    total += 1
        conn.commit()
    finally:
        conn.close()
    return total


def relatorio(resultado):
    for tipo, r in resultado.items():
        log("=" * 100)
        log(f"{tipo.upper()}: {r['dias']} dias de fazenda, {r['eventos']} com evento (chance normal "
            f"{(r['taxa_normal'] or 0) * 100:.2f}%). Treino: {r['treino_eventos']} eventos; teste (desde "
            f"{INICIO_TESTE:%d/%m/%Y}): {r['teste_eventos']} eventos.")
        for g in sorted(r["regras"], key=lambda g: -g["ganho"]):
            log(f"   {g['nivel']:<8} {rc.descrever(g):<60} chance {g['chance']:5.1f}%  ganho {g['ganho']:5.1f}x  "
                f"casos {g['casos']}")
        v = r["validacao"]
        if v:
            log(f"   Validação (dias não vistos): eventos com aviso Alto/Crítico {(v['recall'] or 0) * 100:.0f}%; "
                f"com aviso Médio ou mais {(v['recall_medio'] or 0) * 100:.0f}%; "
                f"acerto dos avisos {(v['precisao'] or 0) * 100:.1f}%; ganho sobre o acaso {v['ganho'] or 0:.1f}x; "
                f"VP {v['vp']} FP {v['fp']} FN {v['fn']} VN {v['vn']}")
        else:
            log("   Validação indisponível: sem regras no treino ou sem dias de teste.")


def main():
    parser = argparse.ArgumentParser(description="Gera as regras de risco hidrológico e de deslizamento.")
    parser.add_argument("--simular", action="store_true", help="gera e valida, sem gravar")
    args = parser.parse_args()
    resultado = rodar_treinamento_chuva()
    relatorio(resultado)
    if args.simular:
        log("Simulação: nada foi gravado.")
    else:
        log(f"{gravar_regras_chuva(resultado)} regra(s) gravada(s) em CS_EVENTOS_PREDICAO.")


if __name__ == "__main__":
    main()
