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
    """Metadados da obra a partir do título do Issuu, com o nome do arquivo como reforço."""
    fonte = "{} {}".format(title or "", (filename or "").replace("_", " ").replace("-", " "))
    volume_unico = bool(re.search(r"volume\s*unico", norm(fonte)))
    return {
        "colecao": detect_colecao(fonte),
        "disciplina": detect_disciplina(fonte),
        "ano": None if volume_unico else detect_ano(fonte),
        "volume_unico": volume_unico,
    }


def parse_pergunta(pergunta: str) -> dict:
    """Filtros implícitos na pergunta do professor (sem LLM)."""
    return {
        "colecao": detect_colecao(pergunta),
        "disciplina": detect_disciplina(pergunta),
        "ano": detect_ano(pergunta),
    }
