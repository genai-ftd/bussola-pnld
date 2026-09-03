"""Decide quando a Bússola deve dizer que não sabe.

Por que não dá para usar a similaridade do embedding: medimos, e ela não separa.
Perguntas fora do acervo ("campeonato brasileiro de futebol", "tabuada") pontuam
0,45–0,61 contra prosa pedagógica genérica em português — acima de vários acertos
legítimos. Qualquer limiar absoluto sobre o cosseno erra dos dois lados.

O sinal que separa é lexical: o trecho recuperado contém, de fato, o que a
pergunta pede? Medimos como cobertura da massa de IDF — cada unidade da pergunta
vale o quanto é discriminante no acervo, e exigimos que o trecho cubra uma fração
mínima desse peso.

As unidades são as palavras E os pares adjacentes. O par importa porque é o que
distingue composto de coincidência: "sistema solar" não existe em nenhuma página,
mas "solar" existe — em "filtro solar", numa orientação de saúde. Sem o par, a
pergunta sobre astronomia casava com protetor solar e era respondida com
confiança. Com ele, o composto ausente entra no denominador e derruba a nota.

Regra determinística e idêntica no motor local e no publicado — sem LLM.
"""
import math

# Calibrados em scripts/avaliar_confianca.py sobre um conjunto rotulado.
#
#   cobertura >= ALTO      responde direto
#   BAIXO..ALTO            responde com ressalva explícita
#   < BAIXO                diz que não encontrou, e diz qual termo faltou
#
# Subir ALTO = menos resposta errada sem aviso, mais resposta com ressalva.
LIMIAR_ALTO = 0.68
LIMIAR_BAIXO = 0.50


def idf(frequencia_documento: int, total_documentos: int) -> float:
    """Peso da unidade: quanto mais rara no acervo, mais ela define o assunto."""
    return math.log(1 + (total_documentos - frequencia_documento + 0.5)
                    / (frequencia_documento + 0.5))


def unidades(tokens, colados, df, total_documentos, df_bigrama):
    """Palavras da pergunta e os pares que estavam COLADOS nela, com seus pesos.

    Só o par colado vira unidade. "sistema solar" é composto e exige aparecer
    junto; "fotossíntese e as plantas" são dois assuntos numa frase, e exigir o
    par derrubaria a pergunta à toa.
    """
    saida = [(t, None, idf(df.get(t, 0), total_documentos))
             for t in dict.fromkeys(tokens)]
    for i, (a, b) in enumerate(zip(tokens, tokens[1:])):
        if i < len(colados) and colados[i]:
            saida.append((a, b, idf(df_bigrama(a, b), total_documentos)))
    return saida


def cobertura(unidades_pergunta, tokens_trecho) -> float:
    """Fração da massa de IDF da pergunta que o trecho realmente contém (0 a 1)."""
    if not unidades_pergunta:
        return 0.0
    presentes = set(tokens_trecho)
    pares = set(zip(tokens_trecho, tokens_trecho[1:]))
    total = obtido = 0.0
    for a, b, peso in unidades_pergunta:
        total += peso
        if (a in presentes) if b is None else ((a, b) in pares):
            obtido += peso
    return obtido / total if total > 0 else 0.0


def faixa(valor_cobertura: float) -> str:
    """Classifica a cobertura em "alta", "parcial" ou "nenhuma"."""
    if valor_cobertura >= LIMIAR_ALTO:
        return "alta"
    if valor_cobertura >= LIMIAR_BAIXO:
        return "parcial"
    return "nenhuma"


def termos_ausentes(tokens, df):
    """Termos da pergunta que não aparecem em nenhuma página do acervo.

    Servem para a mensagem de "não encontrei" ser específica ("não achei nada
    sobre Páscoa") em vez de genérica.
    """
    return [t for t in dict.fromkeys(tokens) if df.get(t, 0) == 0]
