"""Buscas de referência: quando o professor procura uma ocorrência exata.

Perguntar "EF35LP07" não é perguntar um tema — é pedir o índice remissivo de um
código. Aí ele não quer os três trechos mais parecidos, quer TODAS as páginas
onde aquilo aparece, agrupadas por obra. Foi a reclamação unânime da primeira
rodada de testes: o código existe em 48 páginas e a Bússola devolvia 3, porque a
regra de diversidade (um trecho por obra) foi desenhada para busca temática.

Detecção determinística por formato, sem LLM: código da BNCC, com ou sem o "EF"
inicial, e trecho entre aspas.
"""
import re

# EF03LP06, EF12EF01, EF35LP07 — e também 35LP07, que o professor digita sem o EF
CODIGO_BNCC = re.compile(r"\b(?:ef)?(\d{2}[a-z]{2}\d{2})\b", re.I)
FRASE_CITADA = re.compile(r"[\"“]([^\"”]{3,80})[\"”]")

MAX_RESULTADOS = 15


def detectar(pergunta):
    """Devolve ("codigo"|"frase", alvo) quando a pergunta é de referência."""
    m = CODIGO_BNCC.search(pergunta or "")
    if m:
        return ("codigo", m.group(1).lower())
    m = FRASE_CITADA.search(pergunta or "")
    if m:
        return ("frase", m.group(1).strip())
    return None


def indexar_codigos(tokens_por_chunk):
    """sufixo do código -> índices dos trechos que o citam.

    Indexamos pelo sufixo (sem "EF") para "35LP07" e "EF35LP07" caírem no mesmo
    lugar: o professor digita das duas formas, e antes só a completa funcionava.
    """
    padrao = re.compile(r"^(?:ef)?(\d{2}[a-z]{2}\d{2})$")
    indice = {}
    for i, tokens in enumerate(tokens_por_chunk):
        for token in set(tokens):
            m = padrao.match(token)
            if m:
                indice.setdefault(m.group(1), []).append(i)
    return indice
