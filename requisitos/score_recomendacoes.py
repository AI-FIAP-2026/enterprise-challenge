# -*- coding: utf-8 -*-
"""
Recomendações preventivas derivadas do Score de Risco (requisitos/score_risco.py).

Para cada item com nota acima de 1, uma recomendação com critério explícito:
  - o critério da nota atual e o que leva à nota 1 (score_risco.CRITERIOS);
  - a ação recomendada e quem age (produtor ou Sompo);
  - o impacto: quantos pontos o score do cliente (0 a 100) cai se o item chegar à nota 1 (peso x (nota - 1) / 2);
  - a prioridade: Alta (nota 3 ou impacto de 7,5 pontos ou mais), Média (demais).
Item sem dados (nota neutra) recebe a recomendação de completar o dado, porque a nota neutra esconde o risco real.
Também simula o cenário "itens de prioridade Alta resolvidos": novo score e nova classe.
"""
from requisitos import score_risco as sr
from requisitos import comprovacao as cp

PRODUTOR, SOMPO, AMBOS = "Produtor", "Sompo (subscrição)", "Produtor e Sompo"
IMPACTO_ALTO = 7.5                  # pontos

ACOES = {
    "exposicao": (SOMPO, "Concentração de valor segurado: avaliar limite por apólice, franquia ou cosseguro e exigir "
                         "inspeção periódica das máquinas de maior valor."),
    "cumprimento": (PRODUTOR, "Enviar os comprovantes das revisões vencidas e refazer as reprovadas na validação. A "
                              "nota vai a 1 com realização e atendimento de 80% ou mais."),
    "sinistros": (AMBOS, "Analisar a causa dos sinistros dos últimos 5 anos e exigir ações corretivas: treinamento dos "
                         "operadores, inspeção das máquinas sinistradas e revisão das condições de uso."),
    "queimadas": (PRODUTOR, "Manter aceiros limpos, brigada e caminhão-pipa prontos e acompanhar o risco previsto na "
                            "Central de Alertas. Nos dias Alto ou Crítico, evitar trabalho que gere faísca e guardar as "
                            "máquinas longe de palhada seca."),
    "hidrologico": (PRODUTOR, "Nos alertas do CEMADEN, retirar as máquinas de áreas baixas e próximas de rios, suspender "
                              "operações em encostas e manter um local seguro de guarda definido."),
    "climatico": (PRODUTOR, "Evitar tráfego de máquinas depois de chuva acumulada acima do limite do relevo em 72 horas "
                            "(atolamento e deslizamento), priorizar áreas planas nesses dias e manter terraços e "
                            "drenagem."),
    "complexidade": (AMBOS, "Frota com muitas revisões: formalizar o plano de manutenção com a concessionária, "
                            "registrar o horímetro todo mês e priorizar as revisões dos sistemas críticos."),
    "procedimentos": (PRODUTOR, "Implantar as práticas de menor pontuação no questionário de maturidade (manutenção "
                                "preventiva, gestão de riscos e governança) e responder o questionário de novo."),
}
SEM_DADOS = {
    "exposicao": "Cadastrar os equipamentos segurados com o valor.",
    "cumprimento": "Enviar os comprovantes das revisões: sem nenhum, o cumprimento não pode ser medido.",
    "sinistros": "Cliente com menos de 1 ano de seguro: acompanhar até formar histórico.",
    "queimadas": "Cadastrar as coordenadas das fazendas (página Cadastro).",
    "hidrologico": "Coletar o clima das fazendas (serviço fazendas_clima do pipeline) e gerar as regras de chuva "
                   "(página Monitoramento).",
    "climatico": "Coletar o clima das fazendas (serviço fazendas_clima do pipeline).",
    "complexidade": "Cadastrar os equipamentos com modelo que tenha manual lido (página Programação).",
    "procedimentos": "Responder o questionário de maturidade da manutenção (página Cadastro).",
}


def impacto(chave, nota):
    """Quantos pontos o score cai se o item chegar à nota 1."""
    return sr.pontos(chave, nota)


def _fazendas_com_nota(resultado, chave, minimo=2.5):
    return [f["NOME_FAZENDA"] for f in resultado.get("fazendas", [])
            if chave in f["itens"] and f["itens"][chave]["nota"] >= minimo]


def recomendacoes(resultado):
    """Lista de recomendações do cliente, da maior para a menor redução possível do score:
    [{"chave", "item", "nota", "prioridade", "criterio", "acao", "responsavel", "impacto", "situacao"}]."""
    lista = []
    for chave in sr.PESOS:
        item = resultado["itens"][chave]
        nota = float(item["nota"])
        criterio = sr.CRITERIOS[chave]
        if item["sem_dados"]:
            lista.append({"chave": chave, "item": sr.NOMES[chave], "nota": nota, "prioridade": "Dado ausente",
                          "criterio": f"Sem dados: nota neutra 2. Nota 1: {criterio[0]}.",
                          "acao": SEM_DADOS[chave], "responsavel": PRODUTOR if chave != "sinistros" else SOMPO,
                          "impacto": impacto(chave, nota), "situacao": item["detalhe"]})
            continue
        if nota <= 1:
            continue
        faixa = criterio[2] if nota >= 2.5 else criterio[1]
        responsavel, acao = ACOES[chave]
        if chave in sr.ITENS_FAZENDA:
            criticas = _fazendas_com_nota(resultado, chave)
            if criticas:
                acao += f" Começar por: {', '.join(criticas[:4])}{' ...' if len(criticas) > 4 else ''}."
        valor_imp = impacto(chave, nota)
        lista.append({"chave": chave, "item": sr.NOMES[chave], "nota": nota,
                      "prioridade": "Alta" if nota >= 2.5 or valor_imp >= IMPACTO_ALTO else "Média",
                      "criterio": f"Hoje: {faixa} ({item['valor']}). Nota 1: {criterio[0]}.",
                      "acao": acao, "responsavel": responsavel, "impacto": valor_imp, "situacao": item["detalhe"]})

    # bônus da regra ideal ainda não alcançado
    faixa_bonus = max(cp.BONUS_IDEAL)
    if (resultado.get("bonus") or 0) < cp.BONUS_IDEAL[faixa_bonus]:
        lista.append({"chave": "bonus", "item": "Bônus da regra ideal", "nota": None, "prioridade": "Oportunidade",
                      "criterio": f"Bônus atual: {sr._virgula(resultado.get('bonus') or 0)} pontos. Bônus máximo: "
                                  f"{sr._virgula(cp.BONUS_IDEAL[faixa_bonus])} pontos com {faixa_bonus:.0f}% ou mais "
                                  "das revisões feitas pela regra ideal do manual.",
                      "acao": "Fazer as revisões também pela regra ideal do manual (ex.: a cada 250 horas quando o "
                              "cronograma pede a mensal) e enviar o comprovante no card da revisão.",
                      "responsavel": PRODUTOR,
                      "impacto": cp.BONUS_IDEAL[faixa_bonus] - (resultado.get("bonus") or 0), "situacao": ""})
    ordem = {"Alta": 0, "Dado ausente": 1, "Média": 2, "Oportunidade": 3}
    return sorted(lista, key=lambda r: (ordem[r["prioridade"]], -r["impacto"]))


def cenario(resultado, recs, prioridades=("Alta",)):
    """Score e classe do cliente se as recomendações das prioridades indicadas forem cumpridas (itens -> nota 1)."""
    notas = {k: resultado["itens"][k]["nota"] for k in sr.PESOS}
    resolvidos = [r["chave"] for r in recs if r["prioridade"] in prioridades and r["chave"] in notas]
    for chave in resolvidos:
        notas[chave] = 1
    novo = sr._total(notas, resultado.get("bonus"))
    return {"itens": [sr.NOMES[k] for k in resolvidos], "score": novo, "classe": sr.classificar(novo)}


def principal_fator(notas):
    """Item que mais pesa no score: maior peso x (nota - 1). notas = {chave: nota}. None se tudo está em 1."""
    candidatos = [(impacto(k, notas[k]), k) for k in sr.PESOS if notas.get(k) is not None and notas[k] > 1]
    return sr.NOMES[max(candidatos)[1]] if candidatos else None
