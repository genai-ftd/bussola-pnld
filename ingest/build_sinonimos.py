"""Tabela de sinônimos tirada do próprio acervo.

Por que existe: "leitura" e "leituras" são o mesmo assunto e a busca tratava
como termos diferentes; "cantigas" e "música" também. Sem isso, a cobertura
punia quem escreveu a palavra no plural ou usou o sinônimo óbvio.

Como: vetorizamos cada termo do vocabulário com o mesmo modelo da busca e
guardamos os vizinhos mais próximos. O corte é alto de propósito. Medimos o que
acontece abaixo dele:

  leitura   0,96 leituras · 0,95 lendo · 0,88 lemos        <- família da palavra
  cantigas  0,95 cantada · 0,92 musicais · 0,91 musica     <- sinônimo de verdade
  luz       0,76 lightfield · 0,72 escura                  <- ANTÔNIMO
  fotossintese 0,72 fotoarena · 0,64 fotografias           <- casou pelo prefixo
  multiplicacao 0,73 plurais · 0,71 quartetos              <- ruído

Abaixo de 0,86 o modelo confunde prefixo com sentido e chega a sugerir o
contrário do que foi pedido. Preferimos ficar sem sinônimo a ter um errado:
"luz", "fotossíntese" e "multiplicação" saem da tabela sem nenhum, e tudo bem.

Uso: .venv/bin/python ingest/build_sinonimos.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from dotenv import load_dotenv

from api.search import tokenizar

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_INDICE = os.path.join(RAIZ, "data", "index")
SAIDA = os.path.join(DIR_INDICE, "sinonimos.json")

LIMIAR = 0.86            # abaixo disso o modelo confunde prefixo com sentido
MAX_POR_TERMO = 3
DF_MINIMO = 5            # termo raro demais não tem vizinhança confiável
TAMANHO_MINIMO = 4
BLOCO = 512              # linhas da matriz por vez, para não estourar memória


def vocabulario():
    caminho = os.path.join(DIR_INDICE, "chunks.jsonl")
    df = {}
    with open(caminho, encoding="utf-8") as fh:
        for linha in fh:
            for termo in set(tokenizar(json.loads(linha)["texto"])):
                df[termo] = df.get(termo, 0) + 1
    termos = [t for t, n in df.items()
              if n >= DF_MINIMO and len(t) >= TAMANHO_MINIMO and not t.isdigit()]
    return sorted(termos), df


def main():
    load_dotenv(os.path.join(RAIZ, ".env"))
    termos, df = vocabulario()
    print("vocabulário: {} termos".format(len(termos)))

    from sentence_transformers import SentenceTransformer
    modelo = SentenceTransformer(os.environ.get(
        "MODELO_EMBEDDINGS",
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"))
    print("vetorizando…")
    V = modelo.encode(termos, batch_size=256, convert_to_numpy=True,
                      normalize_embeddings=True, show_progress_bar=True).astype("float32")

    print("procurando vizinhos acima de {:.2f}…".format(LIMIAR))
    tabela = {}
    for inicio in range(0, len(termos), BLOCO):
        bloco = V[inicio:inicio + BLOCO]
        sims = bloco @ V.T
        for i, linha in enumerate(sims):
            pos = inicio + i
            linha[pos] = -1.0                      # ele mesmo não é sinônimo
            candidatos = np.flatnonzero(linha >= LIMIAR)
            if not candidatos.size:
                continue
            melhores = candidatos[np.argsort(-linha[candidatos])][:MAX_POR_TERMO]
            tabela[termos[pos]] = [termos[j] for j in melhores]

    with open(SAIDA, "w", encoding="utf-8") as fh:
        json.dump(tabela, fh, ensure_ascii=False)
    print("\n{} termos com sinônimo ({:.0%} do vocabulário)".format(
        len(tabela), len(tabela) / float(len(termos))))
    for exemplo in ("leitura", "cantigas", "avaliacao", "luz", "fotossintese",
                    "paisagem", "escrita", "atividades"):
        print("  {:14s} -> {}".format(exemplo, tabela.get(exemplo, "(nenhum)")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
