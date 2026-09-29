"""Leitura automática (sem IA, sem custo) de qualquer manual em PDF -> recomendações de manutenção (NPL 1).

Como funciona:
1. Identifica o manual pela capa: fabricante, modelo, código do manual e edição (ex.: OMCXT31163, EDIÇÃO B3) e
   sugere o código fonte no padrão FABRICANTE-MODELO-CODIGO-EDICAO (ex.: JD-CH950-OMCXT31163-B3).
2. Se o manual tem leitor próprio (CH950 e 5060E, em parse_interval_tables.py), usa esse leitor, que é exato.
3. Senão, usa a leitura genérica: procura títulos de intervalo ("A Cada 250 Horas", "Diariamente",
   "Every 500 hours", "A cada 10.000 km"...) e pega as tarefas logo abaixo deles (linhas que começam com um verbo
   de manutenção: verifique, troque, lubrifique, check, replace...). Funciona em manuais com tabela ou lista de
   manutenção; não lê tabelas em grade (com X nas colunas) nem regras em texto corrido (para isso, use a IA).

Só extrai manutenção programada (NPL 1). Temperatura extrema (NPL 2) e chuva (NPL 3) ficam em texto corrido nos
manuais e só a leitura com IA (leitor_ia.py) consegue extrair.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # pymupdf

from campo_seguro.nlp.normalize import _load_yaml, chave_normalizada, mapear_acao_tecnica
from campo_seguro.nlp.schema import Orientacao

ORIGEM_DEDICADA = "LEITOR_DEDICADO"
ORIGEM_GENERICA = "LEITURA_GENERICA"

# Fabricantes conhecidos -> sigla usada no código fonte
FABRICANTES = {
    "john deere": "JD", "mahindra": "MAHINDRA", "case ih": "CASE", "case": "CASE", "new holland": "NH",
    "massey ferguson": "MF", "valtra": "VALTRA", "jacto": "JACTO", "stara": "STARA", "kubota": "KUBOTA",
    "agrale": "AGRALE", "fendt": "FENDT", "claas": "CLAAS", "yanmar": "YANMAR", "ls tractor": "LS",
}

# Verbos que abrem uma tarefa de manutenção (sem acento, minúsculo)
VERBOS_TAREFA = (
    "verifique", "verificar", "verificacao", "inspecione", "inspecionar", "inspecao", "troque", "trocar", "troca",
    "substitua", "substituir", "substituicao", "limpe", "limpar", "limpeza", "lubrifique", "lubrificar",
    "lubrificacao", "engraxe", "engraxar", "drene", "drenar", "drenagem", "ajuste", "ajustar", "aperte", "apertar",
    "reaperte", "teste", "testar", "lave", "lavar", "calibre", "calibrar", "abasteca", "complete", "remova",
    "check", "inspect", "change", "replace", "clean", "lubricate", "grease", "drain", "adjust", "tighten",
    "retighten", "test", "wash", "service", "rotate",
)

# NPL 1, seção 3: o que NÃO é manutenção (operação, conforto, avisos genéricos)
EXCLUSOES = (
    "em marcha lenta por", "funcionar em marcha lenta", "deixe o motor em marcha lenta", "let the engine idle",
    "idle for", "ajuste o assento", "ajuste do assento", "seat adjust", "retrovisor", "mirror", "saidas de ar",
    "air vents", "postura", "acelerador de mao", "hand throttle", "mantenha as maos", "keep hands",
)

_DIAS_CALENDARIO = [
    (r"\b(diariamente|diario|diaria|todos os dias|daily|every day)\b", 1, "DIARIO"),
    (r"\b(semanalmente|semanal|weekly|every week)\b", 7, "SEMANAL"),
    (r"\b(mensalmente|mensal|monthly|every month)\b", 30, "MENSAL"),
    (r"\b(semestralmente|semestral|a cada 6 meses|every 6 months)\b", 182, "SEMESTRAL"),
    (r"\b(anualmente|anual|a cada ano|annually|yearly|every year|ano)\b", 365, "ANUAL"),
]
_HORAS = re.compile(r"(?:a cada|cada|every|intervalo de)?\s*(\d[\d.,]*)\s*(?:horas?|h|hrs?|hours?)\b")
_PRIMEIRAS = re.compile(r"\b(?:primeiras?|first|inicia(?:is|l))\s+(\d[\d.,]*)\s*(?:horas?|h|hours?)\b")
_KM = re.compile(r"(?:a cada|cada|every)\s+(\d[\d.,]*)\s*(km|quilometros|milhas|miles|mi)\b")
_ANOS = re.compile(r"\b(?:a cada|every)\s+(\d+)\s*(?:anos|years)\b")
_CONDICIONAL = re.compile(r"\b(conforme (?:o )?necessario|quando necessario|as required|as needed)\b")
_AMACIAMENTO = re.compile(r"\b(amaciamento|break[- ]in)\b")
_PAGINA_REF = re.compile(r"\s*\.{2,}\s*[\w-]*\d[\w-]*\s*$")          # "Troque o óleo ........ 95-A-2"
_NOTAS_FIM = re.compile(r"\.([a-l]{1,2})$")                             # "...do motor.gh" -> notas g, h


@dataclass
class Intervalo:
    metrica: str
    valor: float | None
    unidade: str | None
    fator: str | None
    texto: str


@dataclass
class ResultadoLeitura:
    cod_sugerido: str
    fabricante: str | None
    modelo: str | None
    codigo_manual: str | None
    edicao: str | None
    idioma: str
    nome_documento: str
    leitor: str                      # LEITOR_DEDICADO ou LEITURA_GENERICA
    orientacoes: list[Orientacao] = field(default_factory=list)
    paginas_com_tabela: list[int] = field(default_factory=list)
    total_paginas: int = 0


def _numero(texto: str) -> float:
    texto = texto.strip().rstrip(".,")
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", texto):          # 1.500 / 1,500 = milhar
        return float(re.sub(r"[.,]", "", texto))
    return float(texto.replace(",", "."))


def _formatar(valor: float) -> str:
    return f"{valor:,.0f}".replace(",", ".") if valor >= 1000 else f"{valor:g}"


def identificar_manual(doc: fitz.Document, nome_arquivo: str = "") -> dict:
    """Fabricante, modelo, código e edição a partir das primeiras páginas."""
    capa = "\n".join(doc[i].get_text() for i in range(min(3, len(doc))))
    capa_norm = chave_normalizada(capa)
    fabricante = next((sigla for nome, sigla in FABRICANTES.items() if nome in capa_norm), None)
    if fabricante is None:
        fabricante = next((sigla for nome, sigla in FABRICANTES.items() if nome in chave_normalizada(nome_arquivo)), None)
    codigo = re.search(r"\b(OM[A-Z]{2,5}\d{4,7}|[A-Z]{2,4}\d{5,8})\b", capa)
    codigo = codigo.group(1) if codigo else None
    edicao = re.search(r"\b(?:EDI[CÇ][AÃ]O|EDITION|ED\.)\s+([A-Z]?\d{1,2}|[A-Z]\d?)\b", capa, flags=re.IGNORECASE)
    edicao = edicao.group(1).upper() if edicao else None
    modelos = [m for m in re.findall(r"\b([A-Z]{0,3}\d{3,5}[A-Z]{0,2})\b", capa.split("\n\n")[0] if capa else "")
               if m != codigo and not re.fullmatch(r"\d{6,}", m) and not re.fullmatch(r"(19|20)\d{2}", m)]
    modelo = modelos[0] if modelos else None
    pt = sum(capa_norm.count(p) for p in (" de ", " do ", " para ", " manual do operador", "portuguese"))
    en = sum(capa_norm.count(p) for p in (" the ", " and ", " for ", "operator's manual", "operators manual"))
    return {"fabricante": fabricante, "modelo": modelo, "codigo": codigo, "edicao": edicao,
            "idioma": "EN" if en > pt else "PT"}


def sugerir_codigo(info: dict, conteudo_pdf: bytes = b"") -> str:
    """FABRICANTE-MODELO-CODIGO-EDICAO (o que faltar é omitido; sem código, usa um trecho do SHA-256 do PDF)."""
    partes = [info.get("fabricante") or "MANUAL", info.get("modelo")]
    if info.get("codigo"):
        partes += [info["codigo"], info.get("edicao")]
    else:
        partes.append(hashlib.sha256(conteudo_pdf).hexdigest()[:8].upper() if conteudo_pdf else None)
    return "-".join(p for p in partes if p)[:100]


# Palavras que podem acompanhar um título de intervalo ("Manutenção—A Cada 250 Horas de Operação")
_PALAVRAS_TITULO = set("""a o as os e ou de do da das dos em cada horas hora h hrs hr operacao operacoes primeiras primeira
periodo amaciamento manutencao servico servicos tabela intervalo intervalos lubrificacao periodicidade conforme
necessario quando diariamente diario diaria todos dias semanalmente semanal mensalmente mensal semestralmente
semestral anualmente anual ano anos meses km quilometros milhas o que ocorrer primeiro every hours hour operating
first break in maintenance service interval intervals chart schedule daily weekly monthly yearly annually year years
months as required needed or and the of miles mi whichever comes""".split())


def _eh_titulo(t: str) -> bool:
    """O texto é quase só o intervalo (título), e não uma frase que cita horas."""
    palavras = [p for p in re.split(r"[\s/—–:()-]+", t) if p and not re.fullmatch(r"[\d.,]+", p)]
    return len([p for p in palavras if p not in _PALAVRAS_TITULO]) <= 1


def ler_intervalo(texto: str) -> Intervalo | None:
    """Título de intervalo -> métrica, valor, unidade e condição. None se o texto não é um título de intervalo."""
    t = chave_normalizada(texto)
    if len(t) > 90 or t.split(" ")[0] in VERBOS_TAREFA or not _eh_titulo(t):
        return None
    if _CONDICIONAL.search(t):
        return Intervalo("CONDICIONAL", None, None, None, texto)
    calendario = next(((dias, nome) for padrao, dias, nome in _DIAS_CALENDARIO if re.search(padrao, t)), None)
    anos = _ANOS.search(t)
    if anos:
        calendario = (int(anos.group(1)) * 365, f"{anos.group(1)}_ANOS")
    km = _KM.search(t)
    if km:
        valor = _numero(km.group(1)) * (1.609 if km.group(2) in ("milhas", "miles", "mi") else 1)
        fator = f"OU_CALENDARIO:{calendario[1]}" if calendario else None
        return Intervalo("HODOMETRO", round(valor), "KM", fator, texto)
    primeiras = _PRIMEIRAS.search(t)
    if primeiras:
        return Intervalo("HORIMETRO", _numero(primeiras.group(1)), "HORAS", "AMACIAMENTO_UNICA_VEZ", texto)
    horas = _HORAS.search(t)
    if horas and ("cada" in t or "every" in t or "h" in t.split() or re.search(r"\d\s*h\b", t) or "hora" in t):
        fator = f"OU_CALENDARIO:{calendario[1]}" if calendario else None
        return Intervalo("HORIMETRO", _numero(horas.group(1)), "HORAS", fator, texto)
    if _AMACIAMENTO.search(t):
        return Intervalo("CONDICIONAL", None, None, "AMACIAMENTO_UNICA_VEZ", texto)
    if calendario and ("cada" in t or "every" in t or len(t.split()) <= 3):
        return Intervalo("CALENDARIO", float(calendario[0]), "DIAS", f"OU_CALENDARIO:{calendario[1]}", texto)
    return None


_PRIORIDADE = {"HORIMETRO": 3, "HODOMETRO": 3, "CALENDARIO": 2, "CONDICIONAL": 1}


def _eh_tarefa(linha: str) -> bool:
    """Linha que abre uma tarefa: começa com verbo de manutenção. Passos de procedimento (numerados ou com
    referência de figura, como "(A)") não contam."""
    t = chave_normalizada(linha)
    primeira = t.split(" ")[0] if t else ""
    return (primeira in VERBOS_TAREFA and 12 <= len(linha) <= 160 and not re.match(r"^\d+[.)]", linha)
            and not re.search(r"\([A-Z]\)", linha))


def _eh_excluida(texto: str) -> bool:
    t = chave_normalizada(texto)
    return any(chave_normalizada(e) in t for e in EXCLUSOES)


def subsistema_por_palavras(texto: str, dica: str | None = None) -> str:
    cfg = _load_yaml("subsistemas.yaml")
    alvo = chave_normalizada(texto)
    for palavras, enum in cfg["palavras_chave_gerais"]:
        if any(chave_normalizada(p) in alvo for p in palavras):
            return enum
    if dica:
        dica_norm = chave_normalizada(dica)
        for palavras, enum in cfg["palavras_chave_gerais"]:
            if any(chave_normalizada(p) in dica_norm for p in palavras):
                return enum
    return cfg["default"]


def detalhamento(tarefa: str, intervalo: Intervalo) -> str:
    base = tarefa.rstrip(".").strip()
    if intervalo.metrica == "HORIMETRO" and intervalo.fator == "AMACIAMENTO_UNICA_VEZ":
        sufixo = f"nas primeiras {_formatar(intervalo.valor)} horas (amaciamento, execução única)"
    elif intervalo.metrica == "HORIMETRO":
        sufixo = f"a cada {_formatar(intervalo.valor)} horas"
    elif intervalo.metrica == "HODOMETRO":
        sufixo = f"a cada {_formatar(intervalo.valor)} km"
    elif intervalo.metrica == "CALENDARIO":
        sufixo = {1: "diariamente", 7: "semanalmente", 30: "mensalmente", 182: "semestralmente",
                  365: "anualmente"}.get(int(intervalo.valor), f"a cada {int(intervalo.valor)} dias")
    else:
        sufixo = "conforme necessário"
    texto = f"{base} — {sufixo}"
    if intervalo.fator and intervalo.fator.startswith("OU_CALENDARIO:") and intervalo.metrica != "CALENDARIO":
        texto += f" (ou {intervalo.fator.split(':', 1)[1].replace('_', ' ').lower()}, o que ocorrer primeiro)"
    return texto + "."


def leitura_generica(doc: fitz.Document, cod: str, nome_doc: str, idioma: str) -> tuple[list[Orientacao], list[int]]:
    orientacoes: list[Orientacao] = []
    paginas_tabela: list[int] = []
    vistos: set[tuple] = set()
    intervalo: Intervalo | None = None
    cabecalho: str | None = None
    itens_pagina_anterior = 0
    for indice, pagina in enumerate(doc):
        linhas = [l.strip() for l in pagina.get_text().splitlines() if l.strip()]
        if itens_pagina_anterior < 3:        # o título só vale na página seguinte se a tabela continuar
            intervalo, cabecalho = None, None
        if not linhas or sum(1 for l in linhas if re.search(r"\.{4,}", l)) > 0.2 * len(linhas):
            itens_pagina_anterior = 0        # página vazia ou sumário ("Troque o óleo ........ 95-A-2")
            continue
        inicio_pagina = len(orientacoes)
        linha_inicio_tabela = 0 if intervalo else None
        chaves_pagina: list[tuple] = []
        linhas_tabela = 0                    # títulos + tarefas + cabeçalhos curtos (o resto é texto corrido)
        itens_pagina = 0
        i = 0
        while i < len(linhas):
            # título de intervalo (pode quebrar em até 3 linhas: "Semanalmente ou a Cada 50" / "Horas")
            # o título pode quebrar em até 3 linhas ("Mensalmente ou a Cada 200" / "Horas de Operação"): fica o
            # trecho mais completo (horas/km antes de calendário), sem engolir tarefas
            achado, usado = None, 0
            rodape = i >= len(linhas) - 3      # título repetido no rodapé da página não abre uma tabela
            for span in (() if rodape else (1, 2, 3)):
                if span > 1 and (i + span > len(linhas) or any(_eh_tarefa(l) for l in linhas[i + 1:i + span])):
                    break
                trecho = _PAGINA_REF.sub("", " ".join(linhas[i:i + span]))
                candidato = ler_intervalo(trecho) or ler_intervalo(re.sub(r"(?<=[a-zà-ú])[a-l]$", "", trecho))
                if candidato and (achado is None or _PRIORIDADE[candidato.metrica] > _PRIORIDADE[achado.metrica]):
                    achado, usado = candidato, span
            if achado:
                if linha_inicio_tabela is None:
                    linha_inicio_tabela = i
                intervalo, cabecalho = achado, None
                linhas_tabela += usado
                i += usado
                continue
            linha = _PAGINA_REF.sub("", linhas[i])
            if intervalo and _eh_tarefa(linha):
                partes = [linha]
                j = i + 1
                while (not partes[-1].rstrip().endswith(".") and not _NOTAS_FIM.search(partes[-1]) and j < len(linhas)
                       and len(partes) < 3 and not _eh_tarefa(linhas[j]) and not ler_intervalo(linhas[j])
                       and len(linhas[j]) > 2):
                    partes.append(_PAGINA_REF.sub("", linhas[j]))
                    j += 1
                tarefa = re.sub(r"\s+", " ", " ".join(partes)).strip()
                notas = _NOTAS_FIM.search(tarefa)
                fator = intervalo.fator
                if notas:
                    tarefa = tarefa[:notas.start() + 1]
                    fator = f"{fator + ' + ' if fator else ''}NOTA_{'/'.join(notas.group(1))}"
                chave = (chave_normalizada(tarefa), intervalo.metrica, intervalo.valor)
                itens_pagina += 1
                if not _eh_excluida(tarefa) and chave not in vistos:
                    vistos.add(chave)
                    chaves_pagina.append(chave)
                    orientacoes.append(Orientacao(
                        cod_fonte_manual=cod, nome_documento_origem=nome_doc, idioma_origem=idioma,
                        tipo_orientacao="MANUTENCAO_PROGRAMADA",
                        subsistema=subsistema_por_palavras(tarefa, cabecalho),
                        acao_tecnica=mapear_acao_tecnica(tarefa),
                        detalhamento_orientacao=detalhamento(tarefa, intervalo),
                        metrica_gatilho=intervalo.metrica, valor_gatilho=intervalo.valor,
                        unidade_medida=intervalo.unidade, fator_condicional=fator,
                        texto_bruto_original=f"[{intervalo.texto.strip()}] {' / '.join(partes)}"[:4000],
                        pagina_origem=indice + 1, origem_extracao=ORIGEM_GENERICA))
                linhas_tabela += j - i
                i = j
                continue
            # cabeçalho de sistema da tabela (ex.: "Motor", "Sistema Hidráulico"): curto, sem verbo e sem ponto
            if intervalo and len(linha) <= 60 and not linha.endswith(".") and not _eh_tarefa(linha):
                cabecalho = linha
                linhas_tabela += 1 if len(linha) <= 40 else 0
            i += 1
        # página de tabela: ao menos 3 tarefas e a maior parte das linhas é título/tarefa/cabeçalho
        linhas_uteis = sum(1 for l in linhas[linha_inicio_tabela or 0:] if len(l) > 2)   # legendas/marcadores não contam
        if itens_pagina < 3 or linhas_tabela < 0.6 * linhas_uteis:
            vistos.difference_update(chaves_pagina)
            del orientacoes[inicio_pagina:]
            itens_pagina = 0
        if itens_pagina:
            paginas_tabela.append(indice + 1)
        itens_pagina_anterior = itens_pagina
    return orientacoes, paginas_tabela


def ler_manual(caminho_pdf: str | Path, conteudo_pdf: bytes | None = None, usar_dedicado: bool = True) -> ResultadoLeitura:
    """Lê o manual e devolve as recomendações de manutenção. `usar_dedicado=False` força a leitura genérica
    (útil para comparar com o leitor próprio do manual)."""
    from campo_seguro.nlp.parse_interval_tables import (CH950_COD_FONTE, CH950_NOME_DOC, TRATOR_5060E_COD_FONTE,
                                                        TRATOR_5060E_NOME_DOC, parse_5060e, parse_ch950)
    caminho_pdf = Path(caminho_pdf)
    conteudo_pdf = conteudo_pdf if conteudo_pdf is not None else caminho_pdf.read_bytes()
    doc = fitz.open(caminho_pdf)
    try:
        info = identificar_manual(doc, caminho_pdf.name)
        total = len(doc)
    finally:
        doc.close()
    dedicados = {"OMCXT31163": (parse_ch950, CH950_COD_FONTE, CH950_NOME_DOC),
                 "OMTR132548": (parse_5060e, TRATOR_5060E_COD_FONTE, TRATOR_5060E_NOME_DOC)}
    if usar_dedicado and info["codigo"] in dedicados:
        leitor, cod, nome_doc = dedicados[info["codigo"]]
        orientacoes = leitor(caminho_pdf)
        for o in orientacoes:
            o.origem_extracao = ORIGEM_DEDICADA
        return ResultadoLeitura(cod, info["fabricante"], info["modelo"], info["codigo"], info["edicao"], "PT",
                                nome_doc, ORIGEM_DEDICADA, orientacoes,
                                sorted({o.pagina_origem for o in orientacoes if o.pagina_origem}), total)
    cod = sugerir_codigo(info, conteudo_pdf)
    nome_doc = caminho_pdf.name
    doc = fitz.open(caminho_pdf)
    try:
        orientacoes, paginas = leitura_generica(doc, cod, nome_doc, info["idioma"])
    finally:
        doc.close()
    for o in orientacoes:
        o.validar()
    return ResultadoLeitura(cod, info["fabricante"], info["modelo"], info["codigo"], info["edicao"], info["idioma"],
                            nome_doc, ORIGEM_GENERICA, orientacoes, paginas, total)
