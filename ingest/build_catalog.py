"""Monta data/catalog.json: uma entrada por PDF em data/pdfs/.

Cada entrada junta (a) metadados derivados do arquivo local e (b) metadados do
Issuu (título oficial e `publicLocation`), casados por tamanho/nome do arquivo.
Roda com --sem-issuu para gerar o catálogo offline, sem tocar a API.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

from ingest import issuu
from ingest.extract import contar_paginas
from ingest.metadata import parse_obra

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_PDFS = os.path.join(RAIZ, "data", "pdfs")
CATALOGO = os.path.join(RAIZ, "data", "catalog.json")


def obra_id(nome_arquivo: str) -> str:
    return os.path.splitext(nome_arquivo)[0]


def listar_pdfs_locais():
    if not os.path.isdir(DIR_PDFS):
        return []
    return sorted(f for f in os.listdir(DIR_PDFS) if f.lower().endswith(".pdf"))


def main():
    ap = argparse.ArgumentParser(description="Gera o catálogo de obras")
    ap.add_argument("--sem-issuu", action="store_true",
                    help="não consulta a API do Issuu (links ficam vazios)")
    args = ap.parse_args()

    load_dotenv(os.path.join(RAIZ, ".env"))
    arquivos = listar_pdfs_locais()
    if not arquivos:
        print("Nenhum PDF em data/pdfs/. Nada a fazer.")
        return 1

    locais = []
    for nome in arquivos:
        caminho = os.path.join(DIR_PDFS, nome)
        locais.append({
            "arquivo": nome,
            "tamanho": os.path.getsize(caminho),
            "paginas_pdf": contar_paginas(caminho),
        })
        print("  lido: {} ({} páginas)".format(nome, locais[-1]["paginas_pdf"]))

    publicacoes = []
    if not args.sem_issuu:
        pendentes = {l["tamanho"] for l in locais}

        def ja_casou_tudo(acumulado):
            vistos = {(p.get("fileInfo") or {}).get("size") for p in acumulado}
            return pendentes.issubset(vistos)

        try:
            print("Consultando metadados no Issuu…")
            publicacoes = issuu.listar_publicacoes(parar_quando=ja_casou_tudo)
            print("  {} publicações inspecionadas".format(len(publicacoes)))
        except issuu.IssuuError as e:
            print("  [aviso] {}".format(e))
        except Exception as e:  # rede, 5xx etc. — a POC segue sem links
            print("  [aviso] falha ao consultar o Issuu: {}".format(e))

    catalogo = []
    for local in locais:
        pub, criterio = (None, None)
        if publicacoes:
            pub, criterio = issuu.casar_publicacao(
                publicacoes, local["arquivo"], local["tamanho"], local["paginas_pdf"])
        titulo = (pub or {}).get("title") or ""
        meta = parse_obra(titulo, local["arquivo"])
        info = (pub or {}).get("fileInfo") or {}
        paginas_issuu = info.get("pageCount")
        entrada = {
            "id": obra_id(local["arquivo"]),
            "arquivo": local["arquivo"],
            "titulo": titulo or obra_id(local["arquivo"]).replace("_", " "),
            "colecao": meta["colecao"],
            "disciplina": meta["disciplina"],
            "ano": meta["ano"],
            "volume_unico": meta["volume_unico"],
            "paginas_pdf": local["paginas_pdf"],
            "issuu": {
                "casado_por": criterio,
                "slug": (pub or {}).get("slug"),
                "public_location": (pub or {}).get("publicLocation") or "",
                "paginas": paginas_issuu,
                # se o PDF local e a publicação têm o mesmo nº de páginas, a página
                # física do PDF é a mesma do leitor do Issuu (offset 0)
                "offset_pagina": 0 if paginas_issuu == local["paginas_pdf"] else None,
                "copyright_confirmado": info.get("isCopyrightConfirmed"),
            },
        }
        catalogo.append(entrada)
        print("  {} -> {} [{}]".format(
            entrada["id"], entrada["titulo"], criterio or "sem casamento no Issuu"))
        if paginas_issuu is not None and paginas_issuu != local["paginas_pdf"]:
            print("     [ATENÇÃO] páginas divergem (PDF {} x Issuu {}): o link pode "
                  "cair na página errada. Valide o offset antes de confiar."
                  .format(local["paginas_pdf"], paginas_issuu))

    with open(CATALOGO, "w", encoding="utf-8") as fh:
        json.dump(catalogo, fh, ensure_ascii=False, indent=2)
    print("\nCatálogo salvo em data/catalog.json ({} obras).".format(len(catalogo)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
