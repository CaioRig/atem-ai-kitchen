
# Luna — Progresso da implementação

## Status
- Etapa A: Concluída e validada
- Etapa B: Concluída e validada
- Etapa C: Concluída e validada
- Etapa D: Concluída e validada

## Etapa atual
A–D concluídas conforme autorização do usuário nesta solicitação.

## Arquivos modificados
Etapa A: `server/services/performance.py`, `tests/test_hardware.py` e este registro.
Etapa B: `config/model-catalog.json`, `scripts/bootstrap.py`, `tests/test_model_catalog.py`.
Etapa C: `scripts/bootstrap.py`, `server/services/model_quality.py`, `server/api/app.py`,
`tests/test_bootstrap.py`, `tests/test_model_quality.py`.
Etapa D: `scripts/bootstrap.py`, `server/main.py`, `server/api/app.py`,
`server/storage/config.py`, `server/services/performance.py`, `tests/test_server_startup.py`,
ajustes nos testes de bootstrap/catálogo, `README.md`, `docs/inicializacao.md` e este registro.
O `start.bat` permanece intacto: sua delegação existente integra todas as alterações.

## Funcionalidades concluídas
Etapa A: reutilizada a detecção existente de CPU, núcleos, threads/afinidade, RAM,
limites cgroup v2, disco e NVIDIA/CUDA. Acrescentados inventário conservador de outras
GPUs e avisos de detecção parcial, sem afirmar aceleração pelo Ollama antes de verificar `/api/ps`.
Etapa B: catálogo offline enriquecido com fontes oficiais Ollama/Qwen, identificação,
quantização, licença, estimativas explícitas de RAM/VRAM e classificação relativa.
Seleção exige catálogo conhecido, chat/português, memória disponível e backend confirmado;
reutiliza aliases instalados. Qwen 3B fica fora da seleção automática por sua licença de pesquisa.
Etapa C: verificação de manifesto no registro oficial antes do pull, espaço confirmado,
progresso por camada e confirmação tags/show. Retentativas limitadas por reprovação;
seleção manual preservada; modo offline exige aprovação correspondente já persistida.
Removida troca automática por velocidade; identidade, opções e aprovação salvas reutilizam
o registro existente. Suíte v4 usa as perguntas do pedido e a rota real de receitas.
Etapa D: indisponibilidade da IA não bloqueia outros serviços; flags de execução do
servidor transmitem o resultado sem editar TOML/credenciais. Aprovação anterior e
sobreposição do modelo são preservadas nas falhas. Contexto explícito maior recebe
estimativa proporcional conservadora; memória já carregada pelo Ollama é contabilizada.
Documentados catálogo, extensão, seleção manual/automática, retomada, offline e recuperação.

## Testes realizados
Etapa A: 53 testes passaram (`test_hardware`, `test_performance`, `test_bootstrap`).
Diagnóstico real `scripts/bootstrap.py --check`: Windows, Ryzen 5 5600GT, 6 núcleos,
12 threads, 15,87 GiB RAM, GTX 1080 8 GiB, CUDA inicializada; somente ~1 GiB RAM
e ~589 MiB VRAM livres na medição. Dependências existentes verificadas, sem instalar.
Etapa B: 17 testes de catálogo/hardware passaram, incluindo dados externos inválidos,
GPU ocupada ou sem driver, memória desconhecida, aliases e restrições de seleção.
Etapa C: 86 testes direcionados passaram. Teste real `scripts/check_quality.py --force --show-answers`:
`qwen2.5:7b`, já instalado e configurado manualmente, aprovado nos oito casos (nove mensagens),
incluindo saudação, 42, Brasília, azul, receita e retenção de Ana. Opções reais: temperatura
0,7; top_p 0,8; repeat_penalty 1,05; contexto 4096; 8 threads; 300 tokens (600 na receita).
Nenhum modelo foi baixado; relatório sintético persistido em `data/model-quality.json`.
Etapa D: regressão completa: **391 passaram, 3 ignorados**, em 50,18 s; um aviso
preexistente de depreciação Starlette/httpx. Os 116 testes direcionados passaram,
incluindo primeira execução sem modelos, reinicialização, três reprovações limitadas,
contexto explícito, modo offline, falha da IA com continuidade da voz e flags do servidor.
`start.bat --check`: passou. Duas execuções reais de
`start.bat --prepare-only --offline`: passaram com Ollama, ativação/VAD, Whisper
small-q8_0 e Kokoro/Dora existentes; nenhuma instalação ou download de pesos.
Cache real da aprovação reutilizado sem suíte nova; `/api/ps` confirmou uso de GPU.
Manifesto oficial de `qwen2.5:7b` consultado sem pesos: 4.683.086.845 bytes em camadas.
`git diff --check`: sem erros de whitespace.

## Problemas encontrados
O progresso inicial estava pendente, embora já existissem catálogo, seleção,
download e testes de qualidade. As etapas concluídas corrigiram lacunas nessas rotinas.
Linux foi simulado; não há execução nativa Linux neste ambiente.
Downloads interrompidos e instalações sem modelos foram simulados, sem download real
redundante. Não foram instaladas dependências. Outras GPUs/ARM64 e instalações limpas
reais continuam como limites de validação; não impedem a entrega das etapas implementadas.

## Próxima ação
Iniciar a aplicação com `start.bat` (ou `start.bat --offline` com o ambiente já preparado).
Nenhuma etapa adicional será executada automaticamente.

## Regras
- Atualizar este documento ao concluir cada etapa.
- Registrar arquivos alterados e resultados dos testes.
- Não marcar etapas como concluídas sem validação.
- Não apagar informações importantes de etapas anteriores.

## Correção solicitada — notebook econômico (2026-11-02)

Escopo: corrigir o bloqueio de seleção/download, sem iniciar novas etapas da
especificação original. O catálogo só oferecia 1,5B ou maiores, exigia pelo menos
3 GiB livres e tratava threads como requisito de compatibilidade. Após falha,
o log ainda anunciava o modelo configurado anterior mesmo com a IA desativada.

Alterados: `config/model-catalog.json`, `scripts/bootstrap.py`, `server/main.py`,
`server/storage/config.py`, `server/services/performance.py`,
`server/services/llm.py`, `server/services/model_quality.py`, README e testes
de bootstrap, catálogo, qualidade, LLM e inicialização. `start.bat` preservado,
com integração pela delegação existente ao bootstrap.

Incluído Qwen2.5 0,5B Instruct Q4_K_M, mantendo 1,5B e conferindo metadados nas
páginas oficiais Ollama (398 MB e 986 MB aproximados, respectivamente).
RAM de inferência é estimada separadamente de Whisper, TTS, servidor/VAD e margem
do sistema; RAM já ocupada pelo SO não é subtraída duas vezes. Com os modelos de
voz econômicos, as estimativas iniciais são 1,75 GiB livres para 0,5B e 2,5 GiB
para 1,5B. Não são requisitos oficiais nem medições de inferência neste notebook.
Até quatro threads/perfil econômico priorizam modelos de até 1,6B por latência
estimada; falta de CUDA não veta CPU. Inventário AMD não implica aceleração.

Falhas de memória, timeout e configuração são distinguidas da qualidade,
interrompem testes inúteis e permitem recuperação posterior. Fallback limitado
a três candidatos progressivamente menores, descarregando o reprovado e
reavaliando RAM. Aprovações permanecem no registro; a sobreposição ativa deixa
de anunciar modelos indisponíveis. O servidor recebe modelo/contexto/threads
aprovados por argumentos, inclusive em configurações automáticas sem overlay.

Validação desta correção:
- 120 testes direcionados passaram (bootstrap, catálogo, qualidade, LLM e
  inicialização), com um aviso de depreciação Starlette/httpx.
- Suíte completa executada: uma falha no teste real de velocidade do Kokoro
  `test_real_kokoro_voice_speaks_portuguese`: 7,79 s para 5,55 s de áudio.
  Não foi alterado esse teste nem o motor de voz; o limite pressupõe velocidade
  maior que tempo real e não foi atendido neste A12. Há testes ignorados por
  recursos locais ausentes. Os testes direcionados acima foram repetidos após
  os últimos ajustes de orçamento de memória e validação de catálogo.
- Compilação Python e `git diff --check`: passaram.
- `start.bat --prepare-only` executado duas vezes, ambos com código 0. A primeira
  execução carregou ativação/VAD, Whisper base e Kokoro int8; a segunda reutilizou
  a preparação. Log final correto: `IA=desativada; llm.enabled=False`.
- Hardware real: AMD A12-9720P, quatro threads, 6,96 GiB utilizáveis, apenas
  0,87–0,95 GiB livres nas medições. Ollama `/api/tags` vazio. Consulta CIM fora
  do sandbox confirmou Radeon 500 Series, AMD Radeon R7 Graphics e Parsec Virtual
  Display Adapter; dentro do sandbox a consulta foi negada e a detecção parcial
  usou CPU conservadoramente. Aceleração Ollama não confirmada.
- pytest/httpx instalados no `.venv` com aprovação para executar os testes.

Modelo aprovado no hardware atual: **nenhum**, por RAM disponível insuficiente.
Download econômico, fallback e persistência foram simulados, sem pesos reais.
Download/conversa real em português dos candidatos e inicialização completa do
servidor com modelo aprovado continuam pendentes de RAM livre suficiente. Não
foi removido nenhum modelo nem editado TOML, credenciais ou banco de dados.

## Complemento solicitado — perfil ultraleve (2026-11-02)

Preservadas as alterações locais da correção anterior. Sem avanço para novas
etapas A–D. Complementados `server/services/model_quality.py`,
`server/services/llm.py`, `server/services/performance.py`, `scripts/bootstrap.py`,
`server/assistant/screen.py`, `server/services/videos.py`, README, este registro
e testes de qualidade, desempenho, bootstrap, tela e vídeos. `start.bat` intacto.

- Suíte v5: perfil econômico testa as quatro respostas básicas solicitadas
  (saudação, identidade Luna, 2 + 2, apenas sim) pelo LLM, usando parâmetros reais;
  testa as três intenções de música/clima/parada pelos reconhecedores reais,
  sem consultas meteorológicas fictícias. Perfil padrão mantém seus oito casos.
- Assinatura inclui perfil e versão, evitando reutilizar aprovação econômica
  como aprovação da suíte padrão. Cache, reinicialização e offline cobertos
  pelos testes; falhas distinguem latência, recursos, infraestrutura e qualidade.
- Contexto econômico automático de 2.048 tokens, até 128 tokens nas respostas
  comuns, pelo menos 180 s de timeout, threads limitadas pela política existente.
  Preferência explícita de contexto preservada. `keep_alive=0` solicita descarga
  após cada geração, antes da fala da resposta; a reserva simultânea de ASR/TTS
  continua no orçamento. Receitas preservam seu limite específico existente.
- Música reconhecida sem LLM, encaminhada à pesquisa de vídeos existente;
  pausar/retomar usa o player integrado. Reprodução no YouTube ainda exige
  seleção/interação do usuário e internet; não foi implementada nova integração.
- Reconfirmados nas páginas oficiais Ollama os IDs `qwen2.5:0.5b` (494M,
  Q4_K_M, 398 MB) e `qwen2.5:1.5b` (1,54B, Q4_K_M, 986 MB).

Validação deste complemento:
- 154 testes direcionados passaram, incluindo suíte econômica, isolamento do
  cache por perfil e equivalência dos parâmetros entre bootstrap e servidor.
- Suíte completa: **409 passaram, 8 ignorados, 1 falhou**, em 157,61 s.
  A falha foi novamente o benchmark real do Kokoro não quantizado:
  10,79 s para produzir 5,54 s de áudio. O teste exige síntese mais rápida que
  tempo real; não foram alterados esse teste nem o motor de voz.
- Compilação dos módulos Python alterados e `git diff --check`: passaram.
- Diagnóstico real `scripts/bootstrap.py --check`: passou, dependências presentes.
  AMD A12-9720P, quatro threads, RAM utilizável 6,96 GiB, RAM livre entre
  1,20 e 1,46 GiB nas duas medições; CPU conservadora, aceleração não confirmada.
  Ollama `/api/tags` retornou lista vazia. Seleção real recusou todos os
  candidatos por RAM livre abaixo do orçamento mínimo estimado de 1,75 GiB.

Modelo aprovado real: **nenhum**. Candidato ultraleve disponível no catálogo:
`qwen2.5:0.5b`, condicionado à RAM e à aprovação real. Nenhum peso baixado nesta
execução. Conversa real, latência e consumo do LLM, reprodução real e reinício
offline com modelo aprovado permanecem sem validação neste hardware por falta
de RAM livre suficiente. Fluxos de download, fallback e offline foram simulados.
Não foram instaladas dependências nem editados credenciais, TOML ou bancos.
Para iniciar: `start.bat`; após preparação/aprovação, `start.bat --offline`.

## Expansão solicitada — catálogo multifamília e benchmarks (2026-11-02)

Escopo autorizado pelo texto anexado: pesquisa, catálogo, suíte pt-BR, medição e
integração com a seleção existente. Preservadas alterações locais anteriores;
sem novas etapas da especificação A–D e sem subagentes.

Incluídos oito candidatos: Qwen3 0,6B/1,7B, Granite 4 denso 350M/1B,
Llama 3.2 Instruct 1B/3B e Gemma 3 IT 270M/1B. Total: 13 registros.
Tags, precisão e tamanhos conferidos no Ollama; licenças e instruções/idiomas nos
model cards. SmolLM2 135M/360M/1,7B e TinyLlama 1,1B pesquisados, mas não
incluídos por evidência insuficiente de português. Gemma 270M permanece candidato
experimental de último recurso, nunca aprovação presumida.

Fontes e limitações detalhadas em `docs/model-catalog-research.md`. Benchmarks:
IFEval IBM Granite, MMLU português Meta Llama, throughput Qwen em H20 e medição
CPU independente llmb de Qwen3 0,6B. Metodologias/precisões distintas não geram
ranking comparável; ranks existentes são heurísticos. RAM/VRAM são estimativas
Luna separadas de requisitos oficiais e de medições reais.

Suíte v6 inclui os dez cenários pedidos e mantém capital/geladeira; perfil
econômico acrescenta 2+2, sim e música. Comandos usam roteadores reais sem ações.
Cache identificado por suíte/perfil/digest/template/opções/hardware. Aprovações
antigas ficam no histórico, mas exigem v6 antes de reutilização. Histórico e escolha
manual preservados. Thinking Qwen3 desativado no cliente real e no benchmark.
Fallback de até três candidatos/downloads prefere família diferente de consumo
igual ou menor, incluindo falhas no pull; reavalia recursos após descarga.
Threads passam a ser preferência de latência sem bloqueio absoluto de modelos maiores.

Por turno: métricas streaming existentes (carga, TTFT, tokens/s, total) e novo
sampler de RSS/CPU dos processos Ollama no Windows/Linux, sem dependências extras.
Pico é amostrado a cada 0,5 s, não pico exato; GPU indisponível=null. Medições
locais não são atribuídas a servidores remotos. Comparador opcional
`scripts/benchmark_catalog.py` avalia apenas instalados compatíveis, sequencialmente,
com cache separado e descarga; não troca o modelo ativo nem baixa pesos.

Arquivos deste complemento: `config/model-catalog.json`, `scripts/bootstrap.py`,
`scripts/benchmark_catalog.py`, `server/services/llm.py`,
`server/services/model_quality.py`, `server/services/model_resources.py`,
testes de catálogo/bootstrap/LLM/qualidade/recursos/comparador, README,
documento de pesquisa e este progresso. `start.bat` intacto: integração pela
delegação já existente. Demais alterações locais não foram modificadas neste pedido.

Validação:
- 135 testes direcionados passaram após os últimos ajustes, com um aviso
  preexistente Starlette/httpx; inclui falha de download seguida de outra família.
- Regressão completa antes dos últimos novos testes: 409 passaram, 8 ignorados,
  1 falhou. Falha preexistente `test_real_kokoro_voice_speaks_portuguese`:
  8,62 s de síntese para 5,54 s de áudio; requisito de velocidade não atendido no A12.
  ASR/TTS e esse teste foram preservados.
- JSON validado por `load_catalog`: 13 modelos; compilação dos cinco módulos
  Python alterados/criados e `git diff --check`: passaram.
- Diagnóstico real `scripts/bootstrap.py --check`: código 0, dependências presentes.
- Comparador real executado: código 0; AMD A12-9720P, quatro threads, 6,96 GiB
  utilizáveis, 1,76 GiB livres na medição, sem aceleração comprovada. Configuração
  atual reserva 1,60 GiB para serviços/margem antes do LLM. `/api/tags` vazio,
  nenhum candidato viável, nenhum peso baixado. Resultado em
  `data/catalog-benchmark.json` (arquivo de execução ignorado pelo Git).

Complemento da validação real: após os testes, a RAM disponível aumentou para
2,68–3,31 GiB. `start.bat --prepare-only` aplicou a reserva econômica de 1 GiB.
A primeira tentativa sem rede não conseguiu consultar manifestos; a repetição com
rede autorizada baixou somente Qwen2.5 1,5B, Gemma 3 1B e Qwen2.5 0,5B.
Qwen 1,5B reprovou (3/15, 366,32 s; respostas corrompidas); Gemma 1B reprovou
(9/15, 275,72 s; falhas básicas); Qwen 0,5B encontrou erro do servidor na primeira
pergunta (13,25 s), sem avaliação conclusiva de qualidade. Preparação terminou com
código 0 e IA desativada, voz econômica preparada. Nenhum modelo aprovado/selecionado.
Resultados por caso em `docs/model-catalog-local-results.json`.

A coleta inicial omitia `llama-server` do RSS/CPU. O coletor foi corrigido,
versionado na assinatura e os números iniciais excluídos das métricas de inferência,
preservados como daemon/app no registro de execução. Coleta corrigida isolada de
Gemma 1B: saudação coerente, carga 12,50 s, TTFT 15,31 s, total 16,27 s,
11,43 tokens/s, pico RSS amostrado 1.373.696.000 bytes, CPU normalizada 24,48%,
GPU=null. Essa pergunta não constitui aprovação nem pico de toda a suíte.
Demais novos candidatos continuam não avaliados; não se inventaram métricas.
`start.bat --prepare-only --offline` também terminou com código 0 sem downloads
nem nova suíte; IA permaneceu desativada por ausência de aprovação. Corrigido o
diagnóstico desse caso para não atribuir falta de aprovação a falta de memória.
O inventário de processos Windows foi executado; Linux possui implementação, mas
não foi validado nativamente. Nenhuma instalação de dependências, remoção de modelos,
edição de TOML/credenciais/bancos, push ou deploy.
