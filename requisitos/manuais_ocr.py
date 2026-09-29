# -*- coding: utf-8 -*-
"""
OCR de manuais digitalizados (PDF em que cada página é uma imagem, sem texto).

As leituras automática e com IA precisam do texto das páginas. Para um PDF digitalizado, o OCR (Tesseract, em
português e inglês) reconhece o texto das imagens e grava uma cópia do PDF com o texto por baixo da imagem: a página
continua igual, mas o texto passa a poder ser lido, pesquisado e selecionado.

Requer o Tesseract instalado com os idiomas português e inglês:
  - Mac:     brew install tesseract tesseract-lang
  - Windows: instalador do Tesseract (UB Mannheim), marcando o idioma Portuguese
  - Linux:   sudo apt install tesseract-ocr tesseract-ocr-por
"""
from pathlib import Path

import pymupdf

IDIOMAS_OCR = "por+eng"
DPI_OCR = 300                  # resolução da imagem enviada ao OCR (300 = boa leitura de texto pequeno de tabelas)
MIN_CARACTERES_PAGINA = 50     # página com menos texto que isso conta como "sem texto" (só imagem)

INSTRUCAO_INSTALACAO = ("Instale o Tesseract com os idiomas português e inglês e reinicie o Streamlit. "
                        "No Mac: brew install tesseract tesseract-lang")


class OCRIndisponivel(RuntimeError):
    """O Tesseract (ou o idioma português) não está instalado."""


def paginas_com_texto(caminho_pdf):
    """(páginas com texto, total de páginas)."""
    with pymupdf.open(caminho_pdf) as doc:
        return sum(1 for p in doc if len(p.get_text().strip()) > MIN_CARACTERES_PAGINA), len(doc)


def _tessdata():
    try:
        pasta = pymupdf.get_tessdata()
    except Exception as erro:                               # Tesseract não instalado
        raise OCRIndisponivel(f"OCR indisponível: o Tesseract não foi encontrado. {INSTRUCAO_INSTALACAO}") from erro
    if not (Path(pasta) / "por.traineddata").exists():
        raise OCRIndisponivel(f"OCR indisponível: falta o idioma português do Tesseract. {INSTRUCAO_INSTALACAO}")
    return pasta


def _linhas(palavras):
    """Junta as palavras do OCR em linhas (mesmo bloco e linha): [(x0, y0, x1, y1, texto), ...]."""
    linhas = {}
    for x0, y0, x1, y1, palavra, bloco, linha, _ in palavras:
        atual = linhas.get((bloco, linha))
        if atual is None:
            linhas[(bloco, linha)] = [x0, y0, x1, y1, palavra]
        else:
            atual[0], atual[1] = min(atual[0], x0), min(atual[1], y0)
            atual[2], atual[3] = max(atual[2], x1), max(atual[3], y1)
            atual[4] += " " + palavra
    return list(linhas.values())


def _escrever_texto_invisivel(pagina, palavras):
    """Escreve o texto reconhecido, invisível, na posição de cada linha na página (a imagem não muda)."""
    escritor = pymupdf.TextWriter(pagina.rect)
    fonte = pymupdf.Font("helv")
    for x0, y0, x1, y1, texto in _linhas(palavras):
        altura = max(y1 - y0, 1.0)
        tamanho = altura * 0.85
        largura_natural = fonte.text_length(texto, fontsize=tamanho) or 1.0
        # ajusta a largura do texto à caixa da linha (a seleção com o mouse fica no lugar certo)
        tamanho = min(tamanho, tamanho * (x1 - x0) / largura_natural)
        try:
            escritor.append((x0, y1 - altura * 0.2), texto, font=fonte, fontsize=max(tamanho, 1.0))
        except Exception:                                   # caractere que a fonte não tem: pula a linha
            continue
    escritor.write_text(pagina, render_mode=3)              # 3 = texto invisível (igual aos PDFs "pesquisáveis")


def aplicar_ocr(caminho_pdf, caminho_saida, ao_progresso=None):
    """Grava em caminho_saida uma cópia do PDF com o texto reconhecido por baixo da imagem. Páginas que já têm texto
    ficam como estão. ao_progresso(pagina, total) é chamado a cada página. Devolve o caminho da cópia."""
    tessdata = _tessdata()
    caminho_saida = Path(caminho_saida)
    caminho_saida.parent.mkdir(parents=True, exist_ok=True)
    temporario = caminho_saida.with_suffix(".parcial.pdf")
    with pymupdf.open(caminho_pdf) as doc:
        total = len(doc)
        for indice, pagina in enumerate(doc):
            if len(pagina.get_text().strip()) <= MIN_CARACTERES_PAGINA:
                leitura = pagina.get_textpage_ocr(language=IDIOMAS_OCR, dpi=DPI_OCR, full=True, tessdata=tessdata)
                palavras = pagina.get_text("words", textpage=leitura)
                if palavras:
                    _escrever_texto_invisivel(pagina, palavras)
            if ao_progresso:
                ao_progresso(indice + 1, total)
        doc.save(temporario, garbage=3, deflate=True)
    temporario.replace(caminho_saida)                    # só aparece o arquivo final quando o OCR termina
    return caminho_saida
