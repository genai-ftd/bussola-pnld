"""Busca híbrida (semântica + léxica) sobre o índice gerado pela ingestão.

- Semântica: embeddings multilíngues, similaridade de cosseno (produto interno
  em vetores normalizados). Índice denso simples em memória com NumPy — exato,
  sem dependência extra; para acervo maior, trocar por FAISS (ver README).
- Léxica: BM25, que segura bem termos exatos ("BNCC", "EF15LP03", "cantiga").
- Fusão: Reciprocal Rank Fusion, que dispensa calibrar escalas diferentes.
- Filtros de metadado (ano/disciplina/coleção) saem da pergunta por regex,
  sem LLM, e entram como reforço multiplicativo — nunca zeram o resultado.
"""
import json
import math
import os
import re

import numpy as np

from api import confianca
from api.confianca import cobertura, faixa, termos_ausentes
from ingest.metadata import norm, parse_pergunta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_INDICE = os.path.join(RAIZ, "data", "index")
CATALOGO = os.path.join(RAIZ, "data", "catalog.json")

K_RRF = 60          # constante padrão do Reciprocal Rank Fusion
POOL = 120          # quantos candidatos cada estratégia contribui
PESO_DENSO = 0.06   # desempate: deixa uma similaridade muito maior superar o ranking
REPETICOES_BOILERPLATE = 3  # texto que se repete N vezes na obra é paratexto

STOPWORDS = set("""
a as o os um uma uns umas de do da dos das em no na nos nas por para pelo pela com sem sobre
e ou mas que qual quais quando onde como quem cujo se ao aos à às pra pro entre até
eu voce você tu nos nós vos eles elas ele ela meu minha seu sua nosso nossa
ser sou é sao são era eram foi foram tem tenho temos ter haver há hao
me te lhe lhes isso isto aquilo esse essa este esta aquele aquela
mais menos muito pouco tambem também ja já nao não sim so só bem
quero queria gostaria preciso pode posso poderia tem qual me da
livro livros pagina paginas página páginas material conteudo conteúdo
trabalhar abordar ensinar usar utilizar fazer encontrar mostrar buscar procurar
exemplo exemplos forma formas maneira maneiras jeito tema assunto aula aulas
colecao coleção obra obras trabalho aborda ensina
""".split())
# As últimas linhas são moldura pedagógica, não assunto: em "como trabalhar
# cantigas populares", quem carrega o tema é "cantigas". Deixar "trabalhar"
# valendo IDF fazia a ancoragem punir perguntas boas.


def tokenizar(texto: str):
    return [t for t in norm(texto).split() if len(t) > 2 and t not in STOPWORDS]


def _rrf(ordem):
    """rank -> pontuação RRF, para uma lista de índices já ordenada."""
    return {idx: 1.0 / (K_RRF + posicao) for posicao, idx in enumerate(ordem)}


class IndiceBM25:
    """BM25 Okapi sobre um índice invertido.

    Substitui a varredura do `rank_bm25`, que percorria a lista inteira de
    documentos para cada termo da consulta. Aqui só os documentos que contêm
    algum termo da pergunta são tocados, então o custo passa a ser proporcional
    ao tamanho das postings, não ao tamanho do acervo — é o que permite crescer
    de 5 para 56 obras sem que a busca fique linear no corpus.

    Usa a variante de idf do Lucene, sempre positiva, o que dispensa o piso de
    epsilon que o rank_bm25 precisa aplicar para termos muito frequentes.
    A mesma fórmula está em frontend/motor-estatico.js, para as duas camadas
    ordenarem igual.
    """

    K1 = 1.5
    B = 0.75

    def __init__(self, documentos):
        self.n = len(documentos)
        tamanhos = np.array([len(t) for t in documentos], dtype="float32")
        media = float(tamanhos.mean()) if self.n else 1.0
        norma = 1 - self.B + self.B * (tamanhos / (media or 1.0))

        cru = {}
        for i, tokens in enumerate(documentos):
            frequencias = {}
            for t in tokens:
                frequencias[t] = frequencias.get(t, 0) + 1
            for termo, f in frequencias.items():
                cru.setdefault(termo, []).append((i, f))

        # A contribuição de cada posting é constante: idf do termo, frequência
        # no documento e norma do documento são todos conhecidos na indexação.
        # Guardando-a pronta, a consulta vira concatenar arrays e somar — sem
        # nenhuma aritmética por documento em tempo de busca.
        self.docs, self.contribuicoes, self.df = {}, {}, {}
        for termo, lista in cru.items():
            idf = math.log(1 + (self.n - len(lista) + 0.5) / (len(lista) + 0.5))
            docs = np.fromiter((d for d, _ in lista), dtype="int32", count=len(lista))
            freqs = np.fromiter((f for _, f in lista), dtype="float32", count=len(lista))
            self.docs[termo] = docs
            self.df[termo] = len(lista)
            self.contribuicoes[termo] = (
                idf * freqs * (self.K1 + 1) / (freqs + self.K1 * norma[docs])
            ).astype("float32")

    def top(self, tokens, k):
        """Índices dos k documentos mais pontuados, em ordem decrescente."""
        docs = [self.docs[t] for t in tokens if t in self.docs]
        if not docs:
            return []
        contribuicoes = [self.contribuicoes[t] for t in tokens if t in self.contribuicoes]
        pontos = np.bincount(np.concatenate(docs),
                             weights=np.concatenate(contribuicoes),
                             minlength=self.n)
        candidatos = np.flatnonzero(pontos)
        if candidatos.size > k:
            recorte = np.argpartition(-pontos[candidatos], k)[:k]
            candidatos = candidatos[recorte]
        return [int(i) for i in candidatos[np.argsort(-pontos[candidatos])]]


class Buscador:
    def __init__(self, modelo_nome=None):
        self.catalogo = {o["id"]: o for o in self._ler_json(CATALOGO)}
        self.chunks = self._ler_chunks()
        self.embeddings = np.load(os.path.join(DIR_INDICE, "embeddings.npy"))
        self.manifest = self._ler_json(os.path.join(DIR_INDICE, "manifest.json"))
        if len(self.chunks) != self.embeddings.shape[0]:
            raise RuntimeError("índice inconsistente: refaça `python ingest/build_index.py`")

        from sentence_transformers import SentenceTransformer
        self.modelo = SentenceTransformer(modelo_nome or self.manifest["modelo"])
        self.bm25 = IndiceBM25([tokenizar(c["texto"]) for c in self.chunks])
        self.repeticoes = self._contar_repeticoes()
        self.obras_indexadas = len({c["obra_id"] for c in self.chunks}) or 1

    # ------------------------------------------------------------------ carga
    @staticmethod
    def _ler_json(caminho):
        with open(caminho, encoding="utf-8") as fh:
            return json.load(fh)

    @staticmethod
    def _ler_chunks():
        caminho = os.path.join(DIR_INDICE, "chunks.jsonl")
        if not os.path.exists(caminho):
            raise RuntimeError("índice não encontrado. Rode `python ingest/build_index.py`.")
        with open(caminho, encoding="utf-8") as fh:
            return [json.loads(linha) for linha in fh if linha.strip()]

    def _contar_repeticoes(self):
        """Quantas vezes cada texto se repete dentro da mesma obra.

        Referências bibliográficas comentadas, expedientes e cabeçalhos de seção
        aparecem idênticos em dezenas de páginas. Contar a repetição identifica
        esse paratexto sem precisar de lista de palavras proibidas.
        """
        contagem = {}
        for c in self.chunks:
            contagem[(c["obra_id"], c["texto"])] = contagem.get((c["obra_id"], c["texto"]), 0) + 1
        return [contagem[(c["obra_id"], c["texto"])] for c in self.chunks]

    # ------------------------------------------------------------------ busca
    def _fator_metadado(self, obra, filtros):
        fator = 1.0
        if filtros.get("ano") and obra.get("ano") == filtros["ano"]:
            fator += 0.50
        if filtros.get("disciplina") and obra.get("disciplina") == filtros["disciplina"]:
            fator += 0.35
        if filtros.get("colecao") and obra.get("colecao") == filtros["colecao"]:
            fator += 0.25
        return fator

    def _fator_paratexto(self, idx, chunk):
        """Rebaixa (sem eliminar) o que nunca é resposta útil para o professor."""
        fator = 1.0
        if self.repeticoes[idx] >= REPETICOES_BOILERPLATE:
            fator *= 0.45          # referências/expediente repetidos na obra inteira
        if chunk["pagina_fisica"] <= 2:
            fator *= 0.45          # capa e folha de rosto
        return fator

    def buscar(self, pergunta: str, principais: int = 3, extras: int = 3):
        filtros = parse_pergunta(pergunta)

        vetor = self.modelo.encode([pergunta], convert_to_numpy=True,
                                   normalize_embeddings=True).astype("float32")[0]
        similaridades = self.embeddings @ vetor
        # argpartition acha os POOL maiores sem ordenar o vetor inteiro: o custo
        # deixa de ser N log N e passa a ser N + POOL log POOL
        if similaridades.size > POOL:
            recorte = np.argpartition(-similaridades, POOL)[:POOL]
            top_denso = recorte[np.argsort(-similaridades[recorte])]
        else:
            top_denso = np.argsort(-similaridades)

        tokens = tokenizar(pergunta)
        top_lexico = self.bm25.top(tokens, POOL) if tokens else []

        fundido = _rrf(list(top_denso))
        for idx, valor in _rrf(list(top_lexico)).items():
            fundido[idx] = fundido.get(idx, 0.0) + valor

        classificados = []
        for idx, base in fundido.items():
            chunk = self.chunks[idx]
            obra = self.catalogo.get(chunk["obra_id"], {})
            pontuacao = (base + PESO_DENSO * max(float(similaridades[idx]), 0.0)) \
                * self._fator_metadado(obra, filtros) \
                * self._fator_paratexto(idx, chunk)
            classificados.append((pontuacao, idx))
        classificados.sort(reverse=True)

        selecionados = self._diversificar(classificados, similaridades, pergunta,
                                          principais, extras)

        # Ancoragem lexical: só respondemos com trechos que realmente contêm os
        # termos que carregam o assunto da pergunta. Ver api/confianca.py.
        # lidos do módulo (não importados por valor) para o harness varrer o limiar
        alto, baixo = confianca.LIMIAR_ALTO, confianca.LIMIAR_BAIXO
        melhor = max([r["cobertura"] for r in selecionados], default=0.0)
        confianca_faixa = ("alta" if melhor >= alto
                           else "parcial" if melhor >= baixo else "nenhuma")
        ancorados = [r for r in selecionados if r["cobertura"] >= baixo]
        return {
            "pergunta": pergunta,
            "filtros": filtros,
            "confianca": confianca_faixa,
            "cobertura": round(melhor, 4),
            "confiante": confianca_faixa != "nenhuma",
            "principais": ancorados[:principais] if ancorados else [],
            "tambem_encontrei": ancorados[principais:principais + extras],
            # o que existe de mais próximo quando nada passa na ancoragem: serve
            # para mostrar assunto vizinho SEM apresentá-lo como resposta
            "vizinhos": [] if ancorados else selecionados[:3],
            "termos_ausentes": termos_ausentes(tokens, self.bm25.df),
        }

    @staticmethod
    def _parecidos(a, b, limiar=0.6):
        """Jaccard entre os tokens de dois trechos."""
        if not a or not b:
            return False
        intersecao = len(a & b)
        return intersecao / float(len(a) + len(b) - intersecao) >= limiar

    def _diversificar(self, classificados, similaridades, pergunta, principais, extras):
        """Seleciona os resultados privilegiando variedade de obras.

        Os cartões principais trazem no máximo um trecho por obra: o acervo tem
        manuais do professor longos que casam com quase qualquer pergunta
        pedagógica, e sem esse limite eles ocupariam a resposta inteira. O
        excedente da mesma obra desce para "Também encontrei".
        """
        vistos_pagina, vistos_texto = set(), []
        melhores_por_obra, restantes = [], []
        obras_usadas = set()

        for pontuacao, idx in classificados:
            chunk = self.chunks[idx]
            chave = (chunk["obra_id"], chunk["pagina_fisica"])
            if chave in vistos_pagina:
                continue
            # Os manuais do professor se repetem quase iguais entre volumes da
            # mesma coleção, e as janelas com sobreposição de uma página longa
            # também se parecem. Comparar o conjunto de tokens pega os dois
            # casos; comparar prefixo de texto não pegava nenhum dos dois.
            tokens_chunk = frozenset(tokenizar(chunk["texto"]))
            if any(self._parecidos(tokens_chunk, visto) for visto in vistos_texto):
                continue
            vistos_pagina.add(chave)
            vistos_texto.append(tokens_chunk)
            if chunk["obra_id"] not in obras_usadas:
                obras_usadas.add(chunk["obra_id"])
                melhores_por_obra.append((pontuacao, idx))
            elif len(restantes) < (principais + extras) * 4:
                # a varredura precisa alcançar TODAS as obras do acervo antes de
                # parar, senão as obras mais longas ocupam a resposta inteira
                restantes.append((pontuacao, idx))

        escolhidos = melhores_por_obra[:principais]
        # O teto por obra acompanha o tamanho do acervo: com poucas obras
        # indexadas um limite fixo de 2 devolveria menos resultados do que o
        # pedido (com uma obra só, apenas 2 cartões para um limite de 8).
        vagas = principais + extras
        teto_por_obra = max(2, int(math.ceil(vagas / float(self.obras_indexadas))))
        contagem = {}
        for p, i in escolhidos:
            oid = self.chunks[i]["obra_id"]
            contagem[oid] = contagem.get(oid, 0) + 1
        for p, i in sorted(melhores_por_obra[principais:] + restantes, reverse=True):
            if len(escolhidos) >= vagas:
                break
            oid = self.chunks[i]["obra_id"]
            if contagem.get(oid, 0) >= teto_por_obra:
                continue
            contagem[oid] = contagem.get(oid, 0) + 1
            escolhidos.append((p, i))

        return [self._montar(i, p, float(similaridades[i]), pergunta) for p, i in escolhidos]

    # -------------------------------------------------------------- resultado
    def _montar(self, idx, pontuacao, similaridade, pergunta):
        from ingest.issuu import link_pagina
        chunk = self.chunks[idx]
        obra = self.catalogo.get(chunk["obra_id"], {})
        issuu = obra.get("issuu", {})
        offset = issuu.get("offset_pagina")
        offset_confiavel = offset is not None
        pagina_issuu = chunk["pagina_fisica"] + (offset or 0)
        # Sem offset confirmado não dá para afirmar que a página do leitor é
        # esta: `build_catalog` deixa `offset_pagina` nulo quando o PDF local e
        # a publicação divergem em número de páginas. Melhor não oferecer link
        # nenhum do que mandar o professor para a página errada.
        link = (link_pagina(issuu.get("public_location", ""), pagina_issuu)
                if offset_confiavel else "")
        return {
            "chunk_idx": int(idx),
            "obra_id": chunk["obra_id"],
            "titulo": obra.get("titulo", chunk["obra_id"]),
            "colecao": obra.get("colecao"),
            "disciplina": obra.get("disciplina"),
            "ano": obra.get("ano"),
            "pagina_fisica": chunk["pagina_fisica"],
            "pagina_impressa": chunk.get("pagina_impressa") or None,
            "pagina_issuu": pagina_issuu,
            "offset_confiavel": offset_confiavel,
            "trecho": recortar(chunk["texto"], pergunta),
            "link": link,
            "similaridade": round(float(similaridade), 4),
            "pontuacao": round(float(pontuacao), 6),
            "cobertura": round(cobertura(tokenizar(pergunta), tokenizar(chunk["texto"]),
                                         self.bm25.df, self.bm25.n), 4),
        }


_FRASES = re.compile(r"(?<=[.!?])\s+")


def recortar(texto: str, pergunta: str, limite: int = 260) -> str:
    """Escolhe a janela do trecho com maior sobreposição de termos da pergunta.

    Determinístico: contagem de termos, sem geração de texto.
    """
    if len(texto) <= limite:
        return texto
    termos = set(tokenizar(pergunta))
    frases = _FRASES.split(texto)
    melhor_janela, melhor_nota = frases[0] if frases else texto[:limite], -1
    for i in range(len(frases)):
        janela = ""
        for frase in frases[i:]:
            if len(janela) + len(frase) + 1 > limite:
                break
            janela = (janela + " " + frase).strip()
        if not janela:
            janela = frases[i][:limite]
        nota = sum(1 for t in tokenizar(janela) if t in termos)
        if nota > melhor_nota:
            melhor_nota, melhor_janela = nota, janela
    corte = melhor_janela.strip()
    return corte if corte.endswith((".", "!", "?")) else corte + "…"
