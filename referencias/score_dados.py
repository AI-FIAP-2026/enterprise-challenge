# -*- coding: utf-8 -*-
"""
Carga de DADOS DE TESTE para o Score de Risco do cliente (trabalho acadêmico)

Pré-requisitos (rodar antes, no Oracle): sql/cs_equipamentos_uso.sql, sql/cs_equipamentos_manuais_vinculo.sql,
sql/cs_anexos.sql.

Gera dados coerentes para todos os clientes que têm fazendas, seguindo as mesmas regras do sistema:
  1. CS_EQUIPAMENTOS_SEGURADOS: 2 a 4 máquinas por fazenda, SÓ dos modelos com manual vinculado (os exatos:
     John Deere CH950 e 5060E), com valor segurado, apólice, vigência e dados de uso (aquisição, horímetro, uso médio
     mensal e tipo de operação). Fazendas que já têm equipamento não recebem outros.
  2. CS_MANUTENCOES_REALIZADAS: a lista de manutenções a comprovar, gerada pela programação do sistema
     (servicos/programacao_manutencao.py) a partir das recomendações do manual. As vencidas há mais de 30 dias são
     marcadas como realizadas ou não, conforme o perfil do cliente; as comprovadas recebem um comprovante simulado
     (PDF) em CS_ANEXOS e a avaliação "atende aos critérios".
  3. CS_SINISTROS: alguns sinistros (incêndio, atolamento, tombamento...), com indenização e situação.
  4. CS_SCORE_MANUTENCAO_PROCEDIMENTOS: uma avaliação de maturidade por fazenda (15 respostas, nota de 0 a 15 com
     pesos 40/40/20, igual à página de cadastro). Fazendas que já têm avaliação não recebem outra.

Cada cliente recebe um PERFIL de gestão (em rodízio): exemplar, regular ou crítico, que define a chance de comprovar
as manutenções, de ter sinistros e as respostas do questionário (para o score ter variedade).

Marcas para identificar (e apagar) os dados de teste:
  - equipamentos: NUMERO_APOLICE começa com "TESTE-" (manutenções, comprovantes e sinistros ficam ligados a eles);
  - avaliações: DATA_AVALIACAO com horário 07:07:07.

Uso:
    python tratamento/score_dados.py --simular   (mostra o que seria gravado, sem gravar)
    python tratamento/score_dados.py             (grava)
    python tratamento/score_dados.py --limpar    (apaga os dados de teste gravados por este script)
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import re
import random
import hashlib
import argparse
import datetime
import unicodedata
import oracledb
from auth import USER, PASSWORD, DSN
from requisitos.programacao_manutencao import programar_equipamento, TOLERANCIA_DIAS

SEMENTE = 2026                  # mesma semente -> mesmos dados a cada carga
PREFIXO_APOLICE = "TESTE-"
HORARIO_AVALIACAO = datetime.time(7, 7, 7)
TAMANHO_LOTE = 1000

# Uso típico por modelo: horas por mês (faixa) e operações principais
USO_POR_MODELO = {
    "CH950": {"horas_mes": (90, 180), "operacoes": ["Colheita"]},
    "5060E": {"horas_mes": (40, 100), "operacoes": ["Preparo do solo", "Plantio", "Transporte", "Tratos culturais"]},
}
USO_PADRAO = {"horas_mes": (50, 120), "operacoes": ["Outra"]}

# Perfil de gestão do cliente: chances usadas na geração dos dados
PERFIS = {
    "exemplar": {"comprova": 0.95, "atende": 0.93, "sinistro": 0.04, "telemetria": 0.9,
                 "respostas": [3, 3, 3, 2, 2, 1]},
    "regular": {"comprova": 0.70, "atende": 0.70, "sinistro": 0.12, "telemetria": 0.5,
                "respostas": [3, 2, 2, 2, 1, 1, 0]},
    "critico": {"comprova": 0.40, "atende": 0.45, "sinistro": 0.30, "telemetria": 0.15,
                "respostas": [2, 1, 1, 1, 0, 0]},
}
ORDEM_PERFIS = ["exemplar", "regular", "critico"]

TIPOS_SINISTRO = ["Incêndio", "Atolamento", "Tombamento", "Colisão", "Pane elétrica", "Quebra de transmissão"]
FRACAO_INDENIZACAO = {"Incêndio": (0.3, 0.9), "Tombamento": (0.1, 0.4), "Atolamento": (0.02, 0.1),
                      "Colisão": (0.03, 0.15), "Pane elétrica": (0.02, 0.08), "Quebra de transmissão": (0.03, 0.12)}

# Questionário de maturidade (mesma regra da página de cadastro)
COLUNAS_PERGUNTAS = [
    "P1_Q1_MANUTENCAO", "P1_Q2_CAPACITACAO_TECNICA", "P1_Q3_PECAS_HOMOLOGADAS",
    "P2_Q4_VISTORIA_PRE_OPERACIONAL", "P2_Q5_TELEMETRIA_MONITORAMENTO", "P2_Q6_CONDICOES_TERMICAS",
    "P2_Q7_PARALISACAO_INCENDIO", "P2_Q8_TRAFEGABILIDADE_CHUVA", "P2_Q9_AREAS_INCLINADAS",
    "P2_Q10_LIMPEZA_RADIADORES", "P2_Q11_COMBATE_INCENDIO",
    "P3_Q12_PROCESSOS_DOCUMENTADOS", "P3_Q13_CAPACITACAO", "P3_Q14_AUDITORIA_CONFORMIDADE", "P3_Q15_REVISAO_REGULAR",
]
PERGUNTAS_POR_PILAR = [3, 8, 4]
PESOS_PILARES = [40, 40, 20]
NOTA_MAXIMA = 15
COLUNAS_SUBTOTAL = ["SUBTOTAL_MANUTENCAO_PREVENTIVA", "SUBTOTAL_GESTAO_RISCO", "SUBTOTAL_GOVERNANCA"]


def log(msg):
    print(f"{datetime.datetime.now():%H:%M:%S} {msg}", flush=True)


def moeda(valor):
    return f"R$ {valor:,.0f}".replace(",", ".")


def sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFD", str(texto or "")) if unicodedata.category(c) != "Mn")


def normalizar(texto):
    return re.sub(r"[^A-Z0-9]", "", sem_acento(texto).upper())


def para_data(valor):
    return valor.date() if isinstance(valor, datetime.datetime) else valor


def consultar(cursor, sql, parametros=None):
    cursor.execute(sql, parametros or {})
    colunas = [c[0] for c in cursor.description]
    return [{c: (v.read() if hasattr(v, "read") else v) for c, v in zip(colunas, linha)} for linha in cursor.fetchall()]


def valores_permitidos(cursor, tabela, coluna, padrao):
    """Valores aceitos por uma restrição CHECK da coluna (ou o padrão, se não houver restrição)."""
    try:
        for linha in consultar(cursor, "SELECT SEARCH_CONDITION FROM USER_CONSTRAINTS "
                                       "WHERE TABLE_NAME = :t AND CONSTRAINT_TYPE = 'C'", {"t": tabela}):
            achado = re.search(rf"\b{coluna}\b\s+IN\s*\(([^)]*)\)", str(linha["SEARCH_CONDITION"] or ""), re.I)
            if achado:
                return re.findall(r"'([^']*)'", achado.group(1))
    except oracledb.Error:
        pass
    return padrao


def escolher(valores, *palavras, padrao=None):
    """Primeiro valor aceito que contém alguma das palavras (sem acento, maiúsculas)."""
    for palavra in palavras:
        for valor in valores:
            if palavra in sem_acento(valor).upper():
                return valor
    return padrao if padrao is not None else valores[0]


def id_automatico(cursor, tabela):
    """True se a coluna ID é gerada pelo banco (identity ou valor padrão de sequence)."""
    try:
        cursor.execute("SELECT COUNT(*) FROM USER_TAB_IDENTITY_COLS WHERE TABLE_NAME = :t AND COLUMN_NAME = 'ID'",
                       {"t": tabela})
        if cursor.fetchone()[0]:
            return True
        cursor.execute("SELECT DATA_DEFAULT FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :t AND COLUMN_NAME = 'ID'",
                       {"t": tabela})
        linha = cursor.fetchone()
        return bool(linha and linha[0] and str(linha[0]).strip())
    except oracledb.Error:
        return False


class Gravador:
    """Insere em lotes. Se o banco não gera o ID, numera a partir do maior ID da tabela."""

    def __init__(self, cursor, tabela, colunas):
        self.cursor, self.tabela, self.colunas = cursor, tabela, colunas
        self.automatico = id_automatico(cursor, tabela)
        cursor.execute(f"SELECT NVL(MAX(ID), 0) FROM {tabela}")
        self.proximo = int(cursor.fetchone()[0]) + 1
        self.pendentes = []
        self.total = 0

    def novo_id(self):
        valor, self.proximo = self.proximo, self.proximo + 1
        return valor

    def inserir_com_retorno(self, dados):
        """Insere uma linha e devolve o ID (usado nos equipamentos, que ligam manutenções e sinistros)."""
        if not self.automatico:
            novo = self.novo_id()
            self.cursor.execute(f"INSERT INTO {self.tabela} (ID, {', '.join(self.colunas)}) "
                                f"VALUES (:ID, {', '.join(':' + c for c in self.colunas)})", dict(dados, ID=novo))
            self.total += 1
            return novo
        saida = self.cursor.var(oracledb.NUMBER)
        self.cursor.execute(f"INSERT INTO {self.tabela} ({', '.join(self.colunas)}) "
                            f"VALUES ({', '.join(':' + c for c in self.colunas)}) RETURNING ID INTO :saida",
                            dict(dados, saida=saida))
        self.total += 1
        valor = saida.getvalue()
        return int(valor[0] if isinstance(valor, list) else valor)

    def adicionar(self, dados):
        if not self.automatico:
            dados = dict(dados, ID=self.novo_id())
        self.pendentes.append(dados)
        if len(self.pendentes) >= TAMANHO_LOTE:
            self.descarregar()

    def descarregar(self):
        if not self.pendentes:
            return
        colunas = (["ID"] if not self.automatico else []) + self.colunas
        self.cursor.executemany(f"INSERT INTO {self.tabela} ({', '.join(colunas)}) "
                                f"VALUES ({', '.join(':' + c for c in colunas)})", self.pendentes)
        self.total += len(self.pendentes)
        self.pendentes = []


def pdf_comprovante(texto):
    """PDF mínimo (uma página com o texto) para o comprovante simulado."""
    texto = sem_acento(texto).replace("(", "[").replace(")", "]")[:90]
    conteudo = f"BT /F1 11 Tf 40 780 Td ({texto}) Tj ET".encode("latin-1")
    objetos = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(conteudo)).encode() + b" >>\nstream\n" + conteudo + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    saida, posicoes = b"%PDF-1.4\n", []
    for numero, objeto in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f"{numero} 0 obj\n".encode() + objeto + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    saida += b"".join(f"{p:010d} 00000 n \n".encode() for p in posicoes)
    saida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n".encode()
    return saida


def nota_maturidade(respostas):
    """Subtotais ponderados (até 6, 6 e 3) e nota final de 0 a 15 (mesma regra da página de cadastro)."""
    subtotais, inicio = [], 0
    for quantidade, peso in zip(PERGUNTAS_POR_PILAR, PESOS_PILARES):
        pontos = sum(respostas[inicio:inicio + quantidade])
        subtotais.append(round(pontos / (3 * quantidade) * peso / 100 * NOTA_MAXIMA, 2))
        inicio += quantidade
    return subtotais, round(sum(subtotais), 2)


def limpar(connection):
    cursor = connection.cursor()
    filtro = f"SELECT ID FROM CS_EQUIPAMENTOS_SEGURADOS WHERE NUMERO_APOLICE LIKE '{PREFIXO_APOLICE}%'"
    try:
        cursor.execute(f"""DELETE FROM CS_ANEXOS WHERE TIPO_ENTIDADE = 'MANUTENCAO' AND ID_ENTIDADE IN
                           (SELECT ID FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro}))""")
        log(f"Comprovantes de teste apagados: {cursor.rowcount}")
        cursor.execute(f"DELETE FROM CS_ANEXOS WHERE TIPO_ENTIDADE = 'EQUIPAMENTO' AND ID_ENTIDADE IN ({filtro})")
    except oracledb.Error:
        pass    # tabela de anexos ainda não criada
    cursor.execute(f"DELETE FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro})")
    log(f"Manutenções de teste apagadas: {cursor.rowcount}")
    cursor.execute(f"DELETE FROM CS_SINISTROS WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro})")
    log(f"Sinistros de teste apagados: {cursor.rowcount}")
    cursor.execute(f"DELETE FROM CS_EQUIPAMENTOS_SEGURADOS WHERE NUMERO_APOLICE LIKE '{PREFIXO_APOLICE}%'")
    log(f"Equipamentos de teste apagados: {cursor.rowcount}")
    cursor.execute("DELETE FROM CS_SCORE_MANUTENCAO_PROCEDIMENTOS WHERE TO_CHAR(DATA_AVALIACAO, 'HH24:MI:SS') = :h",
                   {"h": HORARIO_AVALIACAO.strftime("%H:%M:%S")})
    log(f"Avaliações de teste apagadas: {cursor.rowcount}")
    connection.commit()


def carregar(connection, simular):
    rnd = random.Random(SEMENTE)
    cursor = connection.cursor()
    hoje = datetime.date.today()

    clientes = consultar(cursor, """
        SELECT c.ID, c.RAZAO_SOCIAL FROM CS_CLIENTES c
        WHERE EXISTS (SELECT 1 FROM CS_FAZENDAS f WHERE f.ID_CLIENTE = c.ID) ORDER BY c.ID
    """)
    fazendas = consultar(cursor, "SELECT ID, ID_CLIENTE, NOME_FAZENDA FROM CS_FAZENDAS ORDER BY ID")
    # Só modelos com manual vinculado (as recomendações valem para o modelo exato)
    modelos = consultar(cursor, """
        SELECT mo.ID, mo.FABRICANTE, mo.MODELO, mo.TIPO, mo.VALOR_ESTIMADO FROM CS_EQUIPAMENTOS_MODELOS mo
        WHERE mo.VALOR_ESTIMADO IS NOT NULL
          AND EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MANUAIS ma WHERE ma.ID_EQUIPAMENTO = mo.ID)
        ORDER BY mo.ID
    """)
    com_equipamento = {r["ID_FAZENDA"] for r in consultar(cursor, "SELECT DISTINCT ID_FAZENDA FROM CS_EQUIPAMENTOS_SEGURADOS")}
    com_avaliacao = {r["ID_FAZENDA"] for r in consultar(cursor, "SELECT DISTINCT ID_FAZENDA FROM CS_SCORE_MANUTENCAO_PROCEDIMENTOS")}
    log(f"{len(clientes)} cliente(s), {len(fazendas)} fazenda(s); modelos com manual vinculado: "
        f"{', '.join(m['FABRICANTE'] + ' ' + m['MODELO'] for m in modelos) or 'nenhum'}.")
    if not modelos:
        log("[ERRO] Nenhum modelo com manual vinculado. Rode antes sql/cs_equipamentos_manuais_vinculo.sql.")
        return
    colunas_equip = {r["COLUMN_NAME"] for r in consultar(
        cursor, "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CS_EQUIPAMENTOS_SEGURADOS'")}
    colunas_uso = ["DATA_AQUISICAO", "HORIMETRO_ATUAL", "DATA_LEITURA", "USO_MEDIO_HORAS_MES", "TIPO_OPERACAO"]
    if not all(c in colunas_equip for c in colunas_uso):
        log("[ERRO] Faltam as colunas de uso dos equipamentos. Rode antes sql/cs_equipamentos_uso.sql.")
        return

    telemetria_ok = valores_permitidos(cursor, "CS_EQUIPAMENTOS_SEGURADOS", "TELEMETRIA", ["SIM", "NAO"])
    status_ok = valores_permitidos(cursor, "CS_EQUIPAMENTOS_SEGURADOS", "STATUS", ["ATIVO", "INATIVO"])
    realizacao_ok = valores_permitidos(cursor, "CS_MANUTENCOES_REALIZADAS", "REALIZACAO",
                                       ["REALIZADA", "NAO REALIZADA", "PENDENTE"])
    estado_ok = valores_permitidos(cursor, "CS_SINISTROS", "ESTADO_SINISTRO", ["ABERTO", "EM ANALISE", "ENCERRADO"])
    SIM, NAO = escolher(telemetria_ok, "SIM", "S"), escolher(telemetria_ok, "NAO", "N", padrao=telemetria_ok[-1])
    ATIVO = escolher(status_ok, "ATIV")
    REALIZADA = escolher(realizacao_ok, "REALIZADA", "CONCLU")
    NAO_REALIZADA = escolher(realizacao_ok, "NAO", "ATRAS", padrao=realizacao_ok[-1])
    ABERTO = escolher(estado_ok, "ABERT", "ANALISE")
    ENCERRADO = escolher(estado_ok, "ENCERR", "PAGO", "FINALIZ", padrao=estado_ok[-1])

    g_equip = Gravador(cursor, "CS_EQUIPAMENTOS_SEGURADOS", [
        "ID_FAZENDA", "ID_EQUIPAMENTO_MODELO", "NUMERO_SERIE", "IDENTIFICACAO_INTERNA", "VALOR_SEGURADO",
        "TELEMETRIA", "NUMERO_APOLICE", "DATA_INICIO_VIGENCIA", "DATA_FIM_VIGENCIA", "STATUS"] + colunas_uso)
    g_sinistro = Gravador(cursor, "CS_SINISTROS", [
        "ID_EQUIPAMENTO_SEGURADO", "NUMERO_APOLICE", "DATA_OCORRENCIA", "TIPO_SINISTRO", "VALOR_INDENIZACAO",
        "NUMERO_AVISO_SINISTRO", "ESTADO_SINISTRO", "DATA_ENCERRAMENTO"])
    g_aval = Gravador(cursor, "CS_SCORE_MANUTENCAO_PROCEDIMENTOS",
                      ["ID_FAZENDA", "DATA_AVALIACAO"] + COLUNAS_PERGUNTAS + COLUNAS_SUBTOTAL + ["SCORE_FINAL"])
    g_anexo = Gravador(cursor, "CS_ANEXOS", [
        "TIPO_ENTIDADE", "ID_ENTIDADE", "TIPO_ANEXO", "NOME_ARQUIVO", "TIPO_CONTEUDO", "TAMANHO_BYTES", "HASH_SHA256",
        "ARQUIVO", "USUARIO_ENVIO"])
    atualizacoes = []    # manutenções vencidas: realizada ou não, comprovante, atende

    fazendas_por_cliente = {}
    for f in fazendas:
        fazendas_por_cliente.setdefault(f["ID_CLIENTE"], []).append(f)

    resumo = []
    for indice_cliente, cliente in enumerate(clientes):
        perfil_nome = ORDEM_PERFIS[indice_cliente % len(ORDEM_PERFIS)]
        perfil = PERFIS[perfil_nome]
        r = {"cliente": cliente["RAZAO_SOCIAL"], "perfil": perfil_nome, "valor": 0.0, "equip": 0, "programadas": 0,
             "encerradas": 0, "comprovadas": 0, "atendem": 0, "sinistros": 0, "notas": []}
        for fazenda in fazendas_por_cliente.get(cliente["ID"], []):
            # --- Equipamentos, manutenções programadas e sinistros
            if fazenda["ID"] not in com_equipamento:
                for n in range(rnd.randint(2, 4)):
                    modelo = rnd.choice(modelos)
                    uso = USO_POR_MODELO.get(modelo["MODELO"], USO_PADRAO)
                    valor = round(float(modelo["VALOR_ESTIMADO"]) * rnd.uniform(0.6, 1.0), -3)
                    inicio = hoje - datetime.timedelta(days=rnd.randint(200, 3 * 365))
                    fim = inicio + datetime.timedelta(days=365)
                    while fim < hoje:                                  # apólice renovada todo ano
                        fim += datetime.timedelta(days=365)
                    aquisicao = inicio - datetime.timedelta(days=rnd.randint(0, 2 * 365))
                    leitura = hoje - datetime.timedelta(days=rnd.randint(0, 30))
                    horas_mes = round(rnd.uniform(*uso["horas_mes"]), 0)
                    horimetro = round(horas_mes * (leitura - aquisicao).days / 30.4375 * rnd.uniform(0.85, 1.1), 0)
                    apolice = f"{PREFIXO_APOLICE}{cliente['ID']:04d}-{fazenda['ID']:05d}-{n + 1}"
                    id_equip = g_equip.inserir_com_retorno({
                        "ID_FAZENDA": fazenda["ID"], "ID_EQUIPAMENTO_MODELO": modelo["ID"],
                        "NUMERO_SERIE": f"{normalizar(modelo['MODELO'])[:6]}{rnd.randint(100000, 999999)}",
                        "IDENTIFICACAO_INTERNA": f"{normalizar(modelo['TIPO'])[:3]}-{fazenda['ID']}-{n + 1:02d}",
                        "VALOR_SEGURADO": valor,
                        "TELEMETRIA": SIM if rnd.random() < perfil["telemetria"] else NAO,
                        "NUMERO_APOLICE": apolice, "DATA_INICIO_VIGENCIA": inicio, "DATA_FIM_VIGENCIA": fim,
                        "STATUS": ATIVO, "DATA_AQUISICAO": aquisicao, "HORIMETRO_ATUAL": horimetro,
                        "DATA_LEITURA": leitura, "USO_MEDIO_HORAS_MES": horas_mes,
                        "TIPO_OPERACAO": rnd.choice(uso["operacoes"])})
                    r["valor"] += valor
                    r["equip"] += 1

                    # Lista de manutenções a comprovar: a mesma programação usada pelo sistema
                    r["programadas"] += programar_equipamento(connection, id_equip, hoje)["geradas"]
                    for atividade in consultar(cursor, """
                        SELECT ID, DATA_PREVISTA FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO = :e
                    """, {"e": id_equip}):
                        prevista = para_data(atividade["DATA_PREVISTA"])
                        if (hoje - prevista).days <= TOLERANCIA_DIAS:
                            continue              # a vencer ou ainda no prazo de comprovação: fica pendente
                        comprovou = rnd.random() < perfil["comprova"]
                        atende = comprovou and rnd.random() < perfil["atende"]
                        realizada = min(hoje, prevista + datetime.timedelta(days=rnd.randint(-5, 20)))
                        atualizacoes.append({"id": atividade["ID"],
                                             "realizacao": REALIZADA if comprovou else NAO_REALIZADA,
                                             "data_realizada": realizada if comprovou else None,
                                             "comprovante": "SIM" if comprovou else "NAO",
                                             "atende": (1 if atende else 0) if comprovou else None})
                        r["encerradas"] += 1
                        r["comprovadas"] += int(comprovou)
                        r["atendem"] += int(atende)
                        if comprovou:
                            arquivo = pdf_comprovante(f"Comprovante simulado - manutencao {atividade['ID']} - "
                                                      f"{realizada:%d/%m/%Y} - {modelo['MODELO']}")
                            g_anexo.adicionar({
                                "TIPO_ENTIDADE": "MANUTENCAO", "ID_ENTIDADE": atividade["ID"],
                                "TIPO_ANEXO": "COMPROVANTE", "NOME_ARQUIVO": f"comprovante_{atividade['ID']}.pdf",
                                "TIPO_CONTEUDO": "application/pdf", "TAMANHO_BYTES": len(arquivo),
                                "HASH_SHA256": hashlib.sha256(arquivo).hexdigest(), "ARQUIVO": arquivo,
                                "USUARIO_ENVIO": "Carga de teste"})

                    if rnd.random() < perfil["sinistro"]:
                        tipo = rnd.choice(TIPOS_SINISTRO)
                        ocorrencia = datetime.datetime.combine(
                            inicio + datetime.timedelta(days=rnd.randint(0, max(1, (hoje - inicio).days))),
                            datetime.time(rnd.randint(6, 18), rnd.choice([0, 15, 30, 45])))
                        indenizacao = round(valor * rnd.uniform(*FRACAO_INDENIZACAO[tipo]), 2)
                        encerrado = (hoje - ocorrencia.date()).days > 60
                        g_sinistro.adicionar({
                            "ID_EQUIPAMENTO_SEGURADO": id_equip, "NUMERO_APOLICE": apolice,
                            "DATA_OCORRENCIA": ocorrencia, "TIPO_SINISTRO": tipo,
                            "VALOR_INDENIZACAO": indenizacao if encerrado else None,
                            "NUMERO_AVISO_SINISTRO": f"AVS-{ocorrencia:%Y%m}-{rnd.randint(1000, 9999)}",
                            "ESTADO_SINISTRO": ENCERRADO if encerrado else ABERTO,
                            "DATA_ENCERRAMENTO": (ocorrencia.date() + datetime.timedelta(days=rnd.randint(20, 60))
                                                  if encerrado else None)})
                        r["sinistros"] += 1

            # --- Avaliação de maturidade (uma por fazenda)
            if fazenda["ID"] not in com_avaliacao:
                respostas = [rnd.choice(perfil["respostas"]) for _ in COLUNAS_PERGUNTAS]
                subtotais, nota = nota_maturidade(respostas)
                dia = hoje - datetime.timedelta(days=rnd.randint(5, 90))
                dados = {"ID_FAZENDA": fazenda["ID"], "DATA_AVALIACAO": datetime.datetime.combine(dia, HORARIO_AVALIACAO),
                         "SCORE_FINAL": nota}
                dados.update(dict(zip(COLUNAS_PERGUNTAS, respostas)))
                dados.update(dict(zip(COLUNAS_SUBTOTAL, subtotais)))
                g_aval.adicionar(dados)
                r["notas"].append(nota)
        resumo.append(r)

    for inicio_lote in range(0, len(atualizacoes), TAMANHO_LOTE):
        cursor.executemany("""
            UPDATE CS_MANUTENCOES_REALIZADAS SET REALIZACAO = :realizacao, DATA_REALIZADA = :data_realizada,
                   COMPROVANTE_ENTREGUE = :comprovante, ATENDE_CRITERIOS = :atende
            WHERE ID = :id
        """, atualizacoes[inicio_lote:inicio_lote + TAMANHO_LOTE])
    cursor.setinputsizes(ARQUIVO=oracledb.DB_TYPE_BLOB)
    for gravador in (g_sinistro, g_aval, g_anexo):
        gravador.descarregar()

    log("=" * 118)
    log(f" {'Cliente':<36}{'Perfil':<10}{'Equip.':>7}{'Valor segurado':>18}{'Programadas':>13}{'Prazo encerr.':>15}"
        f"{'Comprov.':>10}{'Sinistros':>10}{'Maturidade':>12}")
    for r in resumo:
        comprov = f"{r['comprovadas'] / r['encerradas'] * 100:.0f}%" if r["encerradas"] else "-"
        maturidade = f"{sum(r['notas']) / len(r['notas']):.1f}/15" if r["notas"] else "-"
        log(f" {r['cliente'][:35]:<36}{r['perfil']:<10}{r['equip']:>7}{moeda(r['valor']):>18}{r['programadas']:>13}"
            f"{r['encerradas']:>15}{comprov:>10}{r['sinistros']:>10}{maturidade:>12}")
    log("=" * 118)
    log(f"Linhas: {g_equip.total} equipamentos, {sum(r['programadas'] for r in resumo)} manutenções programadas "
        f"({len(atualizacoes)} com prazo encerrado), {g_anexo.total} comprovantes, {g_sinistro.total} sinistros, "
        f"{g_aval.total} avaliações.")
    if simular:
        connection.rollback()
        log("Simulação: nada foi gravado. Para gravar, rode sem --simular.")
    else:
        connection.commit()
        log("Dados de teste gravados. Para apagar: python tratamento/score_dados.py --limpar")


def main():
    parser = argparse.ArgumentParser(description="Carga de dados de teste para o Score de Risco.")
    parser.add_argument("--simular", action="store_true", help="mostra o que seria gravado, sem gravar")
    parser.add_argument("--limpar", action="store_true", help="apaga os dados de teste gravados por este script")
    args = parser.parse_args()
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        if args.limpar:
            limpar(connection)
        else:
            carregar(connection, args.simular)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
