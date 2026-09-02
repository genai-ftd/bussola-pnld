"""Constrói o índice semântico a partir dos PDFs em data/pdfs/.

Saída em data/index/:
  chunks.jsonl     — um trecho por linha, com obra/página/texto
  embeddings.npy   — matriz float32 (N x dim), vetores já normalizados
  manifest.json    — modelo usado, dimensão, contagens, data da geração

Nenhuma chamada a LLM: só um modelo de embeddings multilíngue rodando local.
"""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from dotenv import load_dotenv

from ingest.chunk import chunks_da_pagina
from ingest.extract import extrair_paginas

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_PDFS = os.path.join(RAIZ, "data", "pdfs")
DIR_INDICE = os.path.join(RAIZ, "data", "index")
CATALOGO = os.path.join(RAIZ, "data", "catalog.json")

MODELO_PADRAO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


def carregar_catalogo():
    if not os.path.exists(CATALOGO):
        raise SystemExit("data/catalog.json não existe. Rode antes: "
                         "python ingest/build_catalog.py")
    with open(CATALOGO, encoding="utf-8") as fh:
        return json.load(fh)


def montar_chunks(catalogo):
    registros = []
    for obra in catalogo:
        caminho = os.path.join(DIR_PDFS, obra["arquivo"])
        if not os.path.exists(caminho):
            print("  [aviso] PDF ausente, obra ignorada: {}".format(obra["arquivo"]))
            continue
        antes = len(registros)
        for pagina in extrair_paginas(caminho):
            for c in chunks_da_pagina(pagina):
                registros.append({
                    "id": "{}#p{}-{}".format(obra["id"], c["pagina_fisica"], c["ordem"]),
                    "obra_id": obra["id"],
                    "pagina_fisica": c["pagina_fisica"],
                    "pagina_impressa": c["pagina_impressa"],
                    "texto": c["texto"],
                })
        print("  {}: {} trechos".format(obra["id"], len(registros) - antes))
    return registros


def main():
    ap = argparse.ArgumentParser(description="Gera o índice semântico")
    ap.add_argument("--modelo", default=None, help="modelo de embeddings (sentence-transformers)")
    ap.add_argument("--batch", type=int, default=32)
    args = ap.parse_args()

    load_dotenv(os.path.join(RAIZ, ".env"))
    modelo_nome = args.modelo or os.environ.get("MODELO_EMBEDDINGS") or MODELO_PADRAO

    catalogo = carregar_catalogo()
    print("Extraindo texto de {} obra(s)…".format(len(catalogo)))
    t0 = time.time()
    registros = montar_chunks(catalogo)
    if not registros:
        raise SystemExit("Nenhum trecho extraído. Há PDFs em data/pdfs/?")
    print("  total: {} trechos em {:.1f}s".format(len(registros), time.time() - t0))

    print("Carregando modelo de embeddings: {}".format(modelo_nome))
    from sentence_transformers import SentenceTransformer
    modelo = SentenceTransformer(modelo_nome)

    print("Gerando embeddings…")
    t0 = time.time()
    vetores = modelo.encode(
        [r["texto"] for r in registros],
        batch_size=args.batch,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=True,
    ).astype("float32")
    print("  {} vetores (dim {}) em {:.1f}s".format(
        vetores.shape[0], vetores.shape[1], time.time() - t0))

    os.makedirs(DIR_INDICE, exist_ok=True)
    with open(os.path.join(DIR_INDICE, "chunks.jsonl"), "w", encoding="utf-8") as fh:
        for r in registros:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    np.save(os.path.join(DIR_INDICE, "embeddings.npy"), vetores)
    with open(os.path.join(DIR_INDICE, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "modelo": modelo_nome,
            "dimensao": int(vetores.shape[1]),
            "trechos": len(registros),
            "obras": len(catalogo),
            "gerado_em": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }, fh, ensure_ascii=False, indent=2)
    print("\nÍndice salvo em data/index/.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
