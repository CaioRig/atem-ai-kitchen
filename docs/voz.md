# Voz da Luna (síntese de fala offline) — Fase 5

## O que foi implementado (2026-10-11)
- **Servidor (Ryzen):** `server/services/tts.py` (motores Kokoro e Piper, ONNX em CPU, espeak-ng embutido
  nas wheels) e `server/assistant/speech.py` (ciclo da fala: estados, envio do áudio, confirmação,
  interrupção, falhas, "falar de novo"). A `Conversation` entrega a resposta **final** do Ollama à `Speech`;
  o texto já está na tela quando a voz começa.
- **Transporte:** o áudio PCM int16 mono desce pelo **mesmo WebSocket** autenticado que sobe o microfone
  (`speak_start` → quadros binários, um por frase → `speak_end`; ver `docs/protocolo.md`). Nada de arquivos,
  URLs ou portas novas; nada sai da rede local; nenhum áudio é gravado em disco.
- **Cliente (LG/Celeron):** `client/src/speaker.ts` toca os trechos em sequência com Web Audio (um
  `AudioBuffer` por frase, agendados sem lacunas) e avisa `speak_done` ao terminar. Custo no Celeron: só a
  decodificação int16 → float e a reprodução; nenhuma biblioteca nova.
- **Estados:** "Pensando…" → "Respondendo…" → **"Preparando a voz…"** (`synthesizing`, indicador
  "Preparando") → **"Falando"** (`speaking`, boca animada pelo nível do áudio) → "Pronta" → espera.
- **Interrupção:** "hey Luna" no meio da fala (barge-in, `tts.barge_in = true`) para o áudio e abre a
  escuta; **Cancelar**/Esc também para. Uma nova resposta sempre interrompe a anterior: nunca há duas falas
  ao mesmo tempo (o cliente descarta qualquer reprodução ao receber `speak_start` ou `speak_stop`).
- **Falhas:** motor ausente/erro ("Não consegui falar"), áudio vazio, ou o próprio quiosque sem conseguir
  tocar (som bloqueado pela política de autoplay: "toque na tela para liberar o som"). Em todos os casos o
  texto da resposta **continua visível**, o rosto mostra "Erro na voz", a linha de estado mostra a mensagem
  amigável e **Tentar novamente** (tecla R) fala de novo sem consultar a IA (`replay`).
- **Sem bloqueio:** a síntese roda em uma thread (`tts`), frase a frase; o loop asyncio continua livre para o
  microfone e o WebSocket. A primeira inferência é feita na inicialização (`voz aquecida em 0,3 s`).

## Motor e voz escolhidos
| | Kokoro-82M (padrão) | Piper (alternativa) |
|---|---|---|
| Pacote | `kokoro-onnx` 0.6.1 (MIT) + pesos Apache-2.0 (hexgrad/Kokoro-82M) | `piper-tts` 1.8.0 (GPL-3.0) |
| Voz pt-BR | **`pf_dora` (feminina)**, `pm_alex`, `pm_santa` | `faber`, `cadu`, `edresson`, `jeff` (**todas masculinas**) |
| Arquivos | `kokoro-v1.0.onnx` 325,5 MB + `voices-v1.0.bin` 28,2 MB (ou `kokoro-v1.0.int8.onnx` 114 MB) | `pt_BR-faber-medium.onnx` 63,2 MB + `.json` |
| Taxa | 24 000 Hz | 22 050 Hz |
| Medido no Ryzen 5 5600GT (CPU) | carga 1,9–2,2 s; **RTF 0,26–0,30** (1 s de áudio em ~0,3 s); 13,3 s de áudio em 3,3 s | carga 4,6 s; RTF 0,19 |
| Por que | única voz feminina pt-BR de código aberto encontrada; prosódia mais natural | mais leve e mais rápido; vozes masculinas |

As quatro vozes oficiais pt-BR do Piper (`rhasspy/piper-voices`) foram conferidas em 2026-10-11 e são
masculinas; por isso o Kokoro é o padrão, como previsto em `docs/plano.md` (decisão 4). Dependências
instaladas no venv: `kokoro-onnx`, `espeakng-loader` (9,4 MB, traz o espeak-ng), `phonemizer`, `attrs`,
`dlinfo`; opcionalmente `piper-tts` (wheel de 34 MB).

## Instalação (servidor Ryzen, uma vez, com internet)
```powershell
.\.venv\Scripts\python -m pip install -r requirements.txt   # kokoro-onnx e dependências
.\scripts\prepare-models.ps1 kokoro                          # 325,5 MB + 28,2 MB -> models/tts/
.\.venv\Scripts\python scripts\check_tts.py                  # carrega a voz e sintetiza uma frase de teste
.\.venv\Scripts\python scripts\check_tts.py --out $env:TEMP\luna.wav "Olá, eu sou a Luna."   # grava para ouvir
```
Origem dos arquivos (tamanhos conferidos por HEAD antes de baixar): releases `model-files-v1.1` de
`github.com/thewh1teagle/kokoro-onnx` (exportação ONNX dos pesos oficiais). Piper, se quiser testar:
`pip install "piper-tts>=1.8,<2"`, `.\scripts\prepare-models.ps1 piper-pt_BR-faber-medium` e
`[tts] engine = "piper"`. Nada é baixado na inicialização do servidor.

## Configuração (`config/settings.toml`, seção `[tts]`)
```toml
[tts]
enabled = true
engine = "kokoro"                       # ou "piper"
model = "models/tts/kokoro-v1.0.onnx"   # piper: models/tts/pt_BR-faber-medium.onnx (o .json fica ao lado)
voices = "models/tts/voices-v1.0.bin"   # só kokoro
voice = "pf_dora"                       # só kokoro: pf_dora, pm_alex, pm_santa
language = "pt-br"                      # só kokoro (código do espeak-ng)
speed = 1.0                             # 0.5 .. 2.0
volume = 1.0                            # o Kokoro soa um pouco baixo (pico ~0,5); 1.5 é razoável
barge_in = true                         # "hey Luna" interrompe a fala
max_chars = 1500                        # fala até o fim de uma frase antes do limite; o texto inteiro fica na tela
```
Sem a seção `[tts]` valem os padrões acima. `enabled = false` desliga a voz e o fluxo volta a terminar em
"Pronta" logo após o texto. Se o modelo faltar, o servidor sobe sem voz, avisa no log
(`voz indisponivel: modelo nao encontrado ... prepare_models.py kokoro`) e a interface mostra
"· voz indisponível" em espera.

## Preparação do texto
Antes de falar, `prepare_text` remove Markdown (`**`, `#`, listas), emojis e URLs, e `split_sentences`
divide em frases (frases muito curtas, como "Claro!", juntam-se à seguinte; frases com mais de 160
caracteres são divididas em orações para a primeira fala sair antes). Entre frases entra 0,35 s de
silêncio. Números são lidos pelo espeak-ng em português ("25" → "vinte e cinco").

## Medições (2026-10-11, Ryzen 5 5600GT, Kokoro fp32, em processo com os modelos reais)
Fluxo "hey Luna" (WAV) + "Qual é a previsão do tempo para amanhã em Ouro Fino?" (WAV) → whisper.cpp →
Ollama `qwen2.5:7b` → Kokoro → cliente WebSocket de teste:

| Etapa | Tempo |
|---|---|
| fim da fala → texto | 0,81 s |
| resposta da IA (198 caracteres; modelo frio, carregando na GPU) | 7,26 s |
| **resposta final na tela → início da fala** | **0,86 s** (primeira frase, 3,5 s de áudio) |
| áudio total | 13,3 s em 3 trechos, todos prontos 3,3 s após o texto |
| aquecimento na inicialização | 0,3 s (carga 1,9 s) |

A reprodução no navegador (Chromium do servidor, página servida em `http://127.0.0.1`) foi validada com o
comando de teste `say`: "Falando" → `speak_done` → "Pronta", sem o aviso `sem confirmacao da reproducao`
no log. **No LG da cozinha ainda não foi testado** (ver pendências).

Reverificação em 2026-10-11 (10h30), servidor real em porta isolada (127.0.0.1:8801, sem TLS) e um cliente
WebSocket de teste no lugar do quiosque: frase de 151 caracteres → `speak_start` em 0,40 s, 3 trechos,
10,9 s de áudio prontos em 2,59 s, `speak_done` → "Pronta". No mesmo processo: segunda fala durante a
primeira → `speak_stop` imediato e só a segunda toca; `speak_done` com `played: false` → `speak_error`
(`playback`, "toque na tela para liberar o som") e "Não consegui falar"; `replay` em seguida → "Pronta".
`/health` ao fim: `spoken: 3`, `speech_failures: 1`.

## Como testar
1. **Sem microfone:** `scripts\check_tts.py` (acima) mede carga, tempo até o primeiro trecho e RTF, e grava
   um WAV para ouvir a pronúncia.
2. **Na interface, sem falar:** com `debug_allow_state_override = true`, qualquer cliente autenticado pode
   enviar `{"type":"say","text":"Olá, eu sou a Luna."}`; o texto aparece em "Resposta" e é falado. Útil para
   ajustar `volume` e `speed` no LG.
3. **Fluxo completo:** `.\scripts\run-server.ps1`, abra `https://<ip>:8800/#token=…` (toque na tela uma vez:
   libera microfone e som), diga **"hey Luna"**, pergunte algo. Esperado: "Você disse" → "Pensando…" →
   "Respondendo…" com o texto crescendo → "Preparando a voz…" (menos de 1 s) → "Falando" com a boca
   animada → "Pronta". O log registra `voz: primeiro trecho em X s, Y s de audio sintetizados em Z s`.
4. **Interrupção:** durante uma resposta longa, diga "hey Luna" de novo: o áudio para na hora e "Ouvindo ·
   pode falar" aparece. Ou clique **Cancelar**/Esc: "Cancelado".
5. **Falha de síntese:** renomeie `models/tts/voices-v1.0.bin`, reinicie: espera mostra "· voz
   indisponível" e o fluxo termina em "Pronta" só com texto. Para simular falha no meio, pare o servidor
   durante "Falando": o cliente para o áudio ao perder a conexão.
6. **Som bloqueado:** abra a página e **não** toque nela; a primeira fala falha com "toque na tela para
   liberar o som", o texto fica na tela e **Tentar novamente** fala após o toque.
7. **Automáticos:** `.\.venv\Scripts\python -m pytest -q` (`tests/test_speech.py`: fluxo com sintetizador
   falso, falhas, cancelamento, interrupção, nunca duas falas, WebSocket; e o Kokoro real se os arquivos
   existirem) e `cd client; npm test`.
8. `GET /health`: `tts.available/engine/voice`, `spoken`, `speech_failures`, `last_synthesis_s`,
   `last_speech_audio_s`.

## Limitações conhecidas e pendências
- **Teste no LG da cozinha pendente** (alto-falante, volume, latência de rede, autoplay do quiosque). No
  Chromium em modo quiosque use `--autoplay-policy=no-user-gesture-required` para o som não depender de um
  toque após reinicializações.
- **Cancelamento de eco continua desligado** (`client/src/audio.ts`): o microfone ouve a própria Luna.
  O detector só reage a "hey Luna", então falsos positivos pela voz dela são improváveis, mas se
  acontecerem no LG, ligue `echoCancellation` ou desligue `barge_in`. Com barge-in, o pré-buffer pode levar
  um resto da voz da Luna para a transcrição.
- A voz começa só depois da resposta **completa** (0,86 s depois). Sintetizar a primeira frase ainda durante
  o streaming do Ollama é a próxima otimização (Fase 8).
- Kokoro em pt-BR: sotaque e entonação muito bons para uma voz de 82 M de parâmetros, mas palavras raras e
  siglas podem sair estranhas; `speed`/`volume` ajustam o resto. Não há controle de ênfase.
- Sem memória, clima, Spotify ou novas integrações (fases seguintes).
