# -*- coding: utf-8 -*-
"""
Comprovação das manutenções (página "Comprovação", em Manutenções): revisões, níveis, validação da Sompo e o item
"Histórico de cumprimento de orientações" do Score de Risco.

Recebe as atividades de CS_MANUTENCOES_REALIZADAS já preparadas pela página (DATA_PREVISTA e DATA_REALIZADA como
date, "tem_comprovante", ATENDE_CRITERIOS, REGRA, ID_ORIENTACAO e os dados da recomendação e da máquina).

REVISÃO: como na tabela de manutenção do manual, os serviços que vencem na mesma data na mesma máquina (ex.: na
revisão de 500 horas entram os serviços "a cada 250 horas" e "a cada 500 horas") formam uma revisão, com um único
comprovante (nota fiscal, ordem de serviço ou fotos) para todos os serviços feitos.

Duas regras por recomendação com duas opções ("a cada 250 horas ou anualmente, o que ocorrer primeiro"):
  - MÍNIMA: o cronograma (revisões), só a opção de intervalo maior para a máquina — menos pendências;
  - IDEAL: a regra completa do manual. Manutenções feitas fora do cronograma (REGRA = IDEAL) contam para ela.

Validação da Sompo, por revisão: Atende (ATENDE_CRITERIOS = 100), Atende na maior parte (75), Atende na menor
parte (25) ou Não atende (0, com motivo). "Não atende" não conta como realizado e a revisão volta a pedir comprovante.

Métricas (o atraso não conta: fazer depois da data prevista vale como realizada):
  - realização = manutenções do cronograma com comprovante válido / manutenções vencidas (as que ainda estão no prazo
    de PRAZO_COMPROVANTE_DIAS dias para o comprovante só entram quando o comprovante chega ou o prazo termina);
  - atendimento = média das notas da validação (100, 75, 25 ou 0) dos serviços já validados pela Sompo;
  - regra ideal = comprovantes válidos no período / manutenções que a regra do manual pediria no período (só
    recomendações com duas opções); dá bônus no Score de Risco (bonus_regra_ideal).
"""
import datetime

from requisitos.auditoria import chave_revisao

from requisitos.programacao_manutencao import (vencimentos, tem_duas_opcoes, inicio_periodo, periodicidade,
                                               periodicidade_completa, medidor_previsto, REGRA_MINIMA, REGRA_IDEAL)

DIAS_PROXIMAS = 30          # revisão "próxima": vence nos próximos dias
PRAZO_COMPROVANTE_DIAS = 90  # depois da data prevista, prazo para enviar o comprovante; depois, "não enviado"
FAIXA_BOA, FAIXA_MEDIA = 80.0, 50.0                  # faixas do Score de Risco (%): nota 1, 2 ou 3
BONUS_IDEAL = {FAIXA_BOA: 5.0, FAIXA_MEDIA: 2.5}     # pontos descontados do score (0 a 100) pela regra ideal
NIVEIS = [(90.0, "Ouro"), (FAIXA_BOA, "Prata"), (FAIXA_MEDIA, "Bronze"), (0.0, "Em risco")]
# Validação da Sompo, por revisão (vale para todos os serviços do comprovante): nota em ATENDE_CRITERIOS
NOTAS_VALIDACAO = {"Atende": 100, "Atende na maior parte": 75, "Atende na menor parte": 25, "Não atende": 0}


def rotulo_validacao(valor):
    """100 -> 'Atende'; None -> None (aguardando). Valores antigos fora da escala vão para o mais próximo."""
    if valor is None:
        return None
    return min(NOTAS_VALIDACAO, key=lambda r: abs(NOTAS_VALIDACAO[r] - float(valor)))
CONCLUIDA, AGUARDANDO, PENDENTE, PROXIMA, FUTURA, NAO_ENVIADO = ("concluída", "aguardando validação", "pendente",
                                                                  "próxima", "futura", "comprovante não enviado")


def _dias(n):
    return f"{n} dia" if abs(n) == 1 else f"{n} dias"


def eh_ideal(atividade):
    return str(atividade.get("REGRA") or REGRA_MINIMA).upper() == REGRA_IDEAL


def reprovada(atividade):
    valor = atividade.get("ATENDE_CRITERIOS")
    return atividade["tem_comprovante"] and valor is not None and float(valor) <= 0


def valida(atividade):
    """Tem comprovante e a Sompo não reprovou (aprovado ou aguardando validação)."""
    return atividade["tem_comprovante"] and not reprovada(atividade)


def aguardando_validacao(atividade):
    return atividade["tem_comprovante"] and atividade.get("ATENDE_CRITERIOS") is None


def percentual_realizacao(atividades, hoje=None):
    """(realizadas, previstas, percentual ou None), só manutenções do cronograma. Entram as vencidas que já têm
    comprovante ou cujo prazo de PRAZO_COMPROVANTE_DIAS dias terminou: a que ainda está no prazo não prejudica."""
    hoje = hoje or datetime.date.today()
    limite = hoje - datetime.timedelta(days=PRAZO_COMPROVANTE_DIAS)
    previstas = [a for a in atividades if a["DATA_PREVISTA"] and a["DATA_PREVISTA"] <= hoje and not eh_ideal(a)
                 and (valida(a) or a["DATA_PREVISTA"] < limite)]
    realizadas = sum(1 for a in previstas if valida(a))
    return realizadas, len(previstas), (realizadas / len(previstas) * 100 if previstas else None)


def nivel(percentual):
    """Nível do equipamento (ou do cliente) pela realização: Ouro, Prata, Bronze ou Em risco; None sem dados."""
    if percentual is None:
        return None
    return next(nome for limite, nome in NIVEIS if percentual >= limite)


def _servico(atividade):
    """Texto do serviço, sem a periodicidade que a leitura do manual acrescenta depois do travessão."""
    texto = str(atividade.get("DETALHAMENTO_ORIENTACAO") or "").split(" — ")[0].strip()
    return texto.rstrip(".") or str(atividade.get("ACAO_TECNICA") or "").replace("_", " ").capitalize()


NOMES_SISTEMA = {"MOTOR": "Motor", "ARREFECIMENTO": "Arrefecimento", "COMBUSTIVEL": "Combustível",
                 "TRANSMISSAO": "Transmissão", "REDUCOES_TREM_ACIONAMENTO": "Trem de acionamento",
                 "HIDRAULICO": "Sistema hidráulico", "CORTE_PROCESSAMENTO": "Corte e processamento",
                 "RODANTE_PNEUS": "Rodas e pneus", "FREIOS": "Freios", "ELETRICO": "Sistema elétrico",
                 "CABINE_ESTRUTURA": "Cabine e estrutura", "CHASSI": "Chassi"}


def _sistema(atividade):
    codigo = str(atividade.get("SUBSISTEMA") or "").strip().upper()
    return NOMES_SISTEMA.get(codigo, codigo.replace("_", " ").capitalize())


def _titulo(texto):
    if texto.startswith("Amaciamento"):
        return texto
    if texto[:1].isdigit():
        partes = texto.split(" / ")
        return f"Revisão de {partes[0]}" + (f" / {partes[1].lower()}" if len(partes) > 1 else "")
    if texto in ("Anual", "Semestral", "Trimestral", "Bimestral", "Mensal"):
        return f"Revisão {texto.lower()}"
    return f"Revisão de {texto.lower()}"


def chave_da_atividade(atividade):
    """Chave da revisão a que a manutenção pertence (a mesma de revisoes()), para agrupar e auditar."""
    _, dias = periodicidade(atividade, atividade)
    return chave_revisao(atividade["ID_EQUIPAMENTO_SEGURADO"], atividade["DATA_PREVISTA"], round(dias or 0))


def titulo_da_atividade(atividade):
    return _titulo(periodicidade_completa(atividade, atividade) or "Outras")


def prazo_comprovante(revisao):
    """Último dia para enviar o comprovante da revisão."""
    return revisao["data"] + datetime.timedelta(days=PRAZO_COMPROVANTE_DIAS)


def revisoes(atividades, hoje=None):
    """Revisões do cronograma de UMA máquina, da mais antiga para a mais nova. Uma revisão = os serviços da MESMA
    periodicidade que vencem na mesma data (a revisão de 200 horas não se mistura com a de 400 horas, mesmo quando
    as duas vencem juntas). [{"data", "titulo", "periodicidade", "varias_periodicidades", "status", "itens"
    [{atividade, periodicidade, sistema, servico}], "feitos", "total", "abertos" (sem comprovante válido),
    "aguardando", "medidor" (horas previstas na data, ou None), "realizada_em", "validacao" (rótulo da validação da
    Sompo ou None), "motivo", "extras" (feitas a mais, regra ideal: ficam com a data prevista da revisão),
    "prazo" (último dia para o comprovante)}].
    Status: concluída / aguardando validação / pendente (venceu, dentro do prazo de PRAZO_COMPROVANTE_DIAS dias) /
    comprovante não enviado (prazo encerrado) / próxima (vence em DIAS_PROXIMAS dias) / futura."""
    hoje = hoje or datetime.date.today()
    grupos, extras = {}, {}
    for a in atividades:
        if not a["DATA_PREVISTA"]:
            continue
        if eh_ideal(a):
            extras.setdefault((a["DATA_PREVISTA"], a["ID_ORIENTACAO"]), []).append(a)
            continue
        _, dias = periodicidade(a, a)
        grupos.setdefault((a["DATA_PREVISTA"], round(dias or 0)), []).append(a)
    resultado = []
    for data, dias in sorted(grupos):
        itens = [{"atividade": a, "periodicidade": periodicidade_completa(a, a) or "Outras",
                  "sistema": _sistema(a), "servico": _servico(a)} for a in grupos[(data, dias)]]
        itens.sort(key=lambda i: (i["sistema"], i["servico"]))
        rotulos = [i["periodicidade"] for i in itens]
        principal = max(set(rotulos), key=lambda r: (rotulos.count(r), " / " in r))
        feitos = sum(1 for i in itens if valida(i["atividade"]))
        aguardando = sum(1 for i in itens if aguardando_validacao(i["atividade"]))
        abertos = [i for i in itens if not valida(i["atividade"])]
        prazo = data + datetime.timedelta(days=PRAZO_COMPROVANTE_DIAS)
        if not abertos:
            status = AGUARDANDO if aguardando else CONCLUIDA
        elif data <= hoje:
            status = PENDENTE if hoje <= prazo else NAO_ENVIADO
        elif (data - hoje).days <= DIAS_PROXIMAS:
            status = PROXIMA
        else:
            status = FUTURA
        feitas = [i["atividade"]["DATA_REALIZADA"] for i in itens
                  if valida(i["atividade"]) and i["atividade"]["DATA_REALIZADA"]]
        notas = [i["atividade"]["ATENDE_CRITERIOS"] for i in itens
                 if i["atividade"]["tem_comprovante"] and i["atividade"].get("ATENDE_CRITERIOS") is not None]
        motivos = [i["atividade"].get("MOTIVO_AVALIACAO") for i in itens if i["atividade"].get("MOTIVO_AVALIACAO")]
        extras_revisao = [a for i in itens for a in extras.get((data, i["atividade"]["ID_ORIENTACAO"]), [])
                          if a["tem_comprovante"]]
        a0 = itens[0]["atividade"]
        resultado.append({"data": data, "dias": dias, "titulo": _titulo(principal), "periodicidade": principal,
                          "chave": chave_revisao(a0["ID_EQUIPAMENTO_SEGURADO"], data, dias),
                          "varias_periodicidades": len(set(rotulos)) > 1, "status": status, "itens": itens,
                          "feitos": feitos, "total": len(itens), "abertos": abertos, "aguardando": aguardando,
                          "medidor": medidor_previsto(itens[0]["atividade"], itens[0]["atividade"], data),
                          "realizada_em": max(feitas) if feitas else None,
                          "validacao": rotulo_validacao(min(notas)) if notas else None,
                          "motivo": motivos[0] if motivos else None, "extras": extras_revisao, "prazo": prazo})
    return resultado


def sequencia(lista_revisoes, hoje=None):
    """Revisões seguidas comprovadas, das mais recentes para trás. As que ainda estão no prazo do comprovante não
    interrompem a sequência; uma com o prazo encerrado sem comprovante interrompe."""
    hoje = hoje or datetime.date.today()
    total = 0
    for r in reversed([r for r in lista_revisoes if r["data"] <= hoje]):
        if r["status"] == PENDENTE:
            continue
        if r["abertos"]:
            break
        total += 1
    return total


def situacao_prazo(revisao, hoje=None):
    """Texto curto do prazo da revisão (ex.: 'vence em 12 dias', 'venceu há 3 dias · envie o comprovante até
    dd/mm/aaaa')."""
    hoje = hoje or datetime.date.today()
    dias = (revisao["data"] - hoje).days
    if dias < 0 and revisao.get("prazo"):
        if hoje > revisao["prazo"]:
            return f"venceu há {_dias(-dias)} · prazo do comprovante encerrado em {revisao['prazo']:%d/%m/%Y}"
        return (f"venceu há {_dias(-dias)} · envie o comprovante até {revisao['prazo']:%d/%m/%Y} "
                f"(faltam {_dias((revisao['prazo'] - hoje).days)})")
    if dias > 0:
        return f"vence em {_dias(dias)}"
    if dias == 0:
        return "vence hoje"
    return f"venceu há {_dias(-dias)}"


def regra_ideal_da_recomendacao(atividades_recomendacao, hoje=None):
    """(manutenções válidas no período, manutenções pela regra do manual no período) de uma recomendação numa
    máquina, ou None se ela não tem duas opções (aí a regra ideal é o próprio cronograma)."""
    hoje = hoje or datetime.date.today()
    base = atividades_recomendacao[0]
    if not tem_duas_opcoes(base, base):
        return None
    inicio = inicio_periodo(base, hoje)
    pedidas = len(vencimentos(base, base, inicio, hoje, REGRA_IDEAL))
    feitas = sum(1 for a in atividades_recomendacao if valida(a)
                 and inicio <= (a.get("DATA_REALIZADA") or a["DATA_PREVISTA"]) <= hoje)
    return min(feitas, pedidas), pedidas


def percentual_ideal(atividades, hoje=None):
    """Percentual da regra ideal nas recomendações com duas opções (None se não há nenhuma com vencimento)."""
    hoje = hoje or datetime.date.today()
    grupos = {}
    for a in atividades:
        grupos.setdefault((a["ID_EQUIPAMENTO_SEGURADO"], a["ID_ORIENTACAO"]), []).append(a)
    feitas = pedidas = 0
    for lista in grupos.values():
        ideal = regra_ideal_da_recomendacao(lista, hoje)
        if ideal:
            feitas += ideal[0]
            pedidas += ideal[1]
    return feitas / pedidas * 100 if pedidas else None


def indicador_cumprimento(atividades, hoje=None):
    """Item "Histórico de cumprimento de orientações" do Score de Risco:
    {"realizacao", "atende", "ideal" (percentuais ou None), "validados", "aguardando", "nota" (1, 2, 3 ou None =
    sem dados, nota neutra), "bonus" (redução no score final por cumprir a regra ideal)}.
    Atendimento = média das notas da validação (Atende 100, maior parte 75, menor parte 25, não atende 0).
    Nota: vale o menor entre realização e atendimento: 1 se >= 80%; 3 se < 50%; senão 2. Sem nenhuma validação da
    Sompo, vale só a realização."""
    hoje = hoje or datetime.date.today()
    _, _, realizacao = percentual_realizacao(atividades, hoje)
    validados = [a for a in atividades if a["tem_comprovante"] and a.get("ATENDE_CRITERIOS") is not None]
    atende = sum(float(a["ATENDE_CRITERIOS"]) for a in validados) / len(validados) if validados else None
    ideal = percentual_ideal(atividades, hoje)
    criterios = [v for v in (realizacao, atende) if v is not None]
    if not criterios:
        nota = None
    elif min(criterios) >= FAIXA_BOA:
        nota = 1
    elif min(criterios) < FAIXA_MEDIA:
        nota = 3
    else:
        nota = 2
    return {"realizacao": realizacao, "atende": atende, "ideal": ideal, "validados": len(validados),
            "aguardando": sum(1 for a in atividades if aguardando_validacao(a)), "nota": nota,
            "bonus": bonus_regra_ideal(ideal)}


def bonus_regra_ideal(percentual_ideal_):
    """Pontos descontados do score (0 a 100) por cumprir a regra ideal do manual: 5 com 80% ou mais; 2,5 de 50% a
    menos de 80%; 0 abaixo disso ou sem recomendações com duas opções."""
    if percentual_ideal_ is None:
        return 0.0
    for faixa, bonus in sorted(BONUS_IDEAL.items(), reverse=True):
        if percentual_ideal_ >= faixa:
            return bonus
    return 0.0


def por_mes(atividades, hoje=None, meses_antes=12, meses_depois=3):
    """Quantidade prevista (pela data prevista, cronograma) e realizada (pela data de realização, comprovantes
    válidos) por mês: [{"mes": date(ano, mes, 1), "serie": "Previstas" | "Realizadas", "quantidade": n}]."""
    hoje = hoje or datetime.date.today()

    def mes(d):
        return datetime.date(d.year, d.month, 1)

    def soma_meses(d, n):
        total = d.year * 12 + d.month - 1 + n
        return datetime.date(total // 12, total % 12 + 1, 1)

    inicio, fim = soma_meses(mes(hoje), -meses_antes), soma_meses(mes(hoje), meses_depois)
    contagem = {}
    for a in atividades:
        if a["DATA_PREVISTA"] and not eh_ideal(a) and inicio <= mes(a["DATA_PREVISTA"]) <= fim:
            chave = (mes(a["DATA_PREVISTA"]), "Previstas")
            contagem[chave] = contagem.get(chave, 0) + 1
        if valida(a) and a.get("DATA_REALIZADA") and inicio <= mes(a["DATA_REALIZADA"]) <= fim:
            chave = (mes(a["DATA_REALIZADA"]), "Realizadas")
            contagem[chave] = contagem.get(chave, 0) + 1
    linhas, atual = [], inicio
    while atual <= fim:
        for serie in ("Previstas", "Realizadas"):
            linhas.append({"mes": atual, "serie": serie, "quantidade": contagem.get((atual, serie), 0)})
        atual = soma_meses(atual, 1)
    return linhas
