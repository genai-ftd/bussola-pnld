"""Infere de qual componente curricular a pergunta trata, a partir do acervo.

O filtro por regex só dispara quando o professor nomeia a disciplina ("em
espanhol", "de matemática"). Mas quase ninguém pergunta assim — pergunta pelo
assunto: tabuada, fotossíntese, cartografia, parlendas. Sem sinal de disciplina,
a busca acerta o tema e erra a obra: "como ensinar a tabuada de multiplicação"
caía no livro de Educação digital, que fala de tabuada ao explicar a matriz de
Pitágoras, em vez do de Matemática, que tem uma unidade inteira de multiplicação.

O sinal está no próprio acervo: medimos como cada termo se distribui entre as
disciplinas e deixamos os termos da pergunta votarem. Nada de lista escrita à
mão — a tabela sai dos livros indexados e se refaz sozinha quando o acervo
muda. Determinístico, sem LLM.
"""
import math

# Um termo só vota se estiver concentrado numa disciplina e não for onipresente.
CONCENTRACAO_MINIMA = 0.55
FRACAO_MAXIMA_DO_ACERVO = 0.25
# A disciplina vencedora precisa dominar a votação, senão preferimos não chutar.
DOMINIO_MINIMO = 0.55


def construir_tabela(tokens_por_chunk, disciplina_por_chunk):
    """termo -> (disciplina dominante, peso do voto).

    O peso combina o quanto o termo é concentrado (a fração de ocorrências na
    disciplina líder) com o quanto há de evidência (quantos trechos sustentam
    isso). Sem o segundo fator, "tabuada", que aparece em 4 trechos, gritaria
    mais alto que "multiplicação", que aparece em 70.
    """
    contagem = {}
    for tokens, disciplina in zip(tokens_por_chunk, disciplina_por_chunk):
        if not disciplina:
            continue
        for termo in set(tokens):
            contagem.setdefault(termo, {})
            contagem[termo][disciplina] = contagem[termo].get(disciplina, 0) + 1

    total_chunks = len(tokens_por_chunk)
    tabela = {}
    for termo, distribuicao in contagem.items():
        total = sum(distribuicao.values())
        if total > total_chunks * FRACAO_MAXIMA_DO_ACERVO:
            continue                      # termo onipresente não diz nada
        lider, n_lider = max(distribuicao.items(), key=lambda kv: kv[1])
        concentracao = n_lider / float(total)
        if concentracao < CONCENTRACAO_MINIMA:
            continue
        tabela[termo] = (lider, concentracao * math.log(1 + n_lider))
    return tabela


def inferir(tokens, tabela):
    """Disciplina provável da pergunta, ou None quando a votação não é clara."""
    votos = {}
    for termo in dict.fromkeys(tokens):
        entrada = tabela.get(termo)
        if entrada:
            votos[entrada[0]] = votos.get(entrada[0], 0.0) + entrada[1]
    if not votos:
        return None
    lider, peso = max(votos.items(), key=lambda kv: kv[1])
    return lider if peso / sum(votos.values()) >= DOMINIO_MINIMO else None
