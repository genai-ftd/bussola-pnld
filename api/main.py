"""API da Bússola PNLD — busca semântica no acervo do PNLD 2027.

Sobe com:  ./scripts/run_dev.sh   (ou: uvicorn api.main:app --reload)
O front-end estático é servido na raiz: http://127.0.0.1:8000/
"""
import asyncio
import functools
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.guiadas import detectar as detectar_guiada
from api.responder import montar_resposta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_FRONT = os.path.join(RAIZ, "frontend")
DIR_REGISTROS = os.path.join(RAIZ, "data", "registros")
load_dotenv(os.path.join(RAIZ, ".env"))

app = FastAPI(title="Bússola PNLD — API de busca", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],          # POC local; restringir antes de qualquer deploy
    allow_methods=["*"],
    allow_headers=["*"],
)

_buscador = None
_erro_carga = None

# A busca é CPU-bound (Torch + NumPy) e há uma única instância do modelo. Se as
# requisições caíssem no threadpool padrão do FastAPI, dezenas delas entrariam
# no modelo ao mesmo tempo, disputando o GIL e as threads do Torch — a latência
# cresceria proporcional à concorrência. Uma fila de um trabalhador serializa o
# acesso: quem chega espera, mas cada busca roda na velocidade máxima.
_FILA_BUSCA = ThreadPoolExecutor(max_workers=1, thread_name_prefix="busca")
CACHE_BUSCAS = 512


@functools.lru_cache(maxsize=CACHE_BUSCAS)
def _buscar_em_cache(pergunta: str, limite: int):
    """Perguntas repetidas (as sugestões do chat, sobretudo) não recalculam."""
    return _buscador.buscar(pergunta, principais=limite, extras=3)


def obter_buscador():
    """Carrega índice e modelo uma única vez (leva alguns segundos)."""
    global _buscador, _erro_carga
    if _buscador is None and _erro_carga is None:
        try:
            from api.search import Buscador
            _buscador = Buscador()
        except Exception as e:
            _erro_carga = str(e)
    return _buscador


@app.on_event("startup")
def aquecer():
    obter_buscador()


class Pergunta(BaseModel):
    pergunta: str = Field(..., min_length=1, max_length=500)
    nome: str = Field("", max_length=80)
    limite: int = Field(3, ge=1, le=8)


@app.get("/api/health")
def health():
    b = obter_buscador()
    return {
        "ok": b is not None,
        "erro": _erro_carga,
        "indice": b.manifest if b else None,
        "obras": len(b.catalogo) if b else 0,
    }


@app.get("/api/catalogo")
def catalogo():
    b = obter_buscador()
    if not b:
        return {"obras": [], "erro": _erro_carga}
    return {"obras": [
        {
            "id": o["id"], "titulo": o["titulo"], "colecao": o["colecao"],
            "disciplina": o["disciplina"], "ano": o["ano"],
            "paginas": o["paginas_pdf"], "link": o["issuu"]["public_location"],
        } for o in b.catalogo.values()
    ]}


def registrar(pergunta, nome, resultado, resposta, ms):
    """Grava a pergunta e o que a busca devolveu, uma linha JSON por pergunta.

    Vale no modo local; na página publicada não há servidor, e o registro fica
    no navegador do testador (ver o módulo `registro` em frontend/index.html).
    """
    try:
        os.makedirs(DIR_REGISTROS, exist_ok=True)
        caminho = os.path.join(DIR_REGISTROS,
                               time.strftime("%Y-%m-%d") + ".jsonl")
        linha = {
            "momento": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "nome": nome,
            "pergunta": pergunta,
            "assunto": resultado.get("assunto"),
            "confianca": resultado.get("confianca"),
            "cobertura": resultado.get("cobertura"),
            "filtros": resultado.get("filtros"),
            "ms": ms,
            "resposta": resposta.get("texto"),
            "resultados": [
                {"obra": r.get("titulo"), "pagina": r.get("descricao_pagina"),
                 "cobertura": r.get("cobertura"), "link": r.get("link")}
                for r in resposta.get("resultados", [])
            ],
        }
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(linha, ensure_ascii=False) + "\n")
    except Exception:
        pass          # registro nunca pode derrubar a busca


@app.post("/api/busca")
async def busca(p: Pergunta):
    inicio = time.time()
    b = obter_buscador()
    if not b:
        return {**montar_resposta({}, p.nome, acervo_vazio=True),
                "erro": _erro_carga, "ms": 0}
    # `async def` + fila própria: o event loop segue livre para aceitar novas
    # conexões enquanto a busca ocupa o trabalhador dedicado.
    # Perguntas sobre a coleção inteira ganham panorama curado, e a busca roda
    # com uma consulta interna melhor formulada para as páginas citadas baterem.
    guiada = detectar_guiada(p.pergunta)
    consulta = guiada["consulta"] if guiada else p.pergunta
    resultado = await asyncio.get_event_loop().run_in_executor(
        _FILA_BUSCA, _buscar_em_cache, consulta, p.limite)
    # o cache guarda o resultado da consulta interna; a resposta fala da pergunta
    # que o professor escreveu
    resultado = dict(resultado, pergunta=p.pergunta)
    if guiada and guiada.get("verificar"):
        from api.responder import descrever_verificacao
        resultado["verificacao"] = descrever_verificacao(
            [(rotulo, b.contar_ocorrencias(padrao))
             for padrao, rotulo in guiada["verificar"]])
    resposta = montar_resposta(resultado, p.nome, guiada=guiada)
    ms = int((time.time() - inicio) * 1000)
    registrar(p.pergunta, p.nome, resultado, resposta, ms)
    return {
        **resposta,
        "filtros": resultado["filtros"],
        "confiante": resultado["confiante"],
        "cobertura": resultado.get("cobertura"),
        "assunto": resultado.get("assunto"),
        "ms": ms,
    }


@app.get("/")
def raiz():
    return FileResponse(os.path.join(DIR_FRONT, "index.html"))


DIR_ASSETS = os.path.join(DIR_FRONT, "assets")
if os.path.isdir(DIR_ASSETS):
    app.mount("/assets", StaticFiles(directory=DIR_ASSETS), name="assets")
if os.path.isdir(DIR_FRONT):
    app.mount("/app", StaticFiles(directory=DIR_FRONT, html=True), name="frontend")
