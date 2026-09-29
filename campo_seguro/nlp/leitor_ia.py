"""Leitura de manuais com IA paga (Claude Sonnet 5, da Anthropic), com gasto controlado.

Extrai os três NPLs da Nádia (prompts/npl1_*.md, npl2_*.md, npl3_*.md):
  NPL 1 MANUTENCAO_PROGRAMADA · NPL 2 ALERTA_TEMP_MINIMA / ALERTA_TEMP_MAXIMA · NPL 3 RISCO_CHUVA_DESLIZE

Controle do gasto (nenhum gasto sem autorização e acima do limite):
1. Nada é enviado sem a chave da API da Anthropic digitada na hora (o código não lê chave de variável de ambiente,
   arquivo ou perfil) e sem o limite em dólares informado pelo usuário.
2. Só vão para a IA as páginas selecionadas por palavras-chave de cada NPL (não o manual inteiro), em lotes.
3. Antes de cada chamada, calcula o custo MÁXIMO possível do lote (entrada estimada por cima + saída máxima
   permitida). Se o gasto já feito mais esse máximo passar do limite, a leitura para ali — nunca ultrapassa.
4. Depois de cada chamada, registra o custo real (tokens informados pela própria API).
5. Sem novas tentativas automáticas em caso de erro (max_retries=0): uma falha não vira gasto repetido.

"Aprendizado": as recomendações rejeitadas pelos revisores (tabela CS_NLP_REVISOES) entram nas instruções como
exemplos do que NÃO extrair.
"""
from __future__ import annotations

import re
import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

import fitz  # pymupdf
from pydantic import BaseModel

from campo_seguro.nlp import schema as sch
from campo_seguro.nlp.schema import Orientacao

MODELO_IA = "claude-sonnet-5"
# Preço por milhão de tokens (US$): entrada, saída — tabela da Anthropic para o Claude Sonnet 5
PRECO_POR_MILHAO = {MODELO_IA: (2.00, 10.00)}
MAX_TOKENS_SAIDA = 16000             # teto de saída por chamada (entra no custo máximo)
CARACTERES_POR_TOKEN = 2.5           # estimativa por cima (o real fica perto de 3 a 4): superestima o custo
CARACTERES_POR_LOTE = 40000          # texto de páginas por chamada (os três temas saem na mesma resposta)
MAX_PAGINAS_PADRAO = 200             # teto de páginas enviadas numa leitura (as mais relevantes primeiro)
MIN_INDICIOS = {"NPL1": 3, "NPL2": 1, "NPL3": 1}   # indícios na página para ela contar como relevante ao tema
ORIGEM_IA = "IA_CLAUDE_SONNET_5"

PASTA_PROMPTS = Path(__file__).resolve().parents[2] / "prompts"
ARQUIVOS_NPL = {"NPL1": "npl1_manutencao_programada.md", "NPL2": "npl2_temperatura_extrema.md",
                "NPL3": "npl3_chuva_excessiva.md"}
TIPOS_DO_NPL = {"NPL1": {"MANUTENCAO_PROGRAMADA"}, "NPL2": {"ALERTA_TEMP_MINIMA", "ALERTA_TEMP_MAXIMA"},
                "NPL3": {"RISCO_CHUVA_DESLIZE"}}
NOMES_NPL = {"NPL1": "NPL 1 - Manutenções programadas", "NPL2": "NPL 2 - Temperatura extrema",
             "NPL3": "NPL 3 - Chuva excessiva"}

# Palavras que indicam páginas úteis para cada NPL (português e inglês), sem acento e minúsculas
PALAVRAS_NPL = {
    "NPL1": r"a cada \d|every \d|\d\s*(h|horas|hours)\b|diariamente|daily|semanalmente|weekly|anualmente|annually|"
            r"primeiras \d|break-in|amaciamento|intervalo|interval|hodometro|odometer|\d\s*km\b|lubrifi|lubricat|"
            r"troque o oleo|change the oil|substitua o filtro|replace the filter|altitude|condicoes severas|severe",
    "NPL2": r"temperatura ambiente|ambient temperature|abaixo de -?\d+\s*°?\s*c|below -?\d+\s*°|acima de \d+\s*°?\s*c|"
            r"above \d+\s*°|frio|cold weather|calor|hot weather|°f|congel|freez|geada|frost|aquecedor|heater|glow|"
            r"pre-?aquec|antigelifica|anti-?gel|inverno|winter|clima quente",
    "NPL3": r"chuva|rain|molhad|wet\b|lama|lamacent|mud|escorreg|slipper|atola|tracao dianteira|\btda\b|mfwd|4wd|"
            r"pedais de freio|brake pedal|aclive|declive|slope|incline|tombamento|capotamento|rollover|tip.?over|"
            r"visibilidade|visibility|derrap|skid",
}


class ItemExtraido(BaseModel):
    """Uma recomendação extraída pela IA (formato garantido pela saída estruturada da API)."""
    tipo_orientacao: Literal["MANUTENCAO_PROGRAMADA", "ALERTA_TEMP_MINIMA", "ALERTA_TEMP_MAXIMA", "RISCO_CHUVA_DESLIZE"]
    subsistema: Literal[sch.SUBSISTEMA]
    acao_tecnica: Literal[sch.ACAO_TECNICA]
    detalhamento_orientacao: str
    metrica_gatilho: Optional[Literal[sch.METRICA_GATILHO]]
    valor_gatilho: Optional[float]
    unidade_medida: Optional[Literal[sch.UNIDADE_MEDIDA]]
    fator_condicional: Optional[str]
    texto_bruto_original: str
    pagina_origem: int
    idioma_origem: Literal["PT", "EN"]


class RespostaExtracao(BaseModel):
    itens: list[ItemExtraido]


@dataclass
class Lote:
    npls: tuple[str, ...]            # temas extraídos juntos nesta chamada
    paginas: list[int]
    texto: str

    @property
    def tokens_entrada_estimados(self) -> int:
        return int((len(self.texto) + len(instrucoes(self.npls))) / CARACTERES_POR_TOKEN) + 2000

    @property
    def custo_maximo(self) -> float:
        return custo(self.tokens_entrada_estimados, MAX_TOKENS_SAIDA)


@dataclass
class Chamada:
    npl: str                         # temas da chamada, ex.: "NPL1+NPL2+NPL3"
    paginas: list[int]
    tokens_entrada: int
    tokens_saida: int
    custo_usd: float
    itens: int
    situacao: str                    # "ok", "resposta cortada", "recusada", "erro: ..."


@dataclass
class ResultadoIA:
    orientacoes: list[Orientacao] = field(default_factory=list)
    chamadas: list[Chamada] = field(default_factory=list)
    interrompida: str | None = None  # motivo, se parou antes do fim (limite, erro)

    @property
    def custo_total(self) -> float:
        return round(sum(c.custo_usd for c in self.chamadas), 4)


def custo(tokens_entrada: int, tokens_saida: int, modelo: str = MODELO_IA) -> float:
    preco_entrada, preco_saida = PRECO_POR_MILHAO[modelo]
    return round(tokens_entrada * preco_entrada / 1e6 + tokens_saida * preco_saida / 1e6, 4)


def _sem_acento(texto: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def nome_temas(npls) -> str:
    """('NPL1', 'NPL3') -> 'NPL1+NPL3' (gravado no registro de uso da IA)."""
    return "+".join(npls)


def selecionar_paginas(caminho_pdf: str | Path, npls: list[str], max_paginas: int | None = None) -> dict:
    """Uma leitura só para todos os temas: cada página relevante a PELO MENOS UM dos temas escolhidos é enviada uma
    vez, e a IA separa os temas na mesma resposta. Se houver mais páginas relevantes que o máximo, ficam as de mais
    indícios. Devolve {"paginas": [...], "por_npl": {npl: páginas relevantes}, "relevantes", "total": páginas do
    manual, "com_texto": páginas com texto (0 = PDF digitalizado, só imagem)}."""
    max_paginas = max_paginas or MAX_PAGINAS_PADRAO
    doc = fitz.open(caminho_pdf)
    try:
        textos = [_sem_acento(p.get_text()) for p in doc]
    finally:
        doc.close()
    padroes = {npl: re.compile(PALAVRAS_NPL[npl]) for npl in npls}
    pontos, por_npl = {}, {npl: 0 for npl in npls}
    for i, texto in enumerate(textos):
        nota = 0.0
        for npl, padrao in padroes.items():
            achados = len(padrao.findall(texto))
            if achados >= MIN_INDICIOS[npl]:
                por_npl[npl] += 1
                nota += achados / MIN_INDICIOS[npl]
        if nota:
            pontos[i + 1] = nota
    escolhidas = sorted(pontos, key=lambda pagina: -pontos[pagina])[:max_paginas]
    return {"paginas": sorted(escolhidas), "por_npl": por_npl, "relevantes": len(pontos), "total": len(textos),
            "com_texto": sum(1 for t in textos if len(t.strip()) > 50)}


def montar_lotes(caminho_pdf: str | Path, npls: list[str], paginas: list[int]) -> list[Lote]:
    """Junta as páginas em lotes de até CARACTERES_POR_LOTE (cada página vai uma vez só)."""
    doc = fitz.open(caminho_pdf)
    try:
        lotes, atual, texto = [], [], ""
        for pagina in paginas:
            conteudo = re.sub(r"[ \t]+", " ", doc[pagina - 1].get_text()).strip()
            trecho = f"[Página {pagina}]\n{conteudo}\n\n"
            if atual and len(texto) + len(trecho) > CARACTERES_POR_LOTE:
                lotes.append(Lote(tuple(npls), atual, texto))
                atual, texto = [], ""
            atual.append(pagina)
            texto += trecho
        if atual:
            lotes.append(Lote(tuple(npls), atual, texto))
        return lotes
    finally:
        doc.close()


def estimativa(lotes: list[Lote]) -> dict:
    """Custo máximo possível (garantido pelo teto de saída) e uma estimativa típica."""
    maximo = round(sum(l.custo_maximo for l in lotes), 4)
    tipico = round(sum(custo(int(l.tokens_entrada_estimados * 0.75), 3000) for l in lotes), 4)
    return {"lotes": len(lotes), "paginas": sum(len(l.paginas) for l in lotes), "custo_maximo": maximo,
            "custo_tipico": tipico}


def instrucoes(npls, exemplos_negativos: dict | None = None) -> str:
    """Instruções da chamada: as dos temas escolhidos juntas, extraídos numa leitura só."""
    npls = [npls] if isinstance(npls, str) else list(npls)
    texto = ""
    if len(npls) > 1:
        texto = ("Você vai ler as páginas UMA vez e extrair, na mesma resposta, as recomendações dos temas abaixo: "
                 + "; ".join(NOMES_NPL[n] for n in npls) + ". Cada item recebe o tipo_orientacao do seu tema. "
                 "Os critérios de exclusão de cada tema valem só para aquele tema: por exemplo, uma instrução de "
                 "condução em piso molhado não é manutenção programada (NPL 1), mas deve ser extraída como risco de "
                 "chuva (NPL 3). Uma mesma frase pode gerar itens de temas diferentes.\n\n")
    for npl in npls:
        texto += (PASTA_PROMPTS / ARQUIVOS_NPL[npl]).read_text(encoding="utf-8") + "\n\n"
    texto += (PASTA_PROMPTS / "regras_saida.md").read_text(encoding="utf-8")
    negativos = [e for npl in npls for e in (exemplos_negativos or {}).get(npl, [])]
    if negativos:
        texto += ("\n\nAprendizado com as revisões anteriores: os revisores REJEITARAM os itens abaixo. "
                  "NÃO extraia itens iguais ou parecidos:\n" + "\n".join(f"- {e}" for e in dict.fromkeys(negativos)))
    return texto


# Início de um serviço nas tabelas de manutenção (verbo no imperativo ou substantivo, com inicial maiúscula)
_INICIO_SERVICO = (r"(?:Verifique|Verificação|Verificar|Troque|Troca|Trocar|Substitua|Substituição|Substituir|"
                   r"Lubrifique|Lubrificação|Lubrificar|Limpe|Limpeza|Limpar|Drene|Drenagem|Drenar|Ajuste|Ajustar|"
                   r"Aperte|Apertar|Inspecione|Inspeção|Inspecionar|Regule|Regulagem|Regular|Calibre|Calibrar|"
                   r"Engraxe|Complete|Teste|Reabasteça|Examine)\b")
# Divisão: espaço (ou marca de rodapé colada à palavra anterior) seguido do início de outro serviço
_DIVISAO_SERVICOS = re.compile(r"(?<=[a-záéíóúâêôãõç)])\s+(?=" + _INICIO_SERVICO + ")")
# Marca de nota de rodapé colada ao fim da palavra: "dianteirof" -> "dianteiro" (palavra terminada em vogal seguida de
# consoante que o português não usa no fim de palavra); não mexe em palavras terminadas em a/e (ambíguas)
_RODAPE_NO_FIM = re.compile(r"(?<=[a-záéíóúâêôãõ]{3}[aeiouáéíóúâêôãõ])[bcdfghj](?=[\s.,;:)]|$)")
# ... e colada a uma sigla: "TDPb" -> "TDP"
_RODAPE_NA_SIGLA = re.compile(r"(?<=\b[A-Z]{2})(?<=[A-Z]{2})([A-Z]*)[a-j](?=[\s.,;:)]|$)")


def limpar_rodape(texto: str) -> str:
    """Remove as letras de nota de rodapé coladas às palavras (ex.: 'eixo dianteirof' -> 'eixo dianteiro')."""
    return _RODAPE_NA_SIGLA.sub(r"\1", _RODAPE_NO_FIM.sub("", texto))


def separar_servicos(detalhamento: str) -> list[str]:
    """Um detalhamento com vários serviços emendados (ex.: 'Verifique os rolamentos ... Verificação do pino ... —
    a cada 400 horas.') vira um por serviço, cada um com a periodicidade do fim. Sem emenda, devolve [limpo]."""
    base, separador, periodicidade = detalhamento.partition(" — ")
    partes = [limpar_rodape(p).strip(" .;") for p in _DIVISAO_SERVICOS.split(limpar_rodape(base).strip())]
    partes = [p for p in partes if p]
    if len(partes) < 2 or any(len(p.split()) < 3 for p in partes):
        partes = [limpar_rodape(base).strip()]
    return [p + (separador + periodicidade if separador else "") for p in partes]


def _para_orientacao(item: ItemExtraido, cod: str, nome_doc: str) -> Orientacao:
    unidade = item.unidade_medida
    if item.valor_gatilho is not None and unidade is None and item.metrica_gatilho in sch.UNIDADE_DA_METRICA:
        unidade = sch.UNIDADE_DA_METRICA[item.metrica_gatilho]
    fator = (item.fator_condicional or "").strip() or None
    o = Orientacao(cod_fonte_manual=cod, nome_documento_origem=nome_doc, idioma_origem=item.idioma_origem,
                   tipo_orientacao=item.tipo_orientacao, subsistema=item.subsistema, acao_tecnica=item.acao_tecnica,
                   detalhamento_orientacao=item.detalhamento_orientacao.strip(),
                   metrica_gatilho=item.metrica_gatilho, valor_gatilho=item.valor_gatilho, unidade_medida=unidade,
                   fator_condicional=fator[:255] if fator else None,
                   texto_bruto_original=item.texto_bruto_original.strip()[:4000] or item.detalhamento_orientacao,
                   pagina_origem=item.pagina_origem, origem_extracao=ORIGEM_IA)
    o.validar()
    return o


def extrair(chave_api: str, cod: str, nome_doc: str, lotes: list[Lote], limite_usd: float,
            exemplos_negativos: dict[str, list[str]] | None = None, cliente=None,
            ao_concluir_chamada=None) -> ResultadoIA:
    """Envia os lotes à IA respeitando o limite. `ao_concluir_chamada(chamada)` é chamado após cada chamada (para
    registrar o gasto no banco e mostrar o andamento). `cliente` só é usado nos testes."""
    if not chave_api or not chave_api.strip():
        raise ValueError("Informe a chave da API da Anthropic para autorizar o gasto.")
    if limite_usd is None or limite_usd <= 0:
        raise ValueError("Informe um limite de gasto maior que zero.")
    if cliente is None:
        import anthropic
        # chave explícita: nunca usa credencial do ambiente; sem novas tentativas automáticas
        cliente = anthropic.Anthropic(api_key=chave_api.strip(), max_retries=0, timeout=300.0)
    resultado = ResultadoIA()
    vistos = set()
    for lote in lotes:
        if resultado.custo_total + lote.custo_maximo > limite_usd:
            resultado.interrompida = (f"Parou antes das páginas {lote.paginas[0]} a "
                                      f"{lote.paginas[-1]}: o gasto poderia passar do limite de US$ "
                                      + f"{limite_usd:.2f}".replace(".", ",") + ".")
            break
        try:
            resposta = cliente.messages.parse(
                model=MODELO_IA,
                max_tokens=MAX_TOKENS_SAIDA,
                thinking={"type": "disabled"},          # extração direta: sem tokens de raciocínio cobrados
                system=instrucoes(lote.npls, exemplos_negativos),
                messages=[{"role": "user", "content":
                           f"Manual {cod} ({nome_doc}). Extraia as recomendações de: "
                           + "; ".join(NOMES_NPL[n] for n in lote.npls)
                           + f", das páginas abaixo.\n\n{lote.texto}"}],
                output_format=RespostaExtracao,
            )
        except Exception as erro:                       # erro de chave, rede, limite da conta...
            resultado.chamadas.append(Chamada(nome_temas(lote.npls), lote.paginas, 0, 0, 0.0, 0, f"erro: {erro}"))
            resultado.interrompida = f"A IA devolveu um erro: {erro}"
            if ao_concluir_chamada:
                ao_concluir_chamada(resultado.chamadas[-1])
            break
        uso = resposta.usage
        tokens_entrada = (uso.input_tokens or 0) + (getattr(uso, "cache_creation_input_tokens", 0) or 0) \
            + (getattr(uso, "cache_read_input_tokens", 0) or 0)
        situacao, itens = "ok", []
        if resposta.stop_reason == "refusal":
            situacao = "recusada"
        elif resposta.stop_reason == "max_tokens":
            situacao = "resposta cortada (lote grande demais)"
        elif resposta.parsed_output is not None:
            itens = resposta.parsed_output.itens
        chamada = Chamada(nome_temas(lote.npls), lote.paginas, tokens_entrada, uso.output_tokens or 0,
                          custo(tokens_entrada, uso.output_tokens or 0), 0, situacao)
        tipos_pedidos = set().union(*(TIPOS_DO_NPL[n] for n in lote.npls))
        for item in itens:
            if item.tipo_orientacao not in tipos_pedidos or item.pagina_origem not in lote.paginas:
                continue                                 # tema não pedido ou página que não foi enviada
            try:
                o = _para_orientacao(item, cod, nome_doc)
            except ValueError:
                continue
            # vários serviços emendados num item só (tabelas de manutenção): um item por serviço, sem notas de rodapé
            for detalhe in separar_servicos(o.detalhamento_orientacao):
                servico = dataclasses.replace(o, detalhamento_orientacao=detalhe)
                chave = (servico.tipo_orientacao, servico.detalhamento_orientacao.lower(), servico.valor_gatilho)
                if chave not in vistos:
                    vistos.add(chave)
                    resultado.orientacoes.append(servico)
                    chamada.itens += 1
        resultado.chamadas.append(chamada)
        if ao_concluir_chamada:
            ao_concluir_chamada(chamada)
    return resultado
