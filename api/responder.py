"""Montagem determinística da resposta conversacional.

Sem LLM e sem geração livre: a frase sai de um template escolhido por regras,
preenchido com os metadados recuperados pela busca. O objetivo da POC é validar
encontrabilidade e navegação, não redação automática.
"""
import random

ABERTURAS = [
    "{nome}, encontrei {n} {trecho_palavra} que {verbo} com a sua busca:",
    "Achei {n} {trecho_palavra} sobre isso, {nome}:",
    "{nome}, isto é o que o acervo do PNLD 2027 traz sobre o tema:",
]

SEM_RESULTADO = (
    "{nome}, não encontrei nada suficientemente próximo dessa pergunta nas obras "
    "que já estão indexadas. Tente descrever o conteúdo com outras palavras "
    "(por exemplo, o tema da aula, o gênero textual ou a habilidade da BNCC), "
    "ou informe o ano e o componente curricular."
)

ACERVO_VAZIO = (
    "{nome}, ainda não há obras indexadas nesta instalação. "
    "Rode a ingestão (`python ingest/build_index.py`) para começar."
)


def _descrever_obra(r):
    partes = [p for p in [r.get("colecao"), r.get("disciplina")] if p]
    if r.get("ano"):
        partes.append("{}º ano".format(r["ano"]))
    return " · ".join(partes) if partes else r.get("titulo", "")


def _descrever_pagina(r):
    if not r.get("offset_confiavel", True):
        # a paginação do leitor não foi confirmada para esta obra
        return "página {} do livro".format(
            r.get("pagina_impressa") or r["pagina_fisica"])
    if r.get("pagina_impressa"):
        return "página {} (página {} do visualizador)".format(
            r["pagina_impressa"], r["pagina_issuu"])
    return "página {} do visualizador".format(r["pagina_issuu"])


def _cartao(r):
    """Acrescenta os campos de apresentação que o front-end espera."""
    c = dict(r)
    c["descricao_obra"] = _descrever_obra(r)
    c["descricao_pagina"] = _descrever_pagina(r)
    return c


def _descrever_filtros(filtros):
    ditos = []
    if filtros.get("ano"):
        ditos.append("{}º ano".format(filtros["ano"]))
    if filtros.get("disciplina"):
        ditos.append(filtros["disciplina"])
    if filtros.get("colecao"):
        ditos.append("coleção {}".format(filtros["colecao"]))
    if not ditos:
        return ""
    return "Priorizei o que é de {}.".format(" · ".join(ditos))


def montar_resposta(resultado, nome=None, acervo_vazio=False, semente=None):
    nome = (nome or "").strip() or "Professor(a)"
    if acervo_vazio:
        return {"texto": ACERVO_VAZIO.format(nome=nome), "resultados": [], "tambem_encontrei": []}

    principais = resultado.get("principais", [])
    if not principais or not resultado.get("confiante"):
        return {
            "texto": SEM_RESULTADO.format(nome=nome),
            "resultados": [],
            # pistas fracas, mas passam pelo mesmo formatador: sem isso o
            # front-end recebe itens sem `descricao_pagina` e imprime "undefined"
            "tambem_encontrei": [_cartao(r) for r in principais[:3]],
        }

    rnd = random.Random(semente if semente is not None else resultado.get("pergunta", ""))
    n = len(principais)
    abertura = rnd.choice(ABERTURAS).format(
        nome=nome,
        n=n,
        trecho_palavra="trecho" if n == 1 else "trechos",
        verbo="conversa" if n == 1 else "conversam",
    )
    complemento = _descrever_filtros(resultado.get("filtros", {}))
    texto = abertura if not complemento else abertura + " " + complemento

    return {
        "texto": texto,
        "resultados": [_cartao(r) for r in principais],
        "tambem_encontrei": [_cartao(r) for r in resultado.get("tambem_encontrei", [])],
    }
