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
from api import disciplinas as mod_disciplinas
from api import correcao as mod_correcao
from api import referencia as mod_referencia
from api.confianca import cobertura, termos_ausentes, unidades
from ingest.metadata import (extrair_assunto, norm, parse_pergunta,
                             remover_termos_de_filtro, titulo_curto)

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_INDICE = os.path.join(RAIZ, "data", "index")
CATALOGO = os.path.join(RAIZ, "data", "catalog.json")

K_RRF = 60          # constante padrão do Reciprocal Rank Fusion
POOL = 120          # quantos candidatos cada estratégia contribui
# Desempate pela similaridade densa. Tem de ser pequeno FRENTE ao RRF, senão
# deixa de desempatar e passa a mandar: as pontuações do RRF vão de 1/60 a
# 1/180, um intervalo de ~0,011, e um peso de 0,06 sobre uma similaridade de
# 0,3–0,8 contribuía de 0,018 a 0,048 — mais que o intervalo inteiro. O efeito
# era a busca ignorar o BM25 mesmo quando ele achava a página exata.
PESO_DENSO = 0.25 / K_RRF   # ~0,004: um quarto de uma posição no ranking
PESO_ANCORAGEM = 0.6        # o quanto conter os termos da pergunta promove um trecho
REPETICOES_BOILERPLATE = 3  # texto que se repete N vezes na obra é paratexto
TETO_POR_OBRA_PRINCIPAIS = 2  # quantos cartões principais a mesma obra pode ocupar
MAX_OCORRENCIAS = 40          # teto do índice remissivo, para a resposta não virar lista infinita
# Acima disto a pergunta é ampla demais para índice remissivo: "atividades de
# leitura" casa com 107 páginas, e uma parede de números não ajuda ninguém a
# escolher. O índice serve a tema específico — "instrumentos musicais", 26.
LIMITE_UTIL_OCORRENCIAS = 60
# Sumários e quadros de conteúdo listam títulos sem pontuar frase nenhuma. No
# acervo a mediana é 1,05 ponto final por 100 caracteres; abaixo de 0,30 estão
# 1% dos trechos, e todos são listagem. Elas nunca são resposta: o professor
# que buscou "fotossíntese" caía numa página onde a palavra é item de índice.
DENSIDADE_MINIMA_DE_FRASE = 0.30

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


def tokenizar_adjacentes(texto: str):
    """Tokens da pergunta e quais pares ficaram COLADOS no texto original.

    "sistema solar" é um composto — as duas palavras se tocam. "fotossíntese e
    as plantas" não é: há "e as" no meio. Sem essa distinção, o par virava uma
    exigência falsa e derrubava perguntas legítimas, porque o acervo obviamente
    não tem "fotossíntese plantas" colado em lugar nenhum.
    """
    tokens, colados, ultimo_indice = [], [], None
    for posicao, bruto in enumerate(norm(texto).split()):
        if len(bruto) <= 2 or bruto in STOPWORDS:
            continue
        if tokens:
            colados.append(posicao == ultimo_indice + 1)
        tokens.append(bruto)
        ultimo_indice = posicao
    return tokens, colados


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
        # tokenizar 4.358 trechos custa caro; fazemos uma vez e guardamos, para
        # o BM25 e para o cálculo de cobertura usarem a mesma lista
        self.tokens_chunk = [tokenizar(c["texto"]) for c in self.chunks]
        self.bm25 = IndiceBM25(self.tokens_chunk)
        self.repeticoes = self._contar_repeticoes()
        self.eh_listagem = [self._parece_listagem(c["texto"]) for c in self.chunks]
        self._memo_bigrama = {}
        # df apurado só sobre os trechos que a busca pode devolver: um termo que
        # existe apenas num sumário não deve contar como presente na hora de
        # dizer ao professor o que faltou
        self.df_conteudo = {}
        for tokens, listagem in zip(self.tokens_chunk, self.eh_listagem):
            if listagem:
                continue
            for termo in set(tokens):
                self.df_conteudo[termo] = self.df_conteudo.get(termo, 0) + 1
        self.disciplina_chunk = [
            (self.catalogo.get(c["obra_id"], {}) or {}).get("disciplina")
            for c in self.chunks]
        # tabela termo -> disciplina, medida no próprio acervo (ver disciplinas.py)
        self.tabela_disciplinas = mod_disciplinas.construir_tabela(
            self.tokens_chunk, self.disciplina_chunk)
        # (obra, página física) -> número impresso, montado uma vez: procurar
        # isso varrendo os trechos a cada consulta custaria caro à toa
        self.pagina_impressa = {}
        for c in self.chunks:
            chave = (c["obra_id"], c["pagina_fisica"])
            if c.get("pagina_impressa") and chave not in self.pagina_impressa:
                self.pagina_impressa[chave] = c["pagina_impressa"]
        self.sinonimos = self._ler_sinonimos()
        self.indice_codigos = mod_referencia.indexar_codigos(self.tokens_chunk)
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

    @staticmethod
    def _ler_sinonimos():
        """Tabela gerada por ingest/build_sinonimos.py; ausente, a busca segue
        funcionando sem sinônimo nenhum."""
        caminho = os.path.join(DIR_INDICE, "sinonimos.json")
        if not os.path.exists(caminho):
            return {}
        with open(caminho, encoding="utf-8") as fh:
            return json.load(fh)

    def expandir(self, tokens):
        """Termos da pergunta mais os parentes deles, para a recuperação.

        Sem isto a página que escreve "leituras" nem entra no bolo de
        candidatos, e a cobertura nunca chega a vê-la.
        """
        saida = list(tokens)
        for t in tokens:
            for parente in self.sinonimos.get(t, ())[:2]:
                if parente not in saida:
                    saida.append(parente)
        return saida

    @staticmethod
    def _parece_listagem(texto):
        """Sumário, quadro de conteúdos, índice: títulos enfileirados sem frase."""
        if len(texto) < 200:
            return False
        pontos = len(re.findall(r"[.!?]", texto))
        return pontos / (len(texto) / 100.0) < DENSIDADE_MINIMA_DE_FRASE

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

    def trechos_com_frase(self, a, b):
        """Índices dos trechos em que o par aparece colado.

        Varremos as listas de tokens em vez de manter um índice de bigramas: são
        ~500 mil pares no acervo, e o mapa teria de ser embutido também na página
        publicada. A varredura custa poucos milissegundos e é memoizada por par.
        """
        chave = (a, b)
        if chave not in self._memo_bigrama:
            achados = []
            for idx, tokens in enumerate(self.tokens_chunk):
                for i in range(len(tokens) - 1):
                    if tokens[i] == a and tokens[i + 1] == b:
                        achados.append(idx)
                        break
            self._memo_bigrama[chave] = achados
        return self._memo_bigrama[chave]

    def contar_ocorrencias(self, padrao):
        """Em quantos trechos (fora sumários) o padrão aparece, e onde.

        Serve para as respostas guiadas afirmarem sobre o acervo de hoje. Texto
        curado que diz "não aparece em nenhuma página" apodrece na primeira obra
        nova — foi o que aconteceu com "multisseriadas".
        """
        regex = re.compile(padrao, re.I)
        trechos, obras = 0, []
        for chunk, listagem in zip(self.chunks, self.eh_listagem):
            if listagem or not regex.search(chunk["texto"]):
                continue
            trechos += 1
            obra = self.catalogo.get(chunk["obra_id"], {})
            rotulo = "{} (página {})".format(
                titulo_curto(obra.get("titulo", "")), chunk["pagina_fisica"])
            if rotulo not in obras:
                obras.append(rotulo)
        return {"trechos": trechos, "obras": obras}

    def df_bigrama(self, a, b):
        return len(self.trechos_com_frase(a, b))

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

    def buscar_referencia(self, tipo, alvo, pergunta, limite=None):
        """Todas as ocorrências exatas, agrupadas por obra e em ordem de página.

        Sem teto por obra e sem diversificação: aqui o professor quer o índice
        remissivo, não uma amostra variada. Sumários entram — numa consulta de
        código, a página do quadro de habilidades é resposta legítima.
        """
        limite = limite or mod_referencia.MAX_RESULTADOS
        if tipo == "codigo":
            indices = self.indice_codigos.get(alvo, [])
        else:
            alvo_tokens = tokenizar(alvo)
            indices = [i for i, toks in enumerate(self.tokens_chunk)
                       if _contem_sequencia(toks, alvo_tokens)] if alvo_tokens else []

        vistos, achados = set(), []
        for i in indices:
            chunk = self.chunks[i]
            chave = (chunk["obra_id"], chunk["pagina_fisica"])
            if chave in vistos:
                continue
            vistos.add(chave)
            achados.append(i)
        achados.sort(key=lambda i: (self.chunks[i]["obra_id"],
                                    self.chunks[i]["pagina_fisica"]))

        self._cobertura_cache = {i: 1.0 for i in achados}
        resultados = [self._montar(i, 1.0, 1.0, pergunta) for i in achados[:limite]]
        obras = {self.chunks[i]["obra_id"] for i in achados}
        return {
            "pergunta": pergunta,
            "filtros": {},
            "modo": "referencia",
            "tipo_referencia": tipo,
            # o código volta na forma canônica, mesmo que digitado sem o "EF"
            "alvo": ("EF" + alvo.upper()) if tipo == "codigo" else '"%s"' % alvo,
            "confianca": "alta" if achados else "nenhuma",
            "cobertura": 1.0 if achados else 0.0,
            "confiante": bool(achados),
            "total_paginas": len(achados),
            "total_obras": len(obras),
            "principais": resultados,
            "tambem_encontrei": [],
            "vizinhos": [],
            "termos_ausentes": [],
            "termos": tokenizar(pergunta),
        }

    def buscar(self, pergunta: str, principais: int = 3, extras: int = 3,
               componente: str = "", _ja_corrigida: bool = False):
        # Antes de qualquer coisa: o professor escreveu alguma palavra que não
        # existe no acervo mas se parece com uma que existe? "fakenews" não é
        # motivo para dizer "não encontrei" — é motivo para perguntar se ele
        # quis dizer "fake news", como faria qualquer busca decente.
        if not _ja_corrigida:
            correcoes = mod_correcao.sugerir(
                tokenizar(extrair_assunto(pergunta)), self.df_conteudo)
            if correcoes:
                corrigida = mod_correcao.aplicar(pergunta, correcoes)
                resultado = self.buscar(corrigida, principais, extras, componente,
                                        _ja_corrigida=True)
                resultado["correcao"] = {"de": pergunta, "para": corrigida}
                resultado["pergunta"] = pergunta
                return resultado

        filtros = parse_pergunta(pergunta)
        # "Quero pesquisar sobre Sistema Solar" vira "Sistema Solar": a moldura
        # do pedido não é assunto e não pode ser procurada dentro dos livros
        assunto = extrair_assunto(pergunta)

        vetor = self.modelo.encode([assunto], convert_to_numpy=True,
                                   normalize_embeddings=True).astype("float32")[0]
        similaridades = self.embeddings @ vetor
        # argpartition acha os POOL maiores sem ordenar o vetor inteiro: o custo
        # deixa de ser N log N e passa a ser N + POOL log POOL
        if similaridades.size > POOL:
            recorte = np.argpartition(-similaridades, POOL)[:POOL]
            top_denso = recorte[np.argsort(-similaridades[recorte])]
        else:
            top_denso = np.argsort(-similaridades)

        # o que já virou filtro não compete de novo como termo de busca;
        # se sobrar nada ("língua portuguesa" inteira é filtro), volta ao original
        tokens = tokenizar(remover_termos_de_filtro(assunto)) or tokenizar(assunto)
        top_lexico = self.bm25.top(self.expandir(tokens), POOL) if tokens else []

        # Terceiro canal: busca por frase. "atividades de leitura" tem as duas
        # palavras comuns demais para o BM25 destacar, então a página que traz a
        # expressão colada nem chegava a ser candidata — e a cobertura, que só
        # pontua o que está no pool, não tinha o que promover.
        top_frase = []
        for a, b in zip(tokens, tokens[1:]):
            achados = self.trechos_com_frase(a, b)
            if achados and len(achados) <= POOL:
                top_frase.extend(sorted(achados, key=lambda i: -similaridades[i]))

        fundido = _rrf(list(top_denso))
        for lista in (list(top_lexico), top_frase[:POOL]):
            for idx, valor in _rrf(lista).items():
                fundido[idx] = fundido.get(idx, 0.0) + valor

        # A cobertura lexical não serve só para decidir se respondemos: uma página
        # que contém o que foi perguntado é melhor resposta que uma que só se
        # parece com a pergunta no espaço vetorial. Sem isto a busca achava a
        # página exata pelo BM25 e a descartava na hora de ordenar.
        # A cobertura julga a pergunta inteira, inclusive o que virou filtro: se
        # o professor escreveu "matemática", a página precisa falar disso para a
        # gente afirmar que encontrou. O filtro decide ranking, não dispensa a
        # palavra de aparecer.
        tokens_cobertura, colados = tokenizar_adjacentes(assunto)
        # o professor raramente nomeia o componente; quando não nomeia, o acervo
        # diz de qual disciplina o assunto é
        # o componente escolhido no seletor vale mais que qualquer dedução
        if componente:
            filtros = dict(filtros, disciplina=componente, disciplina_inferida=False)
        if not filtros.get("disciplina"):
            inferida = mod_disciplinas.inferir(tokens, self.tabela_disciplinas)
            if inferida:
                filtros = dict(filtros, disciplina=inferida, disciplina_inferida=True)
        unidades_pergunta = unidades(tokens_cobertura, colados, self.bm25.df,
                                     self.bm25.n, self.df_bigrama,
                                     disciplina=filtros.get("disciplina"))
        self._cobertura_cache = {}
        classificados = []
        for idx, base in fundido.items():
            chunk = self.chunks[idx]
            obra = self.catalogo.get(chunk["obra_id"], {})
            cob = cobertura(unidades_pergunta, self.tokens_chunk[idx],
                            self.disciplina_chunk[idx], self.sinonimos)
            self._cobertura_cache[idx] = cob
            pontuacao = (base + PESO_DENSO * max(float(similaridades[idx]), 0.0)) \
                * self._fator_metadado(obra, filtros) \
                * self._fator_paratexto(idx, chunk)
            classificados.append((pontuacao, idx))

        # A cobertura ordena PRIMEIRO, e a pontuação de recuperação desempata.
        # Como multiplicador ela não dava conta: um trecho que aparece nos dois
        # canais soma RRF em dobro, e vencia mesmo com um terço da cobertura —
        # a página com "Fotossíntese" e cobertura 1,0, primeira do BM25, estava
        # sendo descartada em favor de páginas com 0,31. Arredondar a cobertura
        # evita que diferenças de ruído atropelem o ranking de relevância.
        # Faixas de 0,05: mais grosso que isso, uma diferença real de cobertura
        # (0,67 contra 0,74, que é o que separa a obra certa da errada) cai na
        # mesma faixa e a pontuação de recuperação decide sozinha.
        classificados.sort(
            key=lambda par: (round(self._cobertura_cache[par[1]] * 20), par[0]),
            reverse=True)

        selecionados = self._diversificar(classificados, similaridades, pergunta,
                                          principais, extras, componente)

        # Ancoragem lexical: só respondemos com trechos que realmente contêm os
        # termos que carregam o assunto da pergunta. Ver api/confianca.py.
        # lidos do módulo (não importados por valor) para o harness varrer o limiar
        alto, baixo = confianca.LIMIAR_ALTO, confianca.LIMIAR_BAIXO
        melhor = max([r["cobertura"] for r in selecionados], default=0.0)
        confianca_faixa = ("alta" if melhor >= alto
                           else "parcial" if melhor >= baixo else "nenhuma")

        # Termo com frequência zero é evidência decisiva, não indício: se a
        # palavra central da pergunta não existe em nenhuma página que a busca
        # possa devolver, não há o que responder — nem com ressalva.
        ausentes = termos_ausentes(tokens_cobertura, self.df_conteudo)
        if ausentes:
            confianca_faixa = "nenhuma"
        ancorados = [r for r in selecionados if r["cobertura"] >= baixo]
        return {
            "pergunta": pergunta,
            "assunto": assunto,
            "filtros": filtros,
            "confianca": confianca_faixa,
            "cobertura": round(melhor, 4),
            "confiante": confianca_faixa != "nenhuma",
            "principais": [] if confianca_faixa == "nenhuma" else ancorados[:principais],
            "tambem_encontrei": ancorados[principais:principais + extras],
            # o que existe de mais próximo quando nada passa na ancoragem: serve
            # para mostrar assunto vizinho SEM apresentá-lo como resposta
            "vizinhos": selecionados[:3] if confianca_faixa == "nenhuma" else [],
            # índice remissivo ao lado dos cartões, só quando temos certeza do
            # assunto: numa resposta com ressalva ele daria falsa precisão
            "ocorrencias": (self._ocorrencias(
                tokens_cobertura,
                {r["obra_id"] for r in ancorados[:principais]})
                if confianca_faixa == "alta" else []),
            "termos_ausentes": ausentes,
            "termos": tokens,
        }

    @staticmethod
    def _parecidos(a, b, limiar=0.6):
        """Jaccard entre os tokens de dois trechos."""
        if not a or not b:
            return False
        intersecao = len(a & b)
        return intersecao / float(len(a) + len(b) - intersecao) >= limiar

    def _ocorrencias(self, tokens, obras_permitidas=None):
        """Todas as páginas onde o tema aparece, agrupadas por obra.

        A reclamação mais repetida do teste foi esta: "a habilidade aparece em
        24 páginas e ele mostra 2", "deixou de mostrar as páginas 94 e 109". Os
        cartões continuam sendo uma amostra comentada; isto aqui é o índice
        remissivo que faltava ao lado deles.

        Sai barato porque não precisa pontuar nada: são as listas de postagem do
        BM25 intersectadas. Uma página que traz todos os termos da pergunta é,
        por definição, uma página sobre o assunto.
        """
        if not tokens:
            return []
        conjuntos = []
        for termo in dict.fromkeys(tokens):
            docs = set()
            for forma in [termo] + list(self.sinonimos.get(termo, ())[:2]):
                indices = self.bm25.docs.get(forma)
                if indices is not None:
                    docs.update(int(i) for i in indices)
            if docs:
                conjuntos.append(docs)
        if not conjuntos:
            return []

        # todos os termos na mesma página; se nada casar tudo, afrouxa para os
        # dois termos mais discriminantes, que são os que carregam o assunto
        comuns = set.intersection(*conjuntos)
        if not comuns and len(conjuntos) > 2:
            conjuntos.sort(key=len)
            comuns = conjuntos[0] & conjuntos[1]
        if not comuns:
            return []

        por_obra = {}
        for idx in comuns:
            if self.eh_listagem[idx]:
                continue
            chunk = self.chunks[idx]
            if obras_permitidas and chunk["obra_id"] not in obras_permitidas:
                continue
            por_obra.setdefault(chunk["obra_id"], set()).add(chunk["pagina_fisica"])

        from ingest.issuu import link_pagina
        saida = []
        for obra_id, paginas in por_obra.items():
            obra = self.catalogo.get(obra_id, {})
            issuu = obra.get("issuu", {})
            confiavel = issuu.get("offset_pagina") is not None
            itens = []
            for fisica in sorted(paginas):
                impressa = self.pagina_impressa.get((obra_id, fisica), "")
                itens.append({
                    "pagina_fisica": fisica,
                    "rotulo": impressa or str(fisica),
                    "link": (link_pagina(issuu.get("public_location", ""), fisica)
                             if confiavel else ""),
                })
            saida.append({
                "obra_id": obra_id,
                "titulo": titulo_curto(obra.get("titulo", obra_id)),
                "total": len(itens),
                "paginas": itens,
            })
        saida.sort(key=lambda o: -o["total"])
        if sum(o["total"] for o in saida) > LIMITE_UTIL_OCORRENCIAS:
            return []
        # o teto vale sobre o total de páginas, não de obras
        restante, cortado = MAX_OCORRENCIAS, []
        for obra in saida:
            if restante <= 0:
                break
            obra["paginas"] = obra["paginas"][:restante]
            restante -= len(obra["paginas"])
            cortado.append(obra)
        return cortado



    def _diversificar(self, classificados, similaridades, pergunta, principais,
                      extras, componente=""):
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
            # Escolher o componente no seletor é restrição, não preferência: o
            # professor pediu isso para parar de receber obra de outra matéria.
            if componente and self.disciplina_chunk[idx] != componente:
                continue
            if self.eh_listagem[idx]:
                continue          # sumário não responde nada; ver _parece_listagem
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

        # Antes os cartões principais traziam no máximo UM trecho por obra. Fazia
        # sentido com 5 obras e um manual que casava com tudo; com 13 virou o
        # defeito mais reclamado no teste — "a habilidade aparece em 24 páginas e
        # ele mostra 2" —, além de enfiar obra sem relação para preencher vaga.
        # Agora a obra certa pode ocupar mais de um lugar, e a variedade fica
        # garantida pelo teto por obra logo abaixo.
        escolhidos = []
        contagem_inicial = {}
        for pontuacao, idx in sorted(melhores_por_obra + restantes, reverse=True):
            oid = self.chunks[idx]["obra_id"]
            if contagem_inicial.get(oid, 0) >= TETO_POR_OBRA_PRINCIPAIS:
                continue
            contagem_inicial[oid] = contagem_inicial.get(oid, 0) + 1
            escolhidos.append((pontuacao, idx))
            if len(escolhidos) >= principais:
                break
        # O teto por obra acompanha o tamanho do acervo: com poucas obras
        # indexadas um limite fixo de 2 devolveria menos resultados do que o
        # pedido (com uma obra só, apenas 2 cartões para um limite de 8).
        vagas = principais + extras
        teto_por_obra = max(2, int(math.ceil(vagas / float(self.obras_indexadas))))
        contagem = {}
        for p, i in escolhidos:
            oid = self.chunks[i]["obra_id"]
            contagem[oid] = contagem.get(oid, 0) + 1
        ja_escolhidos = {i for _, i in escolhidos}
        for p, i in sorted(melhores_por_obra + restantes, reverse=True):
            if i in ja_escolhidos:
                continue
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
            "titulo": titulo_curto(obra.get("titulo", chunk["obra_id"])),
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
            "cobertura": round(self._cobertura_cache.get(idx, 0.0), 4),
        }


_FRASES = re.compile(r"(?<=[.!?])\s+")


def _contem_sequencia(tokens, alvo):
    """A sequência exata de tokens aparece no trecho?"""
    n = len(alvo)
    return any(tokens[i:i + n] == alvo for i in range(len(tokens) - n + 1))


def recortar(texto: str, pergunta: str, limite: int = 165) -> str:
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
