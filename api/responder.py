"""Montagem determinística da resposta conversacional.

Sem LLM e sem geração livre: a frase sai de uma lista de variantes escolhida por
regra, preenchida com os metadados recuperados. As variantes existem para o chat
não soar repetitivo em uso real — e a escolha é semeada pela própria pergunta,
então a mesma pergunta sempre devolve a mesma frase (dá para reproduzir um teste).

Três estados, conforme api/confianca.py:
  alta     — responde direto
  parcial  — responde com ressalva explícita
  nenhuma  — diz que não encontrou, e diz o que faltou
"""
import random
import re
import unicodedata

# ------------------------------------------------------------------ variantes

# Oito variantes em vez de quatro, alternando quem abre a frase (nome, verbo,
# objeto) para o chat não soar recitado quando o professor faz dez buscas
# seguidas. Termo sempre igual — "trecho", nunca "resultado" ou "match".
ABERTURAS = [
    "{nome}, encontrei {n} {palavra} que {verbo} com a sua busca:",
    "Achei {n} {palavra} sobre isso, {nome}:",
    "{nome}, isto é o que o acervo do PNLD 2027 traz sobre o tema:",
    "Encontrei o seguinte nas obras indexadas, {nome}:",
    "Veja o que encontrei, {nome}:",
    "{nome}, estes {palavra} respondem à sua busca:",
    "O acervo traz isto sobre o tema, {nome}:",
    "Encontrei {n} {palavra} sobre o assunto, {nome}:",
]

ABERTURAS_PARCIAIS = [
    "{nome}, não tenho certeza de que é isto que você procura — foi o mais "
    "próximo que encontrei:",
    "Achei algo relacionado, mas pode não ser exatamente o que você pediu, {nome}:",
    "{nome}, isto tangencia a sua pergunta. Se não for o que você queria, tenta "
    "reformular pelo tema da aula:",
    "Encontrei correspondência fraca para essa pergunta, {nome}. Vale conferir "
    "antes de usar:",
    "{nome}, a correspondência aqui é fraca. Confira antes de levar para a aula:",
    "Isto pode não ser o que você procura, {nome}, mas foi o mais perto que cheguei:",
    "Não tenho certeza desta, {nome}. Se eu errei o alvo, nomear o componente ajuda:",
    "Achei só algo aproximado, {nome}. Vale conferir na página antes de usar:",
]

# Quando dá para nomear o que faltou, a mensagem fica muito mais útil: o
# professor entende na hora que o assunto não está no acervo, e não que a
# ferramenta falhou.
SEM_RESULTADO_COM_TERMO = [
    "Não achei nada sobre {termo} nas obras que estão indexadas, {nome}. Quer "
    "tentar por outro caminho — o tema da aula, o gênero textual ou a habilidade "
    "da BNCC?",
    "{nome}, {termo} não aparece em nenhuma página do que eu tenho indexado. Se "
    "for outro nome para a mesma coisa, me diz que eu procuro de novo.",
    # Sem enumerar disciplinas: a lista que estava aqui foi escrita quando o
    # acervo tinha 5 obras e continuou dizendo "Língua Portuguesa, Espanhola e
    # Arte" depois que virou 13, com nove componentes. Copy não afirma fato que
    # muda sem ela saber.
    "Procurei {termo} e não encontrei. O acervo indexado ainda é uma amostra "
    "do PNLD 2027, então pode ser que o tema esteja numa obra que ainda não "
    "entrou.",
    "{termo} não está nas obras que eu tenho aqui, {nome}. Pode ser que esteja "
    "num volume que ainda não entrou no índice.",
    "{termo} não aparece nas obras indexadas. Se o assunto tiver outro nome no "
    "material, me diz qual e eu procuro de novo.",
    "Nada sobre {termo} por aqui, {nome}. O tema da aula ou o código da BNCC "
    "costumam encontrar melhor do que o termo isolado.",
    "Não localizei {termo}, {nome}. Pode ser que a obra que trata disso ainda "
    "não esteja no índice.",
    "{nome}, {termo} não está no que eu indexei. Prefiro te dizer isso a te "
    "mandar para uma página que não responde.",
]

SEM_RESULTADO_GENERICO = [
    "Não encontrei nada suficientemente próximo disso, {nome}. Tenta descrever "
    "pelo conteúdo da aula — por exemplo, \"atividades de leitura para o 3º ano\".",
    "Essa eu não sei responder com o que está indexado, {nome}. Dizer o ano e o "
    "componente curricular costuma ajudar.",
    "Não achei correspondência boa o bastante para te mostrar. Prefiro dizer isso "
    "a te mandar para uma página errada.",
    "{nome}, essa passou longe do que eu tenho indexado. Quer tentar com outras "
    "palavras?",
    "Não consegui casar sua pergunta com nenhuma página, {nome}. Uma palavra mais "
    "específica costuma resolver.",
    "{nome}, não encontrei. Se você souber o código da BNCC, ele acha na hora.",
    "Nada suficientemente próximo por aqui. Tenta nomear o assunto da aula em vez "
    "da pergunta inteira.",
    "Não achei nada que responda isso, {nome}. Escolher o componente aqui embaixo "
    "costuma estreitar bem a busca.",
]

ACERVO_VAZIO = (
    "{nome}, ainda não há obras indexadas nesta instalação. "
    "Rode a ingestão (`python ingest/build_index.py`) para começar."
)

ROTULO_VIZINHOS = "O que existe de mais próximo — assunto vizinho, não resposta:"

# Consulta de referência (código da BNCC, trecho entre aspas) quer índice
# remissivo, não amostra: a frase diz o total encontrado e quanto está à vista.
REFERENCIA = "{alvo} aparece em {paginas} {p_palavra}, {obras}."
REFERENCIA_PARCIAL = (" Estas são as {mostradas} primeiras, em ordem de obra e "
                      "página:")
REFERENCIA_TODAS = " Estas são todas:"
# "Você quis dizer X?" — a correção é anunciada, nunca silenciosa: o professor
# precisa saber que a busca não foi a que ele digitou.
CORRECAO = "Você quis dizer {corrigida}? "
SEM_REFERENCIA = ("{nome}, não encontrei {alvo} em nenhuma página das obras "
                  "indexadas. Confira o código — ou pode ser que a obra que o "
                  "traz ainda não esteja no índice.")
SEM_REFERENCIA_VARIANTES = [
    SEM_REFERENCIA,
    "{alvo} não aparece nas obras indexadas, {nome}. Vale conferir o código; se "
    "estiver certo, é a obra que ainda não entrou no índice.",
    "Não localizei {alvo} em nenhuma página, {nome}. Confira se o código está "
    "completo — eu procuro com ou sem o \"EF\" na frente.",
]

# ------------------------------------------------------------------ auxiliares


def _sem_acento(s):
    return "".join(c for c in unicodedata.normalize("NFD", s)
                   if unicodedata.category(c) != "Mn")


def termo_original(pergunta, token):
    """Recupera a palavra como o professor escreveu, a partir do token normalizado.

    O índice trabalha em minúsculas e sem acento; devolver "pascoa" na mensagem
    ficaria estranho quando a pessoa escreveu "Páscoa".
    """
    for palavra in re.findall(r"\w+", pergunta, re.UNICODE):
        if _sem_acento(palavra).lower() == token:
            return palavra
    return token


def descrever_verificacao(verificacoes):
    """Frase sobre o que existe ou não existe, montada da contagem no acervo.

    `verificacoes` é uma lista de (rótulo, {"trechos": n, "obras": [...]}).
    Existe para nenhuma resposta guiada precisar afirmar ausência por conta
    própria: a frase se refaz sozinha quando o acervo muda.
    """
    ausentes = [rot for rot, r in verificacoes if not r["trechos"]]
    presentes = [(rot, r) for rot, r in verificacoes if r["trechos"]]
    partes = []

    if ausentes:
        partes.append("Não encontrei {} em nenhuma página das obras indexadas."
                      .format(_lista_natural(ausentes)))

    unicos = [(rot, r) for rot, r in presentes if r["trechos"] == 1]
    varios = [(rot, r) for rot, r in presentes if r["trechos"] > 1]

    for rotulo, r in unicos:
        onde = r["obras"][0] if r["obras"] else "uma única página"
        partes.append("{} aparece uma única vez em todo o acervo, em {} — menção "
                      "de passagem, não conteúdo.".format(_maiuscula(rotulo), onde))
    if varios:
        itens = ["{} em {} trechos".format(rot, r["trechos"]) for rot, r in varios]
        partes.append("{}.".format(_maiuscula(_lista_natural(itens)).replace(
            " em ", " aparece em ", 1)))
    return " ".join(partes)


def _lista_natural(itens):
    if len(itens) == 1:
        return itens[0]
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _maiuscula(texto):
    return texto[:1].upper() + texto[1:] if texto else texto


def _descrever_obra(r):
    partes = [p for p in [r.get("colecao"), r.get("disciplina")] if p]
    if r.get("ano"):
        partes.append("{}º ano".format(r["ano"]))
    return " · ".join(partes) if partes else r.get("titulo", "")


def _descrever_pagina(r):
    """O número que o professor procura no livro vem primeiro; o do leitor é
    encanamento e fica como nota."""
    onde = "leitor" if r.get("offset_confiavel") else "PDF"
    destino = r["pagina_issuu"] if r.get("offset_confiavel") else r["pagina_fisica"]
    if r.get("pagina_impressa"):
        return "Página {} do livro · {} no {}".format(
            r["pagina_impressa"], destino, onde)
    return "Página {} do {}".format(destino, onde)


def _descrever_filtros(filtros):
    """Conta ao professor o recorte que foi aplicado — e de onde ele veio.

    O que ele escreveu e o que a ferramenta deduziu não podem sair com a mesma
    frase: quando o palpite erra, ele precisa perceber que a restrição foi
    decisão nossa, para poder desfazê-la.
    """
    pedidos = []
    if filtros.get("ano"):
        pedidos.append("{}º ano".format(filtros["ano"]))
    if filtros.get("colecao"):
        pedidos.append("coleção {}".format(filtros["colecao"]))
    disciplina = filtros.get("disciplina")
    inferida = filtros.get("disciplina_inferida")
    if disciplina and not inferida:
        pedidos.append(disciplina)

    frases = []
    if pedidos:
        frases.append(" Priorizei o que é de {}.".format(" · ".join(pedidos)))
    if disciplina and inferida:
        frases.append(" Entendi como pergunta de {}; se não for, me diz o "
                      "componente.".format(disciplina))
    return "".join(frases)


def _cartao(r):
    c = dict(r)
    c["descricao_obra"] = _descrever_obra(r)
    c["descricao_pagina"] = _descrever_pagina(r)
    return c


def _escolher(variantes, semente):
    return random.Random(semente).choice(variantes)


# -------------------------------------------------------------------- montagem


def _tratar_vocativo(texto, nome):
    """Sem nome, tira o vocativo em vez de chamar todo mundo de "Professor(a)".

    A pergunta do nome saiu do chat: os testadores digitavam a busca primeiro e
    a ferramenta passava a chamá-los de "instrumentos musicais". Melhor não ter
    nome nenhum do que ter um errado — e as frases funcionam sem ele.
    """
    if nome:
        return texto.replace("{nome}", nome)
    texto = re.sub(r"\{nome\}, *", "", texto)
    texto = re.sub(r",? *\{nome\}", "", texto)
    return texto[:1].upper() + texto[1:] if texto else texto


def montar_resposta(resultado, nome=None, acervo_vazio=False, guiada=None):
    nome = (nome or "").strip()
    if acervo_vazio:
        return {"texto": _tratar_vocativo(ACERVO_VAZIO, nome), "resultados": [],
                "tambem_encontrei": [], "rotulo": None, "confianca": "nenhuma"}

    pergunta = resultado.get("pergunta", "")
    principais = resultado.get("principais", [])
    vizinhos = resultado.get("vizinhos", [])

    # 1. pergunta sobre a coleção: panorama curado + páginas onde aquilo aparece
    if guiada:
        texto_guiado = guiada["texto"]
        if "{verificacao}" in texto_guiado:
            texto_guiado = texto_guiado.replace(
                "{verificacao}", resultado.get("verificacao", "").strip())
        mostra = principais if guiada["modo"] == "responde" else []
        apoio = vizinhos or resultado.get("tambem_encontrei", [])
        if guiada["modo"] == "sem_conteudo":
            apoio = (principais + vizinhos)[:3]
        return {
            "texto": texto_guiado,
            "resultados": [_cartao(r) for r in mostra],
            "tambem_encontrei": [] if guiada["modo"] == "responde"
                                else [_cartao(r) for r in apoio],
            "rotulo": None if guiada["modo"] == "responde" else ROTULO_VIZINHOS,
            "confianca": "guiada",
        }

    # 2. consulta de referência: quantas páginas, em quantas obras, e a lista
    if resultado.get("modo") == "referencia":
        alvo = resultado.get("alvo", "")
        total = resultado.get("total_paginas", 0)
        if not total:
            molde = _escolher(SEM_REFERENCIA_VARIANTES, pergunta or alvo)
            return {"texto": _tratar_vocativo(molde.replace("{alvo}", alvo), nome),
                    "resultados": [], "tambem_encontrei": [], "rotulo": None,
                    "confianca": "nenhuma", "termos": resultado.get("termos", [])}
        obras = resultado.get("total_obras", 1)
        mostradas = len(principais)
        texto = REFERENCIA.format(
            alvo=alvo, paginas=total,
            p_palavra="página" if total == 1 else "páginas",
            obras="numa obra" if obras == 1 else "em {} obras".format(obras))
        texto += (REFERENCIA_TODAS if mostradas >= total
                  else REFERENCIA_PARCIAL.format(mostradas=mostradas))
        return {
            "texto": texto,
            "termos": resultado.get("termos", []),
            "resultados": [_cartao(r) for r in principais],
            "tambem_encontrei": [],
            "rotulo": None,
            "confianca": "referencia",
        }

    correcao = resultado.get("correcao")
    prefixo = (CORRECAO.format(corrigida=correcao["para"].strip())
               if correcao else "")

    confianca = resultado.get("confianca", "nenhuma")

    # 2. nada ancorado no acervo: dizer que não sabe, e dizer o que faltou
    if confianca == "nenhuma" or not principais:
        ausentes = resultado.get("termos_ausentes") or []
        if ausentes:
            token = max(ausentes, key=len)
            texto = _tratar_vocativo(
                _escolher(SEM_RESULTADO_COM_TERMO, pergunta).replace(
                    "{termo}", termo_original(pergunta, token)), nome)
        else:
            texto = _tratar_vocativo(_escolher(SEM_RESULTADO_GENERICO, pergunta), nome)
        return {
            "texto": prefixo + texto,
            "correcao": correcao,
            "resultados": [],
            "tambem_encontrei": [_cartao(r) for r in vizinhos[:3]],
            "rotulo": ROTULO_VIZINHOS if vizinhos else None,
            "confianca": "nenhuma",
        }

    # 3. resposta, com ou sem ressalva
    n = len(principais)
    if confianca == "parcial":
        texto = _tratar_vocativo(_escolher(ABERTURAS_PARCIAIS, pergunta), nome)
    else:
        texto = _tratar_vocativo(
            _escolher(ABERTURAS, pergunta)
            .replace("{n}", str(n))
            .replace("{palavra}", "trecho" if n == 1 else "trechos")
            .replace("{verbo}", "conversa" if n == 1 else "conversam"),
            nome) + _descrever_filtros(resultado.get("filtros", {}))

    return {
        "texto": prefixo + texto,
        "assunto": resultado.get("assunto", ""),
        "termos": resultado.get("termos", []),
        "correcao": correcao,
        "resultados": [_cartao(r) for r in principais],
        "ocorrencias": resultado.get("ocorrencias", []),
        "tambem_encontrei": [_cartao(r) for r in resultado.get("tambem_encontrei", [])],
        "rotulo": None,
        "confianca": confianca,
    }
