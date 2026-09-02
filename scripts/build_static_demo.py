"""Gera a versão publicável da POC: uma página única, autossuficiente.

A página publicada não tem servidor, então não roda o modelo de embeddings.
Ela leva embutidos (a) o índice de trechos, para busca livre por BM25, e
(b) os resultados semânticos que o motor completo produziu para um banco de
perguntas — assim as perguntas sugeridas mostram a qualidade real da busca.
Continua sem nenhuma chamada a LLM.

Uso: .venv/bin/python scripts/build_static_demo.py
Saída: dist/bussola-pnld.html
"""
import base64
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.search import Buscador

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_FRONT = os.path.join(RAIZ, "frontend")
DIR_DIST = os.path.join(RAIZ, "dist")
DIR_DOCS = os.path.join(RAIZ, "docs")
LIMITE_TEXTO = 620          # o bastante para BM25 e para recortar a citação

PERGUNTAS_BANCO = [
    "atividades de leitura para o 3º ano",
    "como trabalhar cantigas populares",
    "avaliação diagnóstica em espanhol",
    "proposta de arte com Tarsila do Amaral",
    "como ensinar o alfabeto e as letras iniciais",
    "consciência fonológica na alfabetização",
    "como avaliar a produção de texto dos alunos",
    "trabalho com o gênero notícia",
    "atividades de leitura de imagens",
    "como abordar diversidade cultural em sala de aula",
    "sugestões de projeto de leitura",
    "trabalhar com poemas e rimas",
    "atividades de escuta e oralidade",
    "como usar jogos e brincadeiras na aula",
    "ensino de vocabulário em espanhol",
    "atividades de desenho e pintura",
    "trabalho com literatura infantil",
    "como fazer a mediação de leitura em voz alta",
    "avaliação formativa e acompanhamento da turma",
    "atividades sobre a família e o cotidiano",
    "como trabalhar as competências gerais da BNCC",
    "educação alimentar e hábitos saudáveis",
    "atividades com música e ritmo",
    "trabalhar o gênero receita",
    "como desenvolver a escrita autônoma",
    "atividades de teatro e expressão corporal",
    "inclusão e acessibilidade em sala de aula",
    "trabalho com o meio ambiente e a natureza",
]


def exportar_dados(b):
    obras_ids = list(b.catalogo.keys())
    indice_obra = {oid: i for i, oid in enumerate(obras_ids)}
    obras = []
    for oid in obras_ids:
        o = b.catalogo[oid]
        obras.append([o["titulo"], o["colecao"], o["disciplina"], o["ano"],
                      o["issuu"]["public_location"]])

    trechos, mapa = [], {}
    for i, c in enumerate(b.chunks):
        if b.repeticoes[i] >= 3 or c["pagina_fisica"] <= 2:
            continue          # paratexto e capa nunca são resposta útil
        mapa[i] = len(trechos)
        trechos.append([indice_obra[c["obra_id"]], c["pagina_fisica"],
                        c["pagina_impressa"] or "", c["texto"][:LIMITE_TEXTO]])
    return obras, trechos, mapa


def montar_banco(b, mapa):
    banco = []
    for pergunta in PERGUNTAS_BANCO:
        res = b.buscar(pergunta, principais=3, extras=3)
        pares = []
        for r in res["principais"] + res["tambem_encontrei"]:
            # reencontra o índice do trecho pelo par (obra, página)
            for i, c in enumerate(b.chunks):
                if c["obra_id"] == r["obra_id"] and c["pagina_fisica"] == r["pagina_fisica"] \
                        and i in mapa:
                    pares.append([round(r["pontuacao"], 6), mapa[i]])
                    break
        if pares:
            banco.append({"q": pergunta, "r": pares})
        print("  banco: {} -> {} resultados".format(pergunta, len(pares)))
    return banco


def main():
    print("Carregando o motor completo…")
    b = Buscador()
    obras, trechos, mapa = exportar_dados(b)
    print("  {} obras, {} trechos exportados".format(len(obras), len(trechos)))

    print("Pré-computando o banco de perguntas com a busca semântica real…")
    banco = montar_banco(b, mapa)

    dados = {"obras": obras, "trechos": trechos, "banco": banco}
    blob = json.dumps(dados, ensure_ascii=False, separators=(",", ":"))

    html = open(os.path.join(DIR_FRONT, "index.html"), encoding="utf-8").read()
    motor = open(os.path.join(DIR_FRONT, "motor-estatico.js"), encoding="utf-8").read()

    caminho_img = os.path.join(DIR_FRONT, "assets", "portal-pnld.jpg")
    with open(caminho_img, "rb") as fh:
        img = base64.b64encode(fh.read()).decode("ascii")
    html = html.replace('url("assets/portal-pnld.jpg")',
                        'url("data:image/jpeg;base64,{}")'.format(img))

    injecao = (
        "<script>window.BUSSOLA_DADOS=" + blob + ";</script>\n"
        "<script>" + motor + "</script>\n"
    )
    marcador = "<script>\n(function(){"
    assert marcador in html, "não achei o script principal para injetar antes"
    html = html.replace(marcador, injecao + marcador, 1)

    # dist/ é o artefato local; docs/ é o que o GitHub Pages publica
    os.makedirs(DIR_DIST, exist_ok=True)
    os.makedirs(DIR_DOCS, exist_ok=True)
    saidas = [os.path.join(DIR_DIST, "bussola-pnld.html"),
              os.path.join(DIR_DOCS, "index.html")]
    for saida in saidas:
        with open(saida, "w", encoding="utf-8") as fh:
            fh.write(html)
        print("\n{} — {:.2f} MB".format(saida, os.path.getsize(saida) / 1024 / 1024))
    # impede o Jekyll de reprocessar a página no Pages
    open(os.path.join(DIR_DOCS, ".nojekyll"), "w").close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
