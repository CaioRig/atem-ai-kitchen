
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
