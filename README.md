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
3. `.venv/bin/python ingest/build_index.py` — reconstrói o índice inteiro.
4. Reinicie a API.

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
