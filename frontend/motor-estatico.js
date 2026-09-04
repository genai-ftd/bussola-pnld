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

  /* Tokens e quais pares ficaram COLADOS no texto original. "sistema solar" é
     composto — as palavras se tocam; "fotossíntese e as plantas" não é, há "e
     as" no meio. Sem a distinção, o par vira exigência falsa. */
  function tokenizarAdjacentes(texto){
    var brutos = norm(texto).split(/\s+/), tokens = [], colados = [], ultimo = null;
    for(var i = 0; i < brutos.length; i++){
      var t = brutos[i];
      if(t.length <= 2 || STOP[t]) continue;
      if(tokens.length) colados.push(i === ultimo + 1);
      tokens.push(t);
      ultimo = i;
    }
    return { tokens: tokens, colados: colados };
  }

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

  /* Separa o assunto da moldura do pedido. "Quero pesquisar sobre Sistema
     Solar" precisa virar "Sistema Solar": sem isto, "pesquisar" entra como
     termo e a Bússola vai atrás do verbo dentro dos livros. */
  var VERBOS_PEDIDO = "(?:(?:pesquis|busc|procur|sab|conhec|ach|encontr|consult|"
    + "localiz|mostr|indic|suger|fal|explic|ajud|trat)(?:ar|er|ir|ando|endo|indo|a|e|o)"
    + "|ver|vendo|vejo|dizer|dar)";
  var INICIO_PEDIDO = new RegExp("^\\s*(?:(?:eu|voce|vc)\\s+)?"
    + "(?:(?:quero|queria|gostaria(?:\\s+de)?|preciso|pode|poderia|podes|me|"
    + "estou|to|tou|ando|o\\s+que|oque|qual|quais|onde|tem|tens|existe|ha|"
    + "a|de|para|pra|que)\\s+)*"
    + "(?:" + VERBOS_PEDIDO + "\\s+)*", "i");
  var MARCADOR_ASSUNTO = /\b(?:sobre|a\s+respeito\s+de|acerca\s+de|referente\s+a)\b/i;

  function extrairAssunto(pergunta){
    var texto = (pergunta || "").trim();
    var m = MARCADOR_ASSUNTO.exec(texto);
    if(m){
      var depois = texto.slice(m.index + m[0].length).replace(/^[\s,:;?!]+|[\s,:;?!]+$/g, "");
      if(depois) return depois;
    }
    var cortado = texto.replace(INICIO_PEDIDO, "").replace(/^[\s,:;?!]+|[\s,:;?!]+$/g, "");
    return cortado || texto;
  }

  var EXPRESSOES_FILTRO = [
    /\b[1-5]\s*(?:o|a)?\s*(?:ano|serie)\b/g,
    /\b(?:volume|vol)\s*[1-5]\b/g,
    /\b(primeiro|segundo|terceiro|quarto|quinto)\s+(?:ano|serie)\b/g
  ];

  /* Tira da pergunta o que já virou filtro de metadado: o ano lido como filtro
     não pode competir de novo como termo de busca, senão puxa páginas que só
     dizem "ano" e ainda aparece realçado no lugar do assunto. */
  function removerTermosDeFiltro(pergunta){
    var limpo = norm(pergunta), i;
    for(i = 0; i < EXPRESSOES_FILTRO.length; i++) limpo = limpo.replace(EXPRESSOES_FILTRO[i], " ");
    for(i = 0; i < COLECOES.length; i++) limpo = limpo.replace(new RegExp("\\b" + COLECOES[i][0] + "\\b", "g"), " ");
    for(i = 0; i < DISCIPLINAS.length; i++) limpo = limpo.replace(new RegExp("\\b" + DISCIPLINAS[i][0] + "\\b", "g"), " ");
    return limpo;
  }

  function tokensDaPergunta(pergunta){
    var assunto = extrairAssunto(pergunta);
    var t = tokenizar(removerTermosDeFiltro(assunto));
    return t.length ? t : tokenizar(assunto);
  }

  function filtrosDa(pergunta){
    return {
      colecao: achar(COLECOES, pergunta),
      disciplina: achar(DISCIPLINAS, pergunta),
      ano: detectarAno(pergunta)
    };
  }

  /* ------------------ confiança (espelho de confianca.py) ---------------- */

  function idfTermo(termo){
    var df = D.df[termo] || 0;
    return Math.log(1 + (D.n - df + 0.5) / (df + 0.5));
  }

  /* As unidades da pergunta são as palavras E os pares adjacentes. O par é o
     que distingue composto de coincidência: "sistema solar" não existe em
     nenhuma página, mas "solar" existe — em "filtro solar". Sem o par, a
     pergunta sobre astronomia casava com protetor solar. */
  /* Disciplina provável da pergunta, votada pelos termos. A tabela vem do
     acervo (ver api/disciplinas.py): o professor pergunta "tabuada de
     multiplicação", não "matemática", e sem esse sinal a busca acerta o tema e
     erra a obra. */
  var CODIGO_BNCC = /\b(?:ef)?(\d{2}[a-z]{2}\d{2})\b/i;
  var FRASE_CITADA = /["\u201c]([^"\u201d]{3,80})["\u201d]/;
  var MAX_REFERENCIA = 15;

  /* Código da BNCC ou trecho entre aspas não é busca temática: o professor quer
     o índice remissivo — todas as páginas —, não três trechos parecidos. */
  function detectarReferencia(pergunta){
    var m = CODIGO_BNCC.exec(pergunta || "");
    if(m) return { tipo: "codigo", alvo: m[1].toLowerCase() };
    m = FRASE_CITADA.exec(pergunta || "");
    if(m) return { tipo: "frase", alvo: m[1].trim() };
    return null;
  }

  function buscarReferencia(ref, pergunta){
    if(!indice) construirIndice();
    var padrao = /^(?:ef)?(\d{2}[a-z]{2}\d{2})$/;
    var achados = [], vistos = {}, d, i;

    for(d = 0; d < D.trechos.length; d++){
      var toks = indice.tokens[d], bate = false;
      if(ref.tipo === "codigo"){
        for(i = 0; i < toks.length; i++){
          var m = padrao.exec(toks[i]);
          if(m && m[1] === ref.alvo){ bate = true; break; }
        }
      } else {
        var alvo = tokenizar(ref.alvo);
        if(alvo.length){
          for(i = 0; i + alvo.length <= toks.length && !bate; i++){
            var igual = true;
            for(var j = 0; j < alvo.length; j++){
              if(toks[i + j] !== alvo[j]){ igual = false; break; }
            }
            bate = igual;
          }
        }
      }
      if(!bate) continue;
      var chave = D.trechos[d][0] + ":" + D.trechos[d][1];
      if(vistos[chave]) continue;
      vistos[chave] = 1;
      achados.push(d);
    }

    achados.sort(function(a, b){
      var oa = D.trechos[a][0], ob = D.trechos[b][0];
      return oa !== ob ? oa - ob : D.trechos[a][1] - D.trechos[b][1];
    });
    var obras = {};
    for(i = 0; i < achados.length; i++) obras[D.trechos[achados[i]][0]] = 1;
    return {
      modo: "referencia",
      alvo: ref.tipo === "codigo" ? "EF" + ref.alvo.toUpperCase() : '"' + ref.alvo + '"',
      total_paginas: achados.length,
      total_obras: Object.keys(obras).length,
      resultados: achados.slice(0, MAX_REFERENCIA).map(function(d){
        return montarResultado(d, pergunta, 1);
      })
    };
  }

  // casar pelo parente vale quase tanto quanto pela palavra exata, mas não
  // tanto: a página que usa o termo do professor continua ganhando
  var PESO_SINONIMO = 0.85;
  var LETRAS = "abcdefghijklmnopqrstuvwxyz";
  // ver api/correcao.py: corrigir para termo raro vira correção para nome
  // próprio — "Páscoa" virava "Pascoal", o músico
  var DF_MIN_METADE = 5, DF_MIN_EDICAO = 20, TAM_MIN_EDICAO = 5;

  /* Espelho de api/correcao.py: "fakenews" não existe no acervo, "fake" e
     "news" existem. O professor não deve levar "não encontrei" por causa de um
     espaço — deve ver "Você quis dizer fake news?". Só mexe em termo ausente;
     palavra que existe nunca é corrigida. */
  function separar(termo){
    var melhor = null, melhorPeso = 0;
    for(var i = 3; i < termo.length - 2; i++){
      var fa = indice.df[termo.slice(0, i)] || 0, fb = indice.df[termo.slice(i)] || 0;
      if(fa >= DF_MIN_METADE && fb >= DF_MIN_METADE && Math.min(fa, fb) > melhorPeso){
        melhorPeso = Math.min(fa, fb);
        melhor = termo.slice(0, i) + " " + termo.slice(i);
      }
    }
    return melhor;
  }

  function edicoes(termo){
    var saida = {}, i, c;
    for(i = 0; i <= termo.length; i++){
      var a = termo.slice(0, i), b = termo.slice(i);
      if(b.length){
        saida[a + b.slice(1)] = 1;
        if(b.length > 1) saida[a + b[1] + b[0] + b.slice(2)] = 1;
        for(c = 0; c < LETRAS.length; c++) saida[a + LETRAS[c] + b.slice(1)] = 1;
      }
      for(c = 0; c < LETRAS.length; c++) saida[a + LETRAS[c] + b] = 1;
    }
    delete saida[termo];
    return Object.keys(saida);
  }

  function porEdicao(termo){
    if(termo.length < TAM_MIN_EDICAO) return null;
    var candidatos = edicoes(termo).filter(function(c){
      return (indice.df[c] || 0) >= DF_MIN_EDICAO;
    });
    if(!candidatos.length && termo.length >= 7){
      var vistos = {};
      edicoes(termo).forEach(function(meio){
        edicoes(meio).forEach(function(c){
          if(!vistos[c] && (indice.df[c] || 0) >= DF_MIN_EDICAO){ vistos[c] = 1; candidatos.push(c); }
        });
      });
    }
    if(!candidatos.length) return null;
    return candidatos.reduce(function(a, b){
      return (indice.df[b] || 0) > (indice.df[a] || 0) ? b : a;
    });
  }

  function sugerirCorrecao(tokens){
    if(!indice) construirIndice();
    var correcoes = {}, achou = false, vistos = {};
    for(var i = 0; i < tokens.length; i++){
      var t = tokens[i];
      if(vistos[t] || (indice.df[t] || 0) > 0) continue;
      vistos[t] = 1;
      var alvo = separar(t) || porEdicao(t);
      if(alvo){ correcoes[t] = alvo; achou = true; }
    }
    return achou ? correcoes : null;
  }

  function aplicarCorrecao(pergunta, correcoes){
    return pergunta.replace(/[\wÀ-ÿ]+/g, function(p){
      var chave = semAcento(p).toLowerCase();
      return correcoes[chave] || p;
    });
  }

  function inferirDisciplina(tokens){
    if(!D.voto_disciplina) return null;
    var votos = {}, vistos = {}, total = 0, i;
    for(i = 0; i < tokens.length; i++){
      if(vistos[tokens[i]]) continue;
      vistos[tokens[i]] = 1;
      var e = D.voto_disciplina[tokens[i]];
      if(!e) continue;
      var nome = D.disciplinas[e[0]];
      votos[nome] = (votos[nome] || 0) + e[1];
      total += e[1];
    }
    var lider = null, peso = 0;
    for(var k in votos) if(votos[k] > peso){ peso = votos[k]; lider = k; }
    return (lider && peso / total >= D.dominio_disciplina) ? lider : null;
  }

  function unidadesDaPergunta(tokens, colados, disciplina){
    var vistos = {}, saida = [], i;
    for(i = 0; i < tokens.length; i++){
      if(vistos[tokens[i]]) continue;
      vistos[tokens[i]] = 1;
      saida.push([tokens[i], null, idfTermo(tokens[i])]);
    }
    // só o par colado vira unidade
    for(i = 0; i + 1 < tokens.length; i++){
      if(!colados[i]) continue;
      var df = trechosComFrase(tokens[i], tokens[i + 1]).length;
      saida.push([tokens[i], tokens[i + 1],
                  Math.log(1 + (D.n - df + 0.5) / (df + 0.5))]);
    }
    // meia unidade para a disciplina: orienta a escolha da obra sem inflar,
    // porque qualquer página do componente já a satisfaz
    if(disciplina && saida.length){
      var soma = 0;
      for(i = 0; i < saida.length; i++) soma += saida[i][2];
      saida.push(["__disciplina__", disciplina,
                  (soma / saida.length) * D.peso_disciplina]);
    }
    return saida;
  }

  function cobertura(unidades, tokensTrecho, disciplinaTrecho){
    if(!unidades.length) return 0;
    var presentes = {}, pares = {}, i;
    for(i = 0; i < tokensTrecho.length; i++) presentes[tokensTrecho[i]] = 1;
    for(i = 0; i + 1 < tokensTrecho.length; i++) pares[tokensTrecho[i] + "\u0000" + tokensTrecho[i + 1]] = 1;
    var total = 0, obtido = 0;
    for(i = 0; i < unidades.length; i++){
      var u = unidades[i];
      total += u[2];
      var achou, fator = 1;
      if(u[0] === "__disciplina__"){
        achou = (disciplinaTrecho === u[1]);
      } else if(u[1] === null){
        achou = presentes[u[0]];
        if(!achou && D.sinonimos){
          // "leituras" atende quem perguntou "leitura"; ver build_sinonimos.py
          var parentes = D.sinonimos[u[0]] || [];
          for(var k = 0; k < parentes.length && !achou; k++) achou = presentes[parentes[k]];
          if(achou) fator = PESO_SINONIMO;
        }
      } else {
        achou = pares[u[0] + "\u0000" + u[1]];
      }
      if(achou) obtido += u[2] * fator;
    }
    return total > 0 ? obtido / total : 0;
  }

  /* Ausência é apurada sobre os trechos publicados, que já excluem sumários:
     um termo que só existe num índice não conta como presente na hora de dizer
     ao professor o que faltou. */
  function termosAusentes(tokens){
    if(!indice) construirIndice();
    var saida = [], vistos = {};
    for(var i = 0; i < tokens.length; i++){
      var t = tokens[i];
      if(!vistos[t] && !(indice.df[t] > 0)){ vistos[t] = 1; saida.push(t); }
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
    var tokensPorDoc = new Array(N);

    for(i = 0; i < N; i++){
      var toks = tokenizar(docs[i][3]), tf = {};
      tokensPorDoc[i] = toks;
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
    indice = { postings: postings, n: N, tokens: tokensPorDoc, df: dfLocal };
  }

  var memoFrase = {};

  /* Índices dos trechos em que o par aparece colado. Varremos as listas de
     tokens em vez de embutir um mapa de ~500 mil bigramas na página. */
  function trechosComFrase(a, b){
    var chave = a + "\u0000" + b;
    if(memoFrase[chave]) return memoFrase[chave];
    if(!indice) construirIndice();
    var achados = [];
    for(var d = 0; d < indice.n; d++){
      var toks = indice.tokens[d];
      for(var i = 0; i + 1 < toks.length; i++){
        if(toks[i] === a && toks[i + 1] === b){ achados.push(d); break; }
      }
    }
    memoFrase[chave] = achados;
    return achados;
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
    limite = limite || 165;
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
    // o número que o professor procura no livro vem primeiro; o do leitor é nota
    var onde = obra[4] ? "visualizador" : "PDF";
    if(t[2]) return "Página " + t[2] + " do livro · " + t[1] + " no " + onde;
    return "Página " + t[1] + " do " + onde;
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

  function diversificar(ordenados, principais, extras, componente){
    var vistasPagina = {}, vistosTexto = [], primeiros = [], restantes = [], obras = {};
    for(var i = 0; i < ordenados.length; i++){
      var idx = ordenados[i][1], t = D.trechos[idx], chave = t[0] + ":" + t[1];
      // seletor de componente é restrição, não preferência
      if(componente && D.obras[t[0]][2] !== componente) continue;
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
    var texto = molde;
    // Sem nome, tira o vocativo em vez de chamar todo mundo de "Professor(a)":
    // a pergunta do nome saiu do chat porque os testadores digitavam a busca
    // primeiro e passavam a ser chamados de "instrumentos musicais".
    if(!campos.nome){
      texto = texto.replace(/\{nome\}, */g, "").replace(/,? *\{nome\}/g, "");
      texto = texto.charAt(0).toUpperCase() + texto.slice(1);
    }
    return texto.replace(/\{(\w+)\}/g, function(tudo, chave){
      return campos[chave] !== undefined ? campos[chave] : tudo;
    });
  }

  /* O que o professor escreveu e o que a ferramenta deduziu não podem sair com
     a mesma frase: quando o palpite erra, ele precisa perceber que a restrição
     foi decisão nossa, para poder desfazê-la. */
  function descreverFiltros(f){
    var pedidos = [];
    if(f.ano) pedidos.push(f.ano + "º ano");
    if(f.colecao) pedidos.push("coleção " + f.colecao);
    if(f.disciplina && !f.disciplina_inferida) pedidos.push(f.disciplina);

    var frases = "";
    if(pedidos.length) frases += " Priorizei o que é de " + pedidos.join(" · ") + ".";
    if(f.disciplina && f.disciplina_inferida){
      frases += " Entendi como pergunta de " + f.disciplina
              + "; se não for, me diz o componente.";
    }
    return frases;
  }

  /* Consulta de referência: quantas páginas, em quantas obras, e a lista.
     A copy vem de D.copy, a mesma de api/responder.py, para as duas camadas
     dizerem exatamente a mesma coisa. */
  function montarRespostaReferencia(r, nome){
    if(!r.total_paginas){
      return {
        texto: preencher(D.copy.sem_referencia, { nome: nome, alvo: r.alvo }),
        termos: [], resultados: [], tambem_encontrei: [], rotulo: null,
        confianca: "nenhuma"
      };
    }
    var texto = preencher(D.copy.referencia, {
      alvo: r.alvo, paginas: r.total_paginas,
      p_palavra: r.total_paginas === 1 ? "página" : "páginas",
      obras: r.total_obras === 1 ? "numa obra" : "em " + r.total_obras + " obras"
    });
    texto += r.resultados.length >= r.total_paginas
      ? D.copy.referencia_todas
      : preencher(D.copy.referencia_parcial, { mostradas: r.resultados.length });
    return { texto: texto, termos: [], resultados: r.resultados,
             tambem_encontrei: [], rotulo: null, confianca: "referencia" };
  }

  function montarResposta(pergunta, nome, filtros, resultados, tokens, assunto){
    nome = (nome || "").trim();
    var melhor = 0, i;
    for(i = 0; i < resultados.length; i++) melhor = Math.max(melhor, resultados[i].cobertura);

    var ausentes = termosAusentes(tokens);
    // Termo com frequência zero é evidência decisiva, não indício: se a palavra
    // central da pergunta não existe em nenhuma página publicada, não há o que
    // responder — nem com ressalva.
    var ancorados = ausentes.length
      ? []
      : resultados.filter(function(r){ return r.cobertura >= BAIXO; });

    if(!ancorados.length){
      var texto;
      if(ausentes.length){
        var token = ausentes.reduce(function(a, b){ return b.length > a.length ? b : a; });
        texto = preencher(escolher(D.copy.sem_termo, pergunta),
                          { nome: nome, termo: termoOriginal(pergunta, token) });
      } else {
        texto = preencher(escolher(D.copy.sem_generico, pergunta), { nome: nome });
      }
      return {
        texto: texto,
        assunto: assunto,
        filtros: filtros,
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
      assunto: assunto,
      filtros: filtros,
      termos: tokens,
      resultados: principais,
      tambem_encontrei: ancorados.slice(3),
      rotulo: null,
      confianca: melhor >= ALTO ? "alta" : "parcial"
    };
  }

  /* -------------------------------- busca -------------------------------- */

  function buscar(pergunta, nome, componente, jaCorrigida){
    return new Promise(function(resolve){
      // deixa o "digitando…" pintar antes do primeiro cálculo, que monta o índice
      setTimeout(function(){
        // antes de tudo: o professor escreveu algo que não existe mas se parece
        // com o que existe? corrige e avisa, em vez de recusar
        if(!jaCorrigida){
          if(!indice) construirIndice();
          var correcoes = sugerirCorrecao(tokenizar(extrairAssunto(pergunta)));
          if(correcoes){
            var corrigida = aplicarCorrecao(pergunta, correcoes);
            buscar(corrigida, nome, componente, true).then(function(r){
              r.texto = preencher(D.copy.correcao, { corrigida: corrigida.trim() }) + r.texto;
              r.correcao = { de: pergunta, para: corrigida };
              resolve(r);
            });
            return;
          }
        }

        var ref = detectarReferencia(pergunta);
        if(ref && !detectarGuiada(pergunta)){
          resolve(montarRespostaReferencia(buscarReferencia(ref, pergunta), nome));
          return;
        }
        var filtros = filtrosDa(pergunta), tokens = tokensDaPergunta(pergunta);
        // o componente escolhido no seletor vale mais que qualquer dedução
        if(componente){ filtros.disciplina = componente; filtros.disciplina_inferida = false; }
        var assunto = extrairAssunto(pergunta);
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
            assunto: assunto,
            filtros: filtros,
            confianca: "guiada"
          });
          return;
        }

        // 2. banco pré-computado pelo motor semântico completo
        var precomputado = doBanco(pergunta);
        if(precomputado){
          escolhidos = diversificar(precomputado.r, 3, 3);
          resultados = escolhidos.map(function(p){ return montarResultado(p[1], pergunta, p[2]); });
          resolve(montarResposta(pergunta, nome, filtros, resultados, tokens, assunto));
          return;
        }

        // 3. pergunta livre: BM25 + a mesma ancoragem lexical do motor local
        // sem expandir, a página que escreve "leituras" nem entra no bolo
        var expandidos = tokens.slice();
        if(D.sinonimos){
          tokens.forEach(function(t){
            (D.sinonimos[t] || []).forEach(function(p){
              if(expandidos.indexOf(p) < 0) expandidos.push(p);
            });
          });
        }
        var pontos = bm25(expandidos);
        if(!pontos) pontos = new Float32Array(indice ? indice.n : 0);
        // a cobertura ordena, não só decide confiança: uma página que contém o
        // que foi perguntado é melhor resposta que uma que só se parece
        // terceiro canal: quem traz a expressão colada entra no pool mesmo que
        // as duas palavras sejam comuns demais para o BM25 destacar
        var elegivel = {}, i;
        for(i = 0; i < pontos.length; i++) if(pontos[i] > 0) elegivel[i] = pontos[i];
        for(i = 0; i + 1 < tokens.length; i++){
          var achados = trechosComFrase(tokens[i], tokens[i + 1]);
          for(var j = 0; j < achados.length; j++){
            if(!elegivel[achados[j]]) elegivel[achados[j]] = 0.0001;
          }
        }
        // a cobertura julga a pergunta inteira, inclusive o que virou filtro
        var comAdj = tokenizarAdjacentes(assunto);
        if(!filtros.disciplina && !componente){
          var inferida = inferirDisciplina(tokens);
          if(inferida){ filtros.disciplina = inferida; filtros.disciplina_inferida = true; }
        }
        var unids = unidadesDaPergunta(comAdj.tokens, comAdj.colados, filtros.disciplina);
        var candidatos = [], cobs = {};
        for(var chave in elegivel){
          var d = +chave;
          var cob = cobertura(unids, indice.tokens[d], D.obras[D.trechos[d][0]][2]);
          cobs[d] = cob;
          candidatos.push([elegivel[d] * fatorMetadado(D.obras[D.trechos[d][0]], filtros), d]);
        }
        if(!candidatos.length){
          resolve(montarResposta(pergunta, nome, filtros, [], tokens, assunto));
          return;
        }
        // a cobertura ordena primeiro e a pontuação desempata: um trecho que
        // aparece nos dois canais somava RRF em dobro e vencia mesmo contendo
        // um terço do que foi perguntado
        candidatos.sort(function(a, b){
          var ca = Math.round((cobs[a[1]] || 0) * 20), cb = Math.round((cobs[b[1]] || 0) * 20);
          return cb !== ca ? cb - ca : b[0] - a[0];
        });
        escolhidos = diversificar(candidatos.slice(0, 200), 3, 3, componente);
        resultados = escolhidos.map(function(p){
          return montarResultado(p[1], pergunta, cobs[p[1]] || 0);
        });
        resolve(montarResposta(pergunta, nome, filtros, resultados, tokens, assunto));
      }, 220);
    });
  }

  function componentes(){
    var vistos = {}, saida = [];
    D.obras.forEach(function(o){
      if(o[2] && !vistos[o[2]]){ vistos[o[2]] = 1; saida.push(o[2]); }
    });
    return Promise.resolve(saida.sort());
  }

  return { buscar: buscar, componentes: componentes };
})();
