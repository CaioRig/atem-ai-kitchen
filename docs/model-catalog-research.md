# Catálogo de modelos e benchmarks — 2026-11-02

Escopo desta solicitação: expandir o catálogo e reutilizar seleção, pull, qualidade e
cache existentes. As etapas A–D anteriores permanecem concluídas; este complemento
não altera ASR, TTS, interface, TOML, credenciais ou bancos de dados.

## Candidatos incluídos

As tags abaixo foram consultadas no catálogo oficial Ollama em 2026-11-02.
Tamanhos são arredondados, não medições locais; o manifesto oficial reconfirma os
bytes antes do pull. Todos são ajustados para instruções/conversa, não modelos base.

| Tag/fonte oficial | Precisão | Download | Inferência estimada (GiB) | Perfil inicial |
|---|---|---|---|---|
| [qwen3:0.6b](https://ollama.com/library/qwen3:0.6b) | Q4_K_M | 523 MB | 1 | econômico |
| [qwen3:1.7b](https://ollama.com/library/qwen3:1.7b) | Q4_K_M | 1,4 GB | 2,5 | intermediário |
| [granite4:350m](https://ollama.com/library/granite4:350m) | BF16 | 708 MB | 1 | econômico |
| [granite4:1b](https://ollama.com/library/granite4:1b) | BF16 | 3,3 GB | 4,5 | intermediário |
| [llama3.2:1b](https://ollama.com/library/llama3.2:1b) | Q8_0 | 1,3 GB | 2 | intermediário |
| [llama3.2:3b](https://ollama.com/library/llama3.2:3b) | Q4_K_M | 2 GB | 3,5 | intermediário |
| [gemma3:270m](https://ollama.com/library/gemma3:270m) | Q8_0 | 292 MB | 0,5 | experimental, último recurso |
| [gemma3:1b](https://ollama.com/library/gemma3:1b) | Q4_K_M | 806 MB | 1,25 | econômico |

Os Qwen2.5 0,5B/1,5B e os modelos maiores existentes foram preservados. Qwen2.5 3B
continua fora do automático pela licença de pesquisa. Qwen3 e Granite usam Apache-2.0;
Gemma e Llama têm termos próprios, com restrições descritas no JSON. Uso local da Luna
não dispensa cumprir as políticas de uso e condições de redistribuição.

RAM/VRAM/contexto são **estimativas Luna**, não requisitos oficiais. A estimativa
de inferência usa pesos arredondados mais margem de runtime/cache para contexto de
2.048 tokens nos novos candidatos. Acrescentam-se as reservas da voz configurada,
ASR, servidor/VAD e margem do sistema; RAM já ocupada pelo SO não é subtraída novamente.
Não é uma medição de memória nem garantia de execução. BF16 explica por que o Granite
1B ocupa mais do que vários candidatos com mais parâmetros quantizados.

## Evidência de idioma e exclusões

- [Qwen2.5 0,5B](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) e
  [1,5B](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct): instruct e português
  documentados; sem aprovação local nesta máquina.
- [Qwen3](https://qwenlm.github.io/blog/qwen3/): português listado entre 119 idiomas
  e dialetos; as variantes pós-treinadas são distintas das variantes Base. O cliente
  envia `think=false` para manter o orçamento de resposta da aplicação.
- [Granite 350M](https://huggingface.co/ibm-granite/granite-4.0-350m) e
  [1B](https://huggingface.co/ibm-granite/granite-4.0-1b): português e instruct
  documentados. Foram escolhidas tags densas; não se transferem resultados das híbridas -h.
- [Llama 3.2](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct): português
  explicitamente suportado e avaliações em português publicadas.
- [Gemma 270M IT](https://huggingface.co/google/gemma-3-270m-it) e
  [1B IT](https://huggingface.co/google/gemma-3-1b-it): documentação multilíngue da
  família e variantes IT; evidência específica de conversa pt-BR insuficiente,
  especialmente em 270M. O 270M é experimental e só pode ser habilitado após a suíte
  local completa; a descrição genérica de 140 idiomas não o aprova.
- [SmolLM2](https://ollama.com/library/smollm2/tags): confirmadas tags 135m, 360m e
  1.7b. O [model card](https://huggingface.co/HuggingFaceTB/SmolLM2-1.7B-Instruct)
  concentra-se em inglês. Não incluídos sem evidência adequada de português.
- [TinyLlama](https://ollama.com/library/tinyllama/tags): confirmada tag 1.1b-chat;
  [upstream](https://huggingface.co/TinyLlama/TinyLlama-1.1B-Chat-v1.0) documenta
  inglês. Não incluído como candidato automático pt-BR.

## Benchmarks publicados

Consulta: 2026-11-02. Os registros numéricos com metodologia, precisão e hardware
estão em `config/model-catalog.json`. Ausência de hardware/precisão é explicitada
com null ou texto; nenhum resultado faltante foi preenchido como zero.

| Fonte | Variante e resultado | Limitação |
|---|---|---|
| [IBM](https://huggingface.co/ibm-granite/granite-4.0-350m) | IFEval Instruct Strict: denso 350M 61,63; denso 1B 80,82 | Precisão e hardware não informados na tabela; não é teste pt-BR |
| [Meta](https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct) | MMLU português 5-shot macro_avg/acc: Instruct BF16 1B 39,8; 3B 54,5 | Biblioteca interna Meta; não quantização Ollama nem conversa pt-BR |
| [Qwen](https://qwen.readthedocs.io/en/v3.0/getting_started/speed_benchmark.html) | Qwen3 BF16/SGLang 0.4.6.post1: 0,6B 414,17; 1,7B 227,80 tokens/s | H20 96 GB, batch 1, entrada 1 token, saída 2048; conta entrada+saída, não taxa de decodificação Luna |
| [llmb, autor independente](https://github.com/folkol/llm-inference-benchmark) | Qwen3 0,6B Q4_K_M CPU quente Generic Assistant: 139 tokens/s, Windows Ultra 9 285K | llama.cpp/GGUF bartowski, contexto 1024, até 256 tokens; não extrapolar ao A12, Ollama ou pt-BR; versão do runtime ausente em capturas antigas |

O [bench.toml do llmb](https://github.com/folkol/llm-inference-benchmark/blob/master/bench.toml)
registra a quantização e as condições. Essa fonte é o relato primário do próprio
avaliador independente. Não foi executado nem instalado seu software.
As fontes de qualidade usam metodologias diferentes; suas pontuações não são
comparadas diretamente nem convertidas em um ranking universal. Modelos sem
resultado aplicável ao GGUF local permanecem **não avaliados**. Os ranks do JSON
são prioridade inicial heurística e preservam a ordem anterior; não são benchmarks.

## Validação e seleção

O `start.bat` mantém sua delegação existente ao bootstrap. Ele considera todas as
famílias autorizadas, memória livre, contexto, reservas e aceleração comprovada;
threads influenciam preferência por modelos pequenos, sem excluir um candidato
maior quando só ele é viável. Reutiliza instalados e o último aprovado compatível,
preserva escolha manual e limita a três tentativas/downloads por inicialização.
Após falha, solicita descarga, reavalia RAM e prefere família diferente com consumo
estimado igual ou menor. Falha de descarga no comparador interrompe novas cargas.

Suíte v6: os dez cenários pedidos (saudação Luna, identidade, cansaço, 12+8, pausa,
previsão, ovos mexidos, Ana em duas mensagens, azul e número desconhecido), mais
capital/geladeira já existentes. Perfil econômico acrescenta 2+2, sim e música.
As duas intenções de comandos usam os reconhecedores reais, sem consultar clima,
acionar o player ou exigir tool calling nativo. Isso avalia o fluxo da Luna;
não prova que o LLM sozinho interpreta comandos. Todos os casos devem passar,
com linguagem compreensível e sem falhas graves; não se exige raciocínio avançado.
As heurísticas aceitam variações, mas têm falsos positivos/negativos e não substituem
avaliação humana de conversação. A suíte tem limite de 600 s, verificado entre casos,
mais o timeout da requisição que estiver em andamento.

Por turno são persistidos tempo de carga, TTFT, taxa de geração, duração e pico RSS
amostrado dos processos Ollama locais (intervalo 0,5 s), além de CPU normalizada
pelo número de processadores lógicos. Essa soma pode incluir o daemon/outros
processos Ollama; memória compartilhada pode ser contada mais de uma vez.
CPU pode subestimar processos encerrados. Não é pico exato de alocação; não se usa
RAM do Python como RAM do modelo. GPU sem telemetria disponível permanece null.
Métricas ausentes não são fabricadas. Windows nativo; leitura Linux via `/proc`.

O registro de aprovação inclui digest/template/versão/parâmetros/hardware/perfil/
versão da suíte. O cache evita nova inferência nas reinicializações; v6 exige nova
validação para aprovações antigas, preservando o histórico. Offline exige cache
correspondente. Modelos reprovados não viram fallback ativo.

Comparação opcional, somente instalados compatíveis, até três sequenciais:

```powershell
.venv/Scripts/python.exe scripts/benchmark_catalog.py
# --force refaz; --limit 1 restringe a um modelo
```

Linux: `.venv/bin/python scripts/benchmark_catalog.py`. O comparador grava
`data/catalog-benchmark.json` e usa registro separado `catalog-benchmark-quality.json`,
preservando a seleção ativa. Não baixa modelos. Os prompts/parâmetros são os mesmos
por perfil, com contexto limitado a 2048 quando não explícito. A primeira resposta
pode incluir carga; keep_alive=0 econômico também inclui carga nas seguintes.
Não compare tempos sem considerar essas condições. `start.bat --reprobe` valida e
configura pela rota normal quando houver memória suficiente.

## Resultado real nesta execução

AMD A12-9720P, quatro threads, 6,96 GiB utilizáveis, 1,54 GiB livres no diagnóstico
inicial e 1,76 GiB no comparador. Sem aceleração confirmada. Ollama `/api/tags`: vazio.
O comparador inicial reservou 1,60 GiB para serviços/margem; não encontrou candidatos
instalados nem viáveis nessa primeira medição. Após encerrar os testes, a preparação
real encontrou 2,68–3,31 GiB livres e aplicou o perfil econômico gerenciado com
Whisper base/Kokoro int8 e reserva de 1 GiB, como previsto no bootstrap existente.
Com rede autorizada, baixou somente três candidatos, em sequência:

| Modelo | Resultado local | Duração da suíte |
|---|---|---|
| Qwen2.5 1,5B | Reprovado, 3/15; acertos apenas nos roteadores determinísticos; respostas corrompidas | 366,32 s |
| Gemma 3 1B | Reprovado, 9/15; falhou em identidade, cansaço, matemática, limitação e instrução simples; houve resposta vazia | 275,72 s |
| Qwen2.5 0,5B | Erro do servidor Ollama na primeira pergunta; qualidade não avaliada | 13,25 s |

**Modelo aprovado/selecionado: nenhum.** Preparação terminou com código 0,
IA desativada e serviços de voz preparados. Não foi iniciada a interface/servidor
completo. A falta de aprovação preserva a preferência anterior e não força um
modelo reprovado. Outros candidatos permanecem não avaliados.

Métricas por caso e identidade verificável estão em
[`model-catalog-local-results.json`](model-catalog-local-results.json), sem histórico
de conversas reais. A primeira coleta de RSS/CPU omitiu o processo `llama-server`;
essas métricas foram excluídas, mantendo valores originais claramente identificados
como daemon/app no registro de execução. O coletor foi corrigido e versionado.
Uma coleta isolada corrigida de Gemma 3 1B, com a mesma saudação e parâmetros
econômicos, mediu: carga 12,50 s, TTFT 15,31 s, total 16,27 s, 11,43 tokens/s,
pico RSS amostrado 1.373.696.000 bytes e CPU média 24,48% normalizada ao computador;
GPU indisponível=null. A resposta foi uma saudação coerente, o que **não aprova**
as demais capacidades. Esse pico refere-se a uma pergunta, não à suíte inteira.
O ambiente desktop não foi isolado; não extrapolar para outra máquina ou carga.

## Manutenção

Confira tag e variante instruct no Ollama; consulte model card e licença do mesmo
upstream; registre data, precisão, tamanhos e fontes. Estime memória separadamente
dos serviços e marque estimativas explicitamente. Registre benchmarks com
metodologia/hardware/precisão, sem extrapolar entre versões. Incremente `revision`,
valide com `load_catalog`, execute testes e avalie localmente antes de afirmar
aprovação. Novos campos são aditivos; `schema_version=1` e campos existentes preservados.
