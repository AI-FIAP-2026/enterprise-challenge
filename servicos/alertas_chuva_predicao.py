# -*- coding: utf-8 -*-
"""
Previsão de risco de chuva por fazenda -> CS_ALERTAS_PREDICAO (roda no pipeline)

Todo dia, para cada fazenda:
  1) busca na Open-Meteo a chuva diária dos últimos DIAS_PASSADOS dias e a previsão até +3 dias;
  2) calcula a chuva acumulada (dia, 72 horas, 7 dias e 30 dias) de amanhã a +3 dias;
  3) aplica as regras de CS_EVENTOS_PREDICAO dos tipos 'Hidrológico' e 'Deslizamento' (requisitos/risco_chuva.py;
     no deslizamento, a declividade da fazenda também conta). A regra mais grave define o nível, a chance e o motivo;
  4) grava/atualiza uma linha por fazenda, tipo e dia em CS_ALERTAS_PREDICAO (a Central de Alertas mostra os avisos
     Alto e Crítico).
As regras são geradas por modelos/ml_alertas_chuva_predicao.py (à mão ou pela página Monitoramento).
"""
import sys
import os

# --- AJUSTE DE DIRETÓRIO PARA O PIPELINE E EXECUÇÃO ISOLADA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)
# -------------------------------------------------------------

import logging
import datetime
import oracledb
from auth import USER, PASSWORD, DSN
from servicos.fazendas_clima import buscar_open_meteo, URL_PREVISAO
from servicos.alertas_queimadas_predicao import SQL_MERGE, arredondar
from requisitos import risco_chuva as rc

# Mensagens vão para o console e, quando executado pelo pipeline, também para logs/pipeline.log
logger = logging.getLogger(__name__)

DIAS_PASSADOS = 35               # 30 dias de chuva acumulada antes do primeiro dia previsto
DIAS_PREVISAO = 4                # hoje + 3 dias; gravados só amanhã a +3
PRIMEIRO_HORIZONTE = 1
FAZENDAS_POR_LOTE = 50


def chuva_da_resposta(resposta):
    diario = (resposta or {}).get("daily", {})
    chuva = {}
    for data, valor in zip(diario.get("time", []), diario.get("precipitation_sum", [])):
        if valor is not None:
            chuva[datetime.date.fromisoformat(data)] = float(valor)
    return chuva


def executar():
    logger.info("[PREVISÃO - CHUVA] Calculando o risco hidrológico e de deslizamento das fazendas (próximos 3 dias)...")
    connection = None
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()
        regras = {tipo: rc.carregar_regras(cursor, tipo) for tipo in rc.TIPOS}
        if not any(regras.values()):
            msg = ("Nenhuma regra de risco hidrológico ou de deslizamento em CS_EVENTOS_PREDICAO. "
                   "Gere as regras com: python modelos/ml_alertas_chuva_predicao.py")
            logger.warning(f"   -> [AVISO] {msg}")
            return "ALERTA", msg, 0
        logger.info("   -> Regras: " + ", ".join(f"{tipo} {len(r)}" for tipo, r in regras.items()))

        cursor.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CS_FAZENDAS' "
                       "AND COLUMN_NAME = 'DECLIVIDADE_PCT'")
        tem_declividade = cursor.fetchone() is not None
        cursor.execute(f"""
            SELECT ID, NOME_FAZENDA, ESTADO, LATITUDE, LONGITUDE{', DECLIVIDADE_PCT' if tem_declividade else ''}
            FROM CS_FAZENDAS WHERE LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL ORDER BY ID
        """)
        fazendas = [{"id": l[0], "nome": l[1], "uf": l[2], "lat": float(l[3]), "lon": float(l[4]),
                     "declividade": (float(l[5]) if tem_declividade and l[5] is not None else None)}
                    for l in cursor.fetchall()]
        if not fazendas:
            return "ALERTA", "Nenhuma fazenda com coordenadas em CS_FAZENDAS.", 0

        hoje = datetime.datetime.now(datetime.timezone.utc).date()
        registros, sem_clima, contagem = [], [], {}
        for inicio in range(0, len(fazendas), FAZENDAS_POR_LOTE):
            lote = fazendas[inicio:inicio + FAZENDAS_POR_LOTE]
            respostas = buscar_open_meteo(URL_PREVISAO, {
                "latitude": ",".join(f"{f['lat']:.4f}" for f in lote),
                "longitude": ",".join(f"{f['lon']:.4f}" for f in lote),
                "daily": "precipitation_sum", "past_days": DIAS_PASSADOS, "forecast_days": DIAS_PREVISAO,
            }, f"chuva diária de {len(lote)} fazenda(s)")
            if respostas is None or len(respostas) != len(lote):
                sem_clima.extend(f["nome"] for f in lote)
                continue
            for fazenda, resposta in zip(lote, respostas):
                chuva = chuva_da_resposta(resposta)
                for horizonte in range(PRIMEIRO_HORIZONTE, DIAS_PREVISAO):
                    dia = hoje + datetime.timedelta(days=horizonte)
                    acum = rc.acumulados(chuva, dia)
                    for tipo, regras_tipo in regras.items():
                        if not regras_tipo:
                            continue
                        nivel, chance, regra, motivo = rc.avaliar(regras_tipo, acum, fazenda["declividade"], fazenda["uf"])
                        contagem[(tipo, horizonte, nivel)] = contagem.get((tipo, horizonte, nivel), 0) + 1
                        registros.append({
                            "id_fazenda": fazenda["id"], "tipo": tipo,
                            "data_ref": datetime.datetime.combine(dia, datetime.time.min), "horizonte": horizonte,
                            "valor": arredondar(chance, 4), "nivel": nivel, "id_regra": regra["id"] if regra else None,
                            "motivos": motivo[:1000], "tmax": None, "umin": None, "vmax": None,
                            "chuva7": arredondar(acum.get(7)), "dias_seco": None})
            logger.info(f"   -> Chuva e regras avaliadas: {min(inicio + FAZENDAS_POR_LOTE, len(fazendas))}/"
                        f"{len(fazendas)} fazendas")

        if registros:
            cursor.executemany(SQL_MERGE, registros)
            connection.commit()

        resumo = []
        for tipo in rc.TIPOS:
            graves = sum(q for (t, h, n), q in contagem.items() if t == tipo and n in rc.NIVEIS_RISCO)
            resumo.append(f"{tipo}: {graves} aviso(s) Alto/Crítico em 3 dias")
        msg = f"Risco de chuva calculado para {len(fazendas) - len(sem_clima)} fazenda(s). " + " | ".join(resumo)
        if sem_clima:
            msg += f" | Sem clima da Open-Meteo: {len(sem_clima)} fazenda(s)."
        logger.info(f"[PREVISÃO - CHUVA] {msg}")
        return ("SUCESSO" if len(sem_clima) < len(fazendas) else "ERRO"), msg[:4000], len(registros)
    except Exception as e:
        if connection:
            connection.rollback()
        msg = f"Falha no cálculo do risco de chuva: {e}"
        logger.error(f"[ERRO GERAL] {msg}")
        return "ERRO", msg, 0
    finally:
        if connection:
            connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    status, mensagem, qtd = executar()
    print(f"\n{status}: {mensagem}")
