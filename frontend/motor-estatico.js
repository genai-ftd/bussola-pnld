/* Motor de busca embutido, para a versão publicada da POC.

   Espelha as regras do motor local: mesmas stopwords, mesmos filtros por regex,
   mesma ancoragem lexical (api/confianca.py), mesma diversificação por obra e a
   mesma copy — que vem embutida a partir de api/responder.py, para as duas
   camadas nunca divergirem de texto.

   A única diferença é a recuperação. Sem servidor não dá para rodar o modelo de
   embeddings, então: as 4 perguntas guiadas e o banco de perguntas usam
   resultados semânticos pré-computados pelo motor completo; pergunta livre usa
   BM25. A decisão de "sei / não sei" é idêntica nos dois casos, porque é lexical
   e usa o mesmo `df` do índice completo, embutido na página. Sem IA generativa
   em nenhum caminho. */
window.BUSSOLA_ESTATICO = (function(){
  "use strict";

  var D = window.BUSSOLA_DADOS;
  var ALTO = D.limiares.alto, BAIXO = D.limiares.baixo;

  /* ---------------------------- normalização ---------------------------- */

  function semAcento(s){
    // A faixa \u0300-\u036f (marcas combinantes) vai escapada de propósito:
    // porque o literal cru já se corrompeu em trânsito uma vez
    return (s || "").normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  }
  function norm(s){
    return semAcento(s).toLowerCase().replace(/[^a-z0-9\s]+/g, " ");
  }

  var STOP = {};
  ("a as o os um uma uns umas de do da dos das em no na nos nas por para pelo pela com sem sobre " +
   "e ou mas que qual quais quando onde como quem cujo se ao aos pra pro entre ate " +
   "eu voce tu nos vos eles elas ele ela meu minha seu sua nosso nossa " +
   "ser sou sao era eram foi foram tem tenho temos ter haver " +
   "me te lhe lhes isso isto aquilo esse essa este esta aquele aquela " +
   "mais menos muito pouco tambem ja nao sim so bem " +
   "quero queria gostaria preciso pode posso poderia " +
   "livro livros pagina paginas material conteudo " +
   // moldura pedagógica: em "como trabalhar cantigas populares" quem carrega o
   // assunto é "cantigas". Deixar "trabalhar" valendo IDF punia pergunta boa.
   "trabalhar abordar ensinar usar utilizar fazer encontrar mostrar buscar procurar " +
   "exemplo exemplos forma formas maneira maneiras jeito tema assunto aula aulas " +
   "colecao obra obras trabalho aborda ensina").split(" ").forEach(function(t){ STOP[t] = 1; });

  function tokenizar(texto){
    var saida = [], partes = norm(texto).split(/\s+/);
    for(var i = 0; i < partes.length; i++){
      var t = partes[i];
      if(t.length > 2 && !STOP[t]) saida.push(t);
    }
    return saida;
  }

  /* ------------------- filtros determinísticos (regex) ------------------- */

  var COLECOES = [["plantar","Plantar"],["entrelacos","Entrelaços"],
                  ["a conquista","A Conquista"],["conquista","A Conquista"],["baoba","Baobá"]];
  var DISCIPLINAS = [["lingua portuguesa","Língua Portuguesa"],["portugues","Língua Portuguesa"],
    ["producao de texto","Produção de Texto"],["lingua espanhola","Língua Espanhola"],
    ["espanhol","Língua Espanhola"],["lingua inglesa","Língua Inglesa"],["ingles","Língua Inglesa"],
    ["matematica","Matemática"],["ciencias da natureza","Ciências da Natureza"],
    ["ciencias","Ciências da Natureza"],["geografia","Geografia"],["historia","História"],
    ["educacao fisica","Educação Física"],["ed fisica","Educação Física"],
    ["educacao digital","Educação Digital"],["arte","Arte"]];
  var ORDINAIS = {primeiro:1, segundo:2, terceiro:3, quarto:4, quinto:5};

  function achar(tabela, texto){
    var n = norm(texto);
    for(var i = 0; i < tabela.length; i++){
      if(new RegExp("\\b" + tabela[i][0] + "\\b").test(n)) return tabela[i][1];
    }
    return null;
  }

  function detectarAno(texto){
    var n = norm(texto), m;
    if((m = n.match(/\b([1-5])\s*(?:o|a)?\s*(?:ano|serie)\b/))) return +m[1];
    if((m = n.match(/\b(?:volume|vol)\s*([1-5])\b/))) return +m[1];
    if((m = n.match(/\b(primeiro|segundo|terceiro|quarto|quinto)\s+(?:ano|serie)\b/))) return ORDINAIS[m[1]];
    return null;
  }

  function filtrosDa(pergunta){
    return {
      colecao: achar(COLECOES, pergunta),
      disciplina: achar(DISCIPLINAS, pergunta),
      ano: detectarAno(pergunta)
    };
  }

  /* ------------------ confiança (espelho de confianca.py) ---------------- */

  function idf(termo){
    var df = D.df[termo] || 0;
    return Math.log(1 + (D.n - df + 0.5) / (df + 0.5));
  }

  function cobertura(tokensPergunta, tokensTrecho){
    var termos = {}, presentes = {}, i, total = 0, obtido = 0;
    for(i = 0; i < tokensPergunta.length; i++) termos[tokensPergunta[i]] = 1;
    for(i = 0; i < tokensTrecho.length; i++) presentes[tokensTrecho[i]] = 1;
    for(var t in termos){
      var peso = idf(t);
      total += peso;
      if(presentes[t]) obtido += peso;
    }
    return total > 0 ? obtido / total : 0;
  }

  function termosAusentes(tokens){
    var saida = [], vistos = {};
    for(var i = 0; i < tokens.length; i++){
      var t = tokens[i];
      if(!vistos[t] && !(D.df[t] > 0)){ vistos[t] = 1; saida.push(t); }
    }
    return saida;
  }

  function termoOriginal(pergunta, token){
    var palavras = pergunta.match(/[0-9A-Za-zÀ-ɏ]+/g) || [];
    for(var i = 0; i < palavras.length; i++){
      if(semAcento(palavras[i]).toLowerCase() === token) return palavras[i];
    }
    return token;
  }

  /* ---------------------------- índice BM25 ----------------------------- */

  var K1 = 1.5, B = 0.75, indice = null;

  function construirIndice(){
    var docs = D.trechos, N = docs.length, i, j, t;
    var tfs = new Array(N), tamanhos = new Float32Array(N), soma = 0, dfLocal = {};

    for(i = 0; i < N; i++){
      var toks = tokenizar(docs[i][3]), tf = {};
      for(j = 0; j < toks.length; j++) tf[toks[j]] = (tf[toks[j]] || 0) + 1;
      tfs[i] = tf;
      tamanhos[i] = toks.length;
      soma += toks.length;
      for(t in tf) dfLocal[t] = (dfLocal[t] || 0) + 1;
    }
    var media = soma / N || 1;

    // Contribuição pronta por posting, como no motor local: a consulta vira
    // somar arrays, sem nenhuma aritmética por documento em tempo de busca.
    var postings = {};
    for(var d = 0; d < N; d++){
      var norma = 1 - B + B * (tamanhos[d] / media);
      for(t in tfs[d]){
        var f = tfs[d][t];
        var peso = Math.log(1 + (N - dfLocal[t] + 0.5) / (dfLocal[t] + 0.5));
        (postings[t] || (postings[t] = [])).push([d, peso * f * (K1 + 1) / (f + K1 * norma)]);
      }
    }
    indice = { postings: postings, n: N };
  }

  function bm25(tokens){
    if(!indice) construirIndice();
    var pontos = new Float32Array(indice.n), houve = false;
    for(var i = 0; i < tokens.length; i++){
      var lista = indice.postings[tokens[i]];
      if(!lista) continue;
      houve = true;
      for(var j = 0; j < lista.length; j++) pontos[lista[j][0]] += lista[j][1];
    }
    return houve ? pontos : null;
  }

  /* --------------- banco e respostas guiadas pré-computados -------------- */

  function doBanco(pergunta){
    var alvo = tokenizar(pergunta), i;
    if(!alvo.length) return null;
    var conjunto = {};
    for(i = 0; i < alvo.length; i++) conjunto[alvo[i]] = 1;

    var melhor = null, melhorNota = 0;
    for(i = 0; i < D.banco.length; i++){
      var entrada = D.banco[i], toks = tokenizar(entrada.q), acertos = 0;
      for(var j = 0; j < toks.length; j++) if(conjunto[toks[j]]) acertos++;
      var uniao = Object.keys(conjunto).length + toks.length - acertos;
      var nota = uniao ? acertos / uniao : 0;
      if(nota > melhorNota){ melhorNota = nota; melhor = entrada; }
    }
    return melhorNota >= 0.62 ? melhor : null;
  }

  function detectarGuiada(pergunta){
    var n = norm(pergunta);
    for(var id in D.guiadas){
      var g = D.guiadas[id];
      for(var i = 0; i < g.padroes.length; i++){
        if(new RegExp(g.padroes[i]).test(n)) return g;
      }
    }
    return null;
  }

  /* ------------------------------ montagem ------------------------------ */

  function fatorMetadado(obra, f){
    var fator = 1;
    if(f.ano && obra[3] === f.ano) fator += 0.50;
    if(f.disciplina && obra[2] === f.disciplina) fator += 0.35;
    if(f.colecao && obra[1] === f.colecao) fator += 0.25;
    return fator;
  }

  function recortar(texto, pergunta, limite){
    limite = limite || 260;
    if(texto.length <= limite) return texto;
    var termos = {}, toks = tokenizar(pergunta), i;
    for(i = 0; i < toks.length; i++) termos[toks[i]] = 1;

    // Divide após . ! ? sem usar lookbehind: Safari só passou a suportá-lo na
    // 16.4, e um literal de regex inválido é erro de parse — derrubaria este
    // arquivo inteiro em vez de degradar, deixando o chat mudo no iPhone.
    var SEP = "\u0000";
    var frases = texto.replace(/([.!?])\s+/g, "$1" + SEP).split(SEP);
    var melhor = frases[0] || texto.slice(0, limite), melhorNota = -1;
    for(i = 0; i < frases.length; i++){
      var janela = "";
      for(var j = i; j < frases.length; j++){
        if(janela.length + frases[j].length + 1 > limite) break;
        janela = (janela + " " + frases[j]).trim();
      }
      if(!janela) janela = frases[i].slice(0, limite);
      var jt = tokenizar(janela), nota = 0;
      for(var k = 0; k < jt.length; k++) if(termos[jt[k]]) nota++;
      if(nota > melhorNota){ melhorNota = nota; melhor = janela; }
    }
    melhor = melhor.trim();
    return /[.!?]$/.test(melhor) ? melhor : melhor + "…";
  }

  function descreverPagina(t, obra){
    var visualizador = obra[4] ? " do visualizador" : " do PDF";
    if(t[2]) return "página " + t[2] + " (página " + t[1] + visualizador + ")";
    return "página " + t[1] + visualizador;
  }

  function montarResultado(idxTrecho, pergunta, cob){
    var t = D.trechos[idxTrecho], obra = D.obras[t[0]];
    return {
      titulo: obra[0], colecao: obra[1], disciplina: obra[2], ano: obra[3],
      trecho: recortar(t[3], pergunta),
      descricao_pagina: descreverPagina(t, obra),
      link: obra[4] ? obra[4].replace(/\/$/, "") + "/" + t[1] : "",
      cobertura: cob
    };
  }

  function parecidos(a, b){
    var inter = 0;
    for(var t in a) if(b[t]) inter++;
    var uniao = Object.keys(a).length + Object.keys(b).length - inter;
    return uniao > 0 && inter / uniao >= 0.6;
  }

  function diversificar(ordenados, principais, extras){
    var vistasPagina = {}, vistosTexto = [], primeiros = [], restantes = [], obras = {};
    for(var i = 0; i < ordenados.length; i++){
      var idx = ordenados[i][1], t = D.trechos[idx], chave = t[0] + ":" + t[1];
      if(vistasPagina[chave]) continue;

      // os manuais do professor se repetem quase iguais entre volumes; comparar
      // conjuntos de tokens pega isso, comparar prefixo de texto não pegava
      var conj = {}, toks = tokenizar(t[3]), repetido = false;
      for(var k = 0; k < toks.length; k++) conj[toks[k]] = 1;
      for(var v = 0; v < vistosTexto.length; v++){
        if(parecidos(conj, vistosTexto[v])){ repetido = true; break; }
      }
      if(repetido) continue;

      vistasPagina[chave] = 1;
      vistosTexto.push(conj);
      if(!obras[t[0]]){ obras[t[0]] = 1; primeiros.push(ordenados[i]); }
      else if(restantes.length < (principais + extras) * 4) restantes.push(ordenados[i]);
    }

    var escolhidos = primeiros.slice(0, principais), contagem = {}, j;
    for(j = 0; j < escolhidos.length; j++){
      var o = D.trechos[escolhidos[j][1]][0];
      contagem[o] = (contagem[o] || 0) + 1;
    }
    var teto = Math.max(2, Math.ceil((principais + extras) / D.obras.length));
    var sobra = primeiros.slice(principais).concat(restantes)
      .sort(function(a, b){ return b[0] - a[0]; });
    for(j = 0; j < sobra.length && escolhidos.length < principais + extras; j++){
      var obra = D.trechos[sobra[j][1]][0];
      if((contagem[obra] || 0) >= teto) continue;
      contagem[obra] = (contagem[obra] || 0) + 1;
      escolhidos.push(sobra[j]);
    }
    return escolhidos;
  }

  /* ------------------------------ respostas ------------------------------ */

  function semente(texto){
    var h = 0;
    for(var i = 0; i < texto.length; i++) h = (h * 31 + texto.charCodeAt(i)) | 0;
    return Math.abs(h);
  }
  function escolher(lista, texto){ return lista[semente(texto) % lista.length]; }
  function preencher(molde, campos){
    return molde.replace(/\{(\w+)\}/g, function(tudo, chave){
      return campos[chave] !== undefined ? campos[chave] : tudo;
    });
  }

  function descreverFiltros(f){
    var d = [];
    if(f.ano) d.push(f.ano + "º ano");
    if(f.disciplina) d.push(f.disciplina);
    if(f.colecao) d.push("coleção " + f.colecao);
    return d.length ? " Priorizei o que é de " + d.join(" · ") + "." : "";
  }

  function montarResposta(pergunta, nome, filtros, resultados, tokens){
    nome = (nome || "").trim() || "Professor(a)";
    var melhor = 0, i;
    for(i = 0; i < resultados.length; i++) melhor = Math.max(melhor, resultados[i].cobertura);

    var ancorados = resultados.filter(function(r){ return r.cobertura >= BAIXO; });

    // nada ancorado no acervo: dizer que não sabe, e dizer o que faltou
    if(!ancorados.length){
      var ausentes = termosAusentes(tokens), texto;
      if(ausentes.length){
        var token = ausentes.reduce(function(a, b){ return b.length > a.length ? b : a; });
        texto = preencher(escolher(D.copy.sem_termo, pergunta),
                          { nome: nome, termo: termoOriginal(pergunta, token) });
      } else {
        texto = preencher(escolher(D.copy.sem_generico, pergunta), { nome: nome });
      }
      return {
        texto: texto,
        resultados: [],
        tambem_encontrei: resultados.slice(0, 3),
        rotulo: resultados.length ? D.copy.rotulo_vizinhos : null,
        confianca: "nenhuma"
      };
    }

    var principais = ancorados.slice(0, 3), n = principais.length, abertura;
    if(melhor >= ALTO){
      abertura = preencher(escolher(D.copy.aberturas, pergunta), {
        nome: nome, n: n,
        palavra: n === 1 ? "trecho" : "trechos",
        verbo: n === 1 ? "conversa" : "conversam"
      }) + descreverFiltros(filtros);
    } else {
      abertura = preencher(escolher(D.copy.parciais, pergunta), { nome: nome });
    }
    return {
      texto: abertura,
      resultados: principais,
      tambem_encontrei: ancorados.slice(3),
      rotulo: null,
      confianca: melhor >= ALTO ? "alta" : "parcial"
    };
  }

  /* -------------------------------- busca -------------------------------- */

  function buscar(pergunta, nome){
    return new Promise(function(resolve){
      // deixa o "digitando…" pintar antes do primeiro cálculo, que monta o índice
      setTimeout(function(){
        var filtros = filtrosDa(pergunta), tokens = tokenizar(pergunta);
        var escolhidos, resultados;

        // 1. pergunta sobre a coleção inteira: panorama curado
        var guiada = detectarGuiada(pergunta);
        if(guiada){
          var itens = guiada.r.map(function(p){ return montarResultado(p[1], pergunta, p[2]); });
          var responde = guiada.modo === "responde";
          resolve({
            texto: guiada.texto,
            resultados: responde ? itens.slice(0, 3) : [],
            tambem_encontrei: responde ? [] : itens.slice(0, 3),
            rotulo: responde ? null : D.copy.rotulo_vizinhos,
            confianca: "guiada"
          });
          return;
        }

        // 2. banco pré-computado pelo motor semântico completo
        var precomputado = doBanco(pergunta);
        if(precomputado){
          escolhidos = diversificar(precomputado.r, 3, 3);
          resultados = escolhidos.map(function(p){ return montarResultado(p[1], pergunta, p[2]); });
          resolve(montarResposta(pergunta, nome, filtros, resultados, tokens));
          return;
        }

        // 3. pergunta livre: BM25 + a mesma ancoragem lexical do motor local
        var pontos = bm25(tokens);
        if(!pontos){
          resolve(montarResposta(pergunta, nome, filtros, [], tokens));
          return;
        }
        var candidatos = [];
        for(var i = 0; i < pontos.length; i++){
          if(pontos[i] <= 0) continue;
          candidatos.push([pontos[i] * fatorMetadado(D.obras[D.trechos[i][0]], filtros), i]);
        }
        candidatos.sort(function(a, b){ return b[0] - a[0]; });
        escolhidos = diversificar(candidatos.slice(0, 200), 3, 3);
        resultados = escolhidos.map(function(p){
          return montarResultado(p[1], pergunta, cobertura(tokens, tokenizar(D.trechos[p[1]][3])));
        });
        resolve(montarResposta(pergunta, nome, filtros, resultados, tokens));
      }, 220);
    });
  }

  return { buscar: buscar };
})();
