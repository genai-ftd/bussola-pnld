"""Bateria rápida de perguntas contra o índice, sem subir a API.

Uso: .venv/bin/python scripts/smoke_test.py ["pergunta livre"]
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.responder import montar_resposta
from api.search import Buscador

PERGUNTAS = [
    "atividades de leitura para o 3º ano",
    "como trabalhar cantigas populares com as crianças",
    "avaliação diagnóstica em espanhol",
    "proposta de arte com Tarsila do Amaral",
    "o que a obra fala sobre alfabetização e consciência fonológica",
    "como abordar diversidade cultural em sala de aula",
    "receita de bolo de chocolate",  # controle: deve dar "não encontrei"
]


def main():
    perguntas = sys.argv[1:] or PERGUNTAS
    print("Carregando índice e modelo…")
    t0 = time.time()
    b = Buscador()
    print("  pronto em {:.1f}s — {} trechos, {} obras\n".format(
        time.time() - t0, len(b.chunks), len(b.catalogo)))

    for p in perguntas:
        t0 = time.time()
        res = b.buscar(p, principais=3, extras=2)
        ms = int((time.time() - t0) * 1000)
        resp = montar_resposta(res, nome="Ana")
        print("=" * 78)
        print("? {}   [{} ms | filtros: {}]".format(p, ms, res["filtros"]))
        print("> {}".format(resp["texto"]))
        for r in resp["resultados"]:
            print("   • {} — {} (sim {:.3f})".format(
                r["titulo"], r["descricao_pagina"], r["similaridade"]))
            print("     “{}”".format(r["trecho"][:150]))
            print("     {}".format(r["link"] or "(sem link)"))
        for r in resp["tambem_encontrei"]:
            print("   ~ {} — {}".format(r["titulo"], r["descricao_pagina"]))
        print()


if __name__ == "__main__":
    main()
