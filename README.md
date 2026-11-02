# Atem (Luna Kitchen)

Assistente pessoal por voz, local e offline, para a cozinha. Interface de rosto
pixelado em tela cheia no computador da cozinha e servidor de IA
na rede local.

- Plano, decisoes e inspecao do ambiente: `docs/plano.md`
- Operacao, inicializacao automatica e roteiro de aceite no LG: `docs/operacao.md`
- Inicializador único Windows/Linux, instalação automática e recuperação: `docs/inicializacao.md`
- Interface responsiva, toque e comandos de tela por voz: `docs/interface.md`
- Area visual para respostas, receitas, previsoes e videos: `docs/conteudo-visual.md`
- Protocolo cliente/servidor: `docs/protocolo.md`
- Instalacao do servidor (Windows): `docs/instalacao-servidor.md`
- Microfone e palavra de ativacao: `docs/microfone.md`
- Transcricao offline (whisper.cpp small-q8_0 no servidor, fim da fala por Silero VAD; medicoes da Fase 4.1): `docs/transcricao.md`
- IA local (Ollama no servidor Ryzen, sem nuvem): `docs/ia-local.md`
- Perfis automáticos, hardware, medições e limites conhecidos: `docs/desempenho.md`
- Aprovação de modelos em português, histórico curto e proteção do TTS: `docs/qualidade-ia.md`
- Voz da Luna (Kokoro-82M `pf_dora`, feminina pt-BR, sintetizada no servidor e reproduzida na cozinha): `docs/voz.md`
- Memória local persistente (SQLite em `data/memory.sqlite3`; "Luna, lembre que…", "o que você lembra?", "esqueça que…", "apague todas as memórias" com confirmação; painel "Memória" na tela): `docs/memoria.md`
- Palavra de ativacao: "hey Luna" em uso; treino do modelo "hey Atem" (pronuncia "ei-tem"): `docs/palavra-de-ativacao.md`
- Previsão do tempo (Fase 8: Open-Meteo gratuita e sem chave; "como está o tempo?", "vai chover hoje?", "como estará o tempo amanhã?", "previsão para os próximos três dias", "você tem uma previsão salva?", "quando o tempo foi atualizado?"; cache persistente no mesmo SQLite da memória, respostas offline com a idade dos dados e aviso quando antigos; cartão "Clima" na tela): `docs/clima.md`
- Receitas e ingredientes disponíveis (Fase 7: "o que posso fazer com ovos, arroz e tomate?", "me ensine a fazer panqueca para duas pessoas", "adapte essa receita para quatro", "tenho três ovos e um pacote de arroz", "usei dois ovos", "quais ingredientes tenho?"; estoque na mesma memória SQLite; receitas longas faladas em resumo): `docs/receitas.md`

## Estrutura

```
client/            interface (HTML, CSS, TypeScript, Canvas 2D)
server/api/        FastAPI + WebSocket
server/assistant/  estados (state.py), fluxo de audio/ativacao (voice.py), conversa com a IA (conversation.py), fala (speech.py), comandos de memoria (memory.py), ingredientes disponiveis (ingredients.py), assistente de receitas (recipes.py) e previsao do tempo (weather.py)
server/services/   wakeword.py (openWakeWord), transcriber.py (whisper.cpp), llm.py (Ollama), tts.py (Kokoro/Piper), weather.py (Open-Meteo); midia (fase seguinte)
server/tools/      comandos permitidos (fase 6)
server/storage/    configuracao (config.py), memoria persistente SQLite (memory.py) e cache do tempo no mesmo SQLite (weather.py)
scripts/           preparacao e execucao
tests/             testes automatizados (pytest)
docs/              documentacao
config/            settings.example.toml -> copie para settings.toml
models/            modelos baixados por scripts/prepare_models.py (fora do git)
```

## Início rápido (Windows e Linux)

Na pasta do projeto, execute **`start.bat`** no Windows ou **`./start.sh`** no Linux.
O inicializador verifica o ambiente, instala o que falta, escolhe/reutiliza modelos,
prepara a interface e inicia o servidor. Na primeira vez, permita os downloads e as
autorizações administrativas explicitamente solicitadas. Nas próximas execuções,
as etapas prontas são reutilizadas. Configurações e memórias existentes são preservadas.

Guia de instalação, uso offline, configuração manual, servidor na LAN e diagnóstico:
[`docs/inicializacao.md`](docs/inicializacao.md). Para somente diagnosticar, execute
`start.bat --check` ou `./start.sh --check`.

## Modelos Ollama automáticos

O `start.bat` preserva suas etapas de preparação e usa `scripts/bootstrap.py` para
detectar CPU/núcleos/threads, RAM disponível, GPU/VRAM, driver, SO e disco.
Windows usa as APIs do sistema; Linux consulta `/proc`, afinidade e limites cgroup v2.
NVIDIA exige inicialização CUDA; o uso real pelo Ollama é conferido em `/api/ps`.
Outras GPUs têm inventário conservador, sem presumir aceleração ou VRAM dedicada.

O catálogo offline [`config/model-catalog.json`](config/model-catalog.json) contém
identificação/tag e aliases instruct, família, versão/digest conhecido, parâmetros,
quantização, download, estimativas mínimas/recomendadas de RAM/VRAM, contexto,
CPU/GPU, português, ranks relativos, fontes, data e licença. As estimativas incluem
contexto/cache e reserva para SO/voz/transcrição; não são garantias oficiais.
Qwen 3B está catalogado, mas excluído da seleção automática por sua licença de pesquisa.
Metadados consultados nas [páginas oficiais Ollama](https://ollama.com/library/qwen2.5)
e no [repositório Qwen](https://huggingface.co/Qwen/Qwen2.5-3B-Instruct).

A seleção preserva escolhas manuais e o último modelo aprovado ainda compatível.
Depois prefere candidatos conhecidos já instalados, priorizando qualidade dentro
dos recursos livres. Não troca modelos somente por velocidade. Quando necessário,
confirma a tag no registro oficial, verifica espaço e baixa um candidato pela
API `/api/pull`, exibindo progresso. Interrompeu? Execute novamente para retomar.
Instalação é confirmada por `/api/tags` e `/api/show`; aliases conhecidos evitam
downloads redundantes. Nunca são apagados modelos do usuário.

Cada novo candidato passa pelos oito testes reais da aplicação (saudação, 42,
Brasília, instrução azul, ovos mexidos, contexto em duas mensagens, desconhecimento
de senha e português). Template, digest, versão do Ollama, hardware e parâmetros
fazem parte da identidade da aprovação. Reprovação permite até três candidatos
compatíveis no modo automático; o último aprovado e as preferências permanecem.
HTTP 200 sozinho não aprova nada. Consulte `data/model-quality.json` e
`data/bootstrap-state.json`; o servidor reutiliza a aprovação sem repetir a suíte.
Esses testes detectam falhas evidentes, sem garantir acerto universal.

Para acrescentar modelos, copie um registro completo do JSON, consulte fontes
oficiais, mantenha tags/aliases únicos, explicite estimativas, confira licença e
incremente `revision`. Valide antes de usar:
`python -c "from scripts.bootstrap import ROOT, load_catalog; load_catalog(ROOT)"`
(use o Python do `.venv`). Valores inválidos ou metadados obrigatórios ausentes são
rejeitados. Não há atualização silenciosa pela internet. O Hugging Face Hub pode
informar licença/família do upstream; seus pesos não equivalem automaticamente
ao GGUF do Ollama e sua API não comprova compatibilidade de hardware.

Para fixar um modelo, em `[llm]` de `config/settings.toml` defina
`automatic_model = false` e `model = "nome:tag"`, ou use Configurações → Desempenho.
Para restaurar automático, limpe a escolha no painel, defina `automatic_model = true`
e, para sobreposição automática em instalações antigas, `[bootstrap] managed = true`.
O inicializador nunca reescreve seu TOML existente. Uma edição de `model` diferente
da baseline salva tem prioridade; para restaurar essa sobreposição, renomeie
`data/bootstrap-auto.json` e execute novamente, preservando o arquivo antigo.

Após a primeira preparação aprovada, use `start.bat --offline` para proibir
downloads/instalações. Nesse modo, somente uma aprovação salva correspondente é
aceita; mudança de template/digest/configuração exige validação fora desse modo.
Use `start.bat --reprobe` para forçar nova validação. Sem modelo utilizável, a Luna
inicia os demais serviços com IA indisponível nesta execução, preservando a configuração.
Falhas de dependências, voz/ativação ou TLS ainda podem impedir a inicialização.
Consulte `logs/bootstrap.log`, libere RAM/VRAM/disco e confira Ollama/internet;
veja mais soluções em [`docs/inicializacao.md`](docs/inicializacao.md).

## Instalação manual existente (opcional, Windows)

```powershell
.\scripts\setup-server.ps1    # cria .venv, instala dependencias, gera config, token e certificado TLS
.\scripts\prepare-models.ps1  # baixa hey_luna + modelos base do openWakeWord + Silero VAD + whisper-small-q8_0 + voz Kokoro (uma vez, com internet; ~620 MB)
.\scripts\build-client.ps1    # compila a interface (TypeScript -> JS)
.\scripts\run-server.ps1      # inicia o servidor
```

IA local: instale o Ollama no servidor, `ollama pull qwen2.5:7b` e confira com
`.\.venv\Scripts\python scripts\check_llm.py` (configuracao em `[llm]`; ver `docs/ia-local.md`).
Voz: confira com `.\.venv\Scripts\python scripts\check_tts.py` (configuracao em `[tts]`; ver `docs/voz.md`).
Memoria: confira com `.\.venv\Scripts\python scripts\check_memory.py` (cenario completo em um banco temporario; configuracao em `[memory]`; ver `docs/memoria.md`).
Clima: confira com `.\.venv\Scripts\python scripts\check_weather.py` (uma consulta real a Open-Meteo em um cache temporario, depois cache, reinicio e falta de internet simulada; configuracao em `[weather]`; ver `docs/clima.md`).
Receitas e ingredientes: confira com `.\.venv\Scripts\python scripts\check_recipes.py` (estoque, receita, porcoes, substituicao e seguranca alimentar com a IA real, em um banco temporario; configuracao em `[recipes]`; ver `docs/receitas.md`).

Abra no computador da cozinha: `https://<ip-do-servidor>:8800/#token=<token>`
(o token fica em `config/settings.toml`; o fragmento `#` nao e enviado ao servidor).
O HTTPS usa um certificado autoassinado gerado por `scripts\make-cert.ps1`: o navegador
exige HTTPS para liberar o microfone. Aceite o aviso na primeira visita (ver `docs/microfone.md`).

## Testes

```powershell
.\.venv\Scripts\python -m pytest -q
cd client; npm test
```
# atem-ai-kitchen
