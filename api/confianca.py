"""Decide quando a Bússola deve dizer que não sabe.

Por que não dá para usar a similaridade do embedding: medimos, e ela não separa.
Perguntas fora do acervo ("campeonato brasileiro de futebol", "tabuada") pontuam
0,45–0,61 contra prosa pedagógica genérica em português — acima de vários acertos
legítimos. Qualquer limiar absoluto sobre o cosseno erra dos dois lados.

O sinal que separa é lexical: o trecho recuperado contém, de fato, os termos que
carregam o assunto da pergunta? "Páscoa" não aparece em nenhuma das 1.044 páginas,
então nenhum trecho pode cobri-lo — enquanto "cantigas" aparece em 24.

Medimos isso como cobertura da massa de IDF: cada termo da pergunta vale o quanto
é discriminante no acervo, e exigimos que o trecho cubra uma fração mínima desse
peso. Termos genéricos ("atividades", "professor") valem pouco e não seguram a
decisão sozinhos; termos raros ou ausentes valem muito e derrubam a confiança.

Regra determinística e idêntica no motor local e no publicado — sem LLM.
"""
import math

# Calibrados em scripts/avaliar_confianca.py sobre um conjunto rotulado de 44
# perguntas (25 dentro do acervo, 19 fora). Um limiar único obriga a escolher
# entre errar com confiança e calar em pergunta boa; três faixas evitam isso:
#
#   cobertura >= ALTO      responde direto        2 erros em 19 perguntas fora
#   BAIXO..ALTO            responde com ressalva  2 casos, todos sinalizados
#   < BAIXO                diz que não encontrou  0 perguntas boas perdidas
#
# Os 2 erros restantes são colisão de sentido, não de limiar: "sistema solar"
# casa com "filtro solar" e "dia da consciência negra" com "consciência" e
# "negra" em passagens distintas. Os termos estão mesmo lá — nenhum corte
# lexical separa isso. Subir ALTO para pegá-los custa respostas boas.
#
# Subir ALTO = menos resposta errada sem aviso, mais resposta com ressalva.
LIMIAR_ALTO = 0.50
LIMIAR_BAIXO = 0.38


def idf(frequencia_documento: int, total_documentos: int) -> float:
    """Peso do termo: quanto mais raro no acervo, mais ele define o assunto."""
    return math.log(1 + (total_documentos - frequencia_documento + 0.5)
                    / (frequencia_documento + 0.5))


def cobertura(tokens_pergunta, tokens_trecho, df, total_documentos) -> float:
    """Fração da massa de IDF da pergunta que o trecho realmente contém (0 a 1)."""
    termos = set(tokens_pergunta)
    if not termos:
        return 0.0
    total = sum(idf(df.get(t, 0), total_documentos) for t in termos)
    if total <= 0:
        return 0.0
    presentes = set(tokens_trecho)
    obtido = sum(idf(df.get(t, 0), total_documentos)
                 for t in termos if t in presentes)
    return obtido / total


def faixa(valor_cobertura: float) -> str:
    """Classifica a cobertura em "alta", "parcial" ou "nenhuma"."""
    if valor_cobertura >= LIMIAR_ALTO:
        return "alta"
    if valor_cobertura >= LIMIAR_BAIXO:
        return "parcial"
    return "nenhuma"


def termos_ausentes(tokens_pergunta, df):
    """Termos da pergunta que não aparecem em nenhuma página do acervo.

    Servem para a mensagem de "não encontrei" ser específica ("não achei nada
    sobre Páscoa") em vez de genérica.
    """
    return [t for t in dict.fromkeys(tokens_pergunta) if df.get(t, 0) == 0]
