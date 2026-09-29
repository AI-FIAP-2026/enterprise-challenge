# -*- coding: utf-8 -*-
"""
Dados de exemplo para conferir a página "Comprovação" (Manutenções): revisões do equipamento, nível, validação
da Sompo, regra ideal e envio de comprovantes.

Cria um cliente de teste ("Exemplo Comprovação", CNPJ 00000000000000), uma fazenda e uma máquina segurada de um
modelo que já tem manual com recomendações de manutenção gravadas (apólice EXEMPLO-COMPROV-1). Gera o cronograma da
máquina (requisitos/programacao_manutencao.py) e monta um cenário com todas as situações da página:
  - revisões passadas COMPROVADAS, com um PDF de comprovante por revisão: validadas pela Sompo (atende, atende na
    maior parte, atende na menor parte), aguardando validação (aparecem em "Validação Sompo") e "não atende", com
    motivo (volta a pedir comprovante);
  - 2 revisões passadas SEM comprovante e as revisões dos últimos 60 dias sem comprovante (missões);
  - regra ideal: nas recomendações com duas opções ("a cada 1500 horas ou 2 anos"), revisões feitas a mais (no card
    da revisão anterior) nas datas da regra completa do manual em 2 de cada 3 delas (métrica "Regra ideal" e bônus).

Nada é gravado sem este comando; --simular mostra o cenário sem gravar e --remover apaga todo o exemplo.

Uso:
    python tratamento/comprovacao_exemplo.py --simular
    python tratamento/comprovacao_exemplo.py
    python tratamento/comprovacao_exemplo.py --modelo 6075        (escolhe o modelo pelo nome)
    python tratamento/comprovacao_exemplo.py --remover
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import argparse
import datetime

import oracledb

from requisitos.anexos import _inserir_com_id, salvar_anexo, ENTIDADE_MANUTENCAO, ANEXO_COMPROVANTE
from requisitos.programacao_manutencao import (programar_equipamento, orientacoes_do_modelo, valor_realizacao,
                                               valores_realizacao, para_data, vencimentos, tem_duas_opcoes,
                                               inicio_periodo, tem_coluna_regra, periodicidade, REGRA_IDEAL)

CNPJ_EXEMPLO = "00000000000000"
CLIENTE_EXEMPLO = "Exemplo Comprovação"
FAZENDA_EXEMPLO = "Fazenda Exemplo Comprovação"
APOLICE_EXEMPLO = "EXEMPLO-COMPROV-1"
USUARIO_EXEMPLO = "Exemplo (script)"
# (dias de diferença entre a realização e a data prevista, avaliação da Sompo), repetidos entre as comprovadas
# validação da Sompo: 100 atende, 75 na maior parte, 25 na menor parte, 0 não atende (com motivo); None = aguardando
COMPROVADAS = [(0, 100), (-5, 100), (12, 75), (25, None), (45, 0), (3, 100), (8, 25)]
QTD_PENDENTES = 2                  # revisões passadas deixadas sem comprovante; as demais ficam comprovadas
MOTIVO_REPROVACAO = "A nota fiscal não identifica o equipamento (número de série ou identificação)."


def consultar(cursor, sql, parametros=None):
    cursor.execute(sql, parametros or {})
    colunas = [c[0] for c in cursor.description]
    return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def colunas(cursor, tabela):
    cursor.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :t", {"t": tabela})
    return {c for (c,) in cursor.fetchall()}


def pdf_comprovante(texto):
    """PDF pequeno de uma página, para servir de comprovante de exemplo."""
    import pymupdf
    doc = pymupdf.open()
    pagina = doc.new_page()
    pagina.insert_text((72, 90), "COMPROVANTE DE EXEMPLO (dados de teste)", fontsize=14)
    pagina.insert_text((72, 120), texto, fontsize=11)
    conteudo = doc.tobytes()
    doc.close()
    return conteudo


def _datas_comprovadas(cursor, id_maquina, id_orientacao):
    cursor.execute("SELECT DATA_REALIZADA FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO = :m "
                   "AND ID_ORIENTACAO = :o AND COMPROVANTE_ENTREGUE = 'SIM' AND DATA_REALIZADA IS NOT NULL",
                   {"m": id_maquina, "o": id_orientacao})
    return cursor.fetchall()


def _datas_previstas(cursor, id_maquina, id_orientacao):
    cursor.execute("SELECT DATA_PREVISTA FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO = :m "
                   "AND ID_ORIENTACAO = :o AND REGRA = 'MINIMA'", {"m": id_maquina, "o": id_orientacao})
    return [para_data(d) for (d,) in cursor.fetchall()]


def remover(conn):
    with conn.cursor() as cursor:
        clientes = [c["ID"] for c in consultar(cursor, "SELECT ID FROM CS_CLIENTES WHERE CNPJ = :c",
                                               {"c": CNPJ_EXEMPLO})]
        if not clientes:
            print("Nenhum exemplo para remover.")
            return
        maquinas = [m["ID"] for m in consultar(
            cursor, "SELECT e.ID FROM CS_EQUIPAMENTOS_SEGURADOS e JOIN CS_FAZENDAS f ON f.ID = e.ID_FAZENDA "
                    "WHERE f.ID_CLIENTE = :c", {"c": clientes[0]})]
        tem_leituras = bool(colunas(cursor, "CS_LEITURAS_MEDIDOR"))
        for maquina in maquinas:
            cursor.execute("DELETE FROM CS_ANEXOS WHERE TIPO_ENTIDADE = :t AND ID_ENTIDADE IN "
                           "(SELECT ID FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO = :m)",
                           {"t": ENTIDADE_MANUTENCAO, "m": maquina})
            cursor.execute("DELETE FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO = :m", {"m": maquina})
            if tem_leituras:
                cursor.execute("DELETE FROM CS_LEITURAS_MEDIDOR WHERE ID_EQUIPAMENTO_SEGURADO = :m", {"m": maquina})
            cursor.execute("DELETE FROM CS_EQUIPAMENTOS_SEGURADOS WHERE ID = :m", {"m": maquina})
        cursor.execute("DELETE FROM CS_FAZENDAS WHERE ID_CLIENTE = :c", {"c": clientes[0]})
        cursor.execute("DELETE FROM CS_CLIENTES WHERE ID = :c", {"c": clientes[0]})
    print(f"Exemplo removido: cliente {CLIENTE_EXEMPLO}, {len(maquinas)} máquina(s) e as manutenções delas.")


def escolher_modelo(cursor, nome):
    modelos = consultar(cursor, "SELECT ID, FABRICANTE, MODELO FROM CS_EQUIPAMENTOS_MODELOS ORDER BY FABRICANTE, MODELO")
    candidatos = []
    for m in modelos:
        if nome and nome.lower() not in f"{m['FABRICANTE']} {m['MODELO']}".lower():
            continue
        qtd = len(orientacoes_do_modelo(cursor, m["ID"]))
        if qtd:
            candidatos.append((qtd, m))
    if not candidatos:
        raise SystemExit("Nenhum modelo" + (f" com '{nome}' no nome" if nome else "")
                         + " tem manual com recomendações de manutenção gravadas. Grave um manual na página "
                           "Programação e rode de novo.")
    qtd, modelo = max(candidatos, key=lambda c: c[0])
    print(f"Modelo: {modelo['FABRICANTE']} {modelo['MODELO']} ({qtd} recomendações de manutenção).")
    return modelo


def criar(conn, nome_modelo):
    hoje = datetime.date.today()
    with conn.cursor() as cursor:
        if consultar(cursor, "SELECT ID FROM CS_CLIENTES WHERE CNPJ = :c", {"c": CNPJ_EXEMPLO}):
            raise SystemExit("O exemplo já existe. Para criar de novo: python tratamento/comprovacao_exemplo.py --remover")
        modelo = escolher_modelo(cursor, nome_modelo)
        id_cliente = _inserir_com_id(cursor, "CS_CLIENTES", {"RAZAO_SOCIAL": CLIENTE_EXEMPLO, "CNPJ": CNPJ_EXEMPLO})
        dados_fazenda = {"NOME_FAZENDA": FAZENDA_EXEMPLO, "ID_CLIENTE": id_cliente}
        if "MUNICIPIO" in colunas(cursor, "CS_FAZENDAS"):
            dados_fazenda.update(MUNICIPIO="Rio Verde", ESTADO="GO")
        id_fazenda = _inserir_com_id(cursor, "CS_FAZENDAS", dados_fazenda)
        existentes = colunas(cursor, "CS_EQUIPAMENTOS_SEGURADOS")
        dados_maquina = {"ID_FAZENDA": id_fazenda, "ID_EQUIPAMENTO_MODELO": int(modelo["ID"]),
                         "IDENTIFICACAO_INTERNA": "EXEMPLO-01", "NUMERO_SERIE": "EXEMPLO-0001",
                         "VALOR_SEGURADO": 300000, "NUMERO_APOLICE": APOLICE_EXEMPLO, "TELEMETRIA": "NAO",
                         "STATUS": "ATIVO",
                         "DATA_INICIO_VIGENCIA": hoje - datetime.timedelta(days=365),
                         "DATA_FIM_VIGENCIA": hoje + datetime.timedelta(days=365),
                         "DATA_AQUISICAO": hoje - datetime.timedelta(days=425),
                         "USO_MEDIO_HORAS_MES": 120, "HORIMETRO_ATUAL": 1650, "DATA_LEITURA": hoje}
        id_maquina = _inserir_com_id(cursor, "CS_EQUIPAMENTOS_SEGURADOS",
                                     {c: v for c, v in dados_maquina.items() if c in existentes})
    resultado = programar_equipamento(conn, id_maquina, hoje)
    print(f"Lista de manutenções gerada: {resultado['geradas']} manutenção(ões).")

    with conn.cursor() as cursor:
        atividades = consultar(cursor, "SELECT m.ID, m.DATA_PREVISTA, o.METRICA_GATILHO, o.VALOR_GATILHO, "
                                       "o.FATOR_CONDICIONAL FROM CS_MANUTENCOES_REALIZADAS m "
                                       "JOIN CS_EQUIPAMENTOS_ORIENTACOES o ON o.ID = m.ID_ORIENTACAO "
                                       "WHERE m.ID_EQUIPAMENTO_SEGURADO = :m ORDER BY m.DATA_PREVISTA, m.ID",
                               {"m": id_maquina})
        for a in atividades:
            a["DATA_PREVISTA"] = para_data(a["DATA_PREVISTA"])
        passadas = [a for a in atividades if a["DATA_PREVISTA"] <= hoje - datetime.timedelta(days=60)]
        realizada = valor_realizacao(valores_realizacao(cursor), "realizada")
        com_colunas = tem_coluna_regra(cursor) and "ID_COMPROVANTE" in colunas(cursor, "CS_MANUTENCOES_REALIZADAS")

        # revisões = manutenções da mesma data (como na página); algumas passadas ficam sem comprovante
        revisoes = {}
        for a in passadas:
            # revisão = mesma data e mesma periodicidade (como na página Comprovação)
            _, dias = periodicidade(dict(a, **dados_maquina), dict(a, **dados_maquina))
            revisoes.setdefault((a["DATA_PREVISTA"], round(dias or 0)), []).append(a)
        datas = sorted(revisoes)
        if len(datas) < len(COMPROVADAS) + QTD_PENDENTES:
            print("Aviso: poucas revisões geradas para o modelo; o cenário fica incompleto.")
        passo = max(1, len(datas) // max(1, QTD_PENDENTES))
        pendentes = set(datas[passo // 2::passo][:QTD_PENDENTES])
        comprovadas = [d for d in datas if d not in pendentes]
        for n, (data, _dias) in enumerate(comprovadas):
            diferenca, avaliacao = COMPROVADAS[n % len(COMPROVADAS)]
            data_realizada = min(hoje, data + datetime.timedelta(days=diferenca))
            itens = revisoes[(data, _dias)]
            id_anexo = salvar_anexo(conn, ENTIDADE_MANUTENCAO, itens[0]["ID"], ANEXO_COMPROVANTE,
                                    f"comprovante_exemplo_{itens[0]['ID']}.pdf",
                                    pdf_comprovante(f"Revisão prevista em {data:%d/%m/%Y}, realizada em "
                                                    f"{data_realizada:%d/%m/%Y} ({len(itens)} serviços)."),
                                    "application/pdf", USUARIO_EXEMPLO)
            for a in itens:
                valores = {"r": realizada, "d": data_realizada, "v": avaliacao, "id": a["ID"]}
                extra = ""
                if com_colunas:
                    extra = (", ID_COMPROVANTE = :anexo, AVALIADO_POR = :quem, MOTIVO_AVALIACAO = :motivo, "
                             "DATA_AVALIACAO = :quando")
                    valores.update(anexo=id_anexo, quem=None if avaliacao is None else USUARIO_EXEMPLO,
                                   motivo=MOTIVO_REPROVACAO if avaliacao is not None and avaliacao <= 25 else None,
                                   quando=None if avaliacao is None else data_realizada)
                cursor.execute("UPDATE CS_MANUTENCOES_REALIZADAS SET REALIZACAO = :r, DATA_REALIZADA = :d, "
                               f"COMPROVANTE_ENTREGUE = 'SIM', ATENDE_CRITERIOS = :v{extra} WHERE ID = :id", valores)

        # regra ideal: nas recomendações com duas opções ("250 horas ou anual"), comprovantes a mais nas datas da regra
        # completa do manual, em 2 de cada 3 recomendações (as outras ficam só com o cronograma mínimo)
        extras = 0
        if tem_coluna_regra(cursor):
            maquina = dict(dados_maquina, ID=id_maquina)
            duas_opcoes = [o for o in orientacoes_do_modelo(cursor, modelo["ID"]) if tem_duas_opcoes(o, maquina)]
            inicio = inicio_periodo(maquina, hoje)
            for n, orientacao in enumerate(duas_opcoes):
                if n % 3 == 2:
                    continue
                feitas = [para_data(d) for (d,) in _datas_comprovadas(cursor, id_maquina, orientacao["ID"])]
                for data in vencimentos(orientacao, maquina, inicio, hoje, REGRA_IDEAL):
                    if any(abs((data - f).days) <= 20 for f in feitas):
                        continue                     # já coberta por uma manutenção do cronograma
                    # fica no card da revisão do cronograma anterior (a data prevista é a da revisão)
                    anteriores = [f for f in _datas_previstas(cursor, id_maquina, orientacao["ID"]) if f <= data]
                    id_extra = _inserir_com_id(cursor, "CS_MANUTENCOES_REALIZADAS", {
                        "ID_EQUIPAMENTO_SEGURADO": id_maquina, "ID_ORIENTACAO": orientacao["ID"],
                        "DATA_PREVISTA": max(anteriores) if anteriores else data, "DATA_REALIZADA": data,
                        "REALIZACAO": realizada,
                        "COMPROVANTE_ENTREGUE": "SIM", "ATENDE_CRITERIOS": 100, "REGRA": REGRA_IDEAL})
                    id_anexo = salvar_anexo(conn, ENTIDADE_MANUTENCAO, id_extra, ANEXO_COMPROVANTE,
                                            f"comprovante_exemplo_{id_extra}.pdf",
                                            pdf_comprovante(f"Manutenção a mais (regra ideal), realizada em "
                                                            f"{data:%d/%m/%Y}."), "application/pdf", USUARIO_EXEMPLO)
                    if com_colunas:
                        cursor.execute("UPDATE CS_MANUTENCOES_REALIZADAS SET ID_COMPROVANTE = :a WHERE ID = :id",
                                       {"a": id_anexo, "id": id_extra})
                    extras += 1
    print(f"Cenário: {len(comprovadas)} revisão(ões) comprovada(s) (validadas, aguardando validação e reprovadas), "
          f"{len(pendentes)} passada(s) sem comprovante; as revisões dos últimos 60 dias também ficam sem "
          "comprovante, para testar o envio.")
    print(f"Regra ideal: {extras} comprovante(s) a mais nas recomendações com duas opções." if extras else
          "Regra ideal: sem comprovantes a mais (o modelo não tem recomendação com duas opções ou falta rodar "
          "sql/estrutura_banco.sql).")
    print(f"Na página Comprovação, escolha o cliente \"{CLIENTE_EXEMPLO} ({CNPJ_EXEMPLO})\".")


def main():
    parser = argparse.ArgumentParser(description="Dados de exemplo para a página Comprovação.")
    parser.add_argument("--modelo", help="parte do nome do modelo (ex.: 6075); sem ele, usa o modelo com mais "
                                         "recomendações de manutenção")
    parser.add_argument("--simular", action="store_true", help="monta o cenário sem gravar")
    parser.add_argument("--remover", action="store_true", help="apaga o exemplo")
    args = parser.parse_args()
    from auth import USER, PASSWORD, DSN
    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        if args.remover:
            remover(conn)
        else:
            criar(conn, args.modelo)
        if args.simular:
            conn.rollback()
            print("Simulação: nada foi gravado.")
        else:
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
