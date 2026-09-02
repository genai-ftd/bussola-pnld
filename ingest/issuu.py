"""Cliente mínimo da API do Issuu — SOMENTE metadados.

Conforme investigação prévia, os endpoints de assets (texto/imagem de página)
não retornam conteúdo útil para estas publicações (ver README, "Limitações").
Aqui usamos apenas GET /v2/publications, para descobrir o `publicLocation`
de cada obra e montar o link "Abrir conteúdo".
"""
import json
import os
import time
import urllib.parse

import requests

API = "https://api.issuu.com/v2"


class IssuuError(RuntimeError):
    pass


def _token() -> str:
    tok = os.environ.get("ISSUU_TOKEN", "").strip()
    if not tok:
        raise IssuuError(
            "ISSUU_TOKEN não definido. Copie .env.example para .env e preencha, "
            "ou rode a ingestão com --sem-issuu."
        )
    return tok


def listar_publicacoes(page_size: int = 100, max_paginas: int = 20, parar_quando=None):
    """Itera as publicações da conta, página a página.

    `parar_quando(acumulado)` permite encerrar a paginação assim que todas as
    obras locais já tiverem sido casadas — evita varrer o acervo inteiro.
    """
    headers = {"Authorization": "Bearer " + _token()}
    acumulado = []
    for pagina in range(1, max_paginas + 1):
        params = {"size": page_size, "page": pagina}
        resp = requests.get(API + "/publications", headers=headers, params=params, timeout=60)
        if resp.status_code == 401:
            raise IssuuError("Issuu retornou 401 — token inválido ou expirado.")
        resp.raise_for_status()
        data = resp.json()
        results = data.get("results", [])
        acumulado.extend(results)
        if parar_quando and parar_quando(acumulado):
            break
        if not data.get("links", {}).get("next") or not results:
            break
        time.sleep(0.2)  # cortesia com a API
    return acumulado


def chave_arquivo(nome: str) -> str:
    """`fileInfo.name` vem URL-encoded (ex.: 'Plantar%20_arte.pdf'). Normaliza."""
    return urllib.parse.unquote(nome or "").strip().lower()


def casar_publicacao(publicacoes, nome_arquivo: str, tamanho_bytes: int, num_paginas: int):
    """Casa um PDF local com a publicação do Issuu.

    Estratégia, da mais forte para a mais fraca:
      1. tamanho em bytes idêntico (é literalmente o mesmo arquivo);
      2. nome do arquivo idêntico (após URL-decode) + mesmo nº de páginas;
      3. nome do arquivo idêntico.
    Deliberadamente NÃO casamos por título: há publicações no acervo cujo
    título não corresponde ao arquivo enviado (ver README, "Limitações").
    """
    alvo = chave_arquivo(nome_arquivo)
    por_nome = []
    for p in publicacoes:
        fi = p.get("fileInfo") or {}
        if fi.get("size") == tamanho_bytes and tamanho_bytes:
            return p, "tamanho-do-arquivo"
        if chave_arquivo(fi.get("name")) == alvo:
            por_nome.append(p)
    for p in por_nome:
        if (p.get("fileInfo") or {}).get("pageCount") == num_paginas:
            return p, "nome-do-arquivo+paginas"
    if por_nome:
        return por_nome[0], "nome-do-arquivo"
    return None, None


def link_pagina(public_location: str, pagina_documento: int) -> str:
    """Link direto para a página N do leitor público do Issuu.

    ATENÇÃO: `pagina_documento` é a página FÍSICA do documento (a capa é 1),
    não o número impresso no rodapé. Ver `paginas.py` / README.
    """
    if not public_location:
        return ""
    return "{}/{}".format(public_location.rstrip("/"), int(pagina_documento))
