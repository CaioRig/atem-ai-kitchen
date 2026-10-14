# Atem (Luna Kitchen)

Assistente pessoal por voz, local e offline, para a cozinha. Interface de rosto
pixelado em tela cheia no computador da cozinha e servidor de IA
na rede local.

- Plano, decisoes e inspecao do ambiente: `docs/plano.md`
- Protocolo cliente/servidor: `docs/protocolo.md`
- Instalacao do servidor (Windows): `docs/instalacao-servidor.md`
- Microfone e palavra de ativacao: `docs/microfone.md`
- Transcricao offline (whisper.cpp small-q8_0 no servidor, fim da fala por Silero VAD; medicoes da Fase 4.1): `docs/transcricao.md`
- IA local (Ollama no servidor Ryzen, sem nuvem): `docs/ia-local.md`
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

## Inicio rapido (servidor, Windows)

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
