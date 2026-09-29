# -*- coding: utf-8 -*-
"""
Leitura mensal do horímetro (e do hodômetro, em veículos), com evidência.

Todo mês o usuário informa quantas horas o equipamento marca, com a foto ou PDF do horímetro (CS_ANEXOS,
TIPO_ENTIDADE = 'LEITURA'). Cada leitura:
  1. fica em CS_LEITURAS_MEDIDOR (sql/estrutura_banco.sql);
  2. atualiza o horímetro, a data da leitura e o uso médio mensal do equipamento (CS_EQUIPAMENTOS_SEGURADOS);
  3. refaz as datas das manutenções pendentes pelas horas reais (programacao_manutencao.py). Assim, uma
     manutenção "a cada 200 horas" vence quando a máquina de fato chega às 200 horas.

As funções recebem uma conexão aberta (oracledb) e não fazem commit: quem chama decide quando confirmar.
"""
import datetime

import oracledb

from requisitos.anexos import (salvar_anexo, validar_arquivo, nome_seguro, _inserir_com_id, AnexoInvalido,
                               TIPOS_DOCUMENTO, TAMANHO_MAXIMO_MB, ENTIDADE_LEITURA, ANEXO_HORIMETRO)
from requisitos.programacao_manutencao import programar_equipamento, DIAS_POR_MES

PRAZO_LEITURA_DIAS = 30              # leitura "em dia" se a última tem até 30 dias
MESES_USO_MEDIO = 6                  # uso médio calculado com as leituras dos últimos 6 meses
DIAS_MINIMOS_USO_MEDIO = 20          # intervalo mínimo entre leituras para recalcular o uso médio


class LeituraInvalida(ValueError):
    """Leitura menor que a anterior, data no futuro, sem evidência..."""


def para_data(valor):
    return valor.date() if isinstance(valor, datetime.datetime) else valor


def _linhas(cursor):
    colunas = [c[0] for c in cursor.description]
    return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def leituras(conn, id_equipamento):
    """Leituras do equipamento, da mais antiga para a mais recente."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT ID, DATA_LEITURA, HORIMETRO, HODOMETRO, ID_ANEXO, ORIGEM, USUARIO, DATA_REGISTRO
            FROM CS_LEITURAS_MEDIDOR WHERE ID_EQUIPAMENTO_SEGURADO = :e ORDER BY DATA_LEITURA, ID
        """, {"e": int(id_equipamento)})
        linhas = _linhas(cursor)
    for linha in linhas:
        linha["DATA_LEITURA"] = para_data(linha["DATA_LEITURA"])
    return linhas


def ultimas_leituras(conn, ids_equipamentos):
    """{id_equipamento: última leitura mensal (com evidência)}."""
    ids = [int(i) for i in ids_equipamentos if i is not None]
    if not ids:
        return {}
    binds = {f"e{n}": v for n, v in enumerate(ids[:900])}
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT l.ID_EQUIPAMENTO_SEGURADO, l.DATA_LEITURA, l.HORIMETRO, l.HODOMETRO
            FROM CS_LEITURAS_MEDIDOR l
            WHERE l.ORIGEM = 'LEITURA_MENSAL'
              AND l.ID_EQUIPAMENTO_SEGURADO IN ({', '.join(':' + b for b in binds)})
              AND l.DATA_LEITURA = (SELECT MAX(x.DATA_LEITURA) FROM CS_LEITURAS_MEDIDOR x
                                    WHERE x.ID_EQUIPAMENTO_SEGURADO = l.ID_EQUIPAMENTO_SEGURADO
                                      AND x.ORIGEM = 'LEITURA_MENSAL')
        """, binds)
        resultado = {}
        for linha in _linhas(cursor):
            linha["DATA_LEITURA"] = para_data(linha["DATA_LEITURA"])
            resultado[linha["ID_EQUIPAMENTO_SEGURADO"]] = linha
    return resultado


def situacao_leitura(data_ultima, hoje=None):
    """'em dia', 'pendente' (mais de 30 dias) ou 'sem leitura'."""
    hoje = hoje or datetime.date.today()
    if data_ultima is None:
        return "sem leitura"
    return "em dia" if (hoje - data_ultima).days <= PRAZO_LEITURA_DIAS else "pendente"


def uso_medio(pontos, hoje=None):
    """Uso médio por mês a partir das leituras [(data, valor)] dos últimos MESES_USO_MEDIO meses.
    None se não há duas leituras com ao menos DIAS_MINIMOS_USO_MEDIO dias de diferença."""
    hoje = hoje or datetime.date.today()
    pontos = sorted((d, float(v)) for d, v in pontos if d is not None and v is not None)
    recentes = [p for p in pontos if (hoje - p[0]).days <= MESES_USO_MEDIO * DIAS_POR_MES]
    if len(recentes) < 2:
        recentes = pontos[-2:]
    if len(recentes) < 2:
        return None
    (d0, v0), (d1, v1) = recentes[0], recentes[-1]
    dias = (d1 - d0).days
    if dias < DIAS_MINIMOS_USO_MEDIO or v1 < v0:
        return None
    return round((v1 - v0) / dias * DIAS_POR_MES, 1)


def _numero_br(valor):
    return f"{float(valor):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


def validar_leitura(anteriores, data_leitura, horimetro, hodometro, hoje=None):
    """Confere a nova leitura contra as já registradas (o medidor não volta)."""
    hoje = hoje or datetime.date.today()
    if data_leitura is None or data_leitura > hoje:
        raise LeituraInvalida("A data da leitura não pode ser no futuro.")
    if horimetro is None and hodometro is None:
        raise LeituraInvalida("Informe o horímetro (horas) ou o hodômetro (km).")
    for campo, valor, unidade in (("HORIMETRO", horimetro, "horas"), ("HODOMETRO", hodometro, "km")):
        if valor is None:
            continue
        if valor < 0:
            raise LeituraInvalida(f"O valor em {unidade} não pode ser negativo.")
        ordenadas = sorted((l for l in anteriores if l.get(campo) is not None),
                           key=lambda l: (l["DATA_LEITURA"], float(l[campo])))
        antes = [l for l in ordenadas if l["DATA_LEITURA"] <= data_leitura]
        depois = [l for l in ordenadas if l["DATA_LEITURA"] > data_leitura]
        if antes and valor < float(antes[-1][campo]):
            raise LeituraInvalida(f"O medidor marca {_numero_br(valor)} {unidade}, menos que a leitura de "
                                  f"{antes[-1]['DATA_LEITURA']:%d/%m/%Y} ({_numero_br(antes[-1][campo])} {unidade}). "
                                  "Confira o valor.")
        if depois and valor > float(depois[0][campo]):
            raise LeituraInvalida(f"O valor passa a leitura posterior, de {depois[0]['DATA_LEITURA']:%d/%m/%Y}.")


def _leitura_do_cadastro(conn, id_equipamento):
    """Horímetro/hodômetro informados no cadastro do equipamento, como uma leitura (para a validação)."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT DATA_LEITURA, HORIMETRO_ATUAL, HODOMETRO_ATUAL FROM CS_EQUIPAMENTOS_SEGURADOS "
                       "WHERE ID = :id", {"id": int(id_equipamento)})
        linha = cursor.fetchone()
    if not linha or linha[0] is None or (linha[1] is None and linha[2] is None):
        return []
    return [{"DATA_LEITURA": para_data(linha[0]), "HORIMETRO": linha[1], "HODOMETRO": linha[2]}]


def registrar_leitura(conn, id_equipamento, data_leitura, horimetro=None, hodometro=None, evidencia=None,
                      usuario=None, origem="LEITURA_MENSAL"):
    """Grava a leitura (com a evidência), atualiza o equipamento e refaz a programação das manutenções.
    evidencia = (nome_arquivo, bytes, tipo) — obrigatória na leitura mensal.
    Devolve {'id', 'uso_medio_horas', 'uso_medio_km', 'programacao'}."""
    horimetro = float(horimetro) if horimetro not in (None, "") else None
    hodometro = float(hodometro) if hodometro not in (None, "") else None
    anteriores = leituras(conn, id_equipamento)
    if origem == "LEITURA_MENSAL" and any(l["DATA_LEITURA"] == data_leitura and l["ORIGEM"] == "LEITURA_MENSAL"
                                          for l in anteriores):
        raise LeituraInvalida(f"Já existe leitura mensal deste equipamento em {data_leitura:%d/%m/%Y}.")
    # também confere com o horímetro do cadastro do equipamento (pode não estar no histórico)
    validar_leitura(anteriores + _leitura_do_cadastro(conn, id_equipamento), data_leitura, horimetro, hodometro)
    if origem == "LEITURA_MENSAL":
        if not evidencia:
            raise LeituraInvalida("Anexe a foto ou o PDF do horímetro como evidência.")
        validar_arquivo(nome_seguro(evidencia[0]), evidencia[1], TIPOS_DOCUMENTO, TAMANHO_MAXIMO_MB[ANEXO_HORIMETRO])
    with conn.cursor() as cursor:
        id_leitura = _inserir_com_id(cursor, "CS_LEITURAS_MEDIDOR", {
            "ID_EQUIPAMENTO_SEGURADO": int(id_equipamento), "DATA_LEITURA": data_leitura, "HORIMETRO": horimetro,
            "HODOMETRO": hodometro, "ORIGEM": origem, "USUARIO": (usuario or "")[:150] or None})
    if evidencia:
        id_anexo = salvar_anexo(conn, ENTIDADE_LEITURA, id_leitura, ANEXO_HORIMETRO, evidencia[0], evidencia[1],
                                evidencia[2] if len(evidencia) > 2 else None, usuario)
        with conn.cursor() as cursor:
            cursor.execute("UPDATE CS_LEITURAS_MEDIDOR SET ID_ANEXO = :a WHERE ID = :id", {"a": id_anexo, "id": id_leitura})

    # Equipamento: última leitura e uso médio pelas leituras reais
    todas = anteriores + [{"DATA_LEITURA": data_leitura, "HORIMETRO": horimetro, "HODOMETRO": hodometro}]
    ultima = max(todas, key=lambda l: (l["DATA_LEITURA"], l["HORIMETRO"] or 0, l["HODOMETRO"] or 0))
    novo_uso_h = uso_medio([(l["DATA_LEITURA"], l["HORIMETRO"]) for l in todas])
    novo_uso_km = uso_medio([(l["DATA_LEITURA"], l["HODOMETRO"]) for l in todas])
    dados = {"DATA_LEITURA": ultima["DATA_LEITURA"]}
    if ultima["HORIMETRO"] is not None:
        dados["HORIMETRO_ATUAL"] = ultima["HORIMETRO"]
    if ultima["HODOMETRO"] is not None:
        dados["HODOMETRO_ATUAL"] = ultima["HODOMETRO"]
    if novo_uso_h:
        dados["USO_MEDIO_HORAS_MES"] = novo_uso_h
    if novo_uso_km:
        dados["USO_MEDIO_KM_MES"] = novo_uso_km
    with conn.cursor() as cursor:
        cursor.execute(f"UPDATE CS_EQUIPAMENTOS_SEGURADOS SET {', '.join(f'{c} = :{c}' for c in dados)} WHERE ID = :id",
                       dict(dados, id=int(id_equipamento)))
    programacao = programar_equipamento(conn, id_equipamento)
    return {"id": id_leitura, "uso_medio_horas": novo_uso_h, "uso_medio_km": novo_uso_km, "programacao": programacao}


def registrar_leitura_cadastro(conn, id_equipamento, data_leitura, horimetro, hodometro, usuario=None):
    """Guarda na história a leitura informada no cadastro do equipamento (sem evidência). Não refaz a programação
    (o cadastro já faz). Ignora se a tabela ainda não existe ou se a mesma leitura já está registrada."""
    if data_leitura is None or (horimetro in (None, 0) and hodometro in (None, 0)):
        return None
    try:
        anteriores = leituras(conn, id_equipamento)
    except oracledb.Error:
        return None
    if any(l["DATA_LEITURA"] == data_leitura and l["HORIMETRO"] == horimetro for l in anteriores):
        return None
    try:
        validar_leitura(anteriores, data_leitura, horimetro or None, hodometro or None)
    except LeituraInvalida:
        return None
    with conn.cursor() as cursor:
        return _inserir_com_id(cursor, "CS_LEITURAS_MEDIDOR", {
            "ID_EQUIPAMENTO_SEGURADO": int(id_equipamento), "DATA_LEITURA": data_leitura,
            "HORIMETRO": horimetro or None, "HODOMETRO": hodometro or None, "ORIGEM": "CADASTRO",
            "USUARIO": (usuario or "")[:150] or None})
