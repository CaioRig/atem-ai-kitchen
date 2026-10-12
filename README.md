# Atem (Jarvis Kitchen)

Assistente pessoal por voz, local e offline, para a cozinha. Interface de rosto
pixelado em tela cheia no computador da cozinha (LG All-in-One) e servidor de IA
na rede local (Ryzen 5 5600GT + GTX 1080).

- Plano, decisoes e inspecao do ambiente: `docs/plano.md`
- Protocolo cliente/servidor: `docs/protocolo.md`
- Instalacao do servidor (Windows): `docs/instalacao-servidor.md`
- Microfone e palavra de ativacao: `docs/microfone.md`
- Transcricao offline (whisper.cpp small-q8_0 no servidor, fim da fala por Silero VAD; medicoes da Fase 4.1): `docs/transcricao.md`
- IA local (Ollama no servidor Ryzen, sem nuvem): `docs/ia-local.md`
- Voz da Luna (Kokoro-82M `pf_dora`, feminina pt-BR, sintetizada no servidor e reproduzida na cozinha): `docs/voz.md`
- Memória local persistente (SQLite em `data/memory.sqlite3`; "Luna, lembre que…", "o que você lembra?", "esqueça que…", "apague todas as memórias" com confirmação; painel "Memória" na tela): `docs/memoria.md`
- Palavra de ativacao: "hey Luna" em uso; treino do modelo "hey Atem" (pronuncia "ei-tem"): `docs/palavra-de-ativacao.md`

## Estrutura

```
client/            interface (HTML, CSS, TypeScript, Canvas 2D)
server/api/        FastAPI + WebSocket
server/assistant/  estados (state.py), fluxo de audio/ativacao (voice.py), conversa com a IA (conversation.py), fala (speech.py) e comandos de memoria (memory.py)
server/services/   wakeword.py (openWakeWord), transcriber.py (whisper.cpp), llm.py (Ollama), tts.py (Kokoro/Piper); clima, midia (fase 6)
server/tools/      comandos permitidos (fase 6)
server/storage/    configuracao (config.py) e memoria persistente SQLite (memory.py)
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
