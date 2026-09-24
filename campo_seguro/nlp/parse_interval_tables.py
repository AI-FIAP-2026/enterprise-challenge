"""Parser determinístico (sem LLM) das tabelas de intervalo de manutenção.

Fase 1 do kickoff (docs/prompt_claude_code.md, item 1.1):
- CH950: sumário ("Conteúdo", entradas "<título> ..... 95-<Letra>-<n>").
- 5060E: tabela "Periodicidade | Sistema | Serviço" (seção 207-2 a 207-4).

Ambos usam o rodapé de página do próprio manual (ex.: "95-B-2", "207-3") como
âncora determinística para achar o índice da página real no PDF — é o mesmo
rodapé usado para pagina_origem.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz  # pymupdf

from campo_seguro.nlp.normalize import (
    chave_normalizada,
    faixa_ch950,
    faixa_5060e,
    mapear_acao_tecnica,
    mapear_subsistema_5060e,
    mapear_subsistema_ch950,
    nota_5060e,
    normalizar_periodicidade_5060e,
)
from campo_seguro.nlp.schema import Orientacao

CH950_COD_FONTE = "JD-CH950-OMCXT31163-B3"
CH950_NOME_DOC = "John Deere CH950/CH960 Manual do Operador (OMCXT31163 Ed. B3)"

TRATOR_5060E_COD_FONTE = "JD-5060E-OMTR132548-E6"
TRATOR_5060E_NOME_DOC = "John Deere 5060E/5070E/5080E/5078E/5090E Manual do Operador (OMTR132548 Ed. E6)"

_FOOTER_PAT = re.compile(r"^(\d{2,3}[A-Z]?-[A-Z]-\d+|\d{2,3}[A-Z]?-\d+)$")
_ROMAN_PAT = re.compile(r"^[ivxlcdm]{1,6}$", re.IGNORECASE)
_NOISE_LINES = {"Página", "Conteúdo"}


def mapa_codigo_pagina(doc: fitz.Document) -> dict[str, int]:
    """Rodapé de cada página (ex. '95-B-2', '207-3') -> índice da página no PDF (0-based)."""
    mapa: dict[str, int] = {}
    for i, page in enumerate(doc):
        texto = page.get_text().strip()
        if not texto:
            continue
        ultima_linha = texto.splitlines()[-1].strip()
        m = _FOOTER_PAT.match(ultima_linha)
        if m:
            mapa.setdefault(m.group(1), i)
    return mapa


def construir_detalhamento(base: str, valor_gatilho, metrica_gatilho, fator_condicional) -> str:
    base = base.rstrip(".").strip()
    if metrica_gatilho == "HORIMETRO" and valor_gatilho:
        sufixo = f"a cada {int(valor_gatilho)} horas"
    elif metrica_gatilho == "CALENDARIO":
        sufixo = "anualmente"
    elif metrica_gatilho == "CONDICIONAL":
        sufixo = "conforme necessário"
    else:
        sufixo = None
    texto = f"{base} — {sufixo}" if sufixo else base
    if fator_condicional == "AMACIAMENTO_UNICA_VEZ":
        texto += " (amaciamento, execução única)"
    elif fator_condicional and fator_condicional.startswith("OU_CALENDARIO:"):
        equivalente = fator_condicional.split(":", 1)[1].replace("_", " ").lower()
        texto += f" (ou {equivalente}, o que ocorrer primeiro)"
    return texto + "."


# --------------------------------------------------------------------------
# CH950 — sumário (95-A..M)
# --------------------------------------------------------------------------

def _localizar_paginas_indice_ch950(doc: fitz.Document, limite_paginas: int = 25) -> list[int]:
    paginas = []
    for i in range(min(limite_paginas, len(doc))):
        if re.search(r"95-[A-Z]-\d", doc[i].get_text()):
            paginas.append(i)
    return paginas


def _parse_indice_ch950(doc: fitz.Document, paginas_indice: list[int]) -> list[dict]:
    entry_pat = re.compile(r"^(.*?)\.{2,}\s*(95-[A-Z]-\d+)\s*$")
    entradas: list[dict] = []
    buffer_linhas: list[str] = []
    for pi in paginas_indice:
        for linha in doc[pi].get_text().splitlines():
            linha = linha.strip()
            if not linha or linha in _NOISE_LINES or _ROMAN_PAT.match(linha):
                continue
            m = entry_pat.match(linha)
            if not m:
                buffer_linhas.append(linha)
                continue
            trecho_final, codigo = m.group(1).strip(), m.group(2)
            buffer_linhas.append(trecho_final)
            titulo = " ".join(x for x in buffer_linhas if x).strip()
            buffer_linhas = []
            if "Tabela de Intervalo" in titulo or "Tabela do Intervalo" in titulo:
                continue
            entradas.append({"codigo": codigo, "titulo": titulo})
    return entradas


def parse_ch950(caminho_pdf: str | Path) -> list[Orientacao]:
    doc = fitz.open(caminho_pdf)
    try:
        mapa_paginas = mapa_codigo_pagina(doc)
        paginas_indice = _localizar_paginas_indice_ch950(doc)
        if not paginas_indice:
            raise RuntimeError("Sumário do CH950 não encontrado (padrão '95-X-n' ausente nas primeiras páginas)")
        entradas = _parse_indice_ch950(doc, paginas_indice)

        orientacoes: list[Orientacao] = []
        vistos: set[tuple[str, str]] = set()
        for e in entradas:
            chave_dedup = (e["codigo"], chave_normalizada(e["titulo"]))
            if chave_dedup in vistos:
                continue
            vistos.add(chave_dedup)

            letra_faixa = e["codigo"].split("-")[1]
            faixa = faixa_ch950(letra_faixa)
            valor_gatilho = faixa["valor_gatilho"]
            metrica_gatilho = faixa["metrica_gatilho"]
            fator_condicional = faixa.get("fator_condicional")

            orientacoes.append(
                Orientacao(
                    cod_fonte_manual=CH950_COD_FONTE,
                    nome_documento_origem=CH950_NOME_DOC,
                    tipo_orientacao="MANUTENCAO_PROGRAMADA",
                    subsistema=mapear_subsistema_ch950(e["titulo"]),
                    acao_tecnica=mapear_acao_tecnica(e["titulo"]),
                    detalhamento_orientacao=construir_detalhamento(
                        e["titulo"], valor_gatilho, metrica_gatilho, fator_condicional
                    ),
                    metrica_gatilho=metrica_gatilho,
                    valor_gatilho=float(valor_gatilho) if valor_gatilho is not None else None,
                    unidade_medida="h" if metrica_gatilho == "HORIMETRO" else None,
                    fator_condicional=fator_condicional,
                    texto_bruto_original=f"[{e['codigo']}] {e['titulo']}",
                    pagina_origem=(mapa_paginas.get(e["codigo"], -1) + 1) or None,
                    criticidade="MEDIA",
                )
            )
        return orientacoes
    finally:
        doc.close()


# --------------------------------------------------------------------------
# 5060E — tabela 207-2..4 (Periodicidade | Sistema | Serviço)
# --------------------------------------------------------------------------

_NOISE_5060E = {"Periodicidade", "Sistema", "Serviço", "Intervalos de Manutenção e Serviço"}
_LETRAS_NOTA = "abcdefghijkl"
_INICIO_NOTAS_PAT = re.compile(r"^[a-l][A-ZÀ-Ú]")


def _stripar_notas(linha: str, nomes_validos_normalizados: set[str] | None = None) -> tuple[str, list[str]]:
    """Remove 0-2 letras de nota-de-rodapé coladas ao fim da linha (ex. '...TDP.b' -> ('...TDP.', ['b'])).

    Só aceita a remoção se o resultado for plausível: termina em '.' ou bate
    com um cabeçalho conhecido. Deliberadamente conservador: um punhado de
    linhas do 5060E (seção 'A Cada 400 Horas') cola a nota sem ponto nenhum
    (ex. 'dianteirof') e por isso ficam com a letra solta no texto — melhor
    isso do que arriscar cortar a última letra de palavras legítimas como
    'Anualmente' (termina em 'e', uma das letras de nota), que quebraria o
    reconhecimento de cabeçalho da tabela inteira. Ver PENDENCIAS no export.
    """
    for n in (2, 1):
        if len(linha) <= n:
            continue
        notas_candidatas = list(linha[-n:])
        if not all(c in _LETRAS_NOTA for c in notas_candidatas):
            continue
        core = linha[:-n].rstrip()
        valido = core.endswith(".") or (
            nomes_validos_normalizados and chave_normalizada(core.rstrip(".")) in nomes_validos_normalizados
        )
        if valido:
            return core, notas_candidatas
    return linha, []


def _juntar_partes_frase(partes: list[str]) -> str:
    """Junta linhas de uma mesma frase, remendando hifenização quebrada pelo
    PDF (ex. 'do ar-' + '-condicionado...' -> 'do ar-condicionado...')."""
    texto = partes[0]
    for parte in partes[1:]:
        if texto.endswith("-") and parte.startswith("-"):
            texto = texto + parte[1:]
        else:
            texto = texto.rstrip() + " " + parte.lstrip()
    return texto.strip()


def _consumir_frase_servico(
    linhas: list[str], i: int, nomes_periodicidade: set[str], nomes_sistema: set[str]
) -> tuple[str, list[str], int]:
    """Acumula linhas[i], linhas[i+1], ... até a frase terminar em '.' (a
    última linha pode ter 0-2 letras de nota coladas). Uma frase de serviço
    quase sempre cabe em 1 linha, mas pode quebrar em 2 por causa da largura
    da coluna da tabela (ex. 'Verifique e aperte as mangueiras... da' /
    'entrada de ar e de arrefecimento.').

    Trava de segurança: NUNCA consome uma próxima linha que seja um cabeçalho
    conhecido (periodicidade/sistema) ou o início do bloco de notas — algumas
    linhas de serviço do 5060E não têm ponto final nenhum (ex. '...dianteirof',
    seção 'A Cada 400 Horas'), e sem essa trava o acumulador engoliria a
    tabela inteira até o próximo ponto final por sorte.
    """
    partes: list[str] = []
    notas: list[str] = []
    j = i
    while True:
        core, notas = _stripar_notas(linhas[j])
        partes.append(core)
        fim_de_frase = core.rstrip().endswith(".")
        ultima_linha_disponivel = j + 1 >= len(linhas)
        proxima_e_fronteira = not ultima_linha_disponivel and (
            _INICIO_NOTAS_PAT.match(linhas[j + 1])
            or _tentar_casar_cabecalho(linhas, j + 1, nomes_periodicidade)
            or _tentar_casar_cabecalho(linhas, j + 1, nomes_sistema)
        )
        if fim_de_frase or ultima_linha_disponivel or proxima_e_fronteira:
            break
        j += 1
    return _juntar_partes_frase(partes), notas, j + 1


def _tentar_casar_cabecalho(linhas: list[str], i: int, nomes_normalizados: set[str]) -> tuple[str, int, list[str]] | None:
    """Tenta casar linhas[i] (ou linhas[i]+linhas[i+1], por causa de quebra de página) com um cabeçalho conhecido."""
    for span in (1, 2):
        if i + span > len(linhas):
            continue
        candidato = " ".join(linhas[i : i + span])
        core, notas = _stripar_notas(candidato, nomes_normalizados)
        if chave_normalizada(core.rstrip(".")) in nomes_normalizados:
            return core, span, notas
    return None


def _texto_tabela_5060e(doc: fitz.Document, mapa_paginas: dict[str, int]) -> list[tuple[str, int]]:
    """Retorna [(linha, pdf_page_index), ...] das páginas 207-2..4, sem ruído de cabeçalho/rodapé."""
    codigos = ["207-2", "207-3", "207-4"]
    linhas: list[tuple[str, int]] = []
    for codigo in codigos:
        idx = mapa_paginas.get(codigo)
        if idx is None:
            raise RuntimeError(f"Página {codigo} (tabela de intervalos do 5060E) não encontrada")
        texto = doc[idx].get_text()
        page_lines = [l.strip() for l in texto.splitlines() if l.strip()]
        if codigo == "207-2":
            # descarta o preâmbulo (CUIDADO / explicação do horímetro) antes do título da tabela
            try:
                start = page_lines.index("Tabela de Intervalos de Serviço") + 1
            except ValueError:
                start = 0
            page_lines = page_lines[start:]
        # descarta o rodapé (código de página, já usado em mapa_paginas) e o título corrente
        page_lines = [l for l in page_lines if l != codigo and l not in _NOISE_5060E]
        linhas.extend((l, idx) for l in page_lines)
    return linhas


def parse_5060e(caminho_pdf: str | Path) -> list[Orientacao]:
    from campo_seguro.nlp.normalize import _load_yaml  # reaproveita cache

    doc = fitz.open(caminho_pdf)
    try:
        mapa_paginas = mapa_codigo_pagina(doc)
        linhas_com_pagina = _texto_tabela_5060e(doc, mapa_paginas)

        cfg_periodicidade = _load_yaml("faixas_intervalo.yaml")["tratores_5060e"]
        nomes_periodicidade = {chave_normalizada(v["nome"]) for v in cfg_periodicidade.values()}
        cfg_sistema = _load_yaml("subsistemas.yaml")["tratores_5060e"]
        nomes_sistema = {chave_normalizada(k) for k in cfg_sistema}

        linhas = [l for l, _ in linhas_com_pagina]
        paginas = [p for _, p in linhas_com_pagina]

        orientacoes: list[Orientacao] = []
        periodicidade_atual: str | None = None
        sistema_atual: str | None = None
        i = 0
        while i < len(linhas):
            linha = linhas[i]

            if _INICIO_NOTAS_PAT.match(linha) and periodicidade_atual is not None:
                break  # início do bloco de notas de rodapé (a-l) = fim da tabela

            casado = _tentar_casar_cabecalho(linhas, i, nomes_periodicidade)
            if casado:
                periodicidade_atual, span, _ = casado
                sistema_atual = None
                i += span
                continue

            casado = _tentar_casar_cabecalho(linhas, i, nomes_sistema)
            if casado:
                sistema_atual, span, _ = casado
                i += span
                continue

            # linha de serviço (pode quebrar em mais de uma linha do PDF)
            if periodicidade_atual is None or sistema_atual is None:
                raise RuntimeError(f"Linha de serviço sem periodicidade/sistema definidos: {linha!r}")
            core, notas, proximo_i = _consumir_frase_servico(linhas, i, nomes_periodicidade, nomes_sistema)

            faixa = faixa_5060e(periodicidade_atual)
            valor_gatilho = faixa["valor_gatilho"]
            metrica_gatilho = faixa["metrica_gatilho"]
            fator_condicional = faixa.get("fator_condicional")
            if notas:
                textos_nota = "; ".join(f"{l}: {nota_5060e(l)}" for l in notas)
                fator_condicional = f"{fator_condicional + ' + ' if fator_condicional else ''}NOTA_{'/'.join(notas)}:{textos_nota}"

            orientacoes.append(
                Orientacao(
                    cod_fonte_manual=TRATOR_5060E_COD_FONTE,
                    nome_documento_origem=TRATOR_5060E_NOME_DOC,
                    tipo_orientacao="MANUTENCAO_PROGRAMADA",
                    subsistema=mapear_subsistema_5060e(sistema_atual),
                    acao_tecnica=mapear_acao_tecnica(core),
                    detalhamento_orientacao=construir_detalhamento(core, valor_gatilho, metrica_gatilho, fator_condicional),
                    metrica_gatilho=metrica_gatilho,
                    valor_gatilho=float(valor_gatilho) if valor_gatilho is not None else None,
                    unidade_medida="h" if metrica_gatilho == "HORIMETRO" else None,
                    fator_condicional=fator_condicional,
                    texto_bruto_original=f"[{periodicidade_atual} / {sistema_atual}] "
                    + " / ".join(linhas[i:proximo_i]),
                    pagina_origem=paginas[i] + 1,
                    criticidade="MEDIA",
                )
            )
            i = proximo_i
        return orientacoes
    finally:
        doc.close()
