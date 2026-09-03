# Bússola PNLD — POC de busca conversacional no acervo do PNLD 2027

Protótipo funcional de assistente para o Portal PNLD da FTD: o professor pergunta em
linguagem natural e recebe **obra + trecho + página + link direto** para o visualizador
público do Issuu.

A busca é **semântica de verdade** (embeddings + similaridade), rodando sobre o texto
nativo extraído dos PDFs. **Não há nenhuma chamada a LLM / IA generativa** em nenhuma
etapa — a resposta conversacional é montada por template determinístico a partir dos
metadados recuperados, conforme o escopo aprovado.

---

## Como funciona

```
PDFs (data/pdfs/)
   │  PyMuPDF — texto nativo, página a página, sem OCR
   ▼
Páginas limpas (de-hifenizadas, com o nº impresso no rodapé detectado)
   │  chunking por página (páginas longas viram janelas com sobreposição)
   ▼
Trechos ──► embeddings multilíngues (MiniLM) ──► data/index/embeddings.npy
       └──► BM25 (termos exatos: "BNCC", "EF15LP03", "cantiga")

Pergunta do professor
   │  filtros determinísticos por regex (ano · disciplina · coleção)
   ▼
Busca híbrida (denso + BM25, fundidos por Reciprocal Rank Fusion)
   ▼
Template de resposta ──► cartões com citação, página e link do Issuu
```

O trecho **nunca cruza a fronteira da página**, então o link sempre aponta exatamente
para a página de onde o texto saiu.

## Estrutura

```
data/pdfs/         PDFs do PNLD (fora do Git)
data/index/        índice gerado: chunks.jsonl, embeddings.npy, manifest.json (fora do Git)
data/catalog.json  catálogo das obras + links do Issuu (versionado)
ingest/            extração, chunking, metadados, cliente Issuu, construção do índice
api/               FastAPI: busca híbrida + montagem determinística da resposta
frontend/          front-end do chat (HTML/CSS/JS puro, sem build)
scripts/run_dev.sh sobe API + front
```

---

## Rodando localmente

**Pré-requisitos:** Python 3.9+ e ~2 GB de disco (PyTorch + modelo de embeddings).

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # e preencha ISSUU_TOKEN
```

Coloque os PDFs em `data/pdfs/` e rode a ingestão:

```bash
.venv/bin/python ingest/build_catalog.py   # casa cada PDF com a publicação no Issuu
.venv/bin/python ingest/build_index.py     # extrai, quebra em trechos e gera embeddings
```

O primeiro `build_index.py` baixa o modelo de embeddings (~470 MB). Depois disso tudo
roda offline — só o `build_catalog.py` precisa de rede.

Suba a POC:

```bash
./scripts/run_dev.sh
```

Abra <http://127.0.0.1:8000/> e clique no botão **Bússola PNLD** no canto inferior direito.

### Endpoints

| Método | Rota            | Descrição                                        |
|--------|-----------------|--------------------------------------------------|
| GET    | `/api/health`   | status do índice (modelo, nº de trechos e obras)  |
| GET    | `/api/catalogo` | obras indexadas                                   |
| POST   | `/api/busca`    | `{"pergunta": "...", "nome": "...", "limite": 3}` |

```bash
curl -s localhost:8000/api/busca -H 'Content-Type: application/json' \
  -d '{"pergunta":"atividades com cantigas populares no 1º ano","nome":"Ana"}'
```

---

## Adicionando novos PDFs ao índice

1. Copie o PDF para `data/pdfs/`.
2. `.venv/bin/python ingest/build_catalog.py` — descobre título oficial e link do Issuu.
3. `.venv/bin/python ingest/build_index.py` — vetoriza só o que mudou.
4. Reinicie a API.

A ingestão mantém um cache por obra em `data/index/obras/`, chaveado pelo hash do
PDF e pelo nome do modelo: acrescentar um livro custa a vetorização desse livro,
não do acervo inteiro (medido: 249 s na primeira execução, 1,6 s na segunda, sem
sequer carregar o modelo). Use `--refazer` para ignorar o cache.

O casamento com o Issuu é feito por **tamanho do arquivo em bytes** (é literalmente o
mesmo arquivo que foi publicado) e, como reserva, por nome de arquivo + nº de páginas.
Se o PDF não estiver publicado no Issuu, a obra ainda é indexada e buscável — só fica
sem o link "Abrir conteúdo".

Sem credenciais do Issuu: `python ingest/build_catalog.py --sem-issuu` gera o catálogo
a partir do nome dos arquivos, e o resto funciona normalmente (sem links).

---

## Versão publicável (demo estático)

```bash
.venv/bin/python scripts/build_static_demo.py
```

Gera `dist/bussola-pnld.html`: uma página única e autossuficiente (~2,9 MB) com o
índice embutido, para abrir no celular ou compartilhar sem subir servidor.

**O que muda em relação ao local, e por quê.** A página publicada não tem backend,
então não consegue rodar o modelo de embeddings para vetorizar a pergunta. Ela leva:

- os resultados **semânticos reais**, pré-computados pelo motor completo, para um banco
  de 28 perguntas típicas de professor (as sugestões do chat estão entre elas);
- **BM25** embutido sobre os 4.354 trechos, para qualquer pergunta digitada fora do banco.

Todo o resto é idêntico ao local: mesmas stopwords, mesmos filtros por regex, mesma
diversificação por obra, mesmos templates de resposta, mesmos links. Continua sem
nenhuma chamada a LLM. A busca livre é mais fraca que a semântica — para avaliar a
qualidade real de recuperação, use a versão local.

O fundo é o print do portal `pnld.ftd.com.br`, embutido como data URI a partir de
`frontend/assets/portal-pnld.jpg`.

### GitHub Pages

O build também escreve `index.html` na raiz (com `.nojekyll`), que é o que o Pages serve
em **Settings → Pages → Source: Deploy from a branch → `main` / `(root)`**.
Depois de cada `build_static_demo.py`, commite `index.html` e dê push — o Pages
republica sozinho em cerca de um minuto.

Publicado em <https://genai-ftd.github.io/bussola-pnld/>.

---

## Quando a Bússola diz que não sabe

A primeira versão errava com confiança: perguntada sobre Páscoa — que não aparece
em nenhuma das 1.044 páginas — devolvia semáforo, vocabulário e ficha de avaliação
como se fossem resposta.

A causa não era o limiar estar no lugar errado, era o **método**. Medimos: a
similaridade de cosseno não separa. Perguntas fora do acervo pontuam 0,45–0,61
contra prosa pedagógica em português, acima de vários acertos legítimos. Qualquer
corte sobre o cosseno erra dos dois lados.

O sinal que separa é lexical: **o trecho contém os termos que carregam o assunto
da pergunta?** Pesamos cada termo pelo IDF no acervo e exigimos que o trecho cubra
uma fração mínima dessa massa (`api/confianca.py`). Termos genéricos valem pouco;
termos raros ou ausentes valem muito e derrubam a confiança sozinhos.

Três faixas, em vez de um corte único:

| cobertura | comportamento |
|---|---|
| ≥ 0,50 | responde direto |
| 0,38 – 0,50 | responde **com ressalva explícita** |
| < 0,38 | diz que não encontrou, e diz qual termo faltou |

A cobertura também **ordena**, não só decide: uma página que contém o que foi
perguntado vence uma que só se parece com a pergunta no espaço vetorial.

### Taxa de erro medida

`.venv/bin/python scripts/avaliar_confianca.py` roda 44 perguntas rotuladas
(25 dentro do acervo, 19 fora) e varre o limiar:

| | responde direto | com ressalva | recusa |
|---|---|---|---|
| **25 dentro do acervo** | **25** | 0 | **0** |
| **19 fora do acervo** | 2 | 2 | 15 |

Recall de 100% nas perguntas que o acervo responde, nenhuma recusa indevida, e
2 em 19 perguntas de fora respondidas sem aviso (10,5%). Para mover o ponto de
operação, mude `LIMIAR_ALTO` / `LIMIAR_BAIXO` em `api/confianca.py` e rode o
harness de novo — a varredura mostra o custo de cada escolha.

O conjunto rotulado é pequeno e feito à mão: serve para calibrar e para pegar
regressão, não como medida de produção. Amplie antes de decidir se o formato
determinístico se sustenta.

### Limite conhecido

Os 2 erros restantes são colisão de sentido, não de calibragem, e valem ser
olhados de perto antes de decidir o formato:

- **"atividades sobre o sistema solar"** casa com "filtro solar" (orientação de
  saúde), porque `solar` e `sistema` estão os dois na página.
- **"dia da consciência negra"** casa com uma página que traz `consciência` e
  `negra` em frases distintas — bonecas negras, representatividade.

Os termos estão mesmo lá; nenhum corte lexical separa isso. Subir o limiar para
pegá-los custa respostas boas — a varredura do harness mostra o câmbio. Resolver
de verdade pede proximidade entre termos ou desambiguação de sentido, que é outra
camada de trabalho.

## Perguntas sobre a coleção

Quatro perguntas não são busca por trecho — o professor quer panorama, e um cartão
solto não responde. `api/guiadas.py` detecta por palavra-chave e devolve um texto
curado mais as páginas onde aquilo aparece:

- habilidades de leitura previstas / BNCC
- turmas multisseriadas
- recursos de acessibilidade
- material de apoio ao professor

Toda afirmação nesses textos foi conferida contra o texto extraído. **Multisseriadas
não existe no acervo** — o termo não aparece em nenhuma página — e a resposta diz
isso, mostrando o assunto vizinho sob o rótulo "não é resposta" em vez de empurrar
a página mais parecida.

## Mensagens do chat

`api/responder.py` guarda listas de variantes: aberturas confiantes, aberturas com
ressalva, quatro mensagens de "não encontrei" que nomeiam o termo ausente e quatro
genéricas. A escolha é semeada pela própria pergunta — varia entre perguntas, mas a
mesma pergunta sempre devolve a mesma frase, o que mantém os testes reproduzíveis.
A copy vale para as duas camadas: o build embute essas listas na página publicada.

---

## Registro das sessões de teste

Cada pergunta e o que a busca devolveu ficam registrados, para a rodada de teste
render dado em vez de impressão.

**Na página publicada** (que é estática e não tem servidor): o registro fica no
navegador do testador, e ele não vê nada disso — nenhum botão, nenhum aviso.

Para a equipe pegar os dados, dois caminhos:

- abrir a página com **`?registro`** na URL
  (`https://genai-ftd.github.io/bussola-pnld/?registro`), o que revela um botão
  **Registro** no cabeçalho do chat com a contagem e o download;
- ou `bussolaRegistro.baixar()` no console, que faz a mesma coisa.

O arquivo é um **CSV** separado por `;` e com BOM, então o Excel em português e o
Google Sheets abrem já nas colunas certas. Uma linha por pergunta, com os três
resultados achatados:

`sessao · momento · nome · pergunta · assunto · confianca · cobertura · ms ·
filtro_ano · filtro_disciplina · filtro_colecao · n_resultados · resposta ·
r1_obra · r1_pagina · r1_cobertura · r1_link · r1_trecho · r2_… · r3_…`

Se o download for bloqueado no ambiente, `bussolaRegistro.csv()` devolve o mesmo
conteúdo como texto, e `bussolaRegistro.linhas` devolve o JSON cru.

O registro vive no `localStorage` do navegador de cada testador: é por aparelho,
sobrevive a recarregar a página e some se a pessoa limpar os dados do site. Para
juntar tudo num lugar só, use o envio abaixo.

### Planilha viva, sem o testador exportar nada

Com isto, cada pergunta cai numa planilha do Google na hora. São sete passos e
não precisa saber programar — o código já está pronto em
[`scripts/planilha/registro.gs`](scripts/planilha/registro.gs).

1. Crie uma **planilha nova** no Google Sheets. Pode deixar vazia; as colunas
   são criadas sozinhas na primeira pergunta.
2. Nessa planilha, menu **Extensões → Apps Script**. Abre um editor de código
   numa aba nova.
3. Apague o `function myFunction() {}` que vem de exemplo e **cole todo o
   conteúdo** de `scripts/planilha/registro.gs`. Salve (o disquete, ou ⌘S).
4. Botão azul **Implantar → Nova implantação**.
5. Na engrenagem ao lado de "Selecione o tipo", escolha **App da Web**. Depois:
   - *Executar como*: **Eu**
   - *Quem pode acessar*: **Qualquer pessoa**

   Esse segundo campo precisa ser "Qualquer pessoa" mesmo, não "qualquer pessoa
   com conta do Google": a página manda os dados sem ninguém estar logado.
6. **Implantar**. O Google vai pedir autorização e mostrar um aviso de "app não
   verificado" — é o seu próprio script. Clique em **Avançado → Acessar
   (nome do projeto)** e autorize.
7. Copie a **URL do app da Web** (termina em `/exec`).

Com a URL na mão, ligue na página:

```bash
echo 'BUSSOLA_LOG_URL=https://script.google.com/macros/s/SEU_ID/exec' >> .env
.venv/bin/python scripts/build_static_demo.py
git add index.html && git commit -m "Liga o registro na planilha" && git push
```

O build avisa `registro remoto ligado` quando pega a URL. Para conferir se
subiu, abra a URL do `/exec` no navegador: deve responder *"Bússola PNLD:
endpoint de registro no ar."*.

**Cuidados.** Quem tiver a URL consegue escrever na planilha — não publique num
lugar aberto. Se quiser travar, preencha `SEGREDO` no topo do script e me avise
para a página mandar o mesmo valor. Para trocar o código depois, use
**Implantar → Gerenciar implantações → editar → Nova versão**, senão a URL
antiga continua rodando o código antigo.

**Rodando local**, a API grava sozinha em `data/registros/AAAA-MM-DD.jsonl`, uma
linha JSON por pergunta — fora do controle de versão.

⚠️ O registro inclui o nome que o professor digita no chat. Para uma rodada
interna tudo bem; se for sair da FTD, tire o campo `nome` antes.

---

## Limitações conhecidas

**1. A API do Issuu não entrega o conteúdo das páginas.**
`GET /v2/publications/{slug}/assets?assetType=text` devolve `text: {}` vazio para estas
publicações, e `assetType=image` devolve a marca d'água "MATERIAL DE DIVULGAÇÃO" em vez
da página. Todas as publicações do PNLD 2027 testadas têm
`fileInfo.isCopyrightConfirmed: false` — principal suspeito da trava, e vale testar
confirmar esse campo no painel do Issuu. **Por isso a indexação vem dos PDFs**, e a API
do Issuu é usada só para metadados e para montar o link. O campo `copyright_confirmado`
fica registrado em `data/catalog.json` para acompanhamento.

**2. Numeração de página: dois números diferentes.**
- `pagina_fisica` / `pagina_issuu`: posição no documento, contando a capa como 1 — é o
  que o link do Issuu entende.
- `pagina_impressa`: o número que o professor vê no rodapé. Nas obras testadas há um
  deslocamento consistente (ex.: página física 41 = página impressa 31).

A POC mostra os dois ("página 31 (página 41 do visualizador)") e usa o físico no link.
`build_catalog.py` só marca `offset_pagina: 0` quando o PDF local e a publicação do Issuu
têm o mesmo número de páginas — ou seja, quando é comprovadamente o mesmo arquivo. Se
divergirem, ele avisa e o offset fica `null`; **valide por amostragem antes de confiar**.
Nas 5 obras atuais o offset é 0 e foi conferido.

**3. Termos de Uso do Issuu.**
Os ToS proíbem extração em massa. Esta POC **não extrai nada do Issuu**: o conteúdo vem
dos PDFs originais e a API é chamada apenas para metadados (`GET /v2/publications`), com
paginação que para assim que todas as obras locais são encontradas. Mantenha assim.

**4. Títulos do Issuu nem sempre batem com o arquivo.**
Em parte do acervo (fora do PNLD 2027) o `title` da publicação não corresponde ao
`fileInfo.name` — há um deslocamento de um registro em um bloco de uploads. Por isso o
casamento **nunca** usa título. Vale uma revisão desses títulos no painel do Issuu.

**5. Escopo da POC.** Sem IA generativa, sem memória de conversa, sem login, sem
personalização, sem embed do livro dentro do chat. O nome do professor é guardado só no
`localStorage` do navegador e nunca vai para o servidor além do corpo da requisição.

**6. Escala.** O índice denso é uma matriz NumPy em memória, com busca exata — ótimo até
~10⁵ trechos (o acervo inteiro do PNLD 2027 cabe folgado). Acima disso, trocar por FAISS
é substituir uma função em `api/search.py`; o resto do pipeline não muda.

---

## Segurança

- `.env`, `credentials.txt` e os PDFs estão no `.gitignore` desde o primeiro commit.
- As credenciais do Issuu são lidas **apenas** de variáveis de ambiente.
- ⚠️ O token que veio em `credentials.txt` foi exposto em conversa fora deste
  repositório. **Revogue-o no painel do Issuu e gere um novo** antes de qualquer uso
  além desta POC local.
- O CORS da API está liberado (`*`) por ser POC local — restrinja antes de qualquer deploy.
