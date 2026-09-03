/**
 * Recebe as perguntas da Bússola PNLD e escreve numa planilha.
 *
 * Como usar (o passo a passo completo está no README, seção "Registro das
 * sessões de teste"): cole este arquivo em Extensões > Apps Script de uma
 * planilha nova e publique como app da web.
 */

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

    var aba = SpreadsheetApp.getActiveSpreadsheet().getSheets()[0];
    if (aba.getLastRow() === 0) {
      aba.appendRow(COLUNAS);
      aba.getRange(1, 1, 1, COLUNAS.length).setFontWeight('bold');
      aba.setFrozenRows(1);
    }

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
    aba.appendRow(linha);
    return ContentService.createTextOutput('ok');
  } catch (erro) {
    return ContentService.createTextOutput('erro: ' + erro);
  } finally {
    trava.releaseLock();
  }
}

/** Abrir a URL do web app no navegador cai aqui — serve para conferir se subiu. */
function doGet() {
  return ContentService.createTextOutput('Bússola PNLD: endpoint de registro no ar.');
}
