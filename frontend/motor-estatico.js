/* Motor de busca embutido, para a versão publicada da POC.

   Reproduz em JavaScript as mesmas regras do motor local: mesmas stopwords,
   mesmos filtros de metadado por regex, mesma diversificação por obra e os
   mesmos templates de resposta. A diferença é a camada de recuperação:
   sem servidor não há como rodar o modelo de embeddings, então aqui a busca
   livre usa BM25 e as perguntas sugeridas usam resultados semânticos
   pré-computados pelo motor completo. Nada de IA generativa nos dois casos. */
window.BUSSOLA_ESTATICO = (function(){
  "use strict";

  var D = window.BUSSOLA_DADOS;

  /* ---------------------------- normalização ---------------------------- */

  function semAcento(s){
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
   "livro livros pagina paginas material conteudo").split(" ").forEach(function(t){ STOP[t] = 1; });

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

  /* --------------------------- índice BM25 ------------------------------ */

  var K1 = 1.5, B = 0.75;
  var indice = null;

  function construirIndice(){
    var docs = D.trechos, N = docs.length;
    var df = {}, tfs = new Array(N), tamanhos = new Float32Array(N), soma = 0;

    for(var i = 0; i < N; i++){
      var toks = tokenizar(docs[i][3]), tf = {};
      for(var j = 0; j < toks.length; j++) tf[toks[j]] = (tf[toks[j]] || 0) + 1;
      tfs[i] = tf;
      tamanhos[i] = toks.length;
      soma += toks.length;
      for(var termo in tf) df[termo] = (df[termo] || 0) + 1;
    }

    var postings = {};
    for(var d = 0; d < N; d++){
      for(var t in tfs[d]){
        (postings[t] || (postings[t] = [])).push([d, tfs[d][t]]);
      }
    }
    var idf = {};
    for(var termo2 in df){
      idf[termo2] = Math.log(1 + (N - df[termo2] + 0.5) / (df[termo2] + 0.5));
    }
    indice = { postings: postings, idf: idf, tamanhos: tamanhos, media: soma / N, n: N };
  }

  function bm25(tokens){
    if(!indice) construirIndice();
    var pontos = new Float32Array(indice.n), houve = false;
    for(var i = 0; i < tokens.length; i++){
      var lista = indice.postings[tokens[i]];
      if(!lista) continue;
      houve = true;
      var idf = indice.idf[tokens[i]];
      for(var j = 0; j < lista.length; j++){
        var doc = lista[j][0], f = lista[j][1];
        var norma = 1 - B + B * (indice.tamanhos[doc] / indice.media);
        pontos[doc] += idf * (f * (K1 + 1)) / (f + K1 * norma);
      }
    }
    return houve ? pontos : null;
  }

  /* ---------------- banco de perguntas pré-computadas ------------------- */

  function doBanco(pergunta){
    var alvo = tokenizar(pergunta);
    if(!alvo.length) return null;
    var conjunto = {}, i;
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

  /* ----------------------------- montagem ------------------------------- */

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

    var frases = texto.split(/(?<=[.!?])\s+/);
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

  function descreverPagina(paginaFisica, impressa){
    return impressa
      ? "página " + impressa + " (página " + paginaFisica + " do visualizador)"
      : "página " + paginaFisica + " do visualizador";
  }

  function montarResultado(idxTrecho, pergunta){
    var t = D.trechos[idxTrecho], obra = D.obras[t[0]];
    return {
      titulo: obra[0],
      colecao: obra[1],
      disciplina: obra[2],
      ano: obra[3],
      trecho: recortar(t[3], pergunta),
      descricao_pagina: descreverPagina(t[1], t[2]),
      link: obra[4] ? obra[4].replace(/\/$/, "") + "/" + t[1] : ""
    };
  }

  function diversificar(ordenados, principais, extras){
    var vistas = {}, primeiros = [], restantes = [], obrasUsadas = {};
    for(var i = 0; i < ordenados.length; i++){
      var idx = ordenados[i][1], t = D.trechos[idx], chave = t[0] + ":" + t[1];
      if(vistas[chave]) continue;
      vistas[chave] = 1;
      if(!obrasUsadas[t[0]]){ obrasUsadas[t[0]] = 1; primeiros.push(ordenados[i]); }
      else if(restantes.length < (principais + extras) * 4) restantes.push(ordenados[i]);
    }
    var escolhidos = primeiros.slice(0, principais), contagem = {}, j;
    for(j = 0; j < escolhidos.length; j++){
      var o = D.trechos[escolhidos[j][1]][0];
      contagem[o] = (contagem[o] || 0) + 1;
    }
    var sobra = primeiros.slice(principais).concat(restantes)
      .sort(function(a, b){ return b[0] - a[0]; });
    for(j = 0; j < sobra.length && escolhidos.length < principais + extras; j++){
      var obra = D.trechos[sobra[j][1]][0];
      if((contagem[obra] || 0) >= 2) continue;
      contagem[obra] = (contagem[obra] || 0) + 1;
      escolhidos.push(sobra[j]);
    }
    return escolhidos;
  }

  /* ---------------------------- respostas ------------------------------- */

  var ABERTURAS = [
    "{nome}, encontrei {n} {palavra} que {verbo} com a sua busca:",
    "Achei {n} {palavra} sobre isso, {nome}:",
    "{nome}, isto é o que o acervo do PNLD 2027 traz sobre o tema:"
  ];
  var SEM_RESULTADO = "{nome}, não encontrei nada suficientemente próximo dessa pergunta " +
    "nas obras que já estão indexadas. Tente descrever o conteúdo com outras palavras " +
    "(o tema da aula, o gênero textual ou a habilidade da BNCC), ou informe o ano e o " +
    "componente curricular.";

  function descreverFiltros(f){
    var d = [];
    if(f.ano) d.push(f.ano + "º ano");
    if(f.disciplina) d.push(f.disciplina);
    if(f.colecao) d.push("coleção " + f.colecao);
    return d.length ? " Priorizei o que é de " + d.join(" · ") + "." : "";
  }

  function semente(texto){
    var h = 0;
    for(var i = 0; i < texto.length; i++) h = (h * 31 + texto.charCodeAt(i)) | 0;
    return Math.abs(h);
  }

  function montarResposta(pergunta, nome, filtros, resultados){
    nome = (nome || "").trim() || "Professor(a)";
    if(!resultados.length){
      return { texto: SEM_RESULTADO.replace("{nome}", nome), resultados: [], tambem_encontrei: [] };
    }
    var principais = resultados.slice(0, 3), n = principais.length;
    var abertura = ABERTURAS[semente(pergunta) % ABERTURAS.length]
      .replace("{nome}", nome).replace("{n}", n)
      .replace("{palavra}", n === 1 ? "trecho" : "trechos")
      .replace("{verbo}", n === 1 ? "conversa" : "conversam");
    return {
      texto: abertura + descreverFiltros(filtros),
      resultados: principais,
      tambem_encontrei: resultados.slice(3)
    };
  }

  /* ------------------------------- busca -------------------------------- */

  function buscar(pergunta, nome){
    return new Promise(function(resolve){
      // deixa o "digitando…" pintar antes de um primeiro cálculo mais pesado
      setTimeout(function(){
        var filtros = filtrosDa(pergunta);
        var escolhidos, precomputado = doBanco(pergunta);

        if(precomputado){
          // o banco já vem como [pontuacao, indiceDoTrecho], igual ao BM25
          escolhidos = diversificar(precomputado.r, 3, 3);
        } else {
          var pontos = bm25(tokenizar(pergunta));
          if(!pontos){
            resolve(montarResposta(pergunta, nome, filtros, []));
            return;
          }
          var candidatos = [];
          for(var i = 0; i < pontos.length; i++){
            if(pontos[i] <= 0) continue;
            var obra = D.obras[D.trechos[i][0]];
            candidatos.push([pontos[i] * fatorMetadado(obra, filtros), i]);
          }
          if(!candidatos.length){
            resolve(montarResposta(pergunta, nome, filtros, []));
            return;
          }
          candidatos.sort(function(a, b){ return b[0] - a[0]; });
          escolhidos = diversificar(candidatos.slice(0, 200), 3, 3);
        }

        var resultados = escolhidos.map(function(par){
          return montarResultado(par[1], pergunta);
        });
        resolve(montarResposta(pergunta, nome, filtros, resultados));
      }, 220);
    });
  }

  return { buscar: buscar };
})();
