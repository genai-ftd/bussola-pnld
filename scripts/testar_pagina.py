"""Roda a página publicada fora do navegador, antes do push.

Existe porque eu quebrei a página no ar: `popularComponentes()` era chamada
antes de `MOTOR` existir, o TypeError subia sem captura e matava o script
inteiro — chat morto, com testadores usando. Os testes que eu rodava exercitavam
só o motor de busca, que estava correto; nada cobria a inicialização da
interface.

Aqui os dois lados são exercitados: o motor, com perguntas reais, e o script da
interface contra um DOM de mentira, que pega erro de ordem de inicialização.

Uso: .venv/bin/python scripts/testar_pagina.py   (precisa de bun ou node)
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PAGINA = os.path.join(RAIZ, "dist", "bussola-pnld.html")

CASOS = [
    ("fakenews", "", "", "corrige a grafia"),
    ("cantigaspopulares", "", "", "separa palavras coladas"),
    ("EF35LP07", "", "", "código da BNCC"),
    ("35LP07", "", "", "código sem o EF"),
    ("EF99XX99", "", "", "código inexistente"),
    ("partes do corpo", "Ana", "Língua Inglesa", "componente + nome"),
    ("programação em python", "", "", "fora do acervo"),
    ("atividades de leitura para o 3º ano", "", "", "busca temática"),
    ("fontes de luz", "", "", "sinônimo + índice remissivo"),
    ("instrumentos musicais", "", "", "índice remissivo temático"),
    ("cantiga popular", "", "", "sinônimo: singular alcança plural"),
    ("Páscoa", "", "", "não corrige para nome próprio"),
]

DOM_FALSO = """
var criados = [];
// DOM de mentira com a superfície que a interface realmente usa. Cada método que
// faltou aqui já deixou passar um TypeError para a página no ar — quando
// adicionar API nova ao front, adicione aqui também.
function elem(){
  return {
    style:{}, dataset:{}, options:{length:1}, value:"", textContent:"",
    title:"", hidden:false, scrollHeight:20, scrollTop:0,
    classList:{ add(){}, remove(){}, toggle(){}, contains(){ return false; } },
    appendChild(c){ criados.push(c); }, removeChild(){}, remove(){},
    addEventListener(t, f){ if(t === "submit") this.__submit = function(){ f({preventDefault(){}}); }; },
    removeEventListener(){},
    setAttribute(){}, getAttribute(){ return null; }, removeAttribute(){},
    focus(){}, blur(){}, select(){}, click(){}, closest(){ return null; },
    querySelector(){ return null; }, querySelectorAll(){ return []; },
    insertBefore(){}, contains(){ return false; }
  };
}
globalThis.window = globalThis;
// Elementos estáveis por id: a interface guarda referências no carregamento, e
// um DOM que devolve objeto novo a cada chamada nunca reproduz isso.
var registro = {};
function porId(id){ return registro[id] || (registro[id] = elem()); }
globalThis.document = {
  getElementById: porId, querySelectorAll: () => [], createElement: elem,
  addEventListener(){}, body:{classList:{add(){},remove(){},contains(){return false}}} };
globalThis.localStorage = { getItem:()=>null, setItem(){}, removeItem(){}, clear(){} };
globalThis.location = { search: "" };
globalThis.fetch = () => Promise.reject(new Error("offline"));
globalThis.BUSSOLA_ESTATICO = { buscar: () => Promise.resolve({ texto:"", resultados:[] }),
  componentes: () => Promise.resolve(["Arte"]) };
"""


def executor():
    for nome in ("bun", "node"):
        caminho = shutil.which(nome) or os.path.expanduser("~/.bun/bin/" + nome)
        if os.path.exists(caminho):
            return caminho
    raise SystemExit("preciso de bun ou node no PATH para rodar o teste")


def rodar(js, motor):
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(js)
        caminho = fh.name
    try:
        r = subprocess.run([motor, caminho], capture_output=True, text=True, timeout=180)
        return r.returncode, (r.stdout + r.stderr).strip()
    finally:
        os.unlink(caminho)


def main():
    if not os.path.exists(PAGINA):
        raise SystemExit("rode antes: python scripts/build_static_demo.py")
    html = open(PAGINA, encoding="utf-8").read()
    dados = re.search(r"<script>window\.BUSSOLA_DADOS=(.*?);</script>", html, re.S).group(1)
    blocos = re.findall(r"<script>(.*?)</script>", html, re.S)
    interface = [b for b in blocos if "popularComponentes" in b]
    motor_js = open(os.path.join(RAIZ, "frontend", "motor-estatico.js"),
                    encoding="utf-8").read()
    node = executor()
    falhas = 0

    print("== interface ==")
    if not interface:
        print("  [FALHA] não achei o script da interface na página")
        falhas += 1
    else:
        gatilho = "\nsetTimeout(function(){console.log('ok');},50);"
        codigo, saida = rodar(DOM_FALSO + interface[0] + gatilho, node)
        if codigo or "ok" not in saida:
            print("  [FALHA] {}".format(saida[:600]))
            falhas += 1
        else:
            print("  ok — carrega sem erro")

    # Checagem de ligação: três vezes hoje um replace meu não casou e a função
    # ficou definida sem nunca ser chamada — a página carregava sem erro e o
    # recurso simplesmente não existia. Teste de inicialização não pega isso.
    print("\n== ligação (definido, chamado e estilizado) ==")
    pagina = html
    for rotulo, definicao, chamada, estilo in [
        ("índice remissivo", "function ocorrencias(grupos)", "ocorrencias(d.ocorrencias)", ".ocorrencias{"),
        ("também encontrei", "function tambemEncontrei", "tambemEncontrei(d.tambem_encontrei", ".tambem{"),
        ("cartão de resultado", "function cartao(", "cartao(r, d.termos)", ".cartao{"),
        ("tag de nome", "function pintarNome(", "el.botaoNome.addEventListener", ".marca-nome{"),
    ]:
        faltando = [nome for nome, trecho in
                    (("definição", definicao), ("chamada", chamada), ("estilo", estilo))
                    if trecho not in pagina]
        if faltando:
            print("  [FALHA] {}: falta {}".format(rotulo, ", ".join(faltando)))
            falhas += 1
        else:
            print("  ok   {}".format(rotulo))

    print("\n== motor de busca ==")
    prova = "\n".join(
        "  try {{ const r = await M.buscar({}, {}, {});"
        " console.log('ok|' + {} + '|' + r.resultados.length"
        " + '+' + ((r.ocorrencias||[]).reduce(function(n,g){{return n+g.total;}},0))"
        " + '|' + r.texto.slice(0,64)); }}"
        " catch(e) {{ console.log('FALHA|' + {} + '|' + (e && e.message)); }}".format(
            json.dumps(q), json.dumps(n), json.dumps(c), json.dumps(rotulo),
            json.dumps(rotulo))
        for q, n, c, rotulo in CASOS)
    js = ("globalThis.window = globalThis;\nwindow.BUSSOLA_DADOS = " + dados + ";\n"
          + motor_js + "\nconst M = window.BUSSOLA_ESTATICO;\n(async () => {\n"
          + prova + "\n})();\n")
    codigo, saida = rodar(js, node)
    if codigo:
        print("  [FALHA] o motor não carregou: {}".format(saida[:600]))
        falhas += 1
    for linha in saida.splitlines():
        partes = linha.split("|")
        if partes[0] == "ok":
            print("  ok   {:<34s} {:>7s} :: {}".format(
                partes[1], partes[2] + " pág", partes[3][:56]))
        elif partes[0] == "FALHA":
            print("  FALHA {}: {}".format(partes[1], partes[2]))
            falhas += 1

    print("\n{}".format("tudo passou" if not falhas else
                        "{} falha(s) — não publique".format(falhas)))
    return 1 if falhas else 0


if __name__ == "__main__":
    sys.exit(main())
