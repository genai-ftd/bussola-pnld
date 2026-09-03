"""Extração determinística de metadados de obra (coleção, disciplina, volume/ano).

Sem LLM: só regex e tabelas de sinônimos. Usado tanto na ingestão (a partir do
título do Issuu / nome do arquivo) quanto na busca (a partir da pergunta do professor).
"""
import re
import unicodedata

# --- normalização ------------------------------------------------------------

def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")


def norm(s: str) -> str:
    """minúsculas, sem acento, sem pontuação — para casamento de palavra-chave."""
    s = strip_accents(s or "").lower()
    return re.sub(r"[^a-z0-9\s]+", " ", s)


# --- vocabulário controlado --------------------------------------------------

COLECOES = {
    "plantar": "Plantar",
    "entrelacos": "Entrelaços",
    "a conquista": "A Conquista",
    "conquista": "A Conquista",
    "baoba": "Baobá",
    # coleções de língua estrangeira, que não seguem o nome das demais
    "step by step": "Step by Step",
    "pasitos": "Pasitos",
}

# ordem importa: chaves mais específicas primeiro
DISCIPLINAS = [
    ("lingua portuguesa", "Língua Portuguesa"),
    ("portugues", "Língua Portuguesa"),
    ("producao de texto", "Produção de Texto"),
    ("lingua espanhola", "Língua Espanhola"),
    ("espanhol", "Língua Espanhola"),
    ("lingua inglesa", "Língua Inglesa"),
    ("ingles", "Língua Inglesa"),
    ("matematica", "Matemática"),
    ("ciencias da natureza", "Ciências da Natureza"),
    ("ciencias", "Ciências da Natureza"),
    ("geografia", "Geografia"),
    ("historia", "História"),
    ("educacao fisica", "Educação Física"),
    ("ed fisica", "Educação Física"),
    ("educacao digital", "Educação Digital"),
    ("eddigital", "Educação Digital"),
    ("arte", "Arte"),
]

ORDINAIS = {
    "primeiro": 1, "1": 1, "1o": 1, "1º": 1, "1ª": 1, "um": 1,
    "segundo": 2, "2": 2, "2o": 2, "2º": 2, "2ª": 2, "dois": 2,
    "terceiro": 3, "3": 3, "3o": 3, "3º": 3, "3ª": 3, "tres": 3,
    "quarto": 4, "4": 4, "4o": 4, "4º": 4, "4ª": 4, "quatro": 4,
    "quinto": 5, "5": 5, "5o": 5, "5º": 5, "5ª": 5, "cinco": 5,
}


def detect_colecao(text: str):
    n = norm(text)
    for key, label in COLECOES.items():
        if re.search(r"\b" + re.escape(key) + r"\b", n):
            return label
    return None


def detect_disciplina(text: str):
    n = norm(text)
    for key, label in DISCIPLINAS:
        if re.search(r"\b" + re.escape(key) + r"\b", n):
            return label
    return None


def anos_multiplos(text: str):
    """Volumes que atendem mais de um ano: "1° e 2° anos", "3°, 4° e 5° anos"."""
    n = norm(text)
    if not re.search(r"\b[1-5]\s*(?:o|a)?\s*(?:,|e)\s*[1-5]", n):
        return []
    return sorted({int(d) for d in re.findall(r"\b([1-5])\s*(?:o|a)?\s*(?=[,e\s]|anos)", n)})


def detect_ano(text: str):
    """Ano/volume citado no texto. Retorna int 1..5 ou None.

    Cobre '3º ano', '3 ano', 'terceiro ano', 'volume 3', 'vol 3', '3ano'.
    """
    n = norm(text)
    m = re.search(r"\b([1-5])\s*(?:o|a)?\s*(?:ano|serie)\b", n)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(?:volume|vol)\s*([1-5])\b", n)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(primeiro|segundo|terceiro|quarto|quinto)\s+(?:ano|serie)\b", n)
    if m:
        return ORDINAIS[m.group(1)]
    return None


def parse_obra(title: str, filename: str = "") -> dict:
    """Metadados da obra a partir do título do Issuu e do nome do arquivo.

    Num bloco do acervo o título está deslocado um registro em relação ao
    arquivo, e o volume sai errado: "Plantar_Matemática_Volume 2.pdf" está
    publicado como "Volume 1". Conferimos abrindo o PDF — ele diz 2º ano nas
    páginas de abertura. Por isso o ano vem do NOME DO ARQUIVO quando os dois
    discordam: o nome é o que veio no upload, o título foi digitado depois.
    """
    nome_limpo = (filename or "").replace("_", " ").replace("-", " ")
    fonte = "{} {}".format(title or "", nome_limpo)
    multiplos = anos_multiplos(title or "")
    volume_unico = bool(re.search(r"volume\s*unico", norm(fonte))) or len(multiplos) > 1

    ano_arquivo = detect_ano(nome_limpo)
    ano_titulo = detect_ano(title or "")
    ano = ano_arquivo if ano_arquivo is not None else ano_titulo

    return {
        "colecao": detect_colecao(fonte),
        "disciplina": detect_disciplina(fonte),
        "ano": None if volume_unico else ano,
        "volume_unico": volume_unico,
        "anos": multiplos,          # volumes que cobrem mais de um ano
        "ano_divergente": bool(ano_arquivo and ano_titulo and ano_arquivo != ano_titulo),
    }


# Moldura do pedido: o que vem antes do assunto de verdade. Só é removida no
# INÍCIO da pergunta — "atividades de pesquisa com os estudantes" é assunto
# legítimo e não pode ser desmontado só porque contém "pesquisa".
# radicais + terminações, para pegar infinitivo e gerúndio ("procurar",
# "procurando") sem precisar listar cada conjugação
_VERBOS_PEDIDO = (r"(?:pesquis|busc|procur|sab|conhec|ach|encontr|consult|"
                  r"localiz|mostr|indic|suger|fal|explic|ajud|trat)"
                  r"(?:ar|er|ir|ando|endo|indo|a|e|o)"
                  r"|ver|vendo|vejo|dizer|dar")
_INICIO_PEDIDO = re.compile(
    r"^\s*(?:(?:eu|voce|vc)\s+)?"
    r"(?:(?:quero|queria|gostaria(?:\s+de)?|preciso|pode|poderia|podes|me|"
    r"estou|to|tou|ando|o\s+que|oque|qual|quais|onde|tem|tens|existe|ha|"
    r"a|de|para|pra|que)\s+)*"
    r"(?:(?:" + _VERBOS_PEDIDO + r")(?:ndo|r)?\s+)*",
    re.I)
# Marcador que separa moldura de assunto sem ambiguidade nenhuma
_MARCADOR_ASSUNTO = re.compile(
    r"\b(?:sobre|a\s+respeito\s+de|acerca\s+de|referente\s+a|em\s+rela[cç][aã]o\s+a)\b",
    re.I)


def extrair_assunto(pergunta: str) -> str:
    """Separa o assunto da moldura do pedido.

    "Quero pesquisar sobre Sistema Solar" precisa virar "Sistema Solar": sem
    isto, "pesquisar" entra como termo de busca e a Bússola vai atrás do verbo
    dentro dos livros.

    Duas regras, nesta ordem:
      1. se houver marcador ("sobre", "a respeito de"), o assunto é o que vem
         depois dele — é o separador mais confiável do português;
      2. senão, corta a moldura só do começo da frase.
    Se sobrar vazio, devolve a pergunta inteira.
    """
    texto = (pergunta or "").strip()
    marcador = _MARCADOR_ASSUNTO.search(texto)
    if marcador:
        assunto = texto[marcador.end():].strip(" ,:;?!")
        if assunto:
            return assunto
    assunto = _INICIO_PEDIDO.sub("", texto, count=1).strip(" ,:;?!")
    return assunto or texto


_EXPRESSOES_FILTRO = [
    r"\b[1-5]\s*(?:o|a)?\s*(?:ano|serie)\b",
    r"\b(?:volume|vol)\s*[1-5]\b",
    r"\b(?:primeiro|segundo|terceiro|quarto|quinto)\s+(?:ano|serie)\b",
]


def remover_termos_de_filtro(pergunta: str) -> str:
    """Tira da pergunta o que já virou filtro de metadado.

    "atividades com cantigas no 1º ano" tem o ano lido como filtro de obra. Se
    "ano" seguisse valendo como termo de busca, ele competiria duas vezes: puxaria
    para o topo páginas que só falam "ano", entraria no cálculo de cobertura e
    ainda apareceria realçado na citação no lugar de "cantigas".
    """
    limpo = norm(pergunta)
    for expressao in _EXPRESSOES_FILTRO:
        limpo = re.sub(expressao, " ", limpo)
    for chave, _ in COLECOES.items():
        limpo = re.sub(r"\b" + re.escape(chave) + r"\b", " ", limpo)
    for chave, _ in DISCIPLINAS:
        limpo = re.sub(r"\b" + re.escape(chave) + r"\b", " ", limpo)
    return limpo


def parse_pergunta(pergunta: str) -> dict:
    """Filtros implícitos na pergunta do professor (sem LLM)."""
    return {
        "colecao": detect_colecao(pergunta),
        "disciplina": detect_disciplina(pergunta),
        "ano": detect_ano(pergunta),
    }


# --- apresentação ------------------------------------------------------------

_PREFIXOS = re.compile(
    r"^\s*PNLD\s*20\d\d\s*(?:-\s*)?(?:EFAI|Anos\s+Inicia(?:is|s))?\s*-\s*",
    re.I)


def titulo_curto(titulo: str) -> str:
    """Tira o prefixo que se repete em toda obra do acervo.

    "PNLD 2027 Anos Iniciais - Plantar - Arte - Volume 1" vira
    "Plantar - Arte - Volume 1". Numa lista de três cartões, o prefixo ocupava a
    primeira linha de todos e não distinguia nada — a coleção e o componente já
    aparecem nas etiquetas.
    """
    curto = _PREFIXOS.sub("", titulo or "").strip(" -")
    return curto or (titulo or "")
