# -*- coding: utf-8 -*-
"""
Programação das manutenções: a lista pré-definida de manutenções que o cliente precisa comprovar.

Para cada equipamento segurado, gera em CS_MANUTENCOES_REALIZADAS uma atividade para cada vencimento das
recomendações de manutenção do manual do seu modelo:
  - Manual do modelo: CS_EQUIPAMENTOS_MANUAIS (ID_EQUIPAMENTO = modelo) -> COD_FONTE_MANUAL -> recomendações em
    CS_EQUIPAMENTOS_ORIENTACOES. Só o modelo exato do manual (sem estender a modelos parecidos).
  - Só recomendações do tipo manutenção (requisitos/recomendacoes.py: MANUTENCAO_PROGRAMADA).

Regras de vencimento (METRICA_GATILHO):
  - HORIMETRO: a cada VALOR_GATILHO horas (250, 500, 750 h...). Depois da leitura, a data é projetada pelo uso médio
    de horas por mês; antes dela, as horas são distribuídas por igual desde a aquisição (máquina nova, horímetro em 0).
  - "A cada X horas OU calendário (ex.: OU_CALENDARIO:ANUAL), o que ocorrer primeiro": o texto da recomendação mantém
    as duas opções, mas o cronograma usa UMA regra só, a de intervalo maior para a máquina (pelo uso médio): menos
    manutenções para a equipe fazer e comprovar. regra_cronograma() devolve o texto da regra usada.
  - HODOMETRO / KM: igual, com hodômetro e uso médio de km por mês.
  - CALENDARIO: pelo prazo de FATOR_CONDICIONAL (ex.: OU_CALENDARIO:ANUAL = a cada 12 meses) ou, sem ele, pelo
    VALOR_GATILHO em dias (unidade DIAS), a partir da aquisição.
  - Amaciamento (FATOR_CONDICIONAL com AMACIAMENTO, ex.: primeiras 500 horas): uma única vez, ao atingir o valor.
  - CONDICIONAL: sem prazo definido (depende de uma condição de uso) -> não entra na lista de prestação de contas.
  - Rotinas de intervalo curto (menos de 200 h, 5.000 km ou 30 dias, ex.: lubrificar a cada 10 h) são verificações
    do operador e também não entram na lista (continuam contando na complexidade da manutenção).
  - "Mensalmente ou a cada 200 horas": com gatilho por horas, prazos de calendário curtos (diário, semanal, mensal)
    não contam; o controle é pelas horas reais, informadas na leitura mensal do horímetro (CS_LEITURAS_MEDIDOR).
    Prazos longos (anual, 2 anos) continuam valendo como "o que ocorrer primeiro".
  - Tração: recomendações "somente 4WD" ou "somente 2WD" entram só para a máquina com essa tração (TIPO_TRACAO);
    sem a tração cadastrada, todas entram.
  - Horímetro: as datas usam as leituras reais (entre duas leituras, linha reta) e, depois da última, o uso médio.

Período da lista: do início da vigência do seguro (no máximo 24 meses atrás) até 12 meses à frente.
Tolerância: a manutenção vencida tem 30 dias para ser comprovada antes de contar como atrasada.
Recálculo: ao mudar os dados de uso, as atividades sem comprovante são refeitas; as comprovadas nunca mudam.

Uso (reprograma todos os equipamentos):
    python requisitos/programacao_manutencao.py --simular
    python requisitos/programacao_manutencao.py
    python requisitos/programacao_manutencao.py --apolice EXEMPLO-   (só os equipamentos do exemplo)
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import re
import argparse
import datetime

import oracledb

from requisitos.recomendacoes import eh_manutencao, normalizar

# Recomendações muito frequentes (ex.: lubrificar a cada 10 ou 50 h) são verificações de rotina do operador: não entram
# na prestação de contas (continuam contando na complexidade da manutenção). Entram as revisões periódicas.
INTERVALO_MINIMO_HORAS = 200
INTERVALO_MINIMO_KM = 5000
INTERVALO_MINIMO_DIAS = 30
# "Mensalmente ou a cada 200 horas": quando há gatilho por horas, prazos de calendário curtos (diário, semanal, mensal)
# não contam — o controle é pelas horas reais, com a leitura mensal do horímetro (CS_LEITURAS_MEDIDOR). Prazos
# longos (anual, 2 anos...) continuam valendo como "o que ocorrer primeiro".
CALENDARIO_MINIMO_COM_MEDIDOR_DIAS = 60
MESES_HISTORICO = 24
MESES_FUTURO = 12
TOLERANCIA_DIAS = 30
MAX_OCORRENCIAS = 40               # por recomendação, no período
JANELA_DUPLICIDADE_DIAS = 15       # atividade comprovada "cobre" um vencimento próximo da mesma recomendação
DIAS_POR_MES = 30.4375
DIAS_CALENDARIO = {"DIARI": 1, "SEMANA": 7, "QUINZEN": 15, "MENSAL": 30, "BIMESTR": 61, "TRIMESTR": 91,
                   "SEMESTR": 182, "ANUAL": 365, "BIENAL": 730}
# Valores aceitos em CS_MANUTENCOES_REALIZADAS.REALIZACAO no banco (restrição CK_REALIZACAO):
# SIM (realizada), NAO (não realizada), NAO_SE_APLICA, NAO_DISPONIVEL (ainda sem informação: pendente), CANCELADA
REALIZACAO_PADRAO = ["SIM", "NAO", "NAO_SE_APLICA", "NAO_DISPONIVEL", "CANCELADA"]
# Situação -> valores preferidos, em ordem (o primeiro que o banco aceitar vence)
SINONIMOS_REALIZACAO = {"pendente": ["NAO_DISPONIVEL", "PENDENTE"], "realizada": ["SIM", "REALIZADA"],
                        "nao_realizada": ["NAO", "NAO REALIZADA", "NAO_REALIZADA"]}


def para_data(valor):
    return valor.date() if isinstance(valor, datetime.datetime) else valor


def numero(valor):
    return float(valor) if valor is not None else None


def dias_calendario(fator):
    """'OU_CALENDARIO:ANUAL' -> 365. None se não houver prazo de calendário."""
    texto = normalizar(fator)
    if "CALENDARIO" not in texto:
        return None
    for chave, dias in DIAS_CALENDARIO.items():
        if chave in texto:
            return dias
    achado = re.search(r"(\d+)\s*(DIA|MES|ANO)", texto)
    if achado:
        return int(achado.group(1)) * {"DIA": 1, "MES": 30, "ANO": 365}[achado.group(2)]
    return None


def situacao(data_prevista, tem_comprovante, hoje=None):
    """'comprovada', 'a vencer', 'no prazo' (vencida há até 30 dias) ou 'atrasada'."""
    hoje = hoje or datetime.date.today()
    if tem_comprovante:
        return "comprovada"
    if data_prevista is None or data_prevista > hoje:
        return "a vencer"
    return "no prazo" if (hoje - data_prevista).days <= TOLERANCIA_DIAS else "atrasada"


class Medidor:
    """Projeção de um medidor (horímetro ou hodômetro) a partir das leituras reais.
    Entre duas leituras: distribui por igual (linha reta). Antes da primeira: desde a aquisição (máquina nova, medidor
    em zero); sem data de aquisição, usa o ritmo da primeira leitura conhecida ou o uso médio. Depois da última
    leitura: uso médio informado por mês."""

    def __init__(self, leitura, data_leitura, uso_mes, data_aquisicao=None, leituras=None):
        self.por_dia_futuro = uso_mes / DIAS_POR_MES
        pontos = {}
        for data, valor in (leituras or []):
            if data is not None and valor is not None:
                pontos[data] = max(float(valor), pontos.get(data, 0.0))
        if data_leitura is not None and leitura is not None:
            pontos[data_leitura] = max(float(leitura), pontos.get(data_leitura, 0.0))
        if data_aquisicao is not None and all(d > data_aquisicao for d in pontos):
            pontos[data_aquisicao] = 0.0
        # só leituras crescentes (uma leitura menor que a anterior é descartada)
        self.pontos = []
        for data in sorted(pontos):
            if not self.pontos or pontos[data] >= self.pontos[-1][1]:
                self.pontos.append((data, pontos[data]))
        if len(self.pontos) >= 2 and self.pontos[1][1] > self.pontos[0][1]:
            (d0, v0), (d1, v1) = self.pontos[0], self.pontos[1]
            self.por_dia_passado = (v1 - v0) / max(1, (d1 - d0).days)
        else:
            self.por_dia_passado = self.por_dia_futuro
        self.leitura, self.data_leitura = self.pontos[-1][1], self.pontos[-1][0]

    def valor_em(self, data):
        (d_ultima, v_ultima), (d_primeira, v_primeira) = self.pontos[-1], self.pontos[0]
        if data >= d_ultima:
            return v_ultima + self.por_dia_futuro * (data - d_ultima).days
        if data <= d_primeira:
            return max(0.0, v_primeira - self.por_dia_passado * (d_primeira - data).days)
        for (d0, v0), (d1, v1) in zip(self.pontos, self.pontos[1:]):
            if d0 <= data <= d1:
                return v0 + (v1 - v0) * (data - d0).days / max(1, (d1 - d0).days)
        return v_ultima

    def data_em(self, valor):
        (d_ultima, v_ultima), (d_primeira, v_primeira) = self.pontos[-1], self.pontos[0]
        if valor >= v_ultima:
            return d_ultima + datetime.timedelta(days=round((valor - v_ultima) / self.por_dia_futuro))
        if valor <= v_primeira:
            return d_primeira - datetime.timedelta(days=round((v_primeira - valor) / max(self.por_dia_passado, 1e-9)))
        for (d0, v0), (d1, v1) in zip(self.pontos, self.pontos[1:]):
            if v0 <= valor <= v1 and v1 > v0:
                return d0 + datetime.timedelta(days=round((valor - v0) / (v1 - v0) * (d1 - d0).days))
        return d_ultima


def aplica_ao_equipamento(orientacao, equipamento):
    """False se a recomendação vale só para outro tipo de tração (ex.: nota "Somente 4WD" num trator 2WD).
    Sem a tração cadastrada, a recomendação vale (todas entram na lista)."""
    tracao = normalizar(equipamento.get("TIPO_TRACAO"))
    if not tracao:
        return True
    condicao = normalizar(orientacao.get("FATOR_CONDICIONAL"))
    so_4wd = "4WD" in condicao or "QUATRO RODAS" in condicao
    so_2wd = "2WD" in condicao or "DUAS RODAS" in condicao
    if so_4wd and not so_2wd:
        return tracao == "4WD"
    if so_2wd and not so_4wd:
        return tracao == "2WD"
    return True


REGRA_MINIMA, REGRA_IDEAL = "MINIMA", "IDEAL"


def _regras(orientacao, equipamento, regra=REGRA_MINIMA):
    """Regras de vencimento da recomendação para o equipamento: (metrica, passo, medidor, calendario, origem).
    medidor = projeção do horímetro/hodômetro (None se a regra não é por medidor); calendario = prazo em dias.
    regra MINIMA (cronograma): com duas opções, só a de intervalo maior; IDEAL (manual): as duas, o que ocorrer
    primeiro."""
    metrica = normalizar(orientacao.get("METRICA_GATILHO"))
    passo = numero(orientacao.get("VALOR_GATILHO"))
    calendario = dias_calendario(orientacao.get("FATOR_CONDICIONAL"))
    if calendario is None and metrica.startswith("CALENDARIO") and passo and passo > 0:
        calendario = int(passo)        # prazo informado em dias no próprio gatilho (unidade DIAS)
    aquisicao = para_data(equipamento.get("DATA_AQUISICAO"))
    origem = aquisicao or para_data(equipamento.get("DATA_INICIO_VIGENCIA"))      # None: começa no período

    if metrica.startswith("HORIMETRO") or metrica.startswith("HORA"):
        leitura, uso = numero(equipamento.get("HORIMETRO_ATUAL")), numero(equipamento.get("USO_MEDIO_HORAS_MES"))
    elif metrica.startswith("HODOMETRO") or metrica.startswith("KM") or metrica.startswith("QUILOMET"):
        leitura, uso = numero(equipamento.get("HODOMETRO_ATUAL")), numero(equipamento.get("USO_MEDIO_KM_MES"))
    else:
        leitura = uso = None
    data_leitura = para_data(equipamento.get("DATA_LEITURA")) or datetime.date.today()
    campo_leituras = "LEITURAS_HORIMETRO" if metrica.startswith(("HORIMETRO", "HORA")) else "LEITURAS_HODOMETRO"
    medidor = (Medidor(leitura, data_leitura, uso, aquisicao, equipamento.get(campo_leituras))
               if passo and passo > 0 and leitura is not None and uso and uso > 0 else None)
    if medidor is not None:
        minimo = INTERVALO_MINIMO_HORAS if metrica.startswith(("HORIMETRO", "HORA")) else INTERVALO_MINIMO_KM
        if passo < minimo:
            medidor = None      # rotina do operador (intervalo curto): só vale o prazo de calendário, se houver
    if calendario is not None and calendario < INTERVALO_MINIMO_DIAS:
        calendario = None
    if medidor is not None and calendario is not None and calendario < CALENDARIO_MINIMO_COM_MEDIDOR_DIAS:
        calendario = None       # "mensalmente ou a cada 200 horas": vale o horímetro (leitura mensal)
    amaciamento = "AMACIAMENTO" in normalizar(orientacao.get("FATOR_CONDICIONAL"))
    if medidor is not None and calendario is not None and not amaciamento and regra == REGRA_MINIMA:
        # "a cada X horas ou Y, o que ocorrer primeiro": o texto da recomendação mantém as duas opções, mas o
        # cronograma usa UMA regra só, a de intervalo maior (menos manutenções para a equipe e para comprovar)
        dias_medidor = passo / max(medidor.por_dia_futuro, 1e-9)
        if calendario >= dias_medidor:
            medidor = None
        else:
            calendario = None
    return metrica, passo, medidor, calendario, origem


def _texto_calendario(calendario):
    if calendario % 365 == 0:
        anos = calendario // 365
        return "a cada ano" if anos == 1 else f"a cada {anos} anos"
    if calendario >= 28:
        meses = round(calendario / DIAS_POR_MES)
        return "a cada mês" if meses == 1 else f"a cada {meses} meses"
    return f"a cada {calendario} dias"


def tem_duas_opcoes(orientacao, equipamento):
    """True se a recomendação tem duas opções ("X horas ou calendário") e o cronograma usa só uma delas."""
    _, _, medidor, calendario, _ = _regras(orientacao, equipamento, REGRA_IDEAL)
    return (medidor is not None and calendario is not None
            and "AMACIAMENTO" not in normalizar(orientacao.get("FATOR_CONDICIONAL")))


def regra_cronograma(orientacao, equipamento, regra=REGRA_MINIMA):
    """Texto da regra (ex.: 'a cada 250 horas', 'a cada ano', 'a cada 250 horas ou a cada ano, o que ocorrer
    primeiro', 'uma vez, ao atingir 50 horas'), ou '' se a recomendação não entra no cronograma."""
    metrica, passo, medidor, calendario, _ = _regras(orientacao, equipamento, regra)
    unidade = "horas" if metrica.startswith(("HORIMETRO", "HORA")) else "km"
    valor = f"{passo:g}".replace(".", ",") if passo else ""
    if "AMACIAMENTO" in normalizar(orientacao.get("FATOR_CONDICIONAL")):
        return f"uma vez, ao atingir {valor} {unidade}" if medidor is not None else ""
    if medidor is not None and calendario is not None:
        return f"a cada {valor} {unidade} ou {_texto_calendario(calendario)}, o que ocorrer primeiro"
    if medidor is not None:
        return f"a cada {valor} {unidade}"
    if calendario is not None:
        return _texto_calendario(calendario)
    return ""


def periodicidade(orientacao, equipamento, regra=REGRA_MINIMA):
    """(texto curto da periodicidade, intervalo aproximado em dias) da regra, como na tabela de manutenção do manual:
    ('250 horas', 62), ('Anual', 365), ('Amaciamento (50 horas)', 0). ('', None) se não entra no cronograma."""
    metrica, passo, medidor, calendario, _ = _regras(orientacao, equipamento, regra)
    unidade = "horas" if metrica.startswith(("HORIMETRO", "HORA")) else "km"
    valor = f"{passo:g}".replace(".", ",") if passo else ""
    if "AMACIAMENTO" in normalizar(orientacao.get("FATOR_CONDICIONAL")):
        return (f"Amaciamento ({valor} {unidade})", 0) if medidor is not None else ("", None)
    if medidor is not None:
        return f"{valor} {unidade}", passo / max(medidor.por_dia_futuro, 1e-9)
    if calendario is not None:
        return (NOMES_PERIODO.get(calendario, _texto_calendario(calendario).replace("a cada ", "").capitalize()),
                calendario)
    return "", None


NOMES_PERIODO = {365: "Anual", 730: "2 anos", 182: "Semestral", 91: "Trimestral", 61: "Bimestral", 30: "Mensal",
                 15: "Quinzenal", 7: "Semanal", 1: "Diário"}


def periodicidade_completa(orientacao, equipamento):
    """Periodicidade como no manual, com as duas opções quando houver (ex.: '200 horas / Mensal', '400 horas /
    Semestral'), para quem controla por horas ou por período, mesmo quando o cronograma usa só uma delas. Sem período
    no manual, a periodicidade do cronograma."""
    texto, _ = periodicidade(orientacao, equipamento)
    if not texto or texto.startswith("Amaciamento"):
        return texto
    metrica = normalizar(orientacao.get("METRICA_GATILHO"))
    passo = numero(orientacao.get("VALOR_GATILHO"))
    calendario = dias_calendario(orientacao.get("FATOR_CONDICIONAL"))
    if not (passo and passo > 0 and calendario and metrica.startswith(("HORIMETRO", "HORA", "HODOMETRO", "KM"))):
        return texto
    unidade = "horas" if metrica.startswith(("HORIMETRO", "HORA")) else "km"
    periodo = NOMES_PERIODO.get(calendario, _texto_calendario(calendario).replace("a cada ", "").capitalize())
    return f"{passo:g} {unidade} / {periodo}".replace(".", ",")


def medidor_previsto(orientacao, equipamento, data):
    """Horas (ou km) que a máquina deve ter na data, pelo uso médio (None se a regra não é por medidor)."""
    _, _, medidor, _, _ = _regras(orientacao, equipamento, REGRA_IDEAL)
    return None if medidor is None else medidor.valor_em(data)


def vencimentos(orientacao, equipamento, inicio, fim, regra=REGRA_MINIMA):
    """Datas de vencimento da recomendação para o equipamento, entre inicio e fim (inclusive). regra MINIMA: o
    cronograma (lista de manutenções a comprovar); IDEAL: a regra completa do manual (para o bônus do score)."""
    metrica, passo, medidor, calendario, origem = _regras(orientacao, equipamento, regra)
    origem = origem or inicio
    if "AMACIAMENTO" in normalizar(orientacao.get("FATOR_CONDICIONAL")):
        # amaciamento: execução única, quando o medidor atinge o valor (ex.: primeiras 500 horas)
        if medidor is None:
            return []
        data = medidor.data_em(passo)
        return [data] if inicio <= data <= fim else []
    if medidor is None and calendario is None:
        return []      # CONDICIONAL, rotina de intervalo curto, ou medidor sem dados de uso e sem calendário

    datas, ultima = [], origem
    valor_ultima = medidor.valor_em(ultima) if medidor else None
    por_calendario = False        # a última manutenção foi disparada pelo calendário?
    for _ in range(500):
        candidatas = []
        if medidor:
            # intervalo contado da última manutenção; sem calendário, nos múltiplos do intervalo (250, 500, 750 h)
            alvo = (valor_ultima + passo) if por_calendario else (int(valor_ultima // passo) + 1) * passo
            candidatas.append((medidor.data_em(alvo), False, alvo))
        if calendario:
            candidatas.append((ultima + datetime.timedelta(days=calendario), True, None))
        proxima, por_calendario, alvo = min(candidatas, key=lambda c: c[0])
        proxima = max(proxima, ultima + datetime.timedelta(days=1))
        if proxima > fim:
            break
        if proxima >= inicio:
            datas.append(proxima)
            if len(datas) >= MAX_OCORRENCIAS:
                break
        ultima = proxima
        if medidor:
            valor_ultima = medidor.valor_em(ultima) if por_calendario else alvo
    return datas


def valor_realizacao(valores_aceitos, situacao):
    """'pendente', 'realizada' ou 'nao_realizada' -> valor aceito pelo banco em REALIZACAO."""
    aceitos = {v.upper(): v for v in valores_aceitos}
    for candidato in SINONIMOS_REALIZACAO[situacao]:
        if candidato in aceitos:
            return aceitos[candidato]
    return SINONIMOS_REALIZACAO[situacao][0]


def valores_realizacao(cursor):
    try:
        cursor.execute("SELECT SEARCH_CONDITION FROM USER_CONSTRAINTS "
                       "WHERE TABLE_NAME = 'CS_MANUTENCOES_REALIZADAS' AND CONSTRAINT_TYPE = 'C'")
        for (condicao,) in cursor.fetchall():
            achado = re.search(r"\bREALIZACAO\b\s+IN\s*\(([^)]*)\)", str(condicao or ""), re.I)
            if achado:
                return re.findall(r"'([^']*)'", achado.group(1))
    except oracledb.Error:
        pass
    return REALIZACAO_PADRAO


def _consultar(cursor, sql, parametros=None):
    cursor.execute(sql, parametros or {})
    colunas = [c[0] for c in cursor.description]
    return [{c: (v.read() if hasattr(v, "read") else v) for c, v in zip(colunas, linha)} for linha in cursor.fetchall()]


def orientacoes_do_modelo(cursor, id_modelo):
    """Recomendações de manutenção do manual do modelo (ligação exata pelo COD_FONTE_MANUAL)."""
    linhas = _consultar(cursor, """
        SELECT o.ID, o.TIPO_ORIENTACAO, o.METRICA_GATILHO, o.VALOR_GATILHO, o.UNIDADE_MEDIDA, o.FATOR_CONDICIONAL
        FROM CS_EQUIPAMENTOS_ORIENTACOES o
        WHERE o.COD_FONTE_MANUAL IN (SELECT m.COD_FONTE_MANUAL FROM CS_EQUIPAMENTOS_MANUAIS m
                                     WHERE m.ID_EQUIPAMENTO = :modelo)
    """, {"modelo": int(id_modelo)})
    return [o for o in linhas if eh_manutencao(o["TIPO_ORIENTACAO"])]


def _colunas_equipamento(cursor):
    cursor.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CS_EQUIPAMENTOS_SEGURADOS'")
    return {c for (c,) in cursor.fetchall()}


def _inserir_atividades(cursor, novas):
    """Insere as atividades; se o banco não gera o ID sozinho (ORA-01400), numera a partir de MAX(ID) + 1."""
    try:
        cursor.executemany("""
            INSERT INTO CS_MANUTENCOES_REALIZADAS
                (ID_EQUIPAMENTO_SEGURADO, ID_ORIENTACAO, DATA_PREVISTA, REALIZACAO, COMPROVANTE_ENTREGUE)
            VALUES (:equip, :orient, :prevista, :realizacao, 'NAO')
        """, novas)
        return
    except oracledb.DatabaseError as e:
        if getattr(e.args[0], "code", None) != 1400:
            raise
    cursor.execute("LOCK TABLE CS_MANUTENCOES_REALIZADAS IN EXCLUSIVE MODE")
    cursor.execute("SELECT NVL(MAX(ID), 0) FROM CS_MANUTENCOES_REALIZADAS")
    proximo = int(cursor.fetchone()[0])
    cursor.executemany("""
        INSERT INTO CS_MANUTENCOES_REALIZADAS
            (ID, ID_EQUIPAMENTO_SEGURADO, ID_ORIENTACAO, DATA_PREVISTA, REALIZACAO, COMPROVANTE_ENTREGUE)
        VALUES (:id, :equip, :orient, :prevista, :realizacao, 'NAO')
    """, [dict(n, id=proximo + i + 1) for i, n in enumerate(novas)])


def _leituras(cursor, id_equipamento):
    """Leituras mensais do horímetro/hodômetro (CS_LEITURAS_MEDIDOR), se a tabela existir."""
    try:
        linhas = _consultar(cursor, """
            SELECT DATA_LEITURA, HORIMETRO, HODOMETRO FROM CS_LEITURAS_MEDIDOR
            WHERE ID_EQUIPAMENTO_SEGURADO = :id ORDER BY DATA_LEITURA
        """, {"id": int(id_equipamento)})
    except oracledb.Error:
        return {}
    return {"LEITURAS_HORIMETRO": [(para_data(l["DATA_LEITURA"]), numero(l["HORIMETRO"])) for l in linhas
                                   if l["HORIMETRO"] is not None],
            "LEITURAS_HODOMETRO": [(para_data(l["DATA_LEITURA"]), numero(l["HODOMETRO"])) for l in linhas
                                   if l["HODOMETRO"] is not None]}


def inicio_periodo(equipamento, hoje=None):
    """Começo do período da lista: início da vigência do seguro, no máximo MESES_HISTORICO meses atrás."""
    hoje = hoje or datetime.date.today()
    limite = hoje - datetime.timedelta(days=30 * MESES_HISTORICO)
    return max(para_data(equipamento.get("DATA_INICIO_VIGENCIA")) or limite, limite)


def tem_coluna_regra(cursor):
    """A coluna REGRA de CS_MANUTENCOES_REALIZADAS existe? (sql/estrutura_banco.sql a cria)"""
    cursor.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CS_MANUTENCOES_REALIZADAS' "
                   "AND COLUMN_NAME = 'REGRA'")
    return bool(cursor.fetchall())


def programar_equipamento(conn, id_equipamento, hoje=None):
    """Refaz a lista de manutenções do equipamento (mantém as comprovadas). Não faz commit.
    Devolve {'geradas', 'mantidas', 'removidas', 'sem_manual'}."""
    hoje = hoje or datetime.date.today()
    with conn.cursor() as cursor:
        colunas = _colunas_equipamento(cursor)
        extras = [c for c in ("DATA_AQUISICAO", "HORIMETRO_ATUAL", "HODOMETRO_ATUAL", "DATA_LEITURA",
                              "USO_MEDIO_HORAS_MES", "USO_MEDIO_KM_MES", "TIPO_TRACAO") if c in colunas]
        equipamentos = _consultar(cursor, f"""
            SELECT ID, ID_EQUIPAMENTO_MODELO, DATA_INICIO_VIGENCIA{''.join(', ' + c for c in extras)}
            FROM CS_EQUIPAMENTOS_SEGURADOS WHERE ID = :id
        """, {"id": int(id_equipamento)})
        if not equipamentos:
            return {"geradas": 0, "mantidas": 0, "removidas": 0, "sem_manual": True}
        equipamento = equipamentos[0]
        equipamento.update(_leituras(cursor, id_equipamento))
        orientacoes = [o for o in orientacoes_do_modelo(cursor, equipamento["ID_EQUIPAMENTO_MODELO"])
                       if aplica_ao_equipamento(o, equipamento)]

        inicio = inicio_periodo(equipamento, hoje)
        fim = hoje + datetime.timedelta(days=30 * MESES_FUTURO)

        regra = ", REGRA" if tem_coluna_regra(cursor) else ""
        existentes = _consultar(cursor, f"""
            SELECT ID, ID_ORIENTACAO, DATA_PREVISTA, REALIZACAO, COMPROVANTE_ENTREGUE{regra}
            FROM CS_MANUTENCOES_REALIZADAS WHERE ID_EQUIPAMENTO_SEGURADO = :id
        """, {"id": int(id_equipamento)})
        mantidas = [e for e in existentes if str(e["COMPROVANTE_ENTREGUE"] or "").upper().startswith("S")]
        # comprovantes a mais (regra ideal) ficam, mas não ocupam o lugar das manutenções do cronograma
        mantidas_cronograma = [e for e in mantidas if e.get("REGRA", REGRA_MINIMA) != REGRA_IDEAL]
        removidas = [e["ID"] for e in existentes if e not in mantidas]
        for inicio_lote in range(0, len(removidas), 500):
            lote = removidas[inicio_lote:inicio_lote + 500]
            binds = {f"r{n}": v for n, v in enumerate(lote)}
            cursor.execute(f"DELETE FROM CS_MANUTENCOES_REALIZADAS WHERE ID IN ({', '.join(':' + b for b in binds)})",
                           binds)

        pendente = valor_realizacao(valores_realizacao(cursor), "pendente")
        novas = []
        for orientacao in orientacoes:
            cobertas = [para_data(e["DATA_PREVISTA"]) for e in mantidas_cronograma
                        if e["ID_ORIENTACAO"] == orientacao["ID"]]
            for data in vencimentos(orientacao, equipamento, inicio, fim):
                if any(abs((data - c).days) <= JANELA_DUPLICIDADE_DIAS for c in cobertas):
                    continue
                novas.append({"equip": int(id_equipamento), "orient": orientacao["ID"], "prevista": data,
                              "realizacao": pendente})
        if novas:
            _inserir_atividades(cursor, novas)
    return {"geradas": len(novas), "mantidas": len(mantidas), "removidas": len(removidas),
            "sem_manual": not orientacoes}


def main():
    parser = argparse.ArgumentParser(description="Reprograma as manutenções de todos os equipamentos segurados.")
    parser.add_argument("--simular", action="store_true", help="calcula sem gravar")
    parser.add_argument("--apolice", help="só os equipamentos cuja apólice começa com este texto (ex.: EXEMPLO-)")
    args = parser.parse_args()
    from auth import USER, PASSWORD, DSN
    conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    try:
        with conn.cursor() as cursor:
            if args.apolice:
                cursor.execute("SELECT ID FROM CS_EQUIPAMENTOS_SEGURADOS WHERE NUMERO_APOLICE LIKE :p ORDER BY ID",
                               {"p": args.apolice + "%"})
            else:
                cursor.execute("SELECT ID FROM CS_EQUIPAMENTOS_SEGURADOS ORDER BY ID")
            ids = [i for (i,) in cursor.fetchall()]
        total = {"geradas": 0, "mantidas": 0, "removidas": 0, "sem_manual": 0}
        for id_equip in ids:
            r = programar_equipamento(conn, id_equip)
            for chave in ("geradas", "mantidas", "removidas"):
                total[chave] += r[chave]
            total["sem_manual"] += int(r["sem_manual"])
        print(f"{len(ids)} equipamento(s): {total['geradas']} manutenção(ões) programada(s), {total['mantidas']} "
              f"comprovada(s) mantida(s), {total['removidas']} pendente(s) refeita(s); "
              f"{total['sem_manual']} equipamento(s) sem manual com recomendações.")
        if args.simular:
            conn.rollback()
            print("Simulação: nada foi gravado.")
        else:
            conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
