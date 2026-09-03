"""Mede a taxa de erro da decisão "sei / não sei" e calibra o limiar.

Roda um conjunto rotulado de perguntas contra o motor real e varre o limiar de
cobertura lexical, mostrando os dois tipos de erro:

  falso positivo  — respondeu uma pergunta que o acervo não cobre
                    (o erro que o Gabriel encontrou: erra com confiança)
  falso negativo  — calou numa pergunta que o acervo responde

Uso: .venv/bin/python scripts/avaliar_confianca.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from api.search import Buscador

# Conjunto rotulado com evidência, não com palpite: cada pergunta foi conferida
# contra o texto extraído antes de entrar numa lista. Refeito quando o acervo
# passou de 5 para 13 obras — "sistema solar" e "fotossíntese", que antes eram
# exemplos de pergunta fora, viraram conteúdo real ao entrar Ciências.
DENTRO = [
    # componentes que já estavam na base
    "atividades de leitura para o 3º ano",
    "como trabalhar cantigas populares",
    "avaliação diagnóstica em espanhol",
    "proposta de arte com Tarsila do Amaral",
    "consciência fonológica na alfabetização",
    "como avaliar a produção de texto dos alunos",
    "trabalho com o gênero notícia",
    "atividades de leitura de imagens",
    "como abordar diversidade cultural em sala de aula",
    "sugestões de projeto de leitura",
    "trabalhar com poemas e rimas",
    "atividades de escuta e oralidade",
    "ensino de vocabulário em espanhol",
    "atividades de desenho e pintura",
    "trabalho com literatura infantil",
    "mediação de leitura em voz alta",
    "avaliação formativa e acompanhamento da turma",
    "habilidades de leitura previstas na BNCC",
    "recursos de acessibilidade e inclusão",
    "material de apoio para o professor",
    "atividades de teatro e expressão corporal",
    "trabalhar o gênero receita",
    "receita de bolo de chocolate",
    "festa junina",
    "como desenvolver a escrita autônoma",
    "temas contemporâneos transversais",
    # componentes que entraram com as oito obras novas
    "como ensinar a tabuada de multiplicação",
    "atividades sobre o sistema solar",
    "dia da consciência negra",
    "trabalho com mapas e cartografia",
    "atividades sobre passado presente e memória",
    "vocabulário em inglês para crianças",
    "jogos e brincadeiras na educação física",
    "uso seguro da internet com os estudantes",
    "como orientar a reescrita de um texto",
]

# Perguntas plausíveis de um professor, mas fora do que está indexado. Conferido
# termo a termo: "handebol" só aparece como sede olímpica, "Páscoa" é o músico
# Hermeto Pascoal, e os compostos abaixo não existem em nenhuma página.
FORA = [
    "Páscoa",
    "atividades sobre a Páscoa",
    "como trabalhar a Páscoa em sala de aula",
    # a palavra só aparece num sumário; conteúdo mesmo não existe
    "fotossíntese e as plantas",
    "programação em python",
    "campeonato brasileiro de futebol",
    "como a coleção trata turmas multisseriadas",
    "experimentos de química no laboratório",
    "教育について",
    "revolução industrial no século XIX",
    "regras do handebol",
    "atividades de robótica educacional",
    "olimpíadas de matemática",
    "como calcular perímetro e área",
    "como ensinar frações equivalentes",
    "guerra fria e o muro de berlim",
    "tabela periódica dos elementos",
    "declaração de imposto de renda",
    "primeiros socorros e ressuscitação",
]


def main():
    print("Carregando o motor…")
    b = Buscador()
    print("  {} trechos, {} obras\n".format(len(b.chunks), len(b.catalogo)))

    def melhor_cobertura(pergunta):
        # zeramos os limiares para colher a cobertura crua e varrer depois
        from api import confianca
        alto, baixo = confianca.LIMIAR_ALTO, confianca.LIMIAR_BAIXO
        confianca.LIMIAR_ALTO = confianca.LIMIAR_BAIXO = -1.0
        try:
            r = b.buscar(pergunta, principais=3, extras=3)
        finally:
            confianca.LIMIAR_ALTO, confianca.LIMIAR_BAIXO = alto, baixo
        return max([x["cobertura"] for x in r["principais"]], default=0.0)

    print("Medindo…")
    dentro = [(q, melhor_cobertura(q)) for q in DENTRO]
    fora = [(q, melhor_cobertura(q)) for q in FORA]

    print("\n=== varredura do limiar ===")
    print("limiar | responde certo | falso positivo | falso negativo | acurácia")
    melhor_limiar, melhor_acuracia = None, -1
    for passo in range(10, 71, 2):
        limiar = passo / 100.0
        certos = sum(1 for _, c in dentro if c >= limiar)
        fp = sum(1 for _, c in fora if c >= limiar)
        fn = len(dentro) - certos
        acuracia = (certos + (len(fora) - fp)) / float(len(dentro) + len(fora))
        marca = ""
        if acuracia > melhor_acuracia:
            melhor_acuracia, melhor_limiar, marca = acuracia, limiar, "  <<<"
        print("  {:.2f} | {:>13d}/{:d} | {:>14d} | {:>14d} | {:>7.1%}{}".format(
            limiar, certos, len(dentro), fp, fn, acuracia, marca))

    print("\nMelhor acurácia com limiar único: {:.1%} em {:.2f}".format(
        melhor_acuracia, melhor_limiar))

    from api.confianca import LIMIAR_ALTO, LIMIAR_BAIXO
    print("\n=== operação em três faixas (ALTO={:.2f}, BAIXO={:.2f}) ===".format(
        LIMIAR_ALTO, LIMIAR_BAIXO))
    d_alta = sum(1 for _, c in dentro if c >= LIMIAR_ALTO)
    d_parc = sum(1 for _, c in dentro if LIMIAR_BAIXO <= c < LIMIAR_ALTO)
    d_neg = len(dentro) - d_alta - d_parc
    f_alta = sum(1 for _, c in fora if c >= LIMIAR_ALTO)
    f_parc = sum(1 for _, c in fora if LIMIAR_BAIXO <= c < LIMIAR_ALTO)
    f_neg = len(fora) - f_alta - f_parc
    print("  perguntas DENTRO do acervo ({}): {} respondidas direto | {} com ressalva"
          " | {} recusadas por engano".format(len(dentro), d_alta, d_parc, d_neg))
    print("  perguntas FORA do acervo  ({}): {} respondidas por engano | {} com"
          " ressalva | {} recusadas certo".format(len(fora), f_alta, f_parc, f_neg))
    print("\n  >> erro que mais incomoda (responde errado SEM avisar): {}/{} = {:.1%}"
          .format(f_alta, len(fora), f_alta / float(len(fora))))
    print("  >> pergunta boa perdida (recusa indevida): {}/{} = {:.1%}"
          .format(d_neg, len(dentro), d_neg / float(len(dentro))))

    print("\n=== casos limítrofes no limiar escolhido ({:.2f}) ===".format(melhor_limiar))
    for q, c in sorted(fora, key=lambda x: -x[1])[:5]:
        estado = "FALSO POSITIVO" if c >= melhor_limiar else "ok (nega)"
        print("  fora   {:.3f}  {:<45s} {}".format(c, q[:45], estado))
    for q, c in sorted(dentro, key=lambda x: x[1])[:5]:
        estado = "FALSO NEGATIVO" if c < melhor_limiar else "ok (responde)"
        print("  dentro {:.3f}  {:<45s} {}".format(c, q[:45], estado))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
