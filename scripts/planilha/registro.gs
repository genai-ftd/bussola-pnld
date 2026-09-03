/**
 * Recebe as perguntas da Bússola PNLD e escreve numa planilha.
 *
 * Passo a passo completo no README, seção "Registro das sessões de teste".
 * Depois de colar este código, RODE A FUNÇÃO `testar` uma vez no editor: é ela
 * que dispara o pedido de autorização para mexer na planilha. Sem isso o
 * doPost falha calado, porque a autorização concedida na implantação não cobre
 * serviços que o código ainda não usava.
 */

// Deixe vazio se este script foi criado de dentro da planilha
// (Extensões > Apps Script). Se for um projeto avulso, cole aqui o id da
// planilha — é o trecho da URL entre /d/ e /edit.
var PLANILHA_ID = '';

// Deixe vazio para aceitar qualquer envio. Preenchendo, só entram os envios que
// mandarem o mesmo valor — evita que alguém com a URL escreva lixo na planilha.
var SEGREDO = '';

var COLUNAS = [
  'momento', 'sessao', 'nome', 'pergunta', 'assunto', 'confianca', 'cobertura',
  'ms', 'filtro_ano', 'filtro_disciplina', 'filtro_colecao', 'n_resultados',
  'resposta',
  'r1_obra', 'r1_pagina', 'r1_cobertura', 'r1_link',
  'r2_obra', 'r2_pagina', 'r2_cobertura', 'r2_link',
  'r3_obra', 'r3_pagina', 'r3_cobertura', 'r3_link'
];

function abrirAba_() {
  var planilha = PLANILHA_ID
    ? SpreadsheetApp.openById(PLANILHA_ID)
    : SpreadsheetApp.getActiveSpreadsheet();
  if (!planilha) {
    throw new Error('Sem planilha: preencha PLANILHA_ID no topo do script.');
  }
  var aba = planilha.getSheets()[0];
  if (aba.getLastRow() === 0) {
    aba.appendRow(COLUNAS);
    aba.getRange(1, 1, 1, COLUNAS.length).setFontWeight('bold');
    aba.setFrozenRows(1);
  }
  return aba;
}

function gravar_(d) {
  var r = d.resultados || [];
  var linha = [
    d.momento || new Date().toISOString(), d.sessao || '', d.nome || '',
    d.pergunta || '', d.assunto || '', d.confianca || '', d.cobertura || '',
    d.ms || '', d.filtro_ano || '', d.filtro_disciplina || '',
    d.filtro_colecao || '', d.n_resultados || 0, d.resposta || ''
  ];
  for (var i = 0; i < 3; i++) {
    var item = r[i] || {};
    linha.push(item.obra || '', item.pagina || '', item.cobertura || '',
               item.link || '');
  }
  abrirAba_().appendRow(linha);
}

function doPost(e) {
  // Duas abas do navegador podem escrever ao mesmo tempo; sem a trava, uma
  // sobrescreve a linha da outra.
  var trava = LockService.getScriptLock();
  trava.waitLock(30000);
  try {
    var d = JSON.parse(e.postData.contents);
    if (SEGREDO && d.segredo !== SEGREDO) {
      return ContentService.createTextOutput('recusado');
    }
    gravar_(d);
    return ContentService.createTextOutput('ok');
  } catch (erro) {
    // O erro também fica em "Execuções", no menu à esquerda do editor.
    console.error(erro);
    return ContentService.createTextOutput('erro: ' + erro);
  } finally {
    trava.releaseLock();
  }
}

/** Abrir a URL do web app no navegador cai aqui — serve para conferir se subiu. */
function doGet() {
  return ContentService.createTextOutput('Bússola PNLD: endpoint de registro no ar.');
}

/**
 * RODE ESTA FUNÇÃO UMA VEZ no editor, antes de implantar.
 * Ela pede a autorização para escrever na planilha e deixa uma linha de teste,
 * que você pode apagar depois.
 */
function testar() {
  gravar_({
    momento: new Date().toISOString(),
    sessao: 'TESTE',
    nome: '(linha de teste — pode apagar)',
    pergunta: 'teste de conexão do registro',
    assunto: 'teste de conexão',
    confianca: 'alta',
    n_resultados: 0,
    resposta: 'Se você está vendo esta linha, a planilha está recebendo.'
  });
}
