# -*- coding: utf-8 -*-
"""
Ação de subscrição sugerida pelo Score de Risco: ajuste do prêmio do cliente de -15% a +15%.

A sugestão é só um apoio: quem decide é o agente de subscrição, na página Score Risk. Cada decisão (ajuste escolhido,
ajuste sugerido, score e justificativa) fica gravada em CS_LOG_ACOES (ação AJUSTE_PREMIO, entidade PREMIO, chave =
ID do cliente), com quem decidiu e quando (requisitos/auditoria.py).

Faixas da sugestão (score de 0 a 100, depois do bônus da regra ideal):
  abaixo de 15 ............ -15%    de 30 a 40 ......... manter (0%)
  de 15 a menos de 22,5 ... -10%    acima de 40 a 50 ... +5%
  de 22,5 a menos de 30 ... -5%     acima de 50 a 65 ... +10%
                                    acima de 65 ........ +15%
Cliente que piorou desde o cálculo anterior (subiu de classe ou 7,5 pontos ou mais) não recebe sugestão de desconto:
a sugestão sobe para "manter".
"""
AJUSTES = [-15, -10, -5, 0, 5, 10, 15]
ENTIDADE_PREMIO = "PREMIO"
ACAO_PREMIO = "AJUSTE_PREMIO"
FAIXAS = [(15.0, -15), (22.5, -10), (30.0, -5)]          # score abaixo do limite -> desconto


def sugestao(score, piorou=False):
    """Ajuste sugerido (%) para o score do cliente."""
    if score is None:
        return 0
    ajuste = None
    for limite, valor in FAIXAS:
        if score < limite:
            ajuste = valor
            break
    if ajuste is None:
        ajuste = 0 if score <= 40 else 5 if score <= 50 else 10 if score <= 65 else 15
    if piorou and ajuste < 0:
        ajuste = 0
    return ajuste


def rotulo(ajuste):
    if ajuste == 0:
        return "Manter o prêmio"
    return f"{'Aumentar' if ajuste > 0 else 'Reduzir'} {abs(ajuste)}%"


def motivo(score, classe, piorou):
    texto = f"score {score:.1f} (risco {(classe or '-').lower()})".replace(".", ",")
    if piorou:
        texto += "; piorou desde o cálculo anterior, sem sugestão de desconto"
    return texto


def texto_faixas():
    return ("Sugestão pelo score: abaixo de 15, reduzir 15%; de 15 a menos de 22,5, reduzir 10%; de 22,5 a menos "
            "de 30, reduzir 5%; de 30 a 40, manter; acima de 40 a 50, aumentar 5%; acima de 50 a 65, aumentar 10%; "
            "acima de 65, aumentar 15%. Cliente que piorou desde o cálculo anterior não recebe sugestão de desconto.")
