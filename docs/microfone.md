# Microfone e palavra de ativação

## O que foi implementado (2026-10-11)
- **Cliente (navegador):** `getUserMedia` + `AudioWorklet` (`client/public/audio/capture-processor.js`)
  convertem o microfone em PCM int16 mono 16 kHz, blocos de 80 ms, enviados em
  binário pelo WebSocket já existente. Nada é gravado; sem conexão o bloco é descartado.
  Botão "Microfone" (ou tecla **M**) liga/desliga; a preferência fica em `localStorage`.
- **Servidor:** `server/services/wakeword.py` (openWakeWord, ONNX, CPU), `server/services/vad.py`
  (Silero VAD para o fim da fala) e `server/assistant/voice.py` (ativação → captura da fala → transcrição).
- **Interface:** estados "Aguardando ativação · diga …", "Palavra detectada",
  "Microfone desativado", "Microfone indisponível · motivo" (sem dispositivo, permissão
  negada, página sem HTTPS, microfone desconectado) e "Detecção de voz indisponível · motivo".

## Captura no navegador (Fase 4.1)
- **Formato:** o contexto de áudio é pedido em 16 kHz mono; o próprio navegador reamostra com
  qualidade. Se ele recusar (contexto em 44,1/48 kHz), o worklet reamostra com filtro
  anti-aliasing (sinc janelado, corte em 7 kHz) e interpolação linear; antes era só interpolação,
  que dobrava o conteúdo acima de 8 kHz para dentro da banda (verificado: alias de 20 kHz caiu de
  0,5 para 0,0002). O console (F12) mostra `microfone: "<nome>" · dispositivo N Hz … · contexto N Hz`.
- **Processamento do navegador:** cancelamento de eco **desligado** (nada é reproduzido ainda; o
  AEC do Chrome pode atenuar o início das frases), supressão de ruído e ganho automático
  **ligados** (nível estável para o detector e o fim da fala). Constantes em `client/src/audio.ts`
  (`MIC_PROCESSING`); religar o eco quando a Luna falar (Fase 5).
- **Nível de entrada:** o rosto anima com o RMS do bloco; no servidor, cada ativação registra
  `ruido RMS N` (piso em espera) e `/health` expõe `noise_rms`. Piso acima de ~1000 com a cozinha em
  silêncio indica ganho alto demais ou microfone ruim; piso 0 com a pílula verde indica microfone mudo
  no sistema. Ajuste em Configurações de som do Windows (ou `alsamixer`/`pavucontrol` no Lubuntu).
- **Qual microfone:** a pílula verde mostra o nome do dispositivo escolhido pelo navegador (o padrão
  do sistema). Para trocar, mude o dispositivo padrão no sistema e recarregue a página.

## Fim da fala (Fase 4.1)
Com `models/openwakeword/silero_vad.onnx` (baixado por `prepare-models.ps1`), cada quadro de 80 ms
após a ativação recebe uma probabilidade de voz do Silero VAD; a fala começa em ≥ 0,6 e continua
em ≥ 0,4. Fala baixa continua sendo fala e ventilador/água/geladeira não viram fala. Sem o arquivo,
vale um limiar de energia com piso de ruído robusto (ver `docs/transcricao.md`). Os 320 ms anteriores
à detecção entram na captura (`preroll_s`), para quem fala emendado não perder a primeira palavra.

## A detecção é real?
Sim. **Em uso: "hey Luna"** (`hey_luna.onnx`, coleção da comunidade Home Assistant, MIT),
escolhido por soar igual em português; validado com as vozes do Windows em inglês e pt-BR
(score 0,87; "hey Jarvis" e frases em português ficam em 0,001). A frase final do produto é
**"hey Atem"** (pronúncia "ei-tem") e **não existe modelo pronto para ela**: o openWakeWord não tem modelos
em português e a frase exige treino customizado (notebook oficial, dados sintéticos por TTS,
Colab/GPU). O procedimento completo está em `docs/palavra-de-ativacao.md`; o resultado é
`models/openwakeword/hey_atem.onnx`. Se `[wake] model` apontar para um arquivo inexistente,
`welcome.wake.available` é `false` e a interface mostra "Detecção de voz indisponível · modelo
nao encontrado…". O pré-treinado `hey_jarvis_v0.1` (inglês) também serve: ajuste
`[assistant] wake_word` e `[wake] model` em `config/settings.toml`. O servidor nunca simula
uma detecção.

## Como testar
1. Modelos: `.\scripts\prepare-models.ps1` (uma vez, com internet; baixa `hey_luna.onnx`, os
   modelos base do openWakeWord, o `silero_vad.onnx` e o whisper-small-q8_0 em `models/`).
2. Servidor: `.\scripts\run-server.ps1`. `GET /health` mostra `wake.available`,
   `audio_frames` (quadros recebidos), `wakes` (ativações), `endpointing` (`vad` ou `rms`),
   `noise_rms` e `detector_ms_per_frame`.
3. Interface: abra `https://<ip>:8800/#token=…`. O navegador pede permissão de microfone.
   - **Atenção:** `getUserMedia` só funciona em contexto seguro (HTTPS ou `localhost`).
     Em `http://192.168.1.6:8800` a interface mostra "Microfone indisponível: a página
     precisa de HTTPS ou localhost".
   - **Solução padrão (HTTPS):** `.\scripts\make-cert.ps1` gera um certificado autoassinado
     em `config/certs/` (SAN com o IP de `bind_host`) e `settings.toml` aponta para ele em
     `[server] tls_certfile/tls_keyfile`. O servidor passa a atender em `https://` e `wss://`.
     Na primeira visita o navegador avisa que o certificado não é confiável: em
     "Avançado → Continuar" uma vez e o microfone é liberado. Para remover o aviso, importe
     `config/certs/server.crt` em "Autoridades de certificação raiz confiáveis" do computador
     da cozinha. Ao trocar o IP do servidor, gere o certificado de novo (`-Force`).
   - Alternativas sem HTTPS (deixe `tls_certfile`/`tls_keyfile` vazios):
     - Chrome/Chromium (quiosque): `--unsafely-treat-insecure-origin-as-secure=http://192.168.1.6:8800`
       junto com `--use-fake-ui-for-media-stream` (concede o microfone sem perguntar) e
       `--autoplay-policy=no-user-gesture-required`.
     - Firefox: `about:config` → `media.devices.insecure.enabled = true` e
       `media.getusermedia.insecure.enabled = true`.
     - Ou testar no próprio servidor via `http://localhost:8800/#token=…`
       (o servidor precisa escutar em `127.0.0.1` ou use um túnel local).
4. Com o microfone ativo (pílula verde), diga **"hey Luna"**: o rosto muda para
   "Palavra detectada" → "Pode falar"; ao silenciar por 1,2 s passa a "Transcrevendo…", o texto aparece em "Você disse" e volta a
   "Aguardando ativação" (ver `docs/transcricao.md`).
   O log registra `palavra de ativacao detectada (score=…, detector … ms/quadro, ruido RMS …)`,
   `fala capturada: N s (…)` e `transcricao em X s … fim da fala -> texto em Y s`.
5. Teclas de depuração (se `debug_allow_state_override = true`): **3** força `listening`
   e permite testar o fim por silêncio sem a palavra de ativação; **2** volta a `idle`.

### Teste sem microfone (voz sintética do Windows)
```powershell
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
$s.SetOutputToWaveFile("$env:TEMP\hey_luna.wav", $f); $s.Speak("Hey Luna"); $s.Dispose()
.\.venv\Scripts\python scripts\check_wakeword.py "$env:TEMP\hey_luna.wav"
```
Saída esperada: `modelo=hey_luna score_max=0.87 … -> DETECTADO`. Para testar com sotaque
brasileiro, selecione a voz pt-BR antes de falar: `$s.SelectVoice("Microsoft Maria Desktop")`.
As amostras `tests/fixtures/hey_luna_16k.wav` e `hey_jarvis_16k.wav` habilitam os testes
`test_real_model_detects_hey_luna_sample` e `…_hey_jarvis_sample` no pytest. Um negativo em
inglês ("Hello, what time is it") chegou a 0,49: se houver falsos positivos na cozinha, suba
`[wake] threshold` para 0,6.

## Limitações conhecidas
- O computador da cozinha (LG) não estava acessível: a captura foi validada apenas
  nesta máquina. Microfone integrado do LG continua não validado; as constantes de
  processamento (`MIC_PROCESSING`) e o nível de entrada precisam ser conferidos lá.
- `threshold = 0.5` é o padrão do openWakeWord; ajuste após ouvir falsos positivos/negativos.
- Um único cliente alimenta o detector por vez (o segundo recebe `error busy`).
