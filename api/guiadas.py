"""Respostas guiadas para perguntas sobre a coleção como um todo.

Estas perguntas ("quais recursos de acessibilidade?", "que material de apoio
vem pro professor?") não são busca por trecho: o professor quer um panorama, e
um cartão solto de uma página não responde. Aqui a Bússola dá o panorama e
depois mostra as páginas onde aquilo aparece de fato.

Toda afirmação abaixo foi conferida contra o texto extraído das obras indexadas.
Onde o acervo não cobre o assunto — turmas multisseriadas é o caso — a resposta
diz isso com todas as letras, em vez de empurrar a página mais parecida.
Detecção por palavra-chave, determinística, sem LLM.
"""
import re

from ingest.metadata import norm

GUIADAS = [
    {
        "id": "habilidades_leitura",
        "padroes": [r"habilidad\w*.*leitura", r"leitura.*habilidad\w*",
                    r"bncc.*leitura", r"leitura.*bncc",
                    r"habilidad\w*.*(previst|esperad)"],
        "consulta": "habilidades de leitura previstas para o ano na BNCC "
                    "quadro de distribuição de conteúdos",
        "modo": "responde",
        "texto": (
            "Nas obras que estão indexadas, sim. Cada volume traz um quadro de "
            "distribuição de conteúdos que mostra a progressão por unidade, com as "
            "habilidades, as competências e os temas contemporâneos transversais da "
            "BNCC. E as orientações de cada seção citam os códigos direto no texto — "
            "EF01LP01, EF15LP02, EF12LP08 e por aí.\n\n"
            "Se você me disser o ano e o componente, eu vou direto no quadro do "
            "volume certo. Veja onde isso aparece:"
        ),
    },
    {
        "id": "multisseriadas",
        "padroes": [r"multisseriad", r"multi seriad", r"classes? multi"],
        "consulta": "organização da sala de aula agrupamentos pequenos e grandes grupos "
                    "atendimento a diferentes ritmos de aprendizagem",
        "modo": "sem_conteudo",
        "texto": (
            "Não encontrei. O termo \"multisseriadas\" não aparece em nenhuma das "
            "páginas das obras indexadas, então eu não tenho como te dizer como a "
            "coleção trata isso.\n\n"
            "O que existe é assunto vizinho, não resposta: orientações sobre organizar "
            "a sala de formas diferentes, trabalhar em pequenos e grandes grupos e "
            "atender ritmos distintos de aprendizagem. Deixo abaixo caso ajude — mas "
            "prefiro te dizer que não sei a te mostrar uma página que não responde."
        ),
    },
    {
        "id": "acessibilidade",
        "padroes": [r"acessibilidad", r"recursos?.*inclus", r"inclus\w*.*recursos?",
                    r"defici[êe]nc", r"\blibras\b", r"braile|braille", r"audiodescri"],
        "consulta": "inclusão de estudantes com deficiência acessibilidade "
                    "adaptações e estratégias para a sala de aula",
        "modo": "responde",
        "texto": (
            "Preciso separar duas coisas aqui, porque elas costumam se confundir.\n\n"
            "O que eu encontro nas obras indexadas é orientação pedagógica sobre "
            "inclusão: há uma seção \"A inclusão nas escolas\" no manual do professor, "
            "orientações sobre adaptar atividades e referências sobre inclusão de "
            "estudantes com deficiência intelectual. Há também atividades que tratam "
            "acessibilidade como tema com os estudantes — rampas, piso tátil, braile, "
            "sinais sonoros.\n\n"
            "O que eu não encontrei foi uma lista de recursos de acessibilidade do "
            "próprio material, do tipo audiodescrição, versão em braile ou Libras. Se "
            "for isso que você precisa, vale confirmar direto com a editora. As "
            "páginas sobre inclusão:"
        ),
    },
    {
        "id": "material_professor",
        "padroes": [r"material.*(apoio|professor)", r"apoio.*professor",
                    r"manual do professor", r"o que vem (junto|incluso|incluído)",
                    r"recursos?.*professor"],
        "consulta": "manual do professor referências complementares estratégias de "
                    "avaliação fundamentos teórico-metodológicos da coleção",
        "modo": "responde",
        "texto": (
            "O Manual do Professor vem no mesmo volume, na segunda parte. Ele traz os "
            "fundamentos teórico-metodológicos da coleção, a estrutura da BNCC, "
            "estratégias de avaliação e o quadro de distribuição dos conteúdos.\n\n"
            "Fora isso, as unidades têm \"Referências Complementares\", com sugestões "
            "de filmes, livros, sites e documentários voltados à formação docente. "
            "Onde ver:"
        ),
    },
]

_COMPILADAS = [(g, [re.compile(p) for p in g["padroes"]]) for g in GUIADAS]


def detectar(pergunta: str):
    """Devolve a resposta guiada correspondente à pergunta, ou None."""
    n = norm(pergunta)
    for guiada, padroes in _COMPILADAS:
        if any(p.search(n) for p in padroes):
            return guiada
    return None
