"""Quebra as páginas em trechos indexáveis.

Regra: o trecho nunca cruza a fronteira da página — assim o link para o Issuu
sempre aponta exatamente para a página de onde o texto saiu. Páginas longas
(manual do professor costuma ter 3 mil caracteres) são subdivididas em janelas
com sobreposição, para não diluir o embedding.
"""
import re

MIN_CARACTERES = 120      # abaixo disso é capa, folha de rosto ou página só de imagem
MAX_CHUNK = 1400          # acima disso, subdivide
JANELA = 1000
SOBREPOSICAO = 180

_FIM_DE_FRASE = re.compile(r"(?<=[.!?:;])\s+")


def _janelas(texto: str):
    frases = _FIM_DE_FRASE.split(texto)
    atual, saida = "", []
    for frase in frases:
        if atual and len(atual) + len(frase) + 1 > JANELA:
            saida.append(atual.strip())
            cauda = atual[-SOBREPOSICAO:]
            corte = cauda.find(" ")
            atual = (cauda[corte + 1:] if corte >= 0 else "") + " " + frase
        else:
            atual = (atual + " " + frase).strip()
    if atual.strip():
        saida.append(atual.strip())
    return [s for s in saida if len(s) >= MIN_CARACTERES] or ([texto] if texto else [])


def chunks_da_pagina(pagina: dict):
    texto = pagina.get("texto", "")
    if len(texto) < MIN_CARACTERES:
        return []
    partes = [texto] if len(texto) <= MAX_CHUNK else _janelas(texto)
    return [
        {
            "pagina_fisica": pagina["pagina_fisica"],
            "pagina_impressa": pagina.get("pagina_impressa", ""),
            "ordem": i,
            "texto": parte,
        }
        for i, parte in enumerate(partes)
    ]
