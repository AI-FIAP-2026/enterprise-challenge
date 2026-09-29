# -*- coding: utf-8 -*-
import sys
import os

# --- AJUSTE DE DIRETÓRIO PARA O PIPELINE E EXECUÇÃO ISOLADA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)
# -------------------------------------------------------------

import time
import logging
import unicodedata
import datetime
import requests
import oracledb
from auth import USER, PASSWORD, DSN

# Mensagens vão para o console e, quando executado pelo pipeline, também para logs/pipeline.log
logger = logging.getLogger(__name__)

# Os endereços por município (sws.cemaden.gov.br/PED/api/alertas/municipio/... e
# painelalertas.cemaden.gov.br/api/alertas?ibge=...) não existem. O Painel de Alertas do
# Cemaden publica TODOS os alertas do país em um único JSON: {"atualizado": ..., "alertas": [...]}
URL_CEMADEN = "https://painelalertas.cemaden.gov.br/wsAlertas2"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"}

# Níveis do Cemaden -> categorias usadas na Central de Alertas (CONFIG_RISCO)
MAPA_NIVEIS = {
    "moderado": "Médio",
    "alto": "Alto",
    "muito alto": "Crítico",
}


def normalizar(texto):
    """Remove acentos, espaços extras e caixa para comparar nomes de municípios."""
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return " ".join(texto.lower().split())


def converter_nivel(nivel):
    return MAPA_NIVEIS.get(normalizar(nivel), str(nivel or "Médio").strip().title())


def converter_data(valor):
    """O Cemaden já retornou a data em mais de um formato; devolve sempre 'YYYY-MM-DD HH24:MI:SS'."""
    texto = str(valor or "").replace("T", " ").strip()
    for formato in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S %Z", "%d-%m-%Y %H:%M:%S",
                    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M"):
        try:
            return datetime.datetime.strptime(texto, formato).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    # Formatos com fuso (ex.: '2025-05-29 10:00:00 BRT') caem aqui: aproveita só a parte da data/hora
    try:
        return datetime.datetime.strptime(texto[:19], "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def alerta_aberto(status):
    try:
        return int(status) == 1
    except (TypeError, ValueError):
        return normalizar(status) in {"1", "true", "aberto", "open", "ativo"}


def obter_recomendacao_agricola(tipo_alerta, categoria_risco):
    """Gera orientações operacionais específicas para maquinário agrícola e lavoura com base no risco."""
    tipo = normalizar(tipo_alerta)
    risco = normalizar(categoria_risco)

    if "hidrolog" in tipo or "chuva" in tipo or "inunda" in tipo or "enxurrada" in tipo:
        if risco in ["alto", "critico"]:
            return (
                "⚠️ PARALISAÇÃO RECOMENDADA: Risco iminente de alagamentos e enxurradas. "
                "Retire imediatamente tratores, colhedoras e frotas de áreas baixas e margens de rios."
            )
        else:
            return (
                "ℹ️ ATENÇÃO REDOBRADA (Hidrológico): Evite tráfego de máquinas pesadas em solos encharcados "
                "para prevenir compactação severa."
            )
    elif "geolog" in tipo or "mov" in tipo or "massa" in tipo or "deslizamento" in tipo:
        return (
            "🛑 ALERTA MÁXIMO DE ENCOSTA: Alto risco de deslizamentos nas imediações. "
            "Proibida a circulação de frotas e maquinários próximos a taludes e estradas vicinais instáveis."
        )
    else:
        return "Acompanhe os boletins meteorológicos locais e redobre a vigilância nas operações de campo."


def baixar_alertas_cemaden():
    for tentativa in range(1, 4):
        try:
            response = requests.get(URL_CEMADEN, headers=HEADERS, timeout=30)
            if response.status_code == 200:
                dados = response.json()
                # O serviço devolve um objeto com a lista em "alertas"; aceita também uma lista direta
                if isinstance(dados, dict):
                    return dados.get("alertas") or []
                return dados or []
            logger.warning(f"   -> [AVISO] Tentativa {tentativa} retornou HTTP {response.status_code}.")
        except (requests.RequestException, ValueError) as e:
            logger.warning(f"   -> [AVISO] Tentativa {tentativa} falhou: {e}")
        time.sleep(5)
    return None


def executar():
    logger.info("[CEMADEN - TEMPO REAL] Sincronizando alertas vigentes do Painel de Alertas...")

    alertas = baixar_alertas_cemaden()
    if alertas is None:
        msg_erro = f"Falha de conexão ao acessar {URL_CEMADEN}."
        logger.warning(f"   -> [ALERTA] {msg_erro}")
        return "ALERTA", msg_erro, 0

    alertas_abertos = [a for a in alertas if alerta_aberto(a.get("status", 1))]
    logger.info(f"   -> {len(alertas)} alerta(s) recebido(s), {len(alertas_abertos)} vigente(s).")

    connection = None
    cursor = None
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()

        # CS_MUNICIPIOS completa o código IBGE quando o Cemaden não o envia (necessário para o vínculo com CS_FAZENDAS)
        cursor.execute("SELECT MUNICIPIO_IBGE, MUNICIPIO, UF FROM CS_MUNICIPIOS WHERE MUNICIPIO_IBGE IS NOT NULL")
        mapa_por_ibge = {}
        mapa_por_nome = {}
        for ibge, municipio, uf in cursor.fetchall():
            ibge = str(ibge).strip()
            mapa_por_ibge[ibge] = {"municipio": municipio, "uf": uf}
            mapa_por_nome[(normalizar(municipio), normalizar(uf))] = ibge

        total_registros_processados = 0

        for evento in alertas_abertos:
            municipio_nome = evento.get("municipio") or ""
            uf_nome = evento.get("uf") or ""
            ibge = str(evento.get("codibge") or "").strip()
            if not ibge:
                ibge = mapa_por_nome.get((normalizar(municipio_nome), normalizar(uf_nome)), "")
            if ibge in mapa_por_ibge:
                municipio_nome = mapa_por_ibge[ibge]["municipio"]
                uf_nome = mapa_por_ibge[ibge]["uf"]

            tipo_alerta = evento.get("evento") or "Risco de Desastre Natural"
            nivel = converter_nivel(evento.get("nivel"))
            data_str = converter_data(evento.get("datahoracriacao"))
            cod_alerta = str(evento.get("cod_alerta") or "").strip()

            latitude = evento.get("latitude")
            longitude = evento.get("longitude")
            try:
                latitude = float(latitude) if latitude not in (None, "") else None
                longitude = float(longitude) if longitude not in (None, "") else None
            except (TypeError, ValueError):
                latitude, longitude = None, None

            det1 = f"Município: {municipio_nome} - UF: {uf_nome} (IBGE: {ibge})"
            det2 = f"Alerta Cemaden nº {cod_alerta} - Nível original: {evento.get('nivel', '')}"

            # Um mesmo alerta (cod_alerta) só é regravado se o nível mudar
            if cod_alerta:
                cursor.execute("""
                    SELECT COUNT(1) FROM CS_ALERTAS
                    WHERE ORIGEM_ALERTA = 'CEMADEN' AND DETALHAMENTO_2 = :1 AND CATEGORIA_RISCO = :2
                """, [det2, nivel])
            else:
                cursor.execute("""
                    SELECT COUNT(1) FROM CS_ALERTAS
                    WHERE ORIGEM_ALERTA = 'CEMADEN' AND DETALHAMENTO_1 = :1
                      AND DATA_HORA = TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS') AND TIPO_ALERTA = :3
                """, [det1, data_str, tipo_alerta])

            if cursor.fetchone()[0] == 0:
                orientacao_agricola = obter_recomendacao_agricola(tipo_alerta, nivel)
                cursor.execute("""
                    INSERT INTO CS_ALERTAS (TIPO_ALERTA, ORIGEM_ALERTA, DATA_HORA, CATEGORIA_RISCO, LATITUDE, LONGITUDE, ORIENTACAO, DETALHAMENTO_1, DETALHAMENTO_2)
                    VALUES (:1, 'CEMADEN', TO_TIMESTAMP(:2, 'YYYY-MM-DD HH24:MI:SS'), :3, :4, :5, :6, :7, :8)
                """, [tipo_alerta, data_str, nivel, latitude, longitude, orientacao_agricola, det1, det2])
                total_registros_processados += 1
                logger.info(f"   ⚡ {tipo_alerta} ({nivel}) em {municipio_nome}/{uf_nome}")

        connection.commit()
        mensagem_sucesso = f"Sincronização do Cemaden concluída. {total_registros_processados} novo(s) alerta(s) gravado(s)."
        logger.info(f"   -> {mensagem_sucesso}")
        return "SUCESSO", mensagem_sucesso, total_registros_processados

    except Exception as e:
        if connection:
            connection.rollback()
        mensagem_erro = f"Falha crítica no processo do Cemaden: {str(e)}"
        logger.error(f"[ERRO GERAL] {mensagem_erro}")
        return "ERRO", mensagem_erro, 0
    finally:
        if cursor:
            cursor.close()
        if connection:
            connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger.info("--- EXECUTANDO SERVIÇO CEMADEN (Isolada) ---")
    executar()
