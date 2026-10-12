# Transcrição de voz offline (Fase 3, ajustada na Fase 4.1)

## O que foi implementado (2026-10-11)
- **Servidor:** `server/services/transcriber.py` (whisper.cpp via `pywhispercpp`, CPU) e
  `server/assistant/voice.py`: após "hey Luna" a fala é acumulada em memória, o fim é
  detectado por voz (Silero VAD) ou por energia, o áudio vai para o whisper.cpp em uma thread
  separada e o texto é enviado à interface na mensagem `transcript`. Depois o assistente volta a `idle`.
- **Interface:** estados "Palavra detectada" → "Ouvindo · pode falar" → "Transcrevendo…" →
  "Transcrição concluída" (ou "Nada reconhecido" / "Falha na transcrição" / "Cancelado"),
  texto reconhecido no cartão "Você disse", botão "Cancelar" (tecla **Esc**) e indicador
  "Erro de áudio" (rosto vermelho) quando o microfone não tem acesso ou a transcrição falhou.
- **Privacidade:** o áudio fica só em memória e é descartado após a transcrição; o texto
  reconhecido não vai ao log; nada sai da rede local (sem serviços externos).

## Fase 4.1 — latência e precisão (2026-10-11)

### Causas encontradas
1. **Custo fixo do encoder do Whisper.** O encoder processa sempre uma janela de 30 s (1500
   posições), então uma frase de 2 s custava o mesmo que uma de 30 s: **~2,4 s** em 6 threads,
   qualquer que fosse a fala (confirmado em 6 amostras de 1,9 a 8,1 s: 2,33-2,72 s).
2. **Fim da fala por energia com piso de ruído errado.** O piso era uma média que a própria
   "hey Luna" (e qualquer fala em espera) inflava; o limiar ficava em 3× esse valor e fala mais baixa
   virava "silêncio": frases cortadas em pausas e `nenhuma fala apos a ativacao` no log. Reproduzido:
   fala emendada logo após "Luna" saía truncada ("Qual é a previsão do tempo?").
3. **Início da frase perdido.** A detecção dispara 80-160 ms depois do fim de "Luna"; capturar a partir
   de 240 ms dentro da fala perde a primeira palavra ("é a previsão..." em vez de "Qual é a previsão...").
4. **Metade dos núcleos.** `threads = 0` significava 6 de 12 threads; 12 threads são 15-20 % mais rápidas.

### Alterações
| Onde | O que | Efeito medido |
|---|---|---|
| `[transcription] audio_ctx = 768` | contexto do encoder reduzido para falas curtas (sobe automaticamente se o áudio precisar; 0 = completo) | 2,4 s → 1,0-1,3 s por frase, mesmo texto em 6 amostras limpas e 12 com ruído (10 e 5 dB) |
| `[transcription] threads = 0` = todos os núcleos | 12 threads em vez de 6 | −15-20 % |
| `[transcription] model = ggml-small-q8_0.bin` | quantização 8 bits do mesmo `small` | mesmo texto em todas as amostras, −20 %, 265 MB em vez de 465 MB |
| `[wake] vad_model = silero_vad.onnx` | fim da fala pelo Silero VAD (0,4 ms/quadro), não por volume | fala a 15 % do volume capturada; ruído rosa contínuo após a ativação → "nenhuma fala" em 18/18 realizações |
| `[wake] preroll_s = 0.32` | os 320 ms anteriores à detecção entram na captura | frase emendada sai inteira; "Luna" não vaza para o texto (testado até 480 ms de pré-buffer) |
| piso de ruído (sem VAD) | percentil 10 do RMS dos últimos 3 s, com histerese | 2 s de fala alta em espera deixam o limiar em 600 (antes: ~5400) |
| logs de duração | ver abaixo | — |

Descartado após medir: `beam_size = 5` (+0,2 s, nenhum texto melhor nas amostras), `flash_attn`
(mais lento na CPU), contexto dinâmico menor que 768 (alucinações em frases longas),
`listen_silence_s = 1.0` (uma pausa de 0,7 s na fala aparece como ~0,9 s de silêncio para o VAD;
1,2 s fica).

### Tempos: antes e depois (Ryzen 5 5600GT, CPU)
| Etapa | Antes | Depois |
|---|---|---|
| Detecção de "hey Luna" após o fim da palavra | 80 ms (1 quadro), 1,5 ms/quadro | igual |
| Espera pelo silêncio para encerrar | 1,2 s | 1,2 s (configurável) |
| Transcrição, frase de ~3 s pelo microfone (log do servidor) | 2,02-2,15 s | **0,84-0,89 s** (modelos reais, amostra de 4,6 s) |
| Fim da fala → texto na tela | ~3,3 s | **~2,1 s** |
| Suíte de testes (inclui transcrições reais) | 35 s | 21 s |

Os tempos "depois" vêm do fluxo completo (`VoicePipeline` com detector, VAD e whisper reais) e do
benchmark `audio_ctx`/threads/modelo; a validação com o microfone da cozinha ainda precisa ser feita.

### Logs com duração (INFO, `jarvis.voice`)
```
palavra de ativacao detectada (score=0.85, detector 1.5 ms/quadro, ruido RMS 210, fim da fala por VAD)
fala capturada: 3.1 s (fala de 1.4 s comecou 0.24 s apos a ativacao; fim por silencio de 1.2 s)
transcricao em 0.86 s: 19 caracteres (audio 3.1 s); fim da fala -> texto em 2.15 s (silencio 1.21 + fila 0.01 + transcricao 0.86)
nenhuma fala apos a ativacao em 4.0 s (VAD)
```
"silencio" é tempo de relógio: bem acima de `listen_silence_s` indica atraso de rede ou cliente
lento. `GET /health` expõe `endpointing` (`vad`/`rms`), `noise_rms`, `detector_ms_per_frame`,
`last_capture_s`, `last_transcription_s` e `last_speech_to_text_s`.

## Modelo de transcrição
- **whisper.cpp `ggml-small-q8_0.bin`** (multilíngue, 265 MB) com **VAD Silero
  `ggml-silero-v5.1.2.bin`** (0,9 MB), que descarta silêncio/ruído antes do decodificador
  e evita frases inventadas quando não há fala. Idioma fixo `pt`, sem tradução, decodificação
  gulosa sem novas tentativas (latência previsível).
- Por que `small`: `base`/`tiny` erram muito em português; `medium` usa ~2 GB de RAM e é
  ~3x mais lento na CPU. Por que `q8_0`: mesmo texto que o `small` completo em todas as amostras
  (limpas e com ruído), 20 % mais rápido e metade da RAM. `ggml-small.bin` continua aceito.
- **GPU (não implementado):** a GTX 1080 reduziria a transcrição para ~0,3 s e permitiria `medium`
  (mais preciso) em ~1 s, mas o `pywhispercpp` só tem wheel para CPU; a versão CUDA exige compilar
  com CUDA Toolkit 12 e MSVC, que não estão instalados. Fica como opção para a Fase 8.
- **Nenhum modelo é baixado automaticamente.** Para disponibilizar (uma vez, com internet):
  ```powershell
  .\scripts\prepare-models.ps1                 # padrão: hey_luna + Silero VAD + whisper-small-q8_0 + VAD do whisper
  .\scripts\prepare-models.ps1 whisper-small   # variante completa (465 MB), opcional
  ```
  Sem internet no servidor, copie os arquivos manualmente:
  - `models/whisper/ggml-small-q8_0.bin`: https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small-q8_0.bin
  - `models/whisper/ggml-silero-v5.1.2.bin`: https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin
  - `models/openwakeword/silero_vad.onnx`: https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/silero_vad.onnx
- Configuração em `config/settings.toml`, seção `[transcription]`: `model`, `vad_model`,
  `language`, `threads` (0 = todos os núcleos lógicos), `audio_ctx` (768; 0 = completo) e
  `done_hold_s`. Os tempos da captura ficam em `[wake]`: `vad_model`, `listen_silence_s` (1,2 s),
  `listen_max_s` (8 s), `preroll_s` (0,32 s) e `cooldown_s`.
- Modelo ou biblioteca ausente: o servidor sobe mesmo assim, `welcome.transcription.available`
  é `false` com o motivo em `reason`, a interface mostra "Aguardando ativação · diga “hey Luna”
  · transcrição indisponível" e, após a fala, volta à espera sem texto. Sem o `silero_vad.onnx`,
  o fim da fala volta ao limiar de energia (aviso no log; `/health` mostra `endpointing: rms`).

## Onde a transcrição roda (Celeron × Ryzen)
O computador da cozinha (Celeron N3450, 4 GB, HD mecânico) roda **apenas o navegador**
(decisão 3 em `docs/plano.md`): o áudio já é enviado em PCM pelo WebSocket ao servidor Ryzen,
que faz a detecção da palavra de ativação, o fim da fala e a transcrição, no mesmo processo e sem
nenhum canal adicional. Portanto o Celeron **não é o gargalo** da transcrição; nada foi movido.
Medido no Ryzen 5 5600GT (CPU, 12 threads, sem GPU):

| Medida | Valor |
|---|---|
| Carga do modelo small-q8_0 + VAD | 0,2-0,5 s |
| Transcrição de 1,9 a 8,1 s de áudio (`audio_ctx` 768) | 0,8-1,0 s |
| Mesmo áudio com contexto completo e 6 threads (antes) | 2,3-2,7 s |

No Celeron não foi medido (máquina inacessível a partir daqui). Pela diferença de CPU e por
~400 MB de RAM concorrendo com o Chromium em 4 GB, a execução local seria 8 a 15x mais lenta,
o que inviabiliza mover a transcrição para lá.

## Como testar
1. **Sem microfone** (voz pt-BR do Windows → WAV 16 kHz → whisper). Salve o script como `.ps1`
   **com BOM UTF-8** (senão a voz lê "ã" como "libra" e "é" como "copyright"):
   ```powershell
   Add-Type -AssemblyName System.Speech
   $s = New-Object System.Speech.Synthesis.SpeechSynthesizer
   $s.SelectVoice('Microsoft Maria Desktop')
   $f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
   $s.SetOutputToWaveFile("$env:TEMP\frase.wav", $f); $s.Speak('Que horas são?'); $s.Dispose()
   .\.venv\Scripts\python scripts\check_transcription.py "$env:TEMP\frase.wav"
   ```
   A saída mostra `threads=12 vad=True audio_ctx=768` e o tempo; para comparar com o contexto
   completo, mude `audio_ctx = 0` em `settings.toml` e repita.
2. **Com microfone:** `.\scripts\run-server.ps1`, abra `https://<ip>:8800/#token=…`, confirme
   a pílula verde do microfone e diga **"hey Luna"** seguido da frase (com ou sem pausa).
   Sequência esperada: "Palavra detectada" → "Ouvindo · pode falar" → (1,2 s de silêncio) →
   "Transcrevendo…" → texto em "Você disse" → "Transcrição concluída" / resposta da IA.
   O log registra as três linhas com duração mostradas acima (não o texto).
3. **Frases para o teste manual na cozinha** (anote o texto e `fim da fala -> texto` de cada uma):
   curta ("Que horas são?"), longa ("Qual é a previsão do tempo para amanhã em Ouro Fino, e preciso
   levar guarda-chuva?"), com pausas ("Anota aí… dois quilos de farinha… uma dúzia de ovos… e açúcar"),
   em voz baixa, e com a torneira aberta ou o exaustor ligado. Se com ruído forte frases curtas saírem
   erradas, teste `audio_ctx = 0`; se pausas longas cortarem a frase, suba `listen_silence_s`.
4. **Cancelar:** botão "Cancelar" ou **Esc** durante "Ouvindo" ou "Transcrevendo…" → "Cancelado"
   → espera. Com `debug_allow_state_override = true`, a tecla **3** força "Ouvindo" sem a
   palavra de ativação.
5. **Sem microfone / permissão negada / sem HTTPS:** o rosto fica vermelho com "Erro de áudio"
   e o texto explica o motivo. **Sem modelo:** renomeie o `.bin` do modelo e reinicie:
   `GET /health` mostra `transcription.available = false`.
6. **Automáticos:** `.\.venv\Scripts\python -m pytest -q`. Com os modelos presentes rodam
   também os testes reais: `test_real_whisper_transcribes_portuguese_sample_and_ignores_noise`
   (frase em pt-BR reconhecida; ruído puro vira texto vazio) e
   `test_real_pipeline_transcribes_phrase_after_hey_luna_without_the_wake_word`
   ("hey Luna" + frase → transcrição correta **sem** a palavra "Luna", agora com pré-buffer).
   Testes novos da Fase 4.1: pré-buffer, piso de ruído, fim da fala por VAD e cálculo do `audio_ctx`.

## Fim da fala e exclusão da palavra de ativação
- A captura começa 320 ms antes da detecção (`preroll_s`): cobre o atraso de 80-160 ms do
  openWakeWord sem incluir "Luna" no texto (o VAD do whisper descarta o fragmento; verificado pelo
  teste acima e com pré-buffers de até 480 ms).
- Fim da fala: com `silero_vad.onnx`, cada quadro de 80 ms recebe uma probabilidade de voz; a fala
  começa em ≥ 0,6 e continua em ≥ 0,4 (histerese). Sem o arquivo, energia: limiar = max(600,
  3 × piso), piso = percentil 10 do RMS dos últimos 3 s em espera (a fala tem pausas entre
  palavras; o ruído contínuo não), limitado a 4000, e metade do limiar mantém a fala.
- Termina após `listen_silence_s` de silêncio depois de alguma fala, 4 s sem fala alguma (volta à
  espera sem transcrever) ou `listen_max_s` no total.
- O tempo é contado em quadros de áudio, não em relógio; atrasos de rede não encurtam a frase.

## Limitações conhecidas
- Validado apenas nesta máquina (servidor) e com áudio sintético/gravado; o microfone do LG e o
  ruído real da cozinha continuam não validados (ver "Como testar", item 3).
- Uma transcrição final por frase, sem resultados parciais; frases com mais de 8 s são cortadas.
- `audio_ctx = 768` custa robustez só em ruído muito forte (fala quase no nível do ruído, 5 dB):
  uma frase de 1,9 s saiu errada onde o contexto completo acertou. Em 10 dB não houve diferença.
- O Silero reage a ruído branco estacionário forte (chega a 0,7); ruído rosa/ventilador fica em ~0,2.
- Precisão do `small` em pt-BR: erros em nomes próprios e em frases curtas com ruído são esperados;
  o salto de qualidade seria `medium` na GPU (ver "Modelo de transcrição").
