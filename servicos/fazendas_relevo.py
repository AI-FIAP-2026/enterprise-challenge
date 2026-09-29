# -*- coding: utf-8 -*-
"""
Relevo das fazendas -> CS_FAZENDAS (ALTITUDE_M, DECLIVIDADE_PCT, CLASSE_RELEVO, ORIENTACAO_ENCOSTA)

O relevo não muda: cada fazenda é calculada uma única vez (só as que ainda estão sem ALTITUDE_M).
Fonte: Open-Meteo Elevation API (Copernicus DEM GLO-90, ~90 m), gratuita, até 100 pontos por chamada.

Como a declividade é calculada:
  Para cada fazenda consultamos a altitude da sede e de 4 pontos ao redor (Norte, Sul, Leste, Oeste),
  a DISTANCIA_M metros de distância. A declividade é a inclinação do plano formado por esses pontos:
      inclinação L-O = (altitude Leste - altitude Oeste) / (2 x distância)
      inclinação N-S = (altitude Norte - altitude Sul)  / (2 x distância)
      declividade %  = raiz(L-O² + N-S²) x 100
  A orientação da encosta é a direção para a qual o terreno desce.
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import math
import time
import logging
import requests
import oracledb
from auth import USER, PASSWORD, DSN

# Mensagens vão para o console e, quando executado pelo pipeline, também para logs/pipeline.log
logger = logging.getLogger(__name__)

URL_ELEVACAO = "https://api.open-meteo.com/v1/elevation"
PONTOS_POR_CHAMADA = 100          # limite da Open-Meteo
PONTOS_POR_FAZENDA = 5            # sede + Norte, Sul, Leste, Oeste
# Distância dos pontos vizinhos. O modelo de altitude tem células de ~90 m; 180 m (2 células)
# mede a inclinação do terreno ao redor da sede sem depender de uma única célula.
DISTANCIA_M = 180
METROS_POR_GRAU_LAT = 111_320
DECLIVIDADE_MINIMA_ORIENTACAO = 1.0  # abaixo de 1% o terreno é tratado como plano (sem orientação)

# Classes de relevo da Embrapa (Sistema Brasileiro de Classificação de Solos)
CLASSES_RELEVO = [
    (3, "Plano"),
    (8, "Suave ondulado"),
    (20, "Ondulado"),
    (45, "Forte ondulado"),
    (75, "Montanhoso"),
]
CLASSE_ACIMA = "Escarpado"
DIRECOES = ["N", "NE", "L", "SE", "S", "SO", "O", "NO"]


def classificar_relevo(declividade_pct):
    for limite, classe in CLASSES_RELEVO:
        if declividade_pct <= limite:
            return classe
    return CLASSE_ACIMA


def pontos_da_fazenda(lat, lon):
    """Sede + 4 pontos vizinhos (Norte, Sul, Leste, Oeste) a DISTANCIA_M metros."""
    dlat = DISTANCIA_M / METROS_POR_GRAU_LAT
    dlon = DISTANCIA_M / (METROS_POR_GRAU_LAT * max(math.cos(math.radians(lat)), 0.01))
    return [
        (lat, lon),              # sede
        (lat + dlat, lon),       # Norte
        (lat - dlat, lon),       # Sul
        (lat, lon + dlon),       # Leste
        (lat, lon - dlon),       # Oeste
    ]


def calcular_relevo(altitudes):
    """altitudes = [sede, norte, sul, leste, oeste] -> (altitude, declividade %, classe, orientação)"""
    sede, norte, sul, leste, oeste = altitudes
    inclinacao_leste = (leste - oeste) / (2 * DISTANCIA_M)   # sobe para o Leste se > 0
    inclinacao_norte = (norte - sul) / (2 * DISTANCIA_M)     # sobe para o Norte se > 0
    declividade = math.hypot(inclinacao_leste, inclinacao_norte) * 100

    if declividade < DECLIVIDADE_MINIMA_ORIENTACAO:
        orientacao = "Plano"
    else:
        # Direção da descida (sentido oposto à subida), em graus a partir do Norte, sentido horário
        azimute = math.degrees(math.atan2(-inclinacao_leste, -inclinacao_norte)) % 360
        orientacao = DIRECOES[int((azimute + 22.5) // 45) % 8]

    return round(sede, 1), round(declividade, 2), classificar_relevo(declividade), orientacao


def buscar_altitudes(pontos, descricao):
    """Altitude de até 100 pontos numa chamada. Respeita o limite de uso (HTTP 429)."""
    params = {
        "latitude": ",".join(f"{lat:.6f}" for lat, _ in pontos),
        "longitude": ",".join(f"{lon:.6f}" for _, lon in pontos),
    }
    for tentativa in range(1, 4):
        try:
            response = requests.get(URL_ELEVACAO, params=params, timeout=60)
            if response.status_code == 200:
                altitudes = response.json().get("elevation", [])
                if len(altitudes) == len(pontos):
                    return altitudes
                logger.warning(f"   [AVISO] {descricao}: Open-Meteo devolveu {len(altitudes)} altitudes para {len(pontos)} pontos.")
            elif response.status_code == 429:
                logger.warning(f"   [AVISO] Limite de uso da Open-Meteo atingido ({descricao}). Pausando 60s (tentativa {tentativa}/3)...")
                time.sleep(60)
                continue
            else:
                logger.warning(f"   [AVISO] Open-Meteo retornou HTTP {response.status_code} para {descricao} "
                               f"(tentativa {tentativa}/3): {response.text[:200]}")
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"   [AVISO] Falha ao consultar a Open-Meteo para {descricao} (tentativa {tentativa}/3): {e}")
        time.sleep(5)
    return None


def executar():
    logger.info("[RELEVO FAZENDAS] Calculando altitude e declividade das fazendas ainda sem relevo...")

    connection = None
    cursor = None
    total_atualizadas = 0

    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()

        cursor.execute("""
            SELECT ID, NOME_FAZENDA, LATITUDE, LONGITUDE FROM CS_FAZENDAS
            WHERE LATITUDE IS NOT NULL AND LONGITUDE IS NOT NULL AND ALTITUDE_M IS NULL
            ORDER BY ID
        """)
        fazendas = cursor.fetchall()

        if not fazendas:
            msg = "Relevo de todas as fazendas já calculado."
            logger.info(f"[RELEVO FAZENDAS] {msg}")
            return "SUCESSO", msg, 0

        fazendas_por_chamada = PONTOS_POR_CHAMADA // PONTOS_POR_FAZENDA
        lotes = [fazendas[i:i + fazendas_por_chamada] for i in range(0, len(fazendas), fazendas_por_chamada)]
        logger.info(f"   -> {len(fazendas)} fazenda(s) sem relevo, em {len(lotes)} chamada(s) à Open-Meteo.")

        sql_update = """
            UPDATE CS_FAZENDAS
            SET ALTITUDE_M = :1, DECLIVIDADE_PCT = :2, CLASSE_RELEVO = :3, ORIENTACAO_ENCOSTA = :4
            WHERE ID = :5
        """
        sem_resposta = []

        for n, lote in enumerate(lotes, start=1):
            pontos = []
            for _, _, lat, lon in lote:
                pontos.extend(pontos_da_fazenda(float(lat), float(lon)))

            altitudes = buscar_altitudes(pontos, f"lote {n}/{len(lotes)}")
            if altitudes is None:
                sem_resposta.extend(nome for _, nome, _, _ in lote)
                continue

            registros = []
            for k, (id_fazenda, nome, _, _) in enumerate(lote):
                valores = altitudes[k * PONTOS_POR_FAZENDA:(k + 1) * PONTOS_POR_FAZENDA]
                if any(v is None for v in valores):
                    sem_resposta.append(nome)
                    continue
                altitude, declividade, classe, orientacao = calcular_relevo([float(v) for v in valores])
                registros.append([altitude, declividade, classe, orientacao, id_fazenda])

            if registros:
                cursor.executemany(sql_update, registros)
                connection.commit()
            total_atualizadas += len(registros)
            logger.info(f"   [{n}/{len(lotes)}] {len(registros)} fazenda(s) atualizada(s). Total: {total_atualizadas}")

        msg = f"Relevo calculado para {total_atualizadas} fazenda(s)."
        if sem_resposta:
            msg += (f" Sem altitude para {len(sem_resposta)} fazenda(s), que serão tentadas na próxima execução: "
                    f"{', '.join(sem_resposta[:10])}.")
            logger.warning(f"[RELEVO FAZENDAS] {msg}")
            return "ALERTA", msg, total_atualizadas

        logger.info(f"[RELEVO FAZENDAS] {msg}")
        return "SUCESSO", msg, total_atualizadas

    except Exception as e:
        if connection:
            connection.rollback()
        msg_erro = f"Falha no cálculo do relevo das fazendas: {str(e)}"
        if "ALTITUDE_M" in str(e).upper() or "ORA-00904" in str(e):
            msg_erro += " (Rode antes o script sql/estrutura_banco.sql para criar as colunas.)"
        logger.error(f"[ERRO GERAL] {msg_erro}")
        return "ERRO", msg_erro, total_atualizadas
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("--- EXECUTANDO RELEVO FAZENDAS (Isolada) ---")
    executar()
