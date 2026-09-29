# -*- coding: utf-8 -*-
"""
Carga de DADOS DE TESTE para o Score de Risco (trabalho acadêmico).

Pré-requisito (rodar antes, no Oracle): sql/estrutura_banco.sql.

1. Apaga os exemplos anteriores: dados de teste deste script (apólices TESTE-), o cliente "Exemplo Comprovação"
   (tratamento/comprovacao_exemplo.py) e os registros de ações e scores ligados a eles.
2. Para os 20 clientes de CLIENTES_TESTE (clientes que já estão no banco, com fazendas, coordenadas, relevo e clima),
   gera dados coerentes com as regras do sistema, conforme o PERFIL de risco de cada um (baixo, médio ou alto):
   - CS_EQUIPAMENTOS_SEGURADOS: 3 a 10 máquinas por fazenda (umas fazendas com mais, outras com menos), SÓ de
     modelos com manual e recomendações gravadas; a quantidade de colhedoras define a exposição (baixo até R$ 20 mi,
     médio R$ 20 a 60 mi, alto acima de R$ 60 mi, limitada a 10 máquinas por fazenda);
   - CS_MANUTENCOES_REALIZADAS: o cronograma do sistema (requisitos/programacao_manutencao.py); cada REVISÃO
     (mesma data e periodicidade) recebe um comprovante (PDF) conforme o perfil, com atraso realista (a maioria em
     até 15 dias, quase nunca além do prazo de 90 dias), validação da Sompo (atende, maior parte, menor parte, não
     atende) e, no perfil baixo, revisões feitas a mais (regra ideal, bônus). Revisões recentes ficam aguardando
     comprovante ou validação, como na vida real;
   - CS_LEITURAS_MEDIDOR: leituras mensais do horímetro com foto simulada;
   - CS_SINISTROS: só parte dos clientes (nenhum no perfil baixo; pequenos no médio; graves no alto);
   - CS_SCORE_MANUTENCAO_PROCEDIMENTOS: uma avaliação de maturidade por fazenda, conforme o perfil;
   - CS_LOG_ACOES: envio e validação de cada comprovante (usuário "Carga de teste");
   - CS_ANEXOS: comprovante válido = a fatura de exemplo (assets/comprovacao_fatura_exemplo.pdf); reprovado (Não
     atende) = PDF com "EXEMPLO COMPROVANTE INVÁLIDO" em vermelho.
   Os itens ambientais (queimadas e hidrológico) e o climático vêm dos dados reais de cada fazenda.
3. Com os dados gravados, calcule o score: python requisitos/score_risco.py --gravar (ou o botão na página Score Risk).

Marcas para identificar (e apagar) os dados de teste: equipamentos com NUMERO_APOLICE começando por "TESTE-";
avaliações com horário 07:07:07; ações com usuário "Carga de teste".

Uso:
    python tratamento/score_dados.py --simular   (mostra o que seria gravado, sem gravar)
    python tratamento/score_dados.py             (apaga os exemplos anteriores e grava)
    python tratamento/score_dados.py --limpar    (só apaga)
    python tratamento/score_dados.py --continuar (a conexão caiu no meio: carrega só os clientes que faltam)
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
from requisitos.programacao_manutencao import (programar_equipamento, REALIZACAO_PADRAO, valor_realizacao,
                                               periodicidade, orientacoes_do_modelo, tem_duas_opcoes,
                                               vencimentos, inicio_periodo)
from requisitos.comprovacao import PRAZO_COMPROVANTE_DIAS
from requisitos.auditoria import chave_revisao

SEMENTE = 2026                  # mesma semente -> mesmos dados a cada carga
PREFIXO_APOLICE = "TESTE-"
HORARIO_AVALIACAO = datetime.time(7, 7, 7)
USUARIO_CARGA = "Carga de teste"
TAMANHO_LOTE = 1000
MIN_MAQUINAS_FAZENDA, MAX_MAQUINAS_FAZENDA = 3, 10
# Comprovantes: válidos (aprovados, aprovados em parte ou aguardando validação) recebem a fatura de exemplo;
# reprovados (Não atende) recebem um PDF com "EXEMPLO COMPROVANTE INVÁLIDO"
FATURA_EXEMPLO = os.path.join(root_dir, "assets", "comprovacao_fatura_exemplo.pdf")

# Clientes de teste (ID em CS_CLIENTES -> perfil de risco), escolhidos para variar estado e relevo.
# Vazio: os 20 primeiros clientes com fazenda, em rodízio de perfis.
CLIENTES_TESTE = {
    # risco baixo (7): frota bem cuidada, sem sinistro, maturidade alta
    5: "baixo",    # Adecoagro Vale do Ivinhema (MS, plano)
    41: "baixo",   # SLC Agrícola (MG)
    45: "baixo",   # Grupo Scheffer (BA)
    52: "baixo",   # Cerrado Verde Agropecuária (MT)
    73: "baixo",   # Grupo Junqueira Rodas (GO, plano)
    85: "baixo",   # Grupo Perondi (MA)
    68: "baixo",   # Grupo Nakagawa (GO, relevo forte ondulado: climático pior, score continua baixo)
    # risco médio (7)
    9: "medio",    # Raízen Energia (SP)
    42: "medio",   # BrasilAgro (AM)
    44: "medio",   # Bom Futuro Agrícola (MA)
    54: "medio",   # Agro Pastoril Canezin (GO)
    60: "medio",   # Agro Santa Margarida (MT)
    76: "medio",   # Agropecuária Rio Verde (GO, ondulado)
    88: "medio",   # Agropecuária Zanatta (MG)
    # risco alto (6): frota grande, pouca comprovação, sinistros graves, maturidade baixa
    3: "alto",     # Atvos Agroindustrial (GO)
    8: "alto",     # Jalles Machado (GO, plano)
    49: "alto",    # Grupo Andre Maggi (AL)
    67: "alto",    # Agropecuária Sol Nascente (MT)
    81: "alto",    # Grupo Bortolini (GO, forte ondulado)
    83: "alto",    # Grupo Menin (BA, ondulado)
}
QTD_CLIENTES_AUTOMATICO = 20

# Uso típico por modelo: horas por mês (faixa) e operações principais
USO_POR_MODELO = {
    "CH950": {"horas_mes": (90, 180), "operacoes": ["Colheita"]},
    "5060E": {"horas_mes": (40, 100), "operacoes": ["Preparo do solo", "Plantio", "Transporte", "Tratos culturais"]},
    "6075": {"horas_mes": (50, 110), "operacoes": ["Preparo do solo", "Plantio", "Pulverização", "Transporte"]},
    "6065": {"horas_mes": (50, 110), "operacoes": ["Preparo do solo", "Plantio", "Transporte"]},
}
USO_PADRAO = {"horas_mes": (50, 120), "operacoes": ["Outra"]}
VALOR_PADRAO = 240000           # modelo sem VALOR_ESTIMADO (ex.: cadastrado na tela sem valor)
# Tipo de tração por modelo (a colhedora CH950 anda sobre esteiras; os tratores têm versões 2WD e 4WD)
TRACAO_POR_MODELO = {"CH950": lambda rnd: "ESTEIRA"}
MESES_LEITURAS = 6               # leituras mensais do horímetro simuladas

# Perfil de risco: o que cada um controla nos itens do score
#   exposicao: faixa de valor segurado total (R$); uso: fator sobre as horas/mês do modelo (complexidade);
#   envia: chance de enviar o comprovante da revisão; atraso: dias depois da data prevista (faixas e chances);
#   validacao: chances de atende (100), maior parte (75), menor parte (25), não atende (0);
#   extra: chance de fazer a revisão a mais (regra ideal); sinistro: chance do cliente ter sinistros e a gravidade;
#   respostas: respostas possíveis do questionário de maturidade (0 a 3)
PERFIS = {
    "baixo": {"exposicao": (6e6, 16e6), "uso": 0.6, "envia": 0.96, "telemetria": 0.9,
              "atraso": [((-7, 0), 0.25), ((1, 15), 0.65), ((16, 40), 0.10)],
              "validacao": [(100, 0.85), (75, 0.12), (25, 0.03), (0, 0.0)], "extra": 0.7,
              "sinistro": (0.0, None), "respostas": [3, 3, 3, 3, 2]},
    "medio": {"exposicao": (26e6, 50e6), "uso": 0.9, "envia": 0.72, "telemetria": 0.5,
              "atraso": [((-3, 0), 0.10), ((1, 20), 0.55), ((21, 60), 0.30), ((61, 85), 0.05)],
              "validacao": [(100, 0.55), (75, 0.25), (25, 0.12), (0, 0.08)], "extra": 0.15,
              "sinistro": (0.6, "leve"), "respostas": [3, 2, 2, 1, 1, 2]},
    "alto": {"exposicao": (65e6, 95e6), "uso": 1.15, "envia": 0.45, "telemetria": 0.15,
             "atraso": [((1, 20), 0.35), ((21, 60), 0.45), ((61, 88), 0.20)],
             "validacao": [(100, 0.30), (75, 0.25), (25, 0.25), (0, 0.20)], "extra": 0.0,
             "sinistro": (0.85, "grave"), "respostas": [2, 1, 1, 0, 1]},
}
ORDEM_PERFIS = ["baixo", "medio", "alto"]

TIPOS_SINISTRO = {"leve": ["Atolamento", "Colisão", "Pane elétrica", "Quebra de transmissão"],
                  "grave": ["Incêndio", "Tombamento", "Incêndio"]}
FRACAO_INDENIZACAO = {"Incêndio": (0.6, 1.0), "Tombamento": (0.3, 0.6), "Atolamento": (0.02, 0.08),
                      "Colisão": (0.03, 0.12), "Pane elétrica": (0.02, 0.06), "Quebra de transmissão": (0.04, 0.12)}
LIMITE_SINISTRO_LEVE = 0.15      # perfil médio: indenizações até 15% do valor segurado (nota 2 no score)

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


def pdf_invalido():
    """PDF de uma página com "EXEMPLO COMPROVANTE INVÁLIDO" em letras grandes e vermelhas."""
    linhas, tamanho = [("EXEMPLO", 560), ("COMPROVANTE", 480), ("INVÁLIDO", 400)], 56
    # larguras da Helvetica-Bold (milésimos do tamanho da letra), para centralizar
    larguras = {"A": 722, "Á": 722, "C": 722, "D": 722, "E": 667, "I": 278, "L": 611, "M": 833, "N": 722, "O": 778,
                "P": 667, "R": 722, "T": 611, "V": 667, "X": 667}
    comandos = [f"BT /F1 {tamanho} Tf 0.85 0 0 rg"]
    for texto, y in linhas:
        largura = sum(larguras.get(letra, 722) for letra in texto) * tamanho / 1000
        comandos.append(f"1 0 0 1 {(595 - largura) / 2:.0f} {y} Tm ({texto}) Tj")
    comandos.append("ET")
    conteudo = "\n".join(comandos).encode("cp1252")
    objetos = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R "
               b"/Resources << /Font << /F1 5 0 R >> >> >>",
               b"<< /Length " + str(len(conteudo)).encode() + b" >>\nstream\n" + conteudo + b"\nendstream",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>"]
    saida, posicoes = b"%PDF-1.4\n", []
    for numero, objeto in enumerate(objetos, start=1):
        posicoes.append(len(saida))
        saida += f"{numero} 0 obj\n".encode() + objeto + b"\nendobj\n"
    inicio_xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    saida += b"".join(f"{p:010d} 00000 n \n".encode() for p in posicoes)
    saida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{inicio_xref}\n%%EOF\n".encode()
    return saida


def pdf_fatura():
    """Fatura de exemplo (assets/comprovacao_fatura_exemplo.pdf), usada nos comprovantes válidos."""
    if os.path.exists(FATURA_EXEMPLO):
        with open(FATURA_EXEMPLO, "rb") as arquivo:
            return arquivo.read()
    log(f"[AVISO] {FATURA_EXEMPLO} não encontrado: os comprovantes válidos usam um PDF simples.")
    return None


def montar_frota(rnd, fazendas, tratores, colhedoras, alvo):
    """Máquinas de cada fazenda: {id_fazenda: [modelos]}.
    O total do cliente é o da carga anterior (3 a 4 tratores por fazenda + colhedoras até o valor segurado alvo do
    perfil), para não gerar mais comprovantes; ele é redistribuído entre as fazendas com MIN_MAQUINAS_FAZENDA a
    MAX_MAQUINAS_FAZENDA máquinas cada (fazendas com pesos sorteados, umas com mais máquinas que outras)."""
    qtd_fazendas = len(fazendas)
    tratores_base = sum(rnd.randint(3, 4) for _ in fazendas)
    valor_trator = sum(m["VALOR"] for m in tratores) / len(tratores)
    qtd_colhedoras = 0
    if colhedoras:
        valor_colhedora = sum(m["VALOR"] for m in colhedoras) / len(colhedoras)
        qtd_colhedoras = max(0, round((alvo - tratores_base * valor_trator * 0.9) / (valor_colhedora * 0.9)))
    total = tratores_base + qtd_colhedoras
    total = min(max(total, MIN_MAQUINAS_FAZENDA * qtd_fazendas), MAX_MAQUINAS_FAZENDA * qtd_fazendas)
    qtd_colhedoras = min(qtd_colhedoras, total)
    maquinas = [rnd.choice(colhedoras) for _ in range(qtd_colhedoras)] + \
        [rnd.choice(tratores) for _ in range(total - qtd_colhedoras)]
    rnd.shuffle(maquinas)
    contagem = [MIN_MAQUINAS_FAZENDA] * qtd_fazendas
    pesos = [rnd.random() ** 2 + 0.05 for _ in fazendas]
    for _ in range(total - sum(contagem)):
        livres = [i for i in range(qtd_fazendas) if contagem[i] < MAX_MAQUINAS_FAZENDA]
        escolhida = rnd.choices(livres, weights=[pesos[i] for i in livres])[0]
        contagem[escolhida] += 1
    frota, inicio = {}, 0
    for fazenda, qtd in zip(fazendas, contagem):
        frota[fazenda["ID"]] = maquinas[inicio:inicio + qtd]
        inicio += qtd
    return frota


def nota_maturidade(respostas):
    """Subtotais ponderados (até 6, 6 e 3) e nota final de 0 a 15 (mesma regra da página de cadastro)."""
    subtotais, inicio = [], 0
    for quantidade, peso in zip(PERGUNTAS_POR_PILAR, PESOS_PILARES):
        pontos = sum(respostas[inicio:inicio + quantidade])
        subtotais.append(round(pontos / (3 * quantidade) * peso / 100 * NOTA_MAXIMA, 2))
        inicio += quantidade
    return subtotais, round(sum(subtotais), 2)


def sortear(rnd, opcoes):
    """[(valor, chance)] -> um valor, pela chance."""
    x, acumulado = rnd.random(), 0.0
    for valor, chance in opcoes:
        acumulado += chance
        if x <= acumulado:
            return valor
    return opcoes[-1][0]


def tabela_existe(cursor, tabela):
    return bool(consultar(cursor, "SELECT TABLE_NAME FROM USER_TABLES WHERE TABLE_NAME = :t", {"t": tabela}))


def colunas_da_tabela(cursor, tabela):
    return {r["COLUMN_NAME"] for r in consultar(cursor, "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :t",
                                                {"t": tabela})}


def limpar(connection, commit=True):
    """Apaga os exemplos anteriores: dados de teste (apólices TESTE-), o cliente Exemplo Comprovação, os registros de
    ações da carga e os scores das fazendas envolvidas."""
    cursor = connection.cursor()
    filtro = f"SELECT ID FROM CS_EQUIPAMENTOS_SEGURADOS WHERE NUMERO_APOLICE LIKE '{PREFIXO_APOLICE}%'"
    if tabela_existe(cursor, "CS_SCORE_GESTAO"):
        cursor.execute(f"""DELETE FROM CS_SCORE_GESTAO WHERE ID_FAZENDA IN
                           (SELECT ID_FAZENDA FROM CS_EQUIPAMENTOS_SEGURADOS WHERE ID IN ({filtro}))
                           OR ID_FAZENDA IN (SELECT f.ID FROM CS_FAZENDAS f JOIN CS_CLIENTES c ON c.ID = f.ID_CLIENTE
                                             WHERE c.CNPJ = '00000000000000')""")
        log(f"Scores das fazendas de teste apagados: {cursor.rowcount}")
    cursor.execute(f"""DELETE FROM CS_ANEXOS WHERE TIPO_ENTIDADE = 'MANUTENCAO' AND ID_ENTIDADE IN
                       (SELECT ID FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro}))""")
    log(f"Comprovantes de teste apagados: {cursor.rowcount}")
    cursor.execute(f"DELETE FROM CS_ANEXOS WHERE TIPO_ENTIDADE = 'EQUIPAMENTO' AND ID_ENTIDADE IN ({filtro})")
    if tabela_existe(cursor, "CS_LEITURAS_MEDIDOR"):
        cursor.execute(f"""DELETE FROM CS_ANEXOS WHERE TIPO_ENTIDADE = 'LEITURA' AND ID_ENTIDADE IN
                           (SELECT ID FROM CS_LEITURAS_MEDIDOR WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro}))""")
        cursor.execute(f"DELETE FROM CS_LEITURAS_MEDIDOR WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro})")
        log(f"Leituras de teste apagadas: {cursor.rowcount}")
    cursor.execute(f"DELETE FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro})")
    log(f"Manutenções de teste apagadas: {cursor.rowcount}")
    cursor.execute(f"DELETE FROM CS_SINISTROS WHERE ID_EQUIPAMENTO_SEGURADO IN ({filtro})")
    log(f"Sinistros de teste apagados: {cursor.rowcount}")
    cursor.execute(f"DELETE FROM CS_EQUIPAMENTOS_SEGURADOS WHERE NUMERO_APOLICE LIKE '{PREFIXO_APOLICE}%'")
    log(f"Equipamentos de teste apagados: {cursor.rowcount}")
    cursor.execute("DELETE FROM CS_SCORE_MANUTENCAO_PROCEDIMENTOS WHERE TO_CHAR(DATA_AVALIACAO, 'HH24:MI:SS') = :h",
                   {"h": HORARIO_AVALIACAO.strftime("%H:%M:%S")})
    log(f"Avaliações de teste apagadas: {cursor.rowcount}")
    if tabela_existe(cursor, "CS_LOG_ACOES"):
        cursor.execute("DELETE FROM CS_LOG_ACOES WHERE USUARIO IN (:a, :b)", {"a": USUARIO_CARGA, "b": "Exemplo (script)"})
        log(f"Registros de ações de teste apagados: {cursor.rowcount}")
    from tratamento.comprovacao_exemplo import remover as remover_exemplo_comprovacao
    remover_exemplo_comprovacao(connection)
    if commit:
        connection.commit()


def escolher_clientes(cursor):
    """[(cliente, perfil)]: os de CLIENTES_TESTE ou, sem eles, os primeiros com fazenda, em rodízio de perfis."""
    if CLIENTES_TESTE:
        binds = {f"c{n}": v for n, v in enumerate(CLIENTES_TESTE)}
        clientes = consultar(cursor, f"SELECT ID, RAZAO_SOCIAL FROM CS_CLIENTES WHERE ID IN "
                                     f"({', '.join(':' + b for b in binds)}) ORDER BY ID", binds)
        faltando = set(CLIENTES_TESTE) - {c["ID"] for c in clientes}
        if faltando:
            log(f"[AVISO] Clientes de CLIENTES_TESTE que não estão no banco: {sorted(faltando)}")
        return [(c, CLIENTES_TESTE[c["ID"]]) for c in clientes]
    clientes = consultar(cursor, """
        SELECT c.ID, c.RAZAO_SOCIAL FROM CS_CLIENTES c
        WHERE EXISTS (SELECT 1 FROM CS_FAZENDAS f WHERE f.ID_CLIENTE = c.ID) AND c.CNPJ <> '00000000000000'
        ORDER BY c.ID
    """)[:QTD_CLIENTES_AUTOMATICO]
    return [(c, ORDEM_PERFIS[n % len(ORDEM_PERFIS)]) for n, c in enumerate(clientes)]


def carregar(connection, simular, continuar=False):
    rnd = random.Random(SEMENTE)
    cursor = connection.cursor()
    hoje = datetime.date.today()

    if continuar:
        log("Continuando uma carga interrompida: nada é apagado; clientes que já têm máquinas de teste são pulados.")
    else:
        limpar(connection, commit=False)
    escolhidos = escolher_clientes(cursor)
    # Só modelos com manual vinculado E recomendações de manutenção gravadas
    modelos = [m for m in consultar(cursor, """
        SELECT mo.ID, mo.FABRICANTE, mo.MODELO, mo.TIPO, mo.VALOR_ESTIMADO FROM CS_EQUIPAMENTOS_MODELOS mo
        WHERE EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_MANUAIS ma WHERE ma.ID_EQUIPAMENTO = mo.ID)
        ORDER BY mo.ID
    """) if orientacoes_do_modelo(cursor, m["ID"])]
    for m in modelos:
        m["VALOR"] = float(m["VALOR_ESTIMADO"] or VALOR_PADRAO)
    colhedoras = [m for m in modelos if m["VALOR"] >= 1_000_000]
    tratores = [m for m in modelos if m["VALOR"] < 1_000_000]
    log(f"{len(escolhidos)} cliente(s) de teste; modelos com manual e recomendações: "
        f"{', '.join(m['FABRICANTE'] + ' ' + m['MODELO'] for m in modelos) or 'nenhum'}.")
    if not tratores:
        log("[ERRO] Nenhum trator com manual e recomendações gravadas. Grave um manual na página Programação.")
        return
    colunas_equip = colunas_da_tabela(cursor, "CS_EQUIPAMENTOS_SEGURADOS")
    colunas_manut = colunas_da_tabela(cursor, "CS_MANUTENCOES_REALIZADAS")
    novas = all(c in colunas_manut for c in ("REGRA", "ID_COMPROVANTE", "AVALIADO_POR"))
    if not novas:
        log("[AVISO] Rode sql/estrutura_banco.sql: sem as colunas novas, a carga segue sem revisões a mais e sem "
            "ligar o comprovante à revisão.")
    tem_tracao = "TIPO_TRACAO" in colunas_equip
    tem_leituras = tabela_existe(cursor, "CS_LEITURAS_MEDIDOR")
    tem_log = tabela_existe(cursor, "CS_LOG_ACOES")

    telemetria_ok = valores_permitidos(cursor, "CS_EQUIPAMENTOS_SEGURADOS", "TELEMETRIA", ["SIM", "NAO"])
    status_ok = valores_permitidos(cursor, "CS_EQUIPAMENTOS_SEGURADOS", "STATUS", ["ATIVO", "INATIVO"])
    realizacao_ok = valores_permitidos(cursor, "CS_MANUTENCOES_REALIZADAS", "REALIZACAO", REALIZACAO_PADRAO)
    estado_ok = valores_permitidos(cursor, "CS_SINISTROS", "ESTADO_SINISTRO", ["ABERTO", "EM_ANALISE", "ENCERRADO"])
    SIM, NAO = escolher(telemetria_ok, "SIM", "S"), escolher(telemetria_ok, "NAO", "N", padrao=telemetria_ok[-1])
    ATIVO = escolher(status_ok, "ATIV")
    REALIZADA = valor_realizacao(realizacao_ok, "realizada")
    ABERTO = escolher(estado_ok, "ANALISE", "ABERT")
    ENCERRADO = escolher(estado_ok, "INDENIZ", "ENCERR", "PAGO", padrao=estado_ok[-1])

    colunas_uso = ["DATA_AQUISICAO", "HORIMETRO_ATUAL", "DATA_LEITURA", "USO_MEDIO_HORAS_MES", "TIPO_OPERACAO"] + \
        (["TIPO_TRACAO"] if tem_tracao else [])
    g_equip = Gravador(cursor, "CS_EQUIPAMENTOS_SEGURADOS", [
        "ID_FAZENDA", "ID_EQUIPAMENTO_MODELO", "NUMERO_SERIE", "IDENTIFICACAO_INTERNA", "VALOR_SEGURADO",
        "TELEMETRIA", "NUMERO_APOLICE", "DATA_INICIO_VIGENCIA", "DATA_FIM_VIGENCIA", "STATUS"] + colunas_uso)
    g_sinistro = Gravador(cursor, "CS_SINISTROS", [
        "ID_EQUIPAMENTO_SEGURADO", "NUMERO_APOLICE", "DATA_OCORRENCIA", "TIPO_SINISTRO", "VALOR_INDENIZACAO",
        "NUMERO_AVISO_SINISTRO", "ESTADO_SINISTRO", "DATA_ENCERRAMENTO"])
    g_aval = Gravador(cursor, "CS_SCORE_MANUTENCAO_PROCEDIMENTOS",
                      ["ID_FAZENDA", "DATA_AVALIACAO"] + COLUNAS_PERGUNTAS + COLUNAS_SUBTOTAL + ["SCORE_FINAL"])
    g_leitura = Gravador(cursor, "CS_LEITURAS_MEDIDOR", [
        "ID_EQUIPAMENTO_SEGURADO", "DATA_LEITURA", "HORIMETRO", "ORIGEM", "USUARIO"]) if tem_leituras else None
    # cursor próprio: setinputsizes vale para o próximo comando do cursor, e o BLOB só existe no INSERT do anexo
    cursor_anexo = connection.cursor()
    g_anexo = Gravador(cursor_anexo, "CS_ANEXOS", [
        "TIPO_ENTIDADE", "ID_ENTIDADE", "TIPO_ANEXO", "NOME_ARQUIVO", "TIPO_CONTEUDO", "TAMANHO_BYTES", "HASH_SHA256",
        "ARQUIVO", "USUARIO_ENVIO"])
    g_extra = Gravador(cursor, "CS_MANUTENCOES_REALIZADAS", [
        "ID_EQUIPAMENTO_SEGURADO", "ID_ORIENTACAO", "DATA_PREVISTA", "DATA_REALIZADA", "REALIZACAO",
        "COMPROVANTE_ENTREGUE", "ATENDE_CRITERIOS", "REGRA"]) if novas else None
    g_log = Gravador(cursor, "CS_LOG_ACOES", ["DATA_HORA", "USUARIO", "PERFIL", "PAGINA", "ACAO", "ENTIDADE",
                                              "CHAVE", "DETALHE"]) if tem_log else None
    atualizacoes = []

    # Na simulação tudo é desfeito no fim: não vale mandar ~100 MB de PDFs ao banco só para apagar
    fatura, invalido = (None, None) if simular else (pdf_fatura(), pdf_invalido())
    if simular:
        log("Simulação: os comprovantes usam um PDF pequeno no lugar da fatura de exemplo (mais rápido).")

    def anexo(entidade, id_entidade, nome, texto, conteudo=None):
        arquivo = conteudo or pdf_comprovante(texto)
        cursor_anexo.setinputsizes(ARQUIVO=oracledb.DB_TYPE_BLOB)
        return g_anexo.inserir_com_retorno({
            "TIPO_ENTIDADE": entidade, "ID_ENTIDADE": id_entidade, "TIPO_ANEXO": "COMPROVANTE" if entidade == "MANUTENCAO"
            else "HORIMETRO", "NOME_ARQUIVO": nome, "TIPO_CONTEUDO": "application/pdf", "TAMANHO_BYTES": len(arquivo),
            "HASH_SHA256": hashlib.sha256(arquivo).hexdigest(), "ARQUIVO": arquivo, "USUARIO_ENVIO": USUARIO_CARGA})

    def registrar_log(quando, acao, chave, detalhe, perfil_usuario):
        if g_log is not None:
            g_log.adicionar({"DATA_HORA": quando, "USUARIO": USUARIO_CARGA, "PERFIL": perfil_usuario,
                             "PAGINA": "Comprovação", "ACAO": acao, "ENTIDADE": "REVISAO", "CHAVE": chave,
                             "DETALHE": detalhe})

    def descarregar():
        """Grava o que está pendente (validações das manutenções e lotes). Na carga real, confirma (commit) a cada
        cliente: uma transação só com ~100 MB de PDFs derruba a conexão com o banco."""
        extras_sql = (", ID_COMPROVANTE = :anexo, AVALIADO_POR = :quem, DATA_AVALIACAO = :quando, "
                      "MOTIVO_AVALIACAO = :motivo") if novas else ""
        for inicio_lote in range(0, len(atualizacoes), TAMANHO_LOTE):
            lote = atualizacoes[inicio_lote:inicio_lote + TAMANHO_LOTE]
            if not novas:
                lote = [{k: v for k, v in a.items() if k in ("id", "realizacao", "data_realizada", "atende")}
                        for a in lote]
            cursor.executemany(f"""
                UPDATE CS_MANUTENCOES_REALIZADAS SET REALIZACAO = :realizacao, DATA_REALIZADA = :data_realizada,
                       COMPROVANTE_ENTREGUE = 'SIM', ATENDE_CRITERIOS = :atende{extras_sql}
                WHERE ID = :id
            """, lote)
        total_atualizacoes[0] += len(atualizacoes)
        atualizacoes.clear()
        for gravador in (g_sinistro, g_aval, g_anexo, g_log):
            if gravador is not None:
                gravador.descarregar()
        if not simular:
            connection.commit()

    total_atualizacoes = [0]
    resumo = []
    for posicao, (cliente, perfil_nome) in enumerate(escolhidos, start=1):
        log(f"Cliente {posicao} de {len(escolhidos)}: {cliente['RAZAO_SOCIAL']} (perfil {perfil_nome})...")
        perfil = PERFIS[perfil_nome]
        r = {"cliente": cliente["RAZAO_SOCIAL"], "id": cliente["ID"], "perfil": perfil_nome, "valor": 0.0,
             "fazendas": 0, "equip": 0, "revisoes": 0, "comprovadas": 0, "extras": 0, "sinistros": 0,
             "indenizado": 0.0, "notas": []}
        fazendas = consultar(cursor, """
            SELECT f.ID, f.NOME_FAZENDA FROM CS_FAZENDAS f WHERE f.ID_CLIENTE = :c
              AND NOT EXISTS (SELECT 1 FROM CS_EQUIPAMENTOS_SEGURADOS e WHERE e.ID_FAZENDA = f.ID)
            ORDER BY f.ID
        """, {"c": cliente["ID"]})
        if not fazendas:
            log(f"   {cliente['RAZAO_SOCIAL']}: já carregado (todas as fazendas têm máquinas); pulado." if continuar
                else f"[AVISO] {cliente['RAZAO_SOCIAL']}: nenhuma fazenda livre (todas já têm equipamento); pulado.")
            continue
        r["fazendas"] = len(fazendas)

        frota = montar_frota(rnd, fazendas, tratores, colhedoras, rnd.uniform(*perfil["exposicao"]))

        equipamentos_cliente = []
        for fazenda in fazendas:
            for n, modelo in enumerate(frota[fazenda["ID"]]):
                uso = USO_POR_MODELO.get(modelo["MODELO"], USO_PADRAO)
                valor = round(modelo["VALOR"] * rnd.uniform(0.8, 1.0), -3)
                inicio = hoje - datetime.timedelta(days=rnd.randint(420, 4 * 365))
                fim = inicio + datetime.timedelta(days=365)
                while fim < hoje:                                  # apólice renovada todo ano
                    fim += datetime.timedelta(days=365)
                aquisicao = inicio - datetime.timedelta(days=rnd.randint(0, 2 * 365))
                leitura = hoje - datetime.timedelta(days=rnd.randint(0, 25))
                horas_mes = round(rnd.uniform(*uso["horas_mes"]) * perfil["uso"], 0)
                horimetro = round(horas_mes * (leitura - aquisicao).days / 30.4375 * rnd.uniform(0.9, 1.1), 0)
                apolice = f"{PREFIXO_APOLICE}{cliente['ID']:04d}-{fazenda['ID']:05d}-{n + 1}"
                dados = {"ID_FAZENDA": fazenda["ID"], "ID_EQUIPAMENTO_MODELO": modelo["ID"],
                         "NUMERO_SERIE": f"{normalizar(modelo['MODELO'])[:6]}{rnd.randint(100000, 999999)}",
                         "IDENTIFICACAO_INTERNA": f"{normalizar(modelo['TIPO'])[:3]}-{fazenda['ID']}-{n + 1:02d}",
                         "VALOR_SEGURADO": valor, "TELEMETRIA": SIM if rnd.random() < perfil["telemetria"] else NAO,
                         "NUMERO_APOLICE": apolice, "DATA_INICIO_VIGENCIA": inicio, "DATA_FIM_VIGENCIA": fim,
                         "STATUS": ATIVO, "DATA_AQUISICAO": aquisicao, "HORIMETRO_ATUAL": horimetro,
                         "DATA_LEITURA": leitura, "USO_MEDIO_HORAS_MES": horas_mes,
                         "TIPO_OPERACAO": rnd.choice(uso["operacoes"])}
                if tem_tracao:
                    dados["TIPO_TRACAO"] = TRACAO_POR_MODELO.get(modelo["MODELO"],
                                                                 lambda x: "4WD" if x.random() < 0.7 else "2WD")(rnd)
                id_equip = g_equip.inserir_com_retorno(dados)
                maquina = dict(dados, ID=id_equip)
                equipamentos_cliente.append((maquina, modelo))
                r["valor"] += valor
                r["equip"] += 1

                # Leituras mensais do horímetro (últimos meses), com foto simulada
                if g_leitura is not None:
                    for meses_atras in range(MESES_LEITURAS, 0, -1):
                        if rnd.random() > perfil["envia"]:
                            continue
                        dia = leitura - datetime.timedelta(days=int(meses_atras * 30.4375))
                        if dia <= aquisicao:
                            continue
                        horas = round(max(0.0, horimetro - horas_mes * meses_atras * rnd.uniform(0.95, 1.05)), 1)
                        id_leitura = g_leitura.inserir_com_retorno({
                            "ID_EQUIPAMENTO_SEGURADO": id_equip, "DATA_LEITURA": dia, "HORIMETRO": horas,
                            "ORIGEM": "LEITURA_MENSAL", "USUARIO": USUARIO_CARGA})
                        anexo("LEITURA", id_leitura, f"horimetro_{id_leitura}.pdf",
                              f"Foto simulada do horimetro - {horas:.1f} h - {dia:%d/%m/%Y}")

                # Cronograma do sistema e comprovação por revisão (mesma data e periodicidade)
                programar_equipamento(connection, id_equip, hoje)
                atividades = consultar(cursor, """
                    SELECT m.ID, m.ID_ORIENTACAO, m.DATA_PREVISTA, o.METRICA_GATILHO, o.VALOR_GATILHO,
                           o.FATOR_CONDICIONAL
                    FROM CS_MANUTENCOES_REALIZADAS m JOIN CS_EQUIPAMENTOS_ORIENTACOES o ON o.ID = m.ID_ORIENTACAO
                    WHERE m.ID_EQUIPAMENTO_SEGURADO = :e
                """, {"e": id_equip})
                revisoes = {}
                for a in atividades:
                    a["DATA_PREVISTA"] = para_data(a["DATA_PREVISTA"])
                    _, dias = periodicidade(dict(maquina, **a), dict(maquina, **a))
                    revisoes.setdefault((a["DATA_PREVISTA"], round(dias or 0)), []).append(a)
                feitas_por_orientacao = {}
                for (data, dias), itens in sorted(revisoes.items()):
                    if data > hoje:
                        continue
                    r["revisoes"] += 1
                    realizada = data + datetime.timedelta(days=rnd.randint(*sortear(rnd, perfil["atraso"])))
                    envio = realizada + datetime.timedelta(days=rnd.randint(0, 5))
                    if rnd.random() > perfil["envia"] or envio > hoje:
                        continue                  # sem comprovante (ou ainda não feita): fica pendente
                    if (envio - data).days > PRAZO_COMPROVANTE_DIAS:
                        continue                  # passaria do prazo: conta como comprovante não enviado
                    r["comprovadas"] += 1
                    nota = None if (hoje - envio).days < 20 else sortear(rnd, perfil["validacao"])
                    chave = chave_revisao(id_equip, data, dias)
                    id_anexo = anexo("MANUTENCAO", itens[0]["ID"], f"comprovante_{itens[0]['ID']}.pdf",
                                     f"Comprovante simulado - revisao de {data:%d/%m/%Y} - feita em {realizada:%d/%m/%Y}"
                                     f" - {modelo['MODELO']} - {len(itens)} servicos",
                                     invalido if nota == 0 else fatura)
                    for a in itens:
                        atualizacoes.append({"id": a["ID"], "realizacao": REALIZADA, "data_realizada": realizada,
                                             "atende": nota, "anexo": id_anexo,
                                             "quem": None if nota is None else "Sompo (carga de teste)",
                                             "quando": None if nota is None else envio + datetime.timedelta(days=12),
                                             "motivo": None if nota is None or nota > 25 else
                                             "Comprovante sem identificação do equipamento ou dos serviços."})
                        feitas_por_orientacao.setdefault(a["ID_ORIENTACAO"], []).append(realizada)
                    registrar_log(datetime.datetime.combine(envio, datetime.time(9, 30)), "ENVIO_COMPROVANTE", chave,
                                  f"Arquivo comprovante_{itens[0]['ID']}.pdf; feita em {realizada:%d/%m/%Y}; "
                                  f"{len(itens)} serviço(s)", "produtor")
                    if nota is not None:
                        rotulo = {100: "Atende", 75: "Atende na maior parte", 25: "Atende na menor parte",
                                  0: "Não atende"}[nota]
                        registrar_log(datetime.datetime.combine(envio + datetime.timedelta(days=12),
                                                                datetime.time(15, 0)),
                                      "VALIDACAO", chave, f"{rotulo} ({nota}); {len(itens)} serviço(s)", "sompo")

                # Revisões feitas a mais (regra ideal do manual), no perfil que cuida bem da frota
                if g_extra is not None and perfil["extra"] > 0:
                    minimas = {}
                    for (data, dias), itens in revisoes.items():
                        for a in itens:
                            minimas.setdefault(a["ID_ORIENTACAO"], []).append(data)
                    for o in orientacoes_do_modelo(cursor, modelo["ID"]):
                        if o["ID"] not in minimas or not tem_duas_opcoes(o, maquina):
                            continue
                        feitas = feitas_por_orientacao.get(o["ID"], [])
                        for data in vencimentos(o, maquina, inicio_periodo(maquina, hoje),
                                                hoje - datetime.timedelta(days=20), "IDEAL"):
                            if any(abs((data - f).days) <= 30 for f in feitas) or rnd.random() > perfil["extra"]:
                                continue
                            anteriores = [d for d in minimas[o["ID"]] if d <= data]
                            id_extra = g_extra.inserir_com_retorno({
                                "ID_EQUIPAMENTO_SEGURADO": id_equip, "ID_ORIENTACAO": o["ID"],
                                "DATA_PREVISTA": max(anteriores) if anteriores else data, "DATA_REALIZADA": data,
                                "REALIZACAO": REALIZADA, "COMPROVANTE_ENTREGUE": "SIM", "ATENDE_CRITERIOS": 100,
                                "REGRA": "IDEAL"})
                            id_anexo = anexo("MANUTENCAO", id_extra, f"comprovante_{id_extra}.pdf",
                                             f"Comprovante simulado - revisao a mais - {data:%d/%m/%Y}", fatura)
                            cursor.execute("UPDATE CS_MANUTENCOES_REALIZADAS SET ID_COMPROVANTE = :a WHERE ID = :id",
                                           {"a": id_anexo, "id": id_extra})
                            r["extras"] += 1

        # Sinistros: parte dos clientes (nenhum no perfil baixo). Leves: 1 a 3, somando até 15% do valor segurado
        # (nota 2); graves: 2 a 4 incêndios ou tombamentos nas máquinas de maior valor.
        chance, gravidade = perfil["sinistro"]
        if gravidade and rnd.random() < chance:
            quantidade = rnd.randint(1, 3) if gravidade == "leve" else rnd.randint(2, 4)
            limite = r["valor"] * LIMITE_SINISTRO_LEVE if gravidade == "leve" else float("inf")
            candidatos = list(equipamentos_cliente)
            if gravidade == "leve":
                rnd.shuffle(candidatos)
            else:
                candidatos.sort(key=lambda em: -float(em[0]["VALOR_SEGURADO"]))
            for maquina, modelo in candidatos:
                if r["sinistros"] >= quantidade:
                    break
                tipo = rnd.choice(TIPOS_SINISTRO[gravidade])
                indenizacao = round(float(maquina["VALOR_SEGURADO"]) * rnd.uniform(*FRACAO_INDENIZACAO[tipo]), 2)
                if r["indenizado"] + indenizacao > limite:
                    continue
                inicio = maquina["DATA_INICIO_VIGENCIA"]
                ocorrencia = datetime.datetime.combine(
                    inicio + datetime.timedelta(days=rnd.randint(30, max(31, (hoje - inicio).days - 10))),
                    datetime.time(rnd.randint(6, 18), rnd.choice([0, 15, 30, 45])))
                encerrado = (hoje - ocorrencia.date()).days > 60
                g_sinistro.adicionar({
                    "ID_EQUIPAMENTO_SEGURADO": maquina["ID"], "NUMERO_APOLICE": maquina["NUMERO_APOLICE"],
                    "DATA_OCORRENCIA": ocorrencia, "TIPO_SINISTRO": tipo, "VALOR_INDENIZACAO": indenizacao,
                    "NUMERO_AVISO_SINISTRO": f"AVS-{ocorrencia:%Y%m}-{rnd.randint(1000, 9999)}",
                    "ESTADO_SINISTRO": ENCERRADO if encerrado else ABERTO,
                    "DATA_ENCERRAMENTO": (ocorrencia.date() + datetime.timedelta(days=rnd.randint(20, 60))
                                          if encerrado else None)})
                r["sinistros"] += 1
                r["indenizado"] += indenizacao

        # Avaliação de maturidade (uma por fazenda)
        for fazenda in fazendas:
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
        descarregar()

    descarregar()

    log("=" * 128)
    log(f" {'ID':>5} {'Cliente':<34}{'Perfil':<7}{'Faz.':>5}{'Equip.':>7}{'Valor segurado':>17}{'Revisões':>10}"
        f"{'Comprov.':>10}{'A mais':>8}{'Sinistros':>10}{'Indenizado':>15}{'Maturidade':>12}")
    for r in resumo:
        comprov = f"{r['comprovadas'] / r['revisoes'] * 100:.0f}%" if r["revisoes"] else "-"
        maturidade = f"{sum(r['notas']) / len(r['notas']):.1f}/15" if r["notas"] else "-"
        log(f" {r['id']:>5} {r['cliente'][:33]:<34}{r['perfil']:<7}{r['fazendas']:>5}{r['equip']:>7}"
            f"{moeda(r['valor']):>17}{r['revisoes']:>10}{comprov:>10}{r['extras']:>8}{r['sinistros']:>10}"
            f"{moeda(r['indenizado']):>15}{maturidade:>12}")
    log("=" * 128)
    log(f"Linhas: {g_equip.total} equipamentos, {total_atualizacoes[0]} manutenções comprovadas, {g_anexo.total} anexos, "
        f"{g_sinistro.total} sinistros, {g_aval.total} avaliações.")
    if simular:
        connection.rollback()
        log("Simulação: nada foi gravado (nem apagado). Para gravar, rode sem --simular.")
    else:
        connection.commit()
        log("Dados de teste gravados. Agora calcule o score: python requisitos/score_risco.py --gravar")


def main():
    parser = argparse.ArgumentParser(description="Carga de dados de teste para o Score de Risco.")
    parser.add_argument("--simular", action="store_true", help="mostra o que seria gravado, sem gravar")
    parser.add_argument("--limpar", action="store_true", help="só apaga os exemplos e dados de teste")
    parser.add_argument("--continuar", action="store_true",
                        help="retoma uma carga interrompida: não apaga nada e carrega só os clientes que faltam")
    args = parser.parse_args()
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        if args.limpar:
            limpar(connection)
        else:
            carregar(connection, args.simular, args.continuar)
    except Exception:
        try:
            connection.rollback()
        except oracledb.Error:
            pass                          # conexão já caiu: o erro que importa é o de cima
        raise
    finally:
        connection.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
