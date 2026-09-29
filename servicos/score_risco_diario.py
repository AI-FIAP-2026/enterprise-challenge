# -*- coding: utf-8 -*-
"""
Score de Risco diário -> CS_SCORE_GESTAO (roda no pipeline, depois dos serviços de clima e alertas)

Calcula o score de todos os clientes com equipamentos uma vez por dia (se o dia já tem cálculo, não faz nada).
Com um cálculo por dia, a página Score Risk mostra a evolução do score, a variação desde o cálculo anterior e os
alertas de piora (cliente que subiu de classe). As regras são as de requisitos/score_risco.py; cada linha grava a
versão da matriz, as regras e o detalhe do cálculo.
"""
import sys
import os

# --- AJUSTE DE DIRETÓRIO PARA O PIPELINE E EXECUÇÃO ISOLADA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)
# -------------------------------------------------------------

import datetime
import logging
import oracledb
from auth import USER, PASSWORD, DSN
from requisitos import score_risco as sr

# Mensagens vão para o console e, quando executado pelo pipeline, também para logs/pipeline.log
logger = logging.getLogger(__name__)


def ja_calculado_hoje(cursor, hoje):
    cursor.execute("SELECT COUNT(*) FROM CS_SCORE_GESTAO WHERE DATA_CALCULO >= :inicio",
                   {"inicio": datetime.datetime.combine(hoje, datetime.time())})
    return int(cursor.fetchone()[0]) > 0


def executar():
    logger.info("[SCORE DIÁRIO] Calculando o Score de Risco dos clientes...")
    connection = None
    hoje = datetime.date.today()
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        with connection.cursor() as cursor:
            if ja_calculado_hoje(cursor, hoje):
                msg = "Score de hoje já calculado."
                logger.info(f"[SCORE DIÁRIO] {msg}")
                return "SUCESSO", msg, 0
        clientes = sr.clientes_com_equipamentos(connection)
        linhas, erros = 0, []
        for n, c in enumerate(clientes, start=1):
            try:
                r = sr.calcular_cliente(connection, c["ID"], hoje=hoje)
                sr.gravar(connection, r)
                connection.commit()
                linhas += len(r["fazendas"])
                logger.info(f"   -> {n}/{len(clientes)} {r['nome']}: {r['score']:.2f} ({r['classe']})")
            except oracledb.Error as e:
                connection.rollback()
                erros.append(f"{c['RAZAO_SOCIAL']}: {e}")
                logger.error(f"   -> {c['RAZAO_SOCIAL']}: erro no cálculo ({e})")
        msg = f"{len(clientes) - len(erros)} cliente(s) calculado(s), {linhas} linha(s) gravada(s)"
        if erros:
            msg += f"; {len(erros)} com erro: " + " | ".join(erros)[:1500]
            return ("ALERTA" if len(erros) < len(clientes) else "ERRO"), msg, linhas
        logger.info(f"[SCORE DIÁRIO] {msg}.")
        return "SUCESSO", msg, linhas
    except oracledb.Error as e:
        logger.error(f"[SCORE DIÁRIO] Erro no banco: {e}")
        return "ERRO", f"Erro no banco: {e}", 0
    finally:
        if connection is not None:
            connection.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    print(executar())
