"""Corrige o que o professor digitou, usando o vocabulário do próprio acervo.

"fakenews" não existe em nenhuma página; "fake" e "news" existem, coladas. O
professor não quer receber "não encontrei" por causa de um espaço — quer o que
o Google faz: "Você quis dizer fake news?" e os resultados.

Duas correções, nesta ordem, e só para termos que NÃO existem no acervo — palavra
que existe nunca é mexida:

  1. separação: "fakenews" -> "fake news", quando as duas metades existem;
  2. distância de edição 1 ou 2: "alfabetizaçao", "portuges", "matemtica".

Sem LLM e sem dicionário externo: o vocabulário é o dos livros indexados, então
a correção conhece "parlenda" e "multisseriada", que um corretor comum erraria.
"""
LETRAS = "abcdefghijklmnopqrstuvwxyz"

DF_MINIMO_METADE = 2     # cada metade da separação precisa existir de verdade
DF_MINIMO_EDICAO = 3     # o candidato de correção precisa ser comum o bastante
TAMANHO_MINIMO = 5       # abaixo disso, edição de 1 letra vira outra palavra


def _separar(termo, df):
    """Melhor quebra em duas palavras existentes, ou None."""
    melhor, melhor_peso = None, 0
    for i in range(3, len(termo) - 2):
        a, b = termo[:i], termo[i:]
        fa, fb = df.get(a, 0), df.get(b, 0)
        if fa >= DF_MINIMO_METADE and fb >= DF_MINIMO_METADE:
            peso = min(fa, fb)          # a metade mais rara é quem sustenta
            if peso > melhor_peso:
                melhor, melhor_peso = (a + " " + b), peso
    return melhor


def _edicoes(termo):
    partes = [(termo[:i], termo[i:]) for i in range(len(termo) + 1)]
    saida = set()
    for a, b in partes:
        if b:
            saida.add(a + b[1:])                                  # remoção
            if len(b) > 1:
                saida.add(a + b[1] + b[0] + b[2:])                # troca de ordem
            for c in LETRAS:
                saida.add(a + c + b[1:])                          # substituição
        for c in LETRAS:
            saida.add(a + c + b)                                  # inserção
    saida.discard(termo)
    return saida


def _por_edicao(termo, df):
    """Palavra existente mais comum a uma (ou duas) edições de distância."""
    if len(termo) < TAMANHO_MINIMO:
        return None
    candidatos = [(df[c], c) for c in _edicoes(termo)
                  if df.get(c, 0) >= DF_MINIMO_EDICAO]
    if not candidatos and len(termo) >= 7:
        vistos = set()
        for intermediario in _edicoes(termo):
            for c in _edicoes(intermediario):
                if c not in vistos and df.get(c, 0) >= DF_MINIMO_EDICAO:
                    vistos.add(c)
                    candidatos.append((df[c], c))
    return max(candidatos)[1] if candidatos else None


def sugerir(tokens, df):
    """{termo digitado: termo corrigido} para os termos ausentes do acervo."""
    correcoes = {}
    for termo in dict.fromkeys(tokens):
        if df.get(termo, 0) > 0:
            continue                    # existe: não se mexe
        alvo = _separar(termo, df) or _por_edicao(termo, df)
        if alvo:
            correcoes[termo] = alvo
    return correcoes


def aplicar(pergunta, correcoes):
    """Reescreve a pergunta com as correções, preservando o resto."""
    import re
    from ingest.metadata import strip_accents

    def trocar(m):
        chave = strip_accents(m.group(0)).lower()
        return correcoes.get(chave, m.group(0))

    return re.sub(r"\w+", trocar, pergunta, flags=re.UNICODE)
