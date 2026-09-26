"""Extrator determinístico de limiares de sensor -> tipo_orientacao = LIMIAR_OPERACIONAL_SENSOR.

Kickoff (docs/prompt_claude_code.md, item 1.2): regex sobre o texto das
páginas dos manuais (zonas de instrumento, limites físicos). Cada fato aqui
tem uma âncora de página real (via mapa_codigo_pagina) e uma regex que
extrai o(s) número(s) do texto — se o texto do manual mudar e a regex não
casar mais, a função levanta erro em vez de inventar o valor.

Só inclui fatos que consegui confirmar com página real. Os que a memória
menciona mas eu não localizei com página certa (DEF congela/degrada e
diesel <-10 °C/-20 °C especificamente no 5060E) ficam de fora daqui e vão
para PENDENCIAS no export — ver docs/memoria.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import fitz

from campo_seguro.nlp.parse_interval_tables import (
    CH950_COD_FONTE,
    CH950_NOME_DOC,
    TRATOR_5060E_COD_FONTE,
    TRATOR_5060E_NOME_DOC,
    mapa_codigo_pagina,
)
from campo_seguro.nlp.schema import Orientacao


def _num(texto: str) -> float:
    return float(texto.replace(".", "").replace(",", "."))


def _ws(texto: str) -> str:
    """Colapsa quebras de linha/espaços múltiplos em um só espaço.

    Necessário porque os regex multi-linha (re.DOTALL) capturam o texto tal
    como quebra nas páginas do PDF, com \\n literais no meio — isso quebra
    export.py (CSV/JSON toleram, mas o .sql fica com string cortada em
    múltiplas linhas físicas, o que quebra um parser ingênuo linha-a-linha
    do arquivo gerado).
    """
    return re.sub(r"\s+", " ", texto).strip()


def _texto_paginas(doc: fitz.Document, mapa: dict[str, int], *codigos: str) -> tuple[str, int]:
    """Concatena o texto das páginas pedidas; retorna (texto, pdf_page_da_primeira)."""
    partes = []
    primeira_pagina = None
    for codigo in codigos:
        idx = mapa.get(codigo)
        if idx is None:
            raise RuntimeError(f"Página '{codigo}' não encontrada no manual")
        if primeira_pagina is None:
            primeira_pagina = idx
        partes.append(doc[idx].get_text())
    return "\n".join(partes), primeira_pagina + 1


def _limiar(
    *,
    cod_fonte: str,
    nome_doc: str,
    detalhamento: str,
    metrica_gatilho: str,
    valor_gatilho: float,
    unidade_medida: str,
    criticidade: str,
    sensor_iot: str | None,
    pagina_origem: int,
    texto_bruto_original: str,
) -> Orientacao:
    return Orientacao(
        cod_fonte_manual=cod_fonte,
        nome_documento_origem=nome_doc,
        tipo_orientacao="LIMIAR_OPERACIONAL_SENSOR",
        subsistema="MOTOR",  # ajustado por chamada quando fizer sentido (ex. HIDRAULICO)
        acao_tecnica="VERIFICAR",
        detalhamento_orientacao=detalhamento,
        metrica_gatilho=metrica_gatilho,
        valor_gatilho=valor_gatilho,
        unidade_medida=unidade_medida,
        texto_bruto_original=_ws(texto_bruto_original),
        pagina_origem=pagina_origem,
        criticidade=criticidade,
        sensor_iot=sensor_iot,
    )


def extrair_limiares_ch950(caminho_pdf: str | Path) -> list[Orientacao]:
    doc = fitz.open(caminho_pdf)
    try:
        mapa = mapa_codigo_pagina(doc)
        texto_25, pag_25 = _texto_paginas(doc, mapa, "25-6", "25-7")
        texto_45, pag_45 = _texto_paginas(doc, mapa, "45-1", "45-2", "45-3")

        resultados: list[Orientacao] = []

        m = re.search(r"verde:\s*64[—–-]112\s*°C.*?vermelha:\s*113[—–-]116\s*°C", texto_25, re.DOTALL)
        if not m:
            raise RuntimeError("Padrão de temperatura do motor não encontrado em 25-6/25-7")
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Temperatura do motor acima de 113 °C indica superaquecimento — parar e investigar.",
            metrica_gatilho="TEMPERATURA_MOTOR", valor_gatilho=113.0, unidade_medida="°C",
            criticidade="CRITICA", sensor_iot="TEMP_MOTOR", pagina_origem=pag_25, texto_bruto_original=_ws(m.group(0)),
        ))

        m = re.search(r"verde:\s*-18°C[—–-]92°C.*?vermelha:\s*93°C[—–-]102°C", texto_25, re.DOTALL)
        if not m:
            raise RuntimeError("Padrão de temperatura do óleo hidráulico não encontrado em 25-6/25-7")
        resultados.append(Orientacao(
            cod_fonte_manual=CH950_COD_FONTE, nome_documento_origem=CH950_NOME_DOC,
            tipo_orientacao="LIMIAR_OPERACIONAL_SENSOR", subsistema="HIDRAULICO", acao_tecnica="VERIFICAR",
            detalhamento_orientacao="Temperatura do óleo hidráulico acima de 93 °C indica superaquecimento — parar e investigar.",
            metrica_gatilho="TEMPERATURA_HIDRAULICO", valor_gatilho=93.0, unidade_medida="°C",
            texto_bruto_original=_ws(m.group(0)), pagina_origem=pag_25, criticidade="CRITICA", sensor_iot="TEMP_HIDRAULICO",
        ))

        m = re.search(r"verde:\s*35[—–-]100%.*?amarela:\s*101[—–-]110%.*?vermelha:\s*111[—–-]114%", texto_25, re.DOTALL)
        if not m:
            raise RuntimeError("Padrão de carga do motor (potenciômetro) não encontrado em 25-6/25-7")
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Carga do motor acima de 111% (zona vermelha) — reduzir carga imediatamente.",
            metrica_gatilho="CARGA_MOTOR", valor_gatilho=111.0, unidade_medida="%",
            criticidade="CRITICA", sensor_iot="CARGA_MOTOR", pagina_origem=pag_25, texto_bruto_original=_ws(m.group(0)),
        ))
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Carga do motor entre 101% e 110% (zona amarela) — atenção, aproximando do limite.",
            metrica_gatilho="CARGA_MOTOR", valor_gatilho=101.0, unidade_medida="%",
            criticidade="ALTA", sensor_iot="CARGA_MOTOR", pagina_origem=pag_25, texto_bruto_original=_ws(m.group(0)),
        ))

        m = re.search(r"n[ií]vel atingir 10% de.*?combust[ií]vel restante", texto_25, re.DOTALL)
        if not m:
            raise RuntimeError("Padrão de nível de combustível 10% não encontrado em 25-6/25-7")
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Nível de combustível abaixo de 10% — reabastecer em breve.",
            metrica_gatilho="NIVEL_COMBUSTIVEL", valor_gatilho=10.0, unidade_medida="%",
            criticidade="MEDIA", sensor_iot=None, pagina_origem=pag_25, texto_bruto_original=_ws(m.group(0)),
        ))

        m = re.search(r"n[ií]vel atinge 10%.*?n[ií]vel atinge 0%", texto_25, re.DOTALL)
        if not m:
            raise RuntimeError("Padrão de nível de DEF 10%/0% não encontrado em 25-6/25-7")
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Nível de DEF (fluido de escapamento) abaixo de 10% — abastecer.",
            metrica_gatilho="NIVEL_DEF", valor_gatilho=10.0, unidade_medida="%",
            criticidade="ALTA", sensor_iot=None, pagina_origem=pag_25, texto_bruto_original=_ws(m.group(0)),
        ))
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Nível de DEF em 0% — potência do motor fica limitada até reabastecer.",
            metrica_gatilho="NIVEL_DEF", valor_gatilho=0.0, unidade_medida="%",
            criticidade="CRITICA", sensor_iot=None, pagina_origem=pag_25, texto_bruto_original=_ws(m.group(0)),
        ))

        m = re.search(r"0[—–-]25\.000\s*kPa\s*\(0[—–-]250\s*bar\)", texto_25)
        if not m:
            raise RuntimeError("Padrão de pressão do cortador de base não encontrado em 25-6/25-7")
        resultados.append(Orientacao(
            cod_fonte_manual=CH950_COD_FONTE, nome_documento_origem=CH950_NOME_DOC,
            tipo_orientacao="LIMIAR_OPERACIONAL_SENSOR", subsistema="CORTE_PROCESSAMENTO", acao_tecnica="VERIFICAR",
            detalhamento_orientacao="Faixa normal de pressão do cortador de base: 0 a 250 bar.",
            metrica_gatilho="PRESSAO_CORTADOR_BASE", valor_gatilho=250.0, unidade_medida="bar",
            texto_bruto_original=_ws(m.group(0)), pagina_origem=pag_25, criticidade="MEDIA", sensor_iot=None,
        ))

        m = re.search(r"marcha lenta (?:de|por) (\d)\s*(?:a|minutos)?\s*(?:a\s*)?5\s*minutos", texto_45)
        if not m:
            raise RuntimeError("Padrão de marcha lenta 3-5 min não encontrado em 45-1/45-2/45-3")
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Deixar o motor em marcha lenta de 3 a 5 minutos antes de aumentar rotação ou desligar.",
            metrica_gatilho="TEMPO_MARCHA_LENTA", valor_gatilho=5.0, unidade_medida="min",
            criticidade="BAIXA", sensor_iot=None, pagina_origem=pag_45, texto_bruto_original=_ws(m.group(0)),
        ))

        m = re.search(r"temperatura normal de opera[cç][aã]o entre 77 e 90\s*°C", texto_45)
        if not m:
            raise RuntimeError("Padrão de temperatura normal de operação 77-90°C não encontrado em 45-1/45-2/45-3")
        resultados.append(_limiar(
            cod_fonte=CH950_COD_FONTE, nome_doc=CH950_NOME_DOC,
            detalhamento="Temperatura normal de operação do motor: 77 a 90 °C.",
            metrica_gatilho="TEMPERATURA_MOTOR", valor_gatilho=90.0, unidade_medida="°C",
            criticidade="BAIXA", sensor_iot="TEMP_MOTOR", pagina_origem=pag_45, texto_bruto_original=_ws(m.group(0)),
        ))

        return resultados
    finally:
        doc.close()


def extrair_limiares_5060e(caminho_pdf: str | Path) -> list[Orientacao]:
    doc = fitz.open(caminho_pdf)
    try:
        mapa = mapa_codigo_pagina(doc)
        texto_220, pag_220 = _texto_paginas(doc, mapa, "220-3")
        texto_205_10, pag_205_10 = _texto_paginas(doc, mapa, "205-10")
        texto_205_4, pag_205_4 = _texto_paginas(doc, mapa, "205-4")

        resultados: list[Orientacao] = []

        m = re.search(r"80 a 84\s*°C.*?94\s*°C", texto_220, re.DOTALL)
        if not m:
            raise RuntimeError("Padrão de temperatura do termostato não encontrado em 220-3")
        resultados.append(_limiar(
            cod_fonte=TRATOR_5060E_COD_FONTE, nome_doc=TRATOR_5060E_NOME_DOC,
            detalhamento="Termostato do motor: abertura inicial 80 a 84 °C (nominal 82 °C), totalmente aberto a 94 °C.",
            metrica_gatilho="TEMPERATURA_MOTOR", valor_gatilho=94.0, unidade_medida="°C",
            criticidade="MEDIA", sensor_iot="TEMP_MOTOR", pagina_origem=pag_220, texto_bruto_original=_ws(m.group(0)),
        ))

        m = re.search(r"altitudes acima de (\d+)\s*m.*?reduza os intervalos.*?50%", texto_205_10, re.DOTALL | re.IGNORECASE)
        if not m:
            m = re.search(r"reduza os intervalos.*?50%.*?altitudes acima de (\d+)\s*m", texto_205_10, re.DOTALL | re.IGNORECASE)
        if not m:
            raise RuntimeError("Padrão de redução de intervalo por altitude não encontrado em 205-10")
        resultados.append(Orientacao(
            cod_fonte_manual=TRATOR_5060E_COD_FONTE, nome_documento_origem=TRATOR_5060E_NOME_DOC,
            tipo_orientacao="LIMIAR_OPERACIONAL_SENSOR", subsistema="MOTOR", acao_tecnica="AJUSTAR",
            detalhamento_orientacao=f"Acima de {m.group(1)} m de altitude, reduzir os intervalos de troca de óleo/filtro em 50%.",
            metrica_gatilho="ALTITUDE", valor_gatilho=float(m.group(1)), unidade_medida="m",
            fator_condicional="REDUZ_INTERVALO_OLEO_50PCT",
            texto_bruto_original=_ws(doc[pag_205_10 - 1].get_text()[:400]),
            pagina_origem=pag_205_10, criticidade="MEDIA", sensor_iot=None,
        ))

        m = re.search(r"temperatura for inferior a 0\s*°C.*?combust[ií]vel para inverno", texto_205_4, re.DOTALL)
        if not m:
            raise RuntimeError("Padrão de diesel de inverno <0°C não encontrado em 205-4")
        resultados.append(_limiar(
            cod_fonte=TRATOR_5060E_COD_FONTE, nome_doc=TRATOR_5060E_NOME_DOC,
            detalhamento="Abaixo de 0 °C, usar combustível diesel formulado para inverno (menor ponto de turvação).",
            metrica_gatilho="TEMPERATURA_AMBIENTE", valor_gatilho=0.0, unidade_medida="°C",
            criticidade="MEDIA", sensor_iot="TEMP_AMBIENTE", pagina_origem=pag_205_4, texto_bruto_original=_ws(m.group(0)),
        ))

        return resultados
    finally:
        doc.close()
