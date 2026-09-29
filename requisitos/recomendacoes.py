# -*- coding: utf-8 -*-
"""
Quais recomendações dos manuais (CS_EQUIPAMENTOS_ORIENTACOES) são atividades de MANUTENÇÃO.

Nem toda recomendação é de manutenção (há orientações de operação, segurança, armazenamento...). Só as de manutenção
geram atividades previstas em CS_MANUTENCOES_REALIZADAS, pedem comprovante na página "Comprovação" (Manutenções) e entram no
Score de Risco (cumprimento de orientações e complexidade da manutenção).

TIPOS_MANUTENCAO: valores de TIPO_ORIENTACAO considerados manutenção (comparação sem acento e em maiúsculas).
Se a lista ficar vazia, vale a regra por palavras-chave (PALAVRAS_MANUTENCAO).
"""
import unicodedata

# Valores de TIPO_ORIENTACAO que são manutenção (NPL 1). Os demais (ALERTA_TEMP_MINIMA, ALERTA_TEMP_MAXIMA do NPL 2 e
# RISCO_CHUVA_DESLIZE do NPL 3) são alertas de clima: não geram atividades nem pedem comprovante)
TIPOS_MANUTENCAO = ["MANUTENCAO_PROGRAMADA"]

# Regra provisória: o tipo contém alguma destas palavras
PALAVRAS_MANUTENCAO = ["MANUTEN", "PREVENT", "REVIS", "LUBRIF", "TROCA", "SUBSTITU", "INSPEC", "LIMPEZA",
                       "CALIBR", "AJUSTE", "REAPERT"]


def normalizar(texto):
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", str(texto or ""))
                         if unicodedata.category(c) != "Mn")
    return " ".join(sem_acento.upper().replace("_", " ").split())


def eh_manutencao(tipo_orientacao):
    """True se o tipo de recomendação é uma atividade de manutenção."""
    tipo = normalizar(tipo_orientacao)
    if TIPOS_MANUTENCAO:
        return tipo in {normalizar(t) for t in TIPOS_MANUTENCAO}
    return any(palavra in tipo for palavra in PALAVRAS_MANUTENCAO)
