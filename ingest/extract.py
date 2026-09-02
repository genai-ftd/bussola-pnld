"""Extração de texto nativo dos PDFs, página a página (sem OCR).

Validado: os PDFs do PNLD 2027 têm camada de texto limpa. Cada página vira um
registro com o texto normalizado, o número da página FÍSICA (1-based, igual ao
leitor do Issuu) e o número IMPRESSO no rodapé, quando detectável.
"""
import re
import unicodedata

import fitz  # PyMuPDF

ROMANOS = re.compile(r"^[ivxlcdm]{1,7}$", re.I)


def limpar(texto: str) -> str:
    """De-hifeniza quebras de linha, colapsa espaços, remove lixo de layout."""
    if not texto:
        return ""
    texto = unicodedata.normalize("NFC", texto)
    texto = texto.replace("­", "")                      # hífen condicional
    texto = re.sub(r"(\w)[-‐‑]\s*\n\s*(\w)", r"\1\2", texto)  # palavra quebrada
    texto = re.sub(r"[ \t ]+", " ", texto)
    texto = re.sub(r"\s*\n\s*", " ", texto)
    texto = re.sub(r"\s{2,}", " ", texto)
    return texto.strip()


def numero_impresso(page) -> str:
    """Fólio impresso (o número que o professor vê no rodapé/cabeçalho).

    Heurística determinística: blocos de texto curtos, contendo só dígitos ou
    algarismos romanos, situados nos 12% superiores ou inferiores da página.
    Preferimos o rodapé. Retorna "" quando não houver.
    """
    altura = page.rect.height
    candidatos = []
    for bloco in page.get_text("blocks"):
        x0, y0, x1, y1, txt = bloco[0], bloco[1], bloco[2], bloco[3], bloco[4]
        t = (txt or "").strip()
        # o mesmo fólio às vezes aparece duplicado no bloco ("3\n3")
        partes = {p.strip() for p in t.split() if p.strip()}
        if len(partes) != 1:
            continue
        valor = partes.pop()
        if not (valor.isdigit() and len(valor) <= 3) and not ROMANOS.match(valor):
            continue
        rodape = y0 > altura * 0.88
        cabecalho = y1 < altura * 0.12
        if rodape or cabecalho:
            candidatos.append((0 if rodape else 1, y0, valor))
    if not candidatos:
        return ""
    candidatos.sort()
    return candidatos[0][2]


def extrair_paginas(caminho_pdf: str):
    """Gera dicts: {pagina_fisica, pagina_impressa, texto, texto_bruto}."""
    doc = fitz.open(caminho_pdf)
    try:
        for i, page in enumerate(doc):
            bruto = page.get_text("text")
            yield {
                "pagina_fisica": i + 1,          # capa = 1, igual ao leitor do Issuu
                "pagina_impressa": numero_impresso(page),
                "texto": limpar(bruto),
                "texto_bruto": bruto,
            }
    finally:
        doc.close()


def contar_paginas(caminho_pdf: str) -> int:
    doc = fitz.open(caminho_pdf)
    try:
        return doc.page_count
    finally:
        doc.close()
