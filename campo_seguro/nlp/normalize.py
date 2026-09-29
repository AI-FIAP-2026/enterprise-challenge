"""Normalização: texto bruto do manual -> enums canônicos (schema.py).

Toda tabela vem de config/*.yaml (subsistemas, verbos, faixas_intervalo) —
editável sem tocar em código, por pedido explícito do kickoff (docs/prompt_claude_code.md).
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"


@lru_cache(maxsize=None)
def _load_yaml(nome_arquivo: str) -> dict:
    caminho = CONFIG_DIR / nome_arquivo
    with open(caminho, encoding="utf-8") as f:
        return yaml.safe_load(f)


def chave_normalizada(texto: str) -> str:
    """minúsculo, sem acento, sem pontuação — usada como chave de lookup em yaml.

    '1.500' (separador de milhar) vira '1500', não '1 500' — senão a chave não
    bate com os números escritos sem pontuação em config/faixas_intervalo.yaml.
    """
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = texto.lower().strip()
    texto = re.sub(r"(?<=\d)\.(?=\d)", "", texto)  # 1.500 -> 1500
    texto = re.sub(r"[.–—]", " ", texto)  # . – —
    texto = re.sub(r"\s+", " ", texto).strip()
    return texto


def primeiro_verbo(texto_titulo: str) -> str:
    """Extrai a primeira palavra 'de ação' do título (Verifique/Verificação/...)."""
    m = re.match(r"^\s*([A-Za-zÀ-ÿ]+)", texto_titulo)
    return m.group(1) if m else ""


def mapear_acao_tecnica(texto_titulo: str) -> str:
    """Os padrões em config/verbos.yaml já estão sem acento (ver comentário lá);
    o verbo extraído do título também passa por chave_normalizada antes de comparar."""
    cfg = _load_yaml("verbos.yaml")
    verbo = chave_normalizada(primeiro_verbo(texto_titulo))
    for padrao, enum in cfg["mapeamento"]:
        if re.match(padrao, verbo, flags=re.IGNORECASE):
            return enum
    return cfg["default"]


def mapear_subsistema_5060e(texto_sistema: str) -> str:
    cfg = _load_yaml("subsistemas.yaml")
    mapa = cfg["tratores_5060e"]
    texto_sistema = texto_sistema.strip().rstrip(".")
    if texto_sistema in mapa:
        return mapa[texto_sistema]
    # tolera pequenas variações de espaçamento/pontuação vindas do PDF
    alvo = chave_normalizada(texto_sistema)
    for chave, enum in mapa.items():
        if chave_normalizada(chave) == alvo:
            return enum
    raise KeyError(f"Sistema 5060E não mapeado em config/subsistemas.yaml: {texto_sistema!r}")


def mapear_subsistema_ch950(titulo_tarefa: str) -> str:
    cfg = _load_yaml("subsistemas.yaml")
    alvo = chave_normalizada(titulo_tarefa)
    for palavras, enum in cfg["ch950_palavras_chave"]:
        for palavra in palavras:
            if chave_normalizada(palavra) in alvo:
                return enum
    return cfg["default"]


def faixa_ch950(codigo_secao: str) -> dict:
    """codigo_secao: 'A'..'M' (letra depois de '95-')."""
    cfg = _load_yaml("faixas_intervalo.yaml")
    chave = f"95-{codigo_secao}"
    if chave not in cfg["ch950"]:
        raise KeyError(f"Faixa CH950 não configurada: {chave}")
    return cfg["ch950"][chave]


def normalizar_periodicidade_5060e(texto: str) -> str:
    """'Diariamente ou a Cada 10\\nHoras de Operação' -> 'diariamente ou a cada 10 horas de operacao'."""
    texto = texto.replace("\n", " ")
    texto = re.sub(r"\s+", " ", texto).strip()
    return chave_normalizada(texto)


def faixa_5060e(texto_periodicidade: str) -> dict:
    cfg = _load_yaml("faixas_intervalo.yaml")
    chave = normalizar_periodicidade_5060e(texto_periodicidade)
    if chave not in cfg["tratores_5060e"]:
        raise KeyError(f"Periodicidade 5060E não configurada: {texto_periodicidade!r} (chave={chave!r})")
    return cfg["tratores_5060e"][chave]


def nota_5060e(letra: str) -> str:
    cfg = _load_yaml("faixas_intervalo.yaml")
    return cfg["tratores_5060e_notas"].get(letra, f"Nota '{letra}' não documentada")
