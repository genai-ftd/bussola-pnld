"""Lista o que já está no índice e o que falta baixar, por disciplina.

Consulta os metadados do Issuu (só metadados — os PDFs não são baixáveis por
lá), cruza com os arquivos já indexados e sugere um volume por disciplina
ausente, preferindo os anos que já estão na base para as perguntas cruzarem.

Uso: .venv/bin/python scripts/listar_candidatos.py
"""
import json
import os
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from ingest import issuu
from ingest.metadata import parse_obra

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_PDFS = os.path.join(RAIZ, "data", "pdfs")
CACHE = os.path.join(RAIZ, "data", "index", "publicacoes_issuu.json")


def carregar_publicacoes():
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    pubs = issuu.listar_publicacoes(max_paginas=25)
    with open(CACHE, "w", encoding="utf-8") as fh:
        json.dump(pubs, fh, ensure_ascii=False)
    return pubs


def mb(n):
    return (n or 0) / 1024.0 / 1024.0


# O acervo do Issuu tem muito mais coisa que o PNLD 2027: degustações de 15
# páginas, catálogos, fascículos "Faça Você Mesmo". Sem filtrar, a sugestão por
# menor arquivo cai sempre num desses.
RUIDO = ("degustacao", "degustação", "catalogo", "catálogo", "amostra",
         "faca-", "faça", "faca_", "-faca", "redacao mp", "simples")
COLECOES_PNLD = ("plantar", "entrelacos", "entrelaços", "conquista", "baoba", "baobá")
MINIMO_PAGINAS = 80


def e_obra_pnld(o):
    """Volume de verdade de uma das quatro coleções do PNLD 2027."""
    if (o["paginas"] or 0) < MINIMO_PAGINAS:
        return False
    texto = (o["titulo"] + " " + o["arquivo"]).lower()
    if any(r in texto for r in RUIDO):
        return False
    # os arquivos com nome de código (IMLP...) só se identificam pelo título
    return (any(c in texto for c in COLECOES_PNLD)
            or "pnld" in texto or "step by step" in texto or "pasitos" in texto)


def main():
    load_dotenv(os.path.join(RAIZ, ".env"))
    pubs = carregar_publicacoes()
    print("{} publicações no acervo do Issuu\n".format(len(pubs)))

    locais = {}
    for nome in sorted(os.listdir(DIR_PDFS)):
        if nome.lower().endswith(".pdf"):
            locais[os.path.getsize(os.path.join(DIR_PDFS, nome))] = nome

    obras = []
    for p in pubs:
        fi = p.get("fileInfo") or {}
        arquivo = urllib.parse.unquote(fi.get("name") or "")
        titulo = p.get("title") or ""
        meta = parse_obra(titulo, arquivo)
        # o título de parte do acervo está deslocado em relação ao arquivo; onde
        # os dois discordam, a leitura pelo nome do arquivo é a confiável
        meta_arquivo = parse_obra("", arquivo)
        divergente = (meta_arquivo["disciplina"] and meta["disciplina"]
                      and meta_arquivo["disciplina"] != meta["disciplina"])
        # num bloco do acervo o título está deslocado um registro em relação ao
        # arquivo, e o volume sai errado: "Plantar_Matemática_Volume 5.pdf" tem
        # título "Volume 4". O nome do arquivo é o que veio no upload, então é
        # nele que se confia — mas vale avisar quem for baixar.
        ano_titulo = parse_obra(titulo, "")["ano"]
        volume_incerto = (ano_titulo and meta_arquivo["ano"]
                          and ano_titulo != meta_arquivo["ano"])
        obras.append({
            "titulo": titulo, "arquivo": arquivo,
            "disciplina": meta["disciplina"], "colecao": meta["colecao"],
            "ano": meta["ano"], "paginas": fi.get("pageCount"),
            "tamanho": fi.get("size"), "link": p.get("publicLocation") or "",
            "indexado": locais.get(fi.get("size")),
            "titulo_divergente": divergente,
            "volume_incerto": volume_incerto,
            "ano_arquivo": meta_arquivo["ano"],
        })

    indexadas = [o for o in obras if o["indexado"]]
    print("=" * 100)
    print("JÁ NO ÍNDICE ({})".format(len(indexadas)))
    print("=" * 100)
    for o in sorted(indexadas, key=lambda x: (x["disciplina"] or "", x["ano"] or 0)):
        print("  {:<22s} {:<20s} {}º ano  {:>4} pág  {:>6.1f} MB  {}".format(
            o["disciplina"] or "?", o["colecao"] or "?", o["ano"] or "-",
            o["paginas"], mb(o["tamanho"]), o["arquivo"]))

    presentes = {o["disciplina"] for o in indexadas}
    anos_na_base = sorted({o["ano"] for o in indexadas if o["ano"]})

    faltando = {}
    for o in obras:
        d = o["disciplina"]
        if not d or d in presentes or o["indexado"] or o["titulo_divergente"]:
            continue
        if not e_obra_pnld(o):
            continue
        faltando.setdefault(d, []).append(o)

    def prioridade(o):
        # 1) ano que já existe na base, para dar perguntas cruzadas entre obras
        # 2) arquivo menor, porque a ingestão custa por página
        return (0 if o["ano"] in anos_na_base else 1, o["tamanho"] or 0)

    print("\n" + "=" * 100)
    print("FALTA BAIXAR — um volume sugerido por disciplina ausente")
    print("=" * 100)
    for disciplina in sorted(faltando):
        melhor = sorted(faltando[disciplina], key=prioridade)[0]
        print("\n  {}".format(disciplina.upper()))
        print("    sugerido : {}".format(melhor["arquivo"]))
        print("    obra     : {}".format(melhor["titulo"]))
        print("    tamanho  : {} páginas · {:.0f} MB".format(
            melhor["paginas"], mb(melhor["tamanho"])))
        print("    issuu    : {}".format(melhor["link"]))
        if melhor["volume_incerto"]:
            print("    ATENÇÃO  : título diz volume {} e o arquivo diz {} — "
                  "confira ao baixar".format(melhor["ano"], melhor["ano_arquivo"]))
        alternativas = sorted(faltando[disciplina], key=prioridade)[1:3]
        for alt in alternativas:
            print("    ou       : {}  ({} pág · {:.0f} MB)".format(
                alt["arquivo"], alt["paginas"], mb(alt["tamanho"])))

    # completar uma coleção nos anos que já temos deixa a base coerente: dá para
    # perguntar sobre o 1º ano e comparar componentes dentro da mesma obra
    print("\n" + "=" * 100)
    print("ALTERNATIVA — completar a coleção Plantar nos anos que já estão na base")
    print("=" * 100)
    for o in sorted(obras, key=lambda x: (x["disciplina"] or "", x["ano"] or 0)):
        if (o["colecao"] == "Plantar" and o["ano"] in anos_na_base
                and not o["indexado"] and e_obra_pnld(o)
                and not o["titulo_divergente"]):
            aviso = "  ← título e arquivo discordam do volume" if o["volume_incerto"] else ""
            print("  {:<24s} {}º ano  {:>4} pág  {:>6.0f} MB  {}{}".format(
                o["disciplina"] or "?", o["ano"], o["paginas"],
                mb(o["tamanho"]), o["arquivo"], aviso))

    total = sum(mb(sorted(v, key=prioridade)[0]["tamanho"]) for v in faltando.values())
    paginas = sum(sorted(v, key=prioridade)[0]["paginas"] or 0 for v in faltando.values())
    print("\n" + "-" * 100)
    print("Somando os {} sugeridos: {:.0f} MB, {} páginas.".format(
        len(faltando), total, paginas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
