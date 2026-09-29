# -*- coding: utf-8 -*-
"""
Manuais dos equipamentos: gravação das recomendações revisadas, vínculo do manual ao modelo, registro do gasto com IA
e "aprendizado" com as rejeições dos revisores. Usado pela página "Programação" (Manutenções) (pages/4_Manutencoes_Programadas.py).

Tabelas: CS_EQUIPAMENTOS_ORIENTACOES (recomendações), CS_EQUIPAMENTOS_MANUAIS (manual <-> modelo),
CS_NLP_REVISOES e CS_NLP_USO_IA (sql/estrutura_banco.sql).

As funções recebem uma conexão aberta (oracledb) e não fazem commit: quem chama decide quando confirmar.
"""
import difflib
import re
import unicodedata

import oracledb

from requisitos.anexos import _inserir_com_id

SIMILARIDADE_REJEITADO = 0.85        # a partir de quanto um item "parece" um já rejeitado
MAX_EXEMPLOS_NEGATIVOS = 40          # rejeições enviadas à IA por NPL (mais recentes primeiro)
TIPOS_POR_NPL = {"NPL1": ("MANUTENCAO_PROGRAMADA",), "NPL2": ("ALERTA_TEMP_MINIMA", "ALERTA_TEMP_MAXIMA"),
                 "NPL3": ("RISCO_CHUVA_DESLIZE",)}
COLUNAS_ORIENTACAO = ["COD_FONTE_MANUAL", "TIPO_ORIENTACAO", "SUBSISTEMA", "ACAO_TECNICA", "DETALHAMENTO_ORIENTACAO",
                      "METRICA_GATILHO", "VALOR_GATILHO", "UNIDADE_MEDIDA", "FATOR_CONDICIONAL", "TEXTO_BRUTO_ORIGINAL"]


def normalizar(texto):
    texto = "".join(c for c in unicodedata.normalize("NFD", str(texto or "")) if unicodedata.category(c) != "Mn")
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", texto.lower()).split())


def chave_item(tipo, detalhamento, valor):
    """Identifica uma recomendação para evitar duplicidade (mesmo tipo, mesmo texto base e mesmo gatilho)."""
    base = normalizar(str(detalhamento).split(" — ")[0])
    return (str(tipo or "").upper(), base, None if valor is None else round(float(valor), 2))


def tabela_existe(conn, nome):
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM USER_TABLES WHERE TABLE_NAME = :t", {"t": nome.upper()})
        return cursor.fetchone()[0] > 0


def _linhas(cursor):
    colunas = [c[0] for c in cursor.description]
    return [{c: (v.read() if hasattr(v, "read") else v) for c, v in zip(colunas, linha)} for linha in cursor.fetchall()]


def modelos(conn):
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT mo.ID, mo.FABRICANTE, mo.MODELO, mo.TIPO,
                   (SELECT MIN(ma.COD_FONTE_MANUAL) FROM CS_EQUIPAMENTOS_MANUAIS ma WHERE ma.ID_EQUIPAMENTO = mo.ID) AS COD_MANUAL,
                   (SELECT MIN(ma.ID) FROM CS_EQUIPAMENTOS_MANUAIS ma
                    WHERE ma.ID_EQUIPAMENTO = mo.ID AND ma.ARQUIVO_PDF IS NOT NULL) AS ID_MANUAL_COM_PDF
            FROM CS_EQUIPAMENTOS_MODELOS mo ORDER BY mo.FABRICANTE, mo.MODELO
        """)
        return _linhas(cursor)


def contar_orientacoes(conn, cod):
    with conn.cursor() as cursor:
        cursor.execute("SELECT TIPO_ORIENTACAO, COUNT(*) AS QTD FROM CS_EQUIPAMENTOS_ORIENTACOES "
                       "WHERE COD_FONTE_MANUAL = :c GROUP BY TIPO_ORIENTACAO", {"c": cod})
        return {t: q for t, q in cursor.fetchall()}


def chaves_existentes(conn, cod):
    with conn.cursor() as cursor:
        cursor.execute("SELECT TIPO_ORIENTACAO, DETALHAMENTO_ORIENTACAO, VALOR_GATILHO FROM CS_EQUIPAMENTOS_ORIENTACOES "
                       "WHERE COD_FONTE_MANUAL = :c", {"c": cod})
        return {chave_item(t, d.read() if hasattr(d, "read") else d, v) for t, d, v in cursor.fetchall()}


def gravar_orientacoes(conn, itens):
    """Insere as recomendações aprovadas (dicts com as colunas de COLUNAS_ORIENTACAO, em minúsculas ou maiúsculas).
    Pula as que já existem para o mesmo manual. Devolve (gravadas, repetidas)."""
    gravadas = repetidas = 0
    existentes = {}
    with conn.cursor() as cursor:
        for item in itens:
            dados = {c: item.get(c, item.get(c.lower())) for c in COLUNAS_ORIENTACAO}
            cod = dados["COD_FONTE_MANUAL"]
            if cod not in existentes:
                existentes[cod] = chaves_existentes(conn, cod)
            chave = chave_item(dados["TIPO_ORIENTACAO"], dados["DETALHAMENTO_ORIENTACAO"], dados["VALOR_GATILHO"])
            if chave in existentes[cod]:
                repetidas += 1
                continue
            if dados["VALOR_GATILHO"] is not None:
                dados["VALOR_GATILHO"] = float(dados["VALOR_GATILHO"])
            for coluna in ("DETALHAMENTO_ORIENTACAO", "TEXTO_BRUTO_ORIGINAL"):    # CLOB: texto até 3.900 caracteres
                dados[coluna] = str(dados[coluna] or "")[:3900] or None
            _inserir_com_id(cursor, "CS_EQUIPAMENTOS_ORIENTACOES", dados)
            existentes[cod].add(chave)
            gravadas += 1
    return gravadas, repetidas


# ---------------------------------------------------------------------------
# Comparação da leitura com o que já está gravado para os modelos
# ---------------------------------------------------------------------------
NOVA, DIFERENTE, IGUAL, SO_GRAVADA = ("Nova", "Nova (diferente da gravada)", "Já gravada (igual)",
                                      "Já gravada (não veio nesta leitura)")
ORDEM_SITUACAO = {NOVA: 0, DIFERENTE: 1, IGUAL: 2, SO_GRAVADA: 3}


def orientacoes_gravadas(conn, ids_modelos, cod=None):
    """Recomendações já gravadas para os modelos (de todos os manuais ligados a eles) e para o manual `cod`, com a
    quantidade de manutenções programadas e comprovadas de cada uma."""
    ids = [int(i) for i in ids_modelos]
    binds = {f"m{n}": v for n, v in enumerate(ids)}
    binds["cod"] = cod or ""
    filtro_modelos = (f"o.COD_FONTE_MANUAL IN (SELECT ma.COD_FONTE_MANUAL FROM CS_EQUIPAMENTOS_MANUAIS ma "
                      f"WHERE ma.ID_EQUIPAMENTO IN ({', '.join(':' + b for b in binds if b != 'cod')})) OR "
                      if ids else "")
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT o.ID, o.COD_FONTE_MANUAL, o.TIPO_ORIENTACAO, o.SUBSISTEMA, o.ACAO_TECNICA,
                   o.DETALHAMENTO_ORIENTACAO, o.METRICA_GATILHO, o.VALOR_GATILHO, o.UNIDADE_MEDIDA,
                   o.FATOR_CONDICIONAL, o.TEXTO_BRUTO_ORIGINAL,
                   (SELECT COUNT(*) FROM CS_MANUTENCOES_REALIZADAS r WHERE r.ID_ORIENTACAO = o.ID) AS PROGRAMADAS,
                   (SELECT COUNT(*) FROM CS_MANUTENCOES_REALIZADAS r WHERE r.ID_ORIENTACAO = o.ID
                      AND r.COMPROVANTE_ENTREGUE = 'SIM') AS COMPROVADAS
            FROM CS_EQUIPAMENTOS_ORIENTACOES o
            WHERE {filtro_modelos}o.COD_FONTE_MANUAL = :cod
            ORDER BY o.COD_FONTE_MANUAL, o.TIPO_ORIENTACAO, o.SUBSISTEMA, o.ID
        """, binds)
        return _linhas(cursor)


def _grupo(tipo, subsistema, acao, metrica):
    return tuple(str(v or "").upper() for v in (tipo, subsistema, acao, metrica))


def comparar_com_gravadas(itens_ia, gravadas):
    """Junta a leitura da IA com o que já está gravado, numa lista só para o revisor decidir o que fica.
    Cada linha: {"situacao", "item" (dict em minúsculas), "id" (ID gravado ou None), "gravada_hoje" (texto da
    recomendação gravada parecida, para as diferentes)}.
    - item da IA igual a uma gravada (mesmo tipo, texto base e gatilho): uma linha só, "Já gravada (igual)";
    - item da IA do mesmo tipo, subsistema, ação e métrica de uma gravada, mas com texto ou valor diferente:
      "Nova (diferente da gravada)", mostrando a gravada ao lado;
    - demais itens da IA: "Nova"; gravadas que a leitura não trouxe: "Já gravada (não veio nesta leitura)"."""
    por_chave, por_grupo = {}, {}
    for g in gravadas:
        detalhe = g["DETALHAMENTO_ORIENTACAO"]
        por_chave.setdefault(chave_item(g["TIPO_ORIENTACAO"], detalhe, g["VALOR_GATILHO"]), g)
        por_grupo.setdefault(_grupo(g["TIPO_ORIENTACAO"], g["SUBSISTEMA"], g["ACAO_TECNICA"], g["METRICA_GATILHO"]),
                             []).append(g)
    linhas, usadas = [], set()
    for item in itens_ia:
        igual = por_chave.get(chave_item(item["tipo_orientacao"], item["detalhamento_orientacao"],
                                         item.get("valor_gatilho")))
        if igual is not None and igual["ID"] not in usadas:
            usadas.add(igual["ID"])
            linhas.append({"situacao": IGUAL, "item": _item_gravado(igual, item), "id": igual["ID"],
                           "gravada_hoje": ""})
            continue
        if igual is not None:                      # repetido na própria leitura: já representado
            continue
        parecidas = por_grupo.get(_grupo(item["tipo_orientacao"], item["subsistema"], item["acao_tecnica"],
                                         item.get("metrica_gatilho")), [])
        if parecidas:
            gravada_hoje = " | ".join(_resumo(g) for g in parecidas[:3])
            linhas.append({"situacao": DIFERENTE, "item": item, "id": None, "gravada_hoje": gravada_hoje})
        else:
            linhas.append({"situacao": NOVA, "item": item, "id": None, "gravada_hoje": ""})
    for g in gravadas:
        if g["ID"] not in usadas:
            linhas.append({"situacao": SO_GRAVADA, "item": _item_gravado(g), "id": g["ID"], "gravada_hoje": ""})
    return ordenar_por_pagina(linhas)


def ordenar_por_pagina(linhas):
    """Regras semelhantes juntas (mesmo tipo, subsistema, ação e métrica), na ordem das páginas do manual: cada
    grupo entra na posição da sua primeira página; dentro do grupo, pela página e pela situação. Gravadas sem
    página (a leitura não as trouxe) vêm junto do grupo delas ou, sem grupo, no fim."""
    sem_pagina = float("inf")

    def pagina(linha):
        valor = linha["item"].get("pagina_origem")
        return sem_pagina if valor in (None, "") else float(valor)

    def grupo(linha):
        i = linha["item"]
        return _grupo(i.get("tipo_orientacao"), i.get("subsistema"), i.get("acao_tecnica"), i.get("metrica_gatilho"))

    primeira = {}
    for linha in linhas:
        primeira[grupo(linha)] = min(primeira.get(grupo(linha), sem_pagina), pagina(linha))
    return sorted(linhas, key=lambda l: (primeira[grupo(l)], grupo(l), pagina(l), ORDEM_SITUACAO[l["situacao"]]))


def _resumo(g):
    valor = g["VALOR_GATILHO"]
    gatilho = "" if valor is None else f" ({float(valor):g} {str(g['UNIDADE_MEDIDA'] or '').lower()})".rstrip()
    return f"{g['DETALHAMENTO_ORIENTACAO']}{gatilho}"


def _item_gravado(g, item_ia=None):
    """Linha gravada no formato dos itens da leitura (minúsculas); mantém a página do item da IA igual, se houver."""
    item = {c.lower(): g[c] for c in COLUNAS_ORIENTACAO}
    item["valor_gatilho"] = None if g["VALOR_GATILHO"] is None else float(g["VALOR_GATILHO"])
    item["pagina_origem"] = (item_ia or {}).get("pagina_origem")
    item["origem_extracao"] = (item_ia or {}).get("origem_extracao")
    item["programadas"], item["comprovadas"] = int(g.get("PROGRAMADAS") or 0), int(g.get("COMPROVADAS") or 0)
    return item


def atualizar_orientacao(conn, id_orientacao, dados):
    """Corrige uma recomendação gravada (dados: colunas de COLUNAS_ORIENTACAO, menos o código do manual)."""
    campos = {c: dados.get(c, dados.get(c.lower())) for c in COLUNAS_ORIENTACAO if c != "COD_FONTE_MANUAL"}
    if campos["VALOR_GATILHO"] is not None:
        campos["VALOR_GATILHO"] = float(campos["VALOR_GATILHO"])
    for coluna in ("DETALHAMENTO_ORIENTACAO", "TEXTO_BRUTO_ORIGINAL"):
        campos[coluna] = str(campos[coluna] or "")[:3900] or None
    with conn.cursor() as cursor:
        cursor.execute(f"UPDATE CS_EQUIPAMENTOS_ORIENTACOES SET {', '.join(f'{c} = :{c}' for c in campos)} "
                       "WHERE ID = :id", dict(campos, id=int(id_orientacao)))


def remover_orientacoes(conn, ids):
    """Remove recomendações gravadas que o revisor desmarcou. As manutenções pendentes ligadas a elas saem junto;
    recomendação com manutenção já comprovada NÃO é removida (o comprovante precisa dela).
    Devolve (removidas, mantidas_por_comprovante)."""
    removidas = mantidas = 0
    with conn.cursor() as cursor:
        for id_orientacao in ids:
            cursor.execute("SELECT COUNT(*) FROM CS_MANUTENCOES_REALIZADAS WHERE ID_ORIENTACAO = :id "
                           "AND COMPROVANTE_ENTREGUE = 'SIM'", {"id": int(id_orientacao)})
            if cursor.fetchone()[0]:
                mantidas += 1
                continue
            cursor.execute("DELETE FROM CS_MANUTENCOES_REALIZADAS WHERE ID_ORIENTACAO = :id", {"id": int(id_orientacao)})
            cursor.execute("DELETE FROM CS_EQUIPAMENTOS_ORIENTACOES WHERE ID = :id", {"id": int(id_orientacao)})
            removidas += 1
    return removidas, mantidas


def vincular_manual(conn, ids_modelos, cod, nome_arquivo=None, conteudo_pdf=None):
    """Liga o manual (código fonte) a um ou mais modelos em CS_EQUIPAMENTOS_MANUAIS (um manual pode valer para
    vários modelos da mesma série, ex.: Mahindra 6065 e 6075). Para cada modelo:
    - se o vínculo manual + modelo já existe: nada muda;
    - se o modelo tem um manual enviado pelo cadastro com código provisório (MAN-...): troca pelo código confirmado;
    - senão: cria o vínculo.
    O PDF é guardado uma vez só por manual (no primeiro vínculo sem PDF). Devolve {situação: quantidade}."""
    if isinstance(ids_modelos, (int, str)):
        ids_modelos = [ids_modelos]
    nome_arquivo = (nome_arquivo or cod)[:255]
    resumo = {}
    with conn.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM CS_EQUIPAMENTOS_MANUAIS WHERE COD_FONTE_MANUAL = :c "
                       "AND ARQUIVO_PDF IS NOT NULL", {"c": cod})
        pdf_guardado = cursor.fetchone()[0] > 0
        for id_modelo in ids_modelos:
            id_modelo = int(id_modelo)
            cursor.execute("SELECT ID FROM CS_EQUIPAMENTOS_MANUAIS WHERE COD_FONTE_MANUAL = :c AND ID_EQUIPAMENTO = :m",
                           {"c": cod, "m": id_modelo})
            linha = cursor.fetchone()
            cursor.execute("SELECT ID FROM CS_EQUIPAMENTOS_MANUAIS WHERE ID_EQUIPAMENTO = :m "
                           "AND COD_FONTE_MANUAL LIKE 'MAN-%'", {"m": id_modelo})
            provisorio = None if linha else cursor.fetchone()
            if linha:
                situacao, id_vinculo = "já vinculado", linha[0]
            elif provisorio:
                cursor.execute("UPDATE CS_EQUIPAMENTOS_MANUAIS SET COD_FONTE_MANUAL = :c WHERE ID = :id",
                               {"c": cod, "id": provisorio[0]})
                situacao, id_vinculo = "código do manual atualizado", provisorio[0]
            else:
                id_vinculo = _inserir_com_id(cursor, "CS_EQUIPAMENTOS_MANUAIS", {
                    "ID_EQUIPAMENTO": id_modelo, "COD_FONTE_MANUAL": cod, "NOME_ARQUIVO": nome_arquivo})
                cursor.execute("UPDATE CS_EQUIPAMENTOS_MANUAIS SET DATA_UPLOAD = SYSTIMESTAMP WHERE ID = :id",
                               {"id": id_vinculo})
                situacao = "vinculado"
            if conteudo_pdf and not pdf_guardado:
                cursor.setinputsizes(arquivo=oracledb.DB_TYPE_BLOB)
                cursor.execute("UPDATE CS_EQUIPAMENTOS_MANUAIS SET ARQUIVO_PDF = :arquivo, NOME_ARQUIVO = :nome, "
                               "DATA_UPLOAD = SYSTIMESTAMP WHERE ID = :id",
                               {"arquivo": conteudo_pdf, "nome": nome_arquivo, "id": id_vinculo})
                pdf_guardado = True
            resumo[situacao] = resumo.get(situacao, 0) + 1
    return resumo


def manuais_gravados(conn):
    """Manuais com PDF no banco: [{ID, COD_FONTE_MANUAL, NOME_ARQUIVO, MODELOS}] (MODELOS: 'Mahindra 6065, 6075')."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT ma.COD_FONTE_MANUAL, mo.FABRICANTE, mo.MODELO, ma.ID, ma.NOME_ARQUIVO,
                   CASE WHEN ma.ARQUIVO_PDF IS NULL THEN 0 ELSE 1 END AS TEM_PDF
            FROM CS_EQUIPAMENTOS_MANUAIS ma JOIN CS_EQUIPAMENTOS_MODELOS mo ON mo.ID = ma.ID_EQUIPAMENTO
            ORDER BY ma.COD_FONTE_MANUAL, mo.FABRICANTE, mo.MODELO
        """)
        linhas = cursor.fetchall()
    manuais = {}
    for cod, fabricante, modelo, id_manual, nome, tem_pdf in linhas:
        m = manuais.setdefault(cod, {"COD_FONTE_MANUAL": cod, "ID": None, "NOME_ARQUIVO": nome, "MODELOS": []})
        m["MODELOS"].append(f"{fabricante} {modelo}")
        if tem_pdf:
            m["ID"], m["NOME_ARQUIVO"] = id_manual, nome
    return [m for m in manuais.values() if m["ID"] is not None]


def modelos_do_manual(conn, cod):
    """IDs dos modelos já ligados ao manual."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT ID_EQUIPAMENTO FROM CS_EQUIPAMENTOS_MANUAIS WHERE COD_FONTE_MANUAL = :c", {"c": cod})
        return [int(i) for (i,) in cursor.fetchall()]


def modelos_citados(lista_modelos, texto, fabricante_capa=None):
    """IDs dos modelos cadastrados cujo nome aparece no texto (capa e primeiras páginas do manual). Se a capa indicou o
    fabricante, só os modelos dele. O nome precisa aparecer como palavra inteira (6075 não casa com 60755)."""
    texto_norm = " " + re.sub(r"[^A-Z0-9]+", " ", normalizar(texto)) + " "
    fabricante_norm = normalizar(fabricante_capa or "")
    encontrados = []
    for m in lista_modelos:
        if fabricante_norm and fabricante_norm not in normalizar(m["FABRICANTE"]) \
                and normalizar(m["FABRICANTE"]) not in fabricante_norm:
            continue
        nome = re.sub(r"[^A-Z0-9]+", " ", normalizar(m["MODELO"])).strip()
        if nome and f" {nome} " in texto_norm:
            encontrados.append(int(m["ID"]))
    return encontrados


def cadastrar_modelo(conn, fabricante, modelo, tipo, valor_estimado=None, descricao=None):
    """Cria o modelo em CS_EQUIPAMENTOS_MODELOS (ou devolve o ID se já existir com o mesmo fabricante e modelo)."""
    fabricante, modelo, tipo = str(fabricante).strip(), str(modelo).strip(), str(tipo).strip()
    if not (fabricante and modelo and tipo):
        raise ValueError("Informe fabricante, modelo e tipo.")
    with conn.cursor() as cursor:
        cursor.execute("SELECT ID FROM CS_EQUIPAMENTOS_MODELOS WHERE UPPER(FABRICANTE) = UPPER(:f) "
                       "AND UPPER(MODELO) = UPPER(:m)", {"f": fabricante, "m": modelo})
        existente = cursor.fetchone()
        if existente:
            return int(existente[0])
        dados = {"TIPO": tipo[:100], "MODELO": modelo[:150], "FABRICANTE": fabricante[:100]}
        if descricao:
            dados["DESCRICAO_DETALHADA"] = descricao
        if valor_estimado:
            dados["VALOR_ESTIMADO"] = float(valor_estimado)
        return int(_inserir_com_id(cursor, "CS_EQUIPAMENTOS_MODELOS", dados))


def equipamentos_do_modelo(conn, id_modelo):
    with conn.cursor() as cursor:
        cursor.execute("SELECT ID FROM CS_EQUIPAMENTOS_SEGURADOS WHERE ID_EQUIPAMENTO_MODELO = :m", {"m": int(id_modelo)})
        return [i for (i,) in cursor.fetchall()]


def equipamentos_dos_modelos(conn, ids_modelos):
    """Máquinas seguradas de todos os modelos informados."""
    maquinas = []
    for id_modelo in ids_modelos:
        maquinas += equipamentos_do_modelo(conn, id_modelo)
    return maquinas


# ---------------------------------------------------------------------------
# Aprendizado: rejeições dos revisores
# ---------------------------------------------------------------------------
def registrar_rejeicoes(conn, cod, origem, itens, usuario):
    """itens: dicts com tipo_orientacao, subsistema, detalhamento_orientacao, texto_bruto_original e motivo."""
    with conn.cursor() as cursor:
        for item in itens:
            _inserir_com_id(cursor, "CS_NLP_REVISOES", {
                "COD_FONTE_MANUAL": cod, "ORIGEM": origem, "TIPO_ORIENTACAO": item.get("tipo_orientacao"),
                "SUBSISTEMA": item.get("subsistema"),
                "DETALHAMENTO": str(item.get("detalhamento_orientacao") or "")[:2000] or "(sem texto)",
                "TEXTO_BRUTO": str(item.get("texto_bruto_original") or "")[:4000] or None,
                "MOTIVO": str(item.get("motivo") or "").strip()[:500] or None,
                "USUARIO": (usuario or "")[:150] or None})
    return len(itens)


def rejeicoes(conn, limite=500):
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT TIPO_ORIENTACAO, DETALHAMENTO, TEXTO_BRUTO, MOTIVO FROM CS_NLP_REVISOES
            ORDER BY DATA_REVISAO DESC FETCH FIRST {int(limite)} ROWS ONLY
        """)
        return _linhas(cursor)


def parecido_com_rejeitado(item, lista_rejeicoes):
    """Devolve a rejeição mais parecida com o item (mesmo tipo), se passar do limite de similaridade."""
    alvo = normalizar(str(item.get("detalhamento_orientacao") or "").split(" — ")[0])
    bruto = normalizar(item.get("texto_bruto_original"))
    melhor, nota = None, 0.0
    for r in lista_rejeicoes:
        if r.get("TIPO_ORIENTACAO") and r["TIPO_ORIENTACAO"] != item.get("tipo_orientacao"):
            continue
        for a, b in ((alvo, normalizar(str(r["DETALHAMENTO"]).split(" — ")[0])), (bruto, normalizar(r.get("TEXTO_BRUTO")))):
            if a and b:
                valor = difflib.SequenceMatcher(None, a, b).ratio()
                if valor > nota:
                    melhor, nota = r, valor
    return melhor if nota >= SIMILARIDADE_REJEITADO else None


def exemplos_negativos(lista_rejeicoes):
    """{NPL: ['texto rejeitado (motivo: ...)']} para as instruções da IA."""
    exemplos = {npl: [] for npl in TIPOS_POR_NPL}
    for r in lista_rejeicoes:
        for npl, tipos in TIPOS_POR_NPL.items():
            if (r.get("TIPO_ORIENTACAO") in tipos or not r.get("TIPO_ORIENTACAO")) and len(exemplos[npl]) < MAX_EXEMPLOS_NEGATIVOS:
                texto = str(r.get("TEXTO_BRUTO") or r["DETALHAMENTO"])[:300]
                exemplos[npl].append(texto + (f" (motivo: {r['MOTIVO']})" if r.get("MOTIVO") else ""))
    return exemplos


# ---------------------------------------------------------------------------
# Gasto com IA
# ---------------------------------------------------------------------------
def registrar_uso_ia(conn, cod, modelo_ia, npl, tokens_entrada, tokens_saida, custo_usd, limite_usd, usuario):
    with conn.cursor() as cursor:
        _inserir_com_id(cursor, "CS_NLP_USO_IA", {
            "COD_FONTE_MANUAL": cod, "MODELO_IA": modelo_ia, "NPL": npl, "TOKENS_ENTRADA": int(tokens_entrada),
            "TOKENS_SAIDA": int(tokens_saida), "CUSTO_USD": float(custo_usd), "LIMITE_USD": float(limite_usd),
            "USUARIO": (usuario or "")[:150] or None})


def gasto_ia(conn):
    """Gasto total, do mês atual e a lista das últimas chamadas pagas."""
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT NVL(SUM(CUSTO_USD), 0),
                   NVL(SUM(CASE WHEN DATA_USO >= TRUNC(SYSDATE, 'MM') THEN CUSTO_USD END), 0)
            FROM CS_NLP_USO_IA
        """)
        total, mes = cursor.fetchone()
        cursor.execute("""
            SELECT DATA_USO, COD_FONTE_MANUAL, NPL, TOKENS_ENTRADA, TOKENS_SAIDA, CUSTO_USD, USUARIO FROM CS_NLP_USO_IA
            WHERE TOKENS_ENTRADA > 0 OR TOKENS_SAIDA > 0          -- tentativas que não chegaram à IA (sem custo) ficam de fora
            ORDER BY DATA_USO DESC FETCH FIRST 30 ROWS ONLY
        """)
        return float(total), float(mes), _linhas(cursor)
