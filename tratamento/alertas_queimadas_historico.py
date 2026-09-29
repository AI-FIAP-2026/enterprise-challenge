# -*- coding: utf-8 -*-
"""
Carga do HISTÓRICO de focos de queimada do INPE -> CS_ALERTAS (rodar à mão)

Duas etapas, com as mesmas regras do serviço do pipeline (servicos/alertas_queimadas.py):
risco de fogo acima de 40%, até 50 km de alguma fazenda, sem duplicar (CHAVE_FOCO) e atualizando o que mudou.
  1) Mensal: arquivos .../focos/csv/mensal/Brasil/ (começam em 01/2023) até o mês passado
  2) Diário: arquivos .../focos/csv/diario/Brasil/ do dia 1º do mês atual até ONTEM
Hoje em diante fica com o pipeline (diário + 10 minutos).

Uso:
    python tratamento/queimadas_historico.py                         (tudo: 01/2023 até ontem)
    python tratamento/queimadas_historico.py --inicio 2024-09        (retoma os meses a partir de 09/2024, depois o diário)
    python tratamento/queimadas_historico.py --somente-diario        (só do dia 1º do mês atual até ontem)
    python tratamento/queimadas_historico.py --inicio 2024-09 --fim 2024-09   (reprocessa só um mês, sem o diário)
    python tratamento/queimadas_historico.py --amostra 0              (não sorteia meses para conferir)
    python tratamento/queimadas_historico.py --completo               (baixa e confere tudo, mais lento)

Rodar de novo não duplica nada. Para ser rápido, os meses que JÁ têm focos no banco não são
baixados de novo (conferência amostral):
  - mês sem nenhum foco no banco ............ carregado inteiro;
  - mês que pode ter parado no meio ......... reprocessado (o último mês com dados antes de um mês vazio,
                                              e o último mês do período);
  - demais meses já carregados .............. pulados, exceto uma amostra sorteada (--amostra, padrão 3)
                                              que é baixada e conferida;
  - nos arquivos reprocessados, os textos dos focos que já existem são conferidos em 1 a cada 20;
    focos novos são sempre gravados e os que saíram das regras são sempre removidos.
  --completo volta ao modo antigo: baixa todos os meses e confere todos os focos.
Se a execução parar no meio, o log mostra o comando para retomar.
Dica no Mac: caffeinate -i python tratamento/queimadas_historico.py
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import time
import random
import argparse
import logging
import datetime
import oracledb
from auth import USER, PASSWORD, DSN
from servicos.alertas_queimadas import (
    Carregador, listar_diretorio, escolher_arquivo, URL_MENSAL, URL_DIARIO, RISCO_MINIMO, RAIO_MAXIMO_FAZENDA_KM,
)

logger = logging.getLogger("queimadas_historico")

PRIMEIRO_MES_DISPONIVEL = (2023, 1)   # os arquivos mensais do INPE começam em 01/2023
AMOSTRA_DETALHES = 20                 # nos arquivos reprocessados, confere os textos de 1 a cada 20 focos já gravados


def ler_mes(texto):
    try:
        data = datetime.datetime.strptime(texto, "%Y-%m")
        return data.year, data.month
    except ValueError:
        raise argparse.ArgumentTypeError(f"Use o formato AAAA-MM (ex.: 2024-09), recebido: {texto}")


def proximo_mes(ano, mes):
    return (ano + 1, 1) if mes == 12 else (ano, mes + 1)


def mes_anterior(ano, mes):
    return (ano - 1, 12) if mes == 1 else (ano, mes - 1)


def meses_no_banco(connection):
    """{(ano, mes): quantidade} dos focos confirmados do INPE por mês (UTC, o mesmo mês do arquivo).
    O mês vem da CHAVE_FOCO ('SATELITE|AAAAMMDDHHMM|lat|lon'), então bate exatamente com o arquivo mensal."""
    cursor = connection.cursor()
    cursor.execute("""
        SELECT SUBSTR(CHAVE_FOCO, INSTR(CHAVE_FOCO, '|') + 1, 6) AS MES, COUNT(*)
        FROM CS_ALERTAS
        WHERE ORIGEM_ALERTA = 'INPE' AND CHAVE_FOCO IS NOT NULL AND CATEGORIA_RISCO <> 'Aguardando risco'
        GROUP BY SUBSTR(CHAVE_FOCO, INSTR(CHAVE_FOCO, '|') + 1, 6)
    """)
    resultado = {}
    for mes, quantidade in cursor.fetchall():
        try:
            resultado[(int(mes[:4]), int(mes[4:6]))] = quantidade
        except (TypeError, ValueError):
            continue
    cursor.close()
    return resultado


def planejar_meses(meses, carregados, amostra):
    """Decide o que fazer com cada mês: 'carregar', 'reprocessar', 'amostra' ou 'pular'."""
    plano = {}
    for i, mes in enumerate(meses):
        if mes not in carregados:
            plano[mes] = "carregar"
        elif i == len(meses) - 1 or meses[i + 1] not in carregados:
            plano[mes] = "reprocessar"   # pode ter sido interrompido no meio
        else:
            plano[mes] = "pular"
    candidatos = [m for m in meses if plano[m] == "pular"]
    for mes in random.sample(candidatos, min(amostra, len(candidatos))):
        plano[mes] = "amostra"
    return plano


def main():
    hoje = datetime.date.today()
    ontem = hoje - datetime.timedelta(days=1)
    ultimo_mes_fechado = mes_anterior(hoje.year, hoje.month)

    parser = argparse.ArgumentParser(description="Carga do histórico de queimadas (mensal + diário até ontem).")
    parser.add_argument("--inicio", type=ler_mes, default=PRIMEIRO_MES_DISPONIVEL,
                        help="primeiro mês, AAAA-MM (padrão 2023-01)")
    parser.add_argument("--fim", type=ler_mes, default=ultimo_mes_fechado,
                        help="último mês, AAAA-MM (padrão: mês passado). Com --fim anterior ao mês passado, o diário não roda")
    parser.add_argument("--somente-diario", action="store_true",
                        help="pula os meses e carrega só os dias do mês atual até ontem")
    parser.add_argument("--amostra", type=int, default=3,
                        help="quantos meses já carregados sortear para baixar e conferir (padrão 3)")
    parser.add_argument("--completo", action="store_true",
                        help="baixa todos os meses e confere todos os focos (lento)")
    args = parser.parse_args()

    # Etapa 1: meses fechados
    meses = []
    if not args.somente_diario:
        atual = max(args.inicio, PRIMEIRO_MES_DISPONIVEL)
        fim = min(args.fim, ultimo_mes_fechado)
        while atual <= fim:
            meses.append(atual)
            atual = proximo_mes(*atual)

    # Etapa 2: dias do mês atual até ontem (só quando a carga vai até o mês passado)
    dias = []
    if args.somente_diario or args.fim >= ultimo_mes_fechado:
        dia = hoje.replace(day=1)
        while dia <= ontem:
            dias.append(dia)
            dia += datetime.timedelta(days=1)

    if not meses and not dias:
        logger.info("Nada a carregar no período informado (no dia 1º do mês, o diário até ontem fica vazio).")
        return 0

    logger.info("=" * 70)
    logger.info(" HISTÓRICO DE QUEIMADAS")
    if meses:
        logger.info(f"   Etapa 1 - Mensal: {len(meses)} mês(es), de {meses[0][1]:02d}/{meses[0][0]} a {meses[-1][1]:02d}/{meses[-1][0]}")
    if dias:
        logger.info(f"   Etapa 2 - Diário: {len(dias)} dia(s), de {dias[0]:%d/%m/%Y} a {dias[-1]:%d/%m/%Y}")
    logger.info(f"   Regras: risco de fogo acima de {RISCO_MINIMO:.0%} | até {RAIO_MAXIMO_FAZENDA_KM} km de alguma fazenda")
    logger.info(f"   Modo: {'COMPLETO (confere tudo)' if args.completo else 'rápido (meses já no banco: conferência amostral)'}")
    logger.info("=" * 70)

    inicio_execucao = time.time()
    total_novos = 0
    connection = None
    retomar = "python tratamento/queimadas_historico.py"

    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        carregador = Carregador(connection)
        amostra_detalhes = None if args.completo else AMOSTRA_DETALHES

        # ---------------- ETAPA 1: MENSAL ----------------
        if meses:
            if args.completo:
                plano = {m: "reprocessar" for m in meses}
            else:
                logger.info(" Verificando os meses que já estão no banco...")
                carregados = meses_no_banco(connection)
                plano = planejar_meses(meses, carregados, max(args.amostra, 0))
                resumo = {acao: [f"{m:02d}/{a}" for (a, m) in meses if plano[(a, m)] == acao]
                          for acao in ("carregar", "reprocessar", "amostra", "pular")}
                logger.info(f"   Sem dados no banco (carregar inteiro): {len(resumo['carregar'])} "
                            f"{'(' + ', '.join(resumo['carregar']) + ')' if resumo['carregar'] else ''}")
                logger.info(f"   Podem ter parado no meio (reprocessar): {len(resumo['reprocessar'])} "
                            f"{'(' + ', '.join(resumo['reprocessar']) + ')' if resumo['reprocessar'] else ''}")
                logger.info(f"   Amostra sorteada para conferir: {len(resumo['amostra'])} "
                            f"{'(' + ', '.join(resumo['amostra']) + ')' if resumo['amostra'] else ''}")
                logger.info(f"   Já carregados, pulados: {len(resumo['pular'])}")
            a_processar = [m for m in meses if plano[m] != "pular"]
            nomes = listar_diretorio(URL_MENSAL) if a_processar else []
            if nomes is None:
                logger.error(f"Não foi possível acessar {URL_MENSAL}. Verifique a internet e rode de novo.")
                return 1
            for i, (ano, mes) in enumerate(a_processar, start=1):
                retomar = f"python tratamento/queimadas_historico.py --inicio {ano}-{mes:02d}"
                periodo = f"{ano}{mes:02d}"
                nome = escolher_arquivo(nomes, periodo)
                if not nome:
                    logger.error(f"Arquivo do mês {mes:02d}/{ano} não encontrado em {URL_MENSAL}.")
                    logger.error(f"Para retomar depois: {retomar}")
                    return 1
                c = carregador.processar_arquivo(URL_MENSAL + nome, nome, [f"%|{periodo}______|%"],
                                                 f"   [Mensal {i}/{len(a_processar)} - {plano[(ano, mes)]}]",
                                                 exige_risco=True, amostra_detalhes=amostra_detalhes)
                if c is None:
                    logger.error(f"Falha no download de {nome}.")
                    logger.error(f"Para retomar: {retomar}")
                    return 1
                total_novos += c["novos"]
                restante_min = (time.time() - inicio_execucao) / i * (len(a_processar) - i) / 60
                logger.info(f" ✔ {mes:02d}/{ano} concluído ({i}/{len(a_processar)}). Total de novos focos: {total_novos:,}. "
                            f"Tempo estimado dos meses restantes: {restante_min:.0f} min")

        # ---------------- ETAPA 2: DIÁRIO ATÉ ONTEM ----------------
        if dias:
            retomar = "python tratamento/queimadas_historico.py --somente-diario"
            nomes = listar_diretorio(URL_DIARIO)
            if nomes is None:
                logger.error(f"Não foi possível acessar {URL_DIARIO}. Verifique a internet.")
                logger.error(f"Para retomar: {retomar}")
                return 1
            nao_publicados = []
            for i, dia in enumerate(dias, start=1):
                periodo = dia.strftime("%Y%m%d")
                nome = escolher_arquivo(nomes, periodo)
                if not nome:
                    nao_publicados.append(f"{dia:%d/%m}")
                    continue
                c = carregador.processar_arquivo(URL_DIARIO + nome, nome, [f"%|{periodo}____|%"],
                                                 f"   [Diário {i}/{len(dias)}]", exige_risco=True,
                                                 amostra_detalhes=amostra_detalhes)
                if c is None:
                    logger.error(f"Falha no download de {nome}.")
                    logger.error(f"Para retomar: {retomar}")
                    return 1
                total_novos += c["novos"]
                logger.info(f" ✔ {dia:%d/%m/%Y} concluído ({i}/{len(dias)}). Total de novos focos: {total_novos:,}")
            if nao_publicados:
                logger.warning(f" [AVISO] Sem arquivo diário no INPE para: {', '.join(nao_publicados)}")

    except KeyboardInterrupt:
        logger.warning("Interrompido manualmente.")
        logger.warning(f"Para retomar: {retomar}")
        return 1
    except Exception as e:
        if connection:
            connection.rollback()
        logger.error(f"Falha: {e}")
        logger.error(f"Para retomar: {retomar}")
        return 1
    finally:
        if connection:
            connection.close()

    logger.info("=" * 70)
    logger.info(f" HISTÓRICO CONCLUÍDO: {len(meses)} mês(es) e {len(dias)} dia(s), {total_novos:,} novos focos, "
                f"em {(time.time() - inicio_execucao) / 60:.0f} min.")
    logger.info(" Daqui em diante o pipeline mantém os dados atualizados (diário e 10 minutos).")
    logger.info("=" * 70)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    sys.exit(main())
