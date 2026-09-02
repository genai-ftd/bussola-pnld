"""Constrói o índice semântico a partir dos PDFs em data/pdfs/.

Saída em data/index/:
  chunks.jsonl     — um trecho por linha, com obra/página/texto
  embeddings.npy   — matriz float32 (N x dim), vetores já normalizados
  manifest.json    — modelo usado, dimensão, contagens, data da geração

Nenhuma chamada a LLM: só um modelo de embeddings multilíngue rodando local.
"""
import argparse
import hashlib
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
DIR_CACHE = os.path.join(DIR_INDICE, "obras")
ESTADO = os.path.join(DIR_INDICE, "cache.json")
CATALOGO = os.path.join(RAIZ, "data", "catalog.json")

MODELO_PADRAO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# {obra_id: {"hash": ..., "modelo": ...}} — o que já foi vetorizado
CACHE_ESTADO = {}


def carregar_catalogo():
    if not os.path.exists(CATALOGO):
        raise SystemExit("data/catalog.json não existe. Rode antes: "
                         "python ingest/build_catalog.py")
    with open(CATALOGO, encoding="utf-8") as fh:
        return json.load(fh)


def impressao_digital(caminho: str) -> str:
    """Hash do conteúdo do PDF — a chave do cache por obra."""
    h = hashlib.sha256()
    with open(caminho, "rb") as fh:
        for bloco in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(bloco)
    return h.hexdigest()


def chunks_da_obra(obra, caminho):
    registros = []
    for pagina in extrair_paginas(caminho):
        for c in chunks_da_pagina(pagina):
            registros.append({
                "id": "{}#p{}-{}".format(obra["id"], c["pagina_fisica"], c["ordem"]),
                "obra_id": obra["id"],
                "pagina_fisica": c["pagina_fisica"],
                "pagina_impressa": c["pagina_impressa"],
                "texto": c["texto"],
            })
    return registros


def _caminhos_cache(obra_id):
    base = os.path.join(DIR_CACHE, obra_id)
    return base + ".jsonl", base + ".npy"


def processar_obra(obra, modelo_nome, batch, carregar_modelo, forcar=False):
    """Devolve (chunks, vetores) da obra, reaproveitando o cache quando possível.

    O cache é chaveado pelo hash do PDF e pelo nome do modelo: adicionar uma
    obra nova ao acervo passa a custar só a vetorização dessa obra, em vez de
    reprocessar todas as outras. É o que torna viável ir somando os livros do
    PNLD 2027 aos poucos.
    """
    caminho = os.path.join(DIR_PDFS, obra["arquivo"])
    arq_chunks, arq_vetores = _caminhos_cache(obra["id"])
    digital = impressao_digital(caminho)
    chave = {"hash": digital, "modelo": modelo_nome}
    registro_cache = CACHE_ESTADO.get(obra["id"])

    if (not forcar and registro_cache == chave
            and os.path.exists(arq_chunks) and os.path.exists(arq_vetores)):
        with open(arq_chunks, encoding="utf-8") as fh:
            chunks = [json.loads(l) for l in fh if l.strip()]
        vetores = np.load(arq_vetores)
        if len(chunks) == vetores.shape[0]:
            print("  {}: {} trechos (cache)".format(obra["id"], len(chunks)))
            return chunks, vetores
        print("  {}: cache inconsistente, refazendo".format(obra["id"]))

    chunks = chunks_da_obra(obra, caminho)
    if not chunks:
        return [], None
    modelo = carregar_modelo()
    vetores = modelo.encode(
        [c["texto"] for c in chunks],
        batch_size=batch,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    ).astype("float32")

    os.makedirs(DIR_CACHE, exist_ok=True)
    with open(arq_chunks, "w", encoding="utf-8") as fh:
        for c in chunks:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    np.save(arq_vetores, vetores)
    CACHE_ESTADO[obra["id"]] = chave
    print("  {}: {} trechos (vetorizado)".format(obra["id"], len(chunks)))
    return chunks, vetores


def main():
    ap = argparse.ArgumentParser(description="Gera o índice semântico")
    ap.add_argument("--modelo", default=None, help="modelo de embeddings (sentence-transformers)")
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--refazer", action="store_true",
                    help="ignora o cache e revetoriza todas as obras")
    args = ap.parse_args()

    load_dotenv(os.path.join(RAIZ, ".env"))
    modelo_nome = args.modelo or os.environ.get("MODELO_EMBEDDINGS") or MODELO_PADRAO

    if os.path.exists(ESTADO) and not args.refazer:
        with open(ESTADO, encoding="utf-8") as fh:
            CACHE_ESTADO.update(json.load(fh))

    catalogo = carregar_catalogo()
    print("Processando {} obra(s)…".format(len(catalogo)))

    # o modelo só é carregado se houver alguma obra para vetorizar
    _modelo = []

    def carregar_modelo():
        if not _modelo:
            print("  carregando modelo de embeddings: {}".format(modelo_nome))
            from sentence_transformers import SentenceTransformer
            _modelo.append(SentenceTransformer(modelo_nome))
        return _modelo[0]

    t0 = time.time()
    registros, partes = [], []
    for obra in catalogo:
        if not os.path.exists(os.path.join(DIR_PDFS, obra["arquivo"])):
            print("  [aviso] PDF ausente, obra ignorada: {}".format(obra["arquivo"]))
            continue
        chunks, vetores_obra = processar_obra(
            obra, modelo_nome, args.batch, carregar_modelo, forcar=args.refazer)
        if not chunks:
            continue
        registros.extend(chunks)
        partes.append(vetores_obra)

    if not registros:
        raise SystemExit("Nenhum trecho extraído. Há PDFs em data/pdfs/?")
    vetores = np.vstack(partes)
    print("  total: {} trechos (dim {}) em {:.1f}s".format(
        len(registros), vetores.shape[1], time.time() - t0))

    os.makedirs(DIR_INDICE, exist_ok=True)
    with open(ESTADO, "w", encoding="utf-8") as fh:
        json.dump(CACHE_ESTADO, fh, ensure_ascii=False, indent=2)
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
