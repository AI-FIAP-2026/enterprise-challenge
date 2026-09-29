# -*- coding: utf-8 -*-
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import math
import time
import logging
import datetime
import requests
import oracledb
from auth import USER, PASSWORD, DSN

# Mensagens vão para o console e, quando executado pelo pipeline, também para logs/pipeline.log
logger = logging.getLogger(__name__)

URL_HISTORICO = "https://archive-api.open-meteo.com/v1/archive"
URL_PREVISAO = "https://api.open-meteo.com/v1/forecast"
VARIAVEIS = "temperature_2m,relative_humidity_2m,wind_speed_10m,precipitation"

ANOS_CARGA_INICIAL = 4
# A Open-Meteo aceita várias coordenadas na mesma chamada. Atualizações curtas vão em lotes grandes;
# cargas longas (histórico de anos) vão em lotes pequenos para a resposta não ficar enorme.
FAZENDAS_POR_LOTE_CURTO = 50
FAZENDAS_POR_LOTE_LONGO = 5
DIAS_PERIODO_CURTO = 31
# O histórico (archive) chega com ~2 dias de atraso. As horas mais recentes, ainda vazias no histórico,
# são completadas pela API de previsão (past_days), que traz as últimas horas até agora.
USAR_PREVISAO_PARA_HORAS_RECENTES = True
MAX_PAST_DAYS = 92
TAMANHO_INSERT = 5000


def buscar_open_meteo(url, params, descricao):
    """GET com tentativas; respeita o limite de uso (HTTP 429). Devolve sempre uma lista (uma posição por fazenda)."""
    for tentativa in range(1, 4):
        try:
            response = requests.get(url, params=params, timeout=120)
            if response.status_code == 200:
                dados = response.json()
                return dados if isinstance(dados, list) else [dados]
            if response.status_code == 429:
                logger.warning(f"   [AVISO] Limite de uso da Open-Meteo atingido ({descricao}). Pausando 60s (tentativa {tentativa}/3)...")
                time.sleep(60)
                continue
            logger.warning(f"   [AVISO] Open-Meteo retornou HTTP {response.status_code} para {descricao} "
                           f"(tentativa {tentativa}/3): {response.text[:200]}")
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"   [AVISO] Falha ao consultar a Open-Meteo para {descricao} (tentativa {tentativa}/3): {e}")
        time.sleep(5)
    return None


def extrair_horas(resposta):
    """{data_hora: (temp, umid, vento, chuva)} só com horas completas (sem valores vazios)."""
    hourly = (resposta or {}).get("hourly", {})
    tempos = hourly.get("time", [])
    colunas = [hourly.get(v, []) for v in VARIAVEIS.split(",")]
    horas = {}
    for i, t in enumerate(tempos):
        valores = [c[i] if i < len(c) else None for c in colunas]
        # Temperatura e umidade vazias = dado ainda não disponível. Não grava zero no lugar.
        if valores[0] is None or valores[1] is None:
            continue
        data_hora = datetime.datetime.strptime(t, "%Y-%m-%dT%H:%M")
        horas[data_hora] = tuple(float(v) if v is not None else 0.0 for v in valores)
    return horas


def executar():
    logger.info("[CLIMA FAZENDAS] Iniciando verificação e atualização horária (CS_FAZENDAS_CLIMA - Padrão UTC)...")

    connection = None
    cursor = None
    total_geral_inserido = 0

    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()

        # Uma consulta só para todas as fazendas (antes era um SELECT MAX por fazenda)
        cursor.execute("""
            SELECT f.ID, f.NOME_FAZENDA, f.LATITUDE, f.LONGITUDE, MAX(c.DATA_HORA)
            FROM CS_FAZENDAS f
            LEFT JOIN CS_FAZENDAS_CLIMA c ON c.ID_FAZENDA = f.ID
            WHERE f.LATITUDE IS NOT NULL AND f.LONGITUDE IS NOT NULL
            GROUP BY f.ID, f.NOME_FAZENDA, f.LATITUDE, f.LONGITUDE
        """)
        fazendas = cursor.fetchall()

        if not fazendas:
            msg = "Nenhuma fazenda com coordenadas encontrada na base."
            logger.warning(f"[CLIMA FAZENDAS] {msg}")
            return "ALERTA", msg, 0

        agora_utc = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, minute=0, second=0, microsecond=0)
        inicio_padrao = agora_utc - datetime.timedelta(days=365 * ANOS_CARGA_INICIAL)

        # Agrupa as fazendas pendentes pela data de início: quem começa no mesmo dia vai na mesma chamada
        grupos = {}
        atualizadas = 0
        for id_fazenda, nome, lat, lon, ultima in fazendas:
            data_inicio = (ultima + datetime.timedelta(hours=1)) if ultima else inicio_padrao
            if data_inicio > agora_utc:
                atualizadas += 1
                continue
            grupos.setdefault(data_inicio.date(), []).append({
                "id": id_fazenda, "nome": nome, "lat": float(lat), "lon": float(lon), "ultima": ultima,
            })

        lotes = []
        for data_inicio, grupo in sorted(grupos.items()):
            dias = (agora_utc.date() - data_inicio).days + 1
            tamanho = FAZENDAS_POR_LOTE_CURTO if dias <= DIAS_PERIODO_CURTO else FAZENDAS_POR_LOTE_LONGO
            for i in range(0, len(grupo), tamanho):
                lotes.append((data_inicio, grupo[i:i + tamanho]))

        pendentes = sum(len(g) for _, g in lotes)
        logger.info(f"   -> {len(fazendas)} fazenda(s): {atualizadas} já atualizada(s), {pendentes} a atualizar "
                    f"em {len(lotes)} chamada(s) à Open-Meteo.")
        if not lotes:
            msg = "Clima das fazendas já está atualizado até a última hora disponível."
            logger.info(f"[CLIMA FAZENDAS] {msg}")
            return "SUCESSO", msg, 0

        sql_insert = """
            INSERT INTO CS_FAZENDAS_CLIMA (ID_FAZENDA, TEMPERATURA, UMIDADE, VELOCIDADE_VENTO, PRECIPITACAO, DATA_HORA)
            VALUES (:1, :2, :3, :4, :5, TO_TIMESTAMP(:6, 'YYYY-MM-DD HH24:MI:SS'))
        """
        inicio_execucao = time.time()
        fazendas_sem_dados = []

        for n, (data_inicio, lote) in enumerate(lotes, start=1):
            descricao = f"lote {n}/{len(lotes)} ({len(lote)} fazenda(s) desde {data_inicio:%d/%m/%Y})"
            params = {
                "latitude": ",".join(str(f["lat"]) for f in lote),
                "longitude": ",".join(str(f["lon"]) for f in lote),
                "start_date": data_inicio.strftime("%Y-%m-%d"),
                "end_date": agora_utc.strftime("%Y-%m-%d"),
                "hourly": VARIAVEIS,
            }
            respostas = buscar_open_meteo(URL_HISTORICO, params, descricao)
            if respostas is None:
                fazendas_sem_dados.extend(f["nome"] for f in lote)
                continue
            horas_por_fazenda = [extrair_horas(r) for r in respostas]

            # Horas recentes que o histórico ainda não tem: completa com a API de previsão (past_days)
            if USAR_PREVISAO_PARA_HORAS_RECENTES:
                ultimas = [max(h) if h else None for h in horas_por_fazenda]
                referencia = min((u for u in ultimas if u), default=None) or datetime.datetime.combine(data_inicio, datetime.time())
                dias_faltando = (agora_utc.date() - referencia.date()).days + 1
                if referencia < agora_utc and dias_faltando <= MAX_PAST_DAYS:
                    recentes = buscar_open_meteo(URL_PREVISAO, {
                        "latitude": params["latitude"], "longitude": params["longitude"],
                        "hourly": VARIAVEIS, "past_days": dias_faltando, "forecast_days": 1,
                    }, f"{descricao} - horas recentes")
                    for horas, resposta in zip(horas_por_fazenda, recentes or []):
                        for data_hora, valores in extrair_horas(resposta).items():
                            horas.setdefault(data_hora, valores)

            lote_dados = []
            for fazenda, horas in zip(lote, horas_por_fazenda):
                for data_hora in sorted(horas):
                    if fazenda["ultima"] is not None and data_hora <= fazenda["ultima"]:
                        continue
                    if data_hora > agora_utc:
                        break
                    temp, umid, vento, precip = horas[data_hora]
                    lote_dados.append((fazenda["id"], temp, umid, vento, precip, data_hora.strftime('%Y-%m-%d %H:%M:%S')))

            for i in range(0, len(lote_dados), TAMANHO_INSERT):
                cursor.executemany(sql_insert, lote_dados[i:i + TAMANHO_INSERT])
            connection.commit()
            total_geral_inserido += len(lote_dados)

            decorrido = time.time() - inicio_execucao
            restante = decorrido / n * (len(lotes) - n)
            logger.info(f"   [{n}/{len(lotes)}] {len(lote)} fazenda(s): {len(lote_dados):,} registros horários inseridos. "
                        f"Total: {total_geral_inserido:,} | tempo estimado restante: {math.ceil(restante / 60)} min")

        msg_sucesso = (f"Atualização climática das fazendas concluída. {total_geral_inserido:,} registros inseridos "
                       f"para {pendentes} fazenda(s).")
        if fazendas_sem_dados:
            msg_sucesso += f" Sem resposta da Open-Meteo para {len(fazendas_sem_dados)} fazenda(s): {', '.join(fazendas_sem_dados[:10])}."
            logger.warning(f"[CLIMA FAZENDAS] {msg_sucesso}")
            return "ALERTA", msg_sucesso, total_geral_inserido

        logger.info(f"[CLIMA FAZENDAS] {msg_sucesso}")
        return "SUCESSO", msg_sucesso, total_geral_inserido

    except Exception as e:
        if connection:
            connection.rollback()
        msg_erro = f"Ocorreu um erro durante a atualização climática das fazendas: {str(e)}"
        logger.error(f"[ERRO GERAL] {msg_erro}")
        return "ERRO", msg_erro, total_geral_inserido
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("--- EXECUTANDO CLIMA FAZENDAS (Isolada) ---")
    executar()
