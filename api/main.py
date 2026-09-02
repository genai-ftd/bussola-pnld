"""API da Bússola PNLD — busca semântica no acervo do PNLD 2027.

Sobe com:  ./scripts/run_dev.sh   (ou: uvicorn api.main:app --reload)
O front-end estático é servido na raiz: http://127.0.0.1:8000/
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from api.responder import montar_resposta

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIR_FRONT = os.path.join(RAIZ, "frontend")
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


@app.post("/api/busca")
def busca(p: Pergunta):
    inicio = time.time()
    b = obter_buscador()
    if not b:
        return {**montar_resposta({}, p.nome, acervo_vazio=True),
                "erro": _erro_carga, "ms": 0}
    resultado = b.buscar(p.pergunta, principais=p.limite, extras=3)
    resposta = montar_resposta(resultado, p.nome)
    return {
        **resposta,
        "filtros": resultado["filtros"],
        "confiante": resultado["confiante"],
        "ms": int((time.time() - inicio) * 1000),
    }


@app.get("/")
def raiz():
    return FileResponse(os.path.join(DIR_FRONT, "index.html"))


DIR_ASSETS = os.path.join(DIR_FRONT, "assets")
if os.path.isdir(DIR_ASSETS):
    app.mount("/assets", StaticFiles(directory=DIR_ASSETS), name="assets")
if os.path.isdir(DIR_FRONT):
    app.mount("/app", StaticFiles(directory=DIR_FRONT, html=True), name="frontend")
