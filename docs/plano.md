# Plano e decisões técnicas — Jarvis Kitchen

Documento vivo. Atualizado em 2026-10-11 (Fases 1–2 concluídas; captura de voz, palavra de ativação e transcrição offline implementadas).

## 1. Inspeção do ambiente (Fase 1)

### Repositório
`E:\Atem` estava vazio. Projeto criado do zero com a estrutura sugerida.

### Servidor de IA (esta máquina, hostname DESKTOP-1FA56VD)

| Item | Valor medido |
|---|---|
| CPU | AMD Ryzen 5 5600GT |
| RAM | 15,9 GB total, ~6 GB livres em uso normal (a máquina também é desktop) |
| GPU | NVIDIA GeForce **GTX 1080** (não Ti), **8 GiB VRAM**, compute capability 6.1 |
| Driver NVIDIA | 582.66 (32.0.15.8266), status OK, API CUDA 13.0 disponível |
| SO | Windows 10 Pro 10.0.19045 |
| Rede | Ethernet, IP 192.168.1.6/24; firewall ativo nos 3 perfis |
| Disco | E: ~995 GB livres; C: ~603 GB livres |
| Python | gerenciador oficial `py` 26.3; 3.12.10 instalado para o projeto (3.14.7 já existia) |
| Node | v22.1.0, npm 10.7.0 |
| Ausentes | Ollama, whisper.cpp, Piper, ffmpeg, CUDA Toolkit, Docker, WSL |

Verificações feitas sem presumir que a GPU funciona:

- `nvidia-smi.exe` falha com `Failed to initialize NVML: Unknown Error` em
  qualquer shell. Porém `nvml.dll` (nvmlInit_v2 → Success, driver 582.66) e
  `nvcuda.dll` (cuInit → 0, 1 dispositivo, 8 GiB) funcionam via ctypes.
  Conclusão: o utilitário está quebrado, o driver e o CUDA não. Monitoramento
  de VRAM será feito via NVML por ctypes/pynvml, não via `nvidia-smi`.
- A GTX 1080 (Pascal) consta na lista oficial de GPUs suportadas pelo Ollama
  (exige driver ≥ 570; temos 582.66). O CUDA Toolkit 13 removeu suporte a
  Pascal: **usar sempre builds CUDA 12** de whisper.cpp/llama.cpp; o Ollama
  escolhe o runner `cuda_v12` sozinho.
- Armadilha do gerenciador `py` no Git Bash: ele resolve o diretório de
  instalação relativo ao diretório atual. Instale sempre a partir de
  `%LocalAppData%` (ver `docs/instalacao-servidor.md`).

### Cliente da cozinha (LG 22V270)
Não está acessível a partir desta máquina. Continua em Windows 10 Home; a
migração para Lubuntu e a validação do microfone ficam para as Fases 4 e 7.
Nada foi alterado nele.

## 2. Decisões críticas

1. **SO do servidor permanece Windows 10 Pro.** Não há autorização para migrar
   e todo o stack escolhido (Ollama, whisper.cpp, Piper/Kokoro, FastAPI) tem
   build Windows com CUDA 12. Reavaliar só se um componente falhar.
2. **Python 3.12** para o servidor (wheels garantidos para onnxruntime,
   ctranslate2, piper). O 3.15.0 saiu há dias e não tem wheels nativas.
3. **Cliente = apenas um navegador em modo quiosque.** Captura de microfone
   via `getUserMedia` + AudioWorklet (PCM 16 kHz mono enviado por WebSocket),
   reprodução via `<audio>`. Zero processos extras no Celeron com 4 GB.
   Consequência: a **detecção da palavra de ativação roda no servidor**
   (openWakeWord custa poucos % de um núcleo do Ryzen; o fluxo de áudio é de
   32 KB/s na rede local e nunca sai dela). Se a captura no navegador se
   mostrar instável na Fase 4, a alternativa documentada é um pequeno agente
   Python de áudio no cliente.
4. **Voz feminina pt-BR:** as vozes oficiais do Piper para pt_BR (faber, cadu,
   edresson, jeff) são todas masculinas. Candidatas a testar na Fase 4, nesta
   ordem: Kokoro-82M via ONNX (Apache-2.0, voz `pf_dora`, CPU) e vozes
   comunitárias Piper pt-BR com licença compatível. Piper masculina fica como
   fallback funcional até a decisão.
5. **Palavra de ativação configurável sem presumir modelo pronto:** não existe
   modelo openWakeWord pré-treinado em português. Plano: treinar um modelo
   customizado com o procedimento oficial (dados sintéticos gerados pelo TTS
   local), documentado em `docs/`. Alternativa funcional sem treino: Vosk com
   gramática restrita à palavra-chave (modelo pt-BR pequeno, ~31 MB).
6. **Autenticação:** token compartilhado enviado na primeira mensagem do
   WebSocket (`hello`), nunca em query string, nunca em logs. O quiosque
   recebe o token por fragmento de URL (`#token=…`), que o navegador não
   envia ao servidor; o cliente guarda em `localStorage` e limpa a URL.
7. **Rede:** servidor vinculado ao IP da LAN configurado (0.0.0.0 é
   rejeitado); Ollama fica em 127.0.0.1. Regra de firewall só no perfil Privado.
8. **Comandos determinísticos primeiro:** intenções simples (hora, volume,
   mídia, memória) por regras; o LLM só para conversação e perguntas abertas.
   O LLM nunca executa comandos de SO; só escolhe entre ferramentas
   registradas com parâmetros validados.
9. **Nenhum download automático na inicialização.** Modelos entram por
   `scripts/prepare-models.ps1` (Fase 4/5), executado manualmente com internet.
10. **Sem bundler no cliente:** apenas `tsc` gera módulos ES nativos. Testes
    de lógica pura com o `node --test` embutido. Nenhuma dependência de runtime.

11. **Palavra de ativação (implementado em 2026-10-11):** openWakeWord 0.6 com
    onnxruntime 1.31 em CPU, verificado neste Windows/Python 3.12 (carga 0,09 s,
    ~1,5 ms por quadro de 80 ms). **Em uso: "hey Luna"** (`hey_luna.onnx`, coleção da
    comunidade Home Assistant, MIT, validado com TTS em inglês e pt-BR), configurado em
    `config/settings.toml` (`[assistant] wake_word`, `[wake] model`) e baixado por
    `scripts/prepare_models.py`. A frase final do produto é **"hey Atem"** (pronúncia
    "ei-tem"). **Não existe modelo pronto para "Atem"**: o modelo customizado é treinado
    com o procedimento oficial do openWakeWord (dados sintéticos por TTS, Colab/GPU,
    grafias fonéticas "hey aytem", "hey eight em"), documentado em
    `docs/palavra-de-ativacao.md`. Quando o `.onnx` for copiado para
    `models/openwakeword/`, basta trocar `wake_word` e `model` na configuração.
    A detecção é real, não simulada: ver `docs/microfone.md` para o teste com WAV.
12. **Transcrição offline (implementado em 2026-10-11):** whisper.cpp (`pywhispercpp` 1.5,
    CPU) com `ggml-small.bin` multilíngue + VAD Silero, **no servidor Ryzen**, no mesmo
    processo que já recebe o áudio e detecta a palavra de ativação; nenhum canal novo entre
    as máquinas. Medido: carga 0,5 s, ~690 MB de RAM no pico, 2,3 s para 4,6 s de áudio em
    6 threads. No Celeron do LG (4 GB) a execução local foi descartada por estimativa
    (8–15x mais lento, RAM concorrendo com o Chromium). Fim da fala por energia (RMS) com
    1,2 s de silêncio; cancelamento por botão/Esc. Detalhes em `docs/transcricao.md`.

## 3. Plano por fases

| Fase | Entrega | Estado |
|---|---|---|
| 1 Inspeção | este documento | concluída |
| 2 Interface mínima | rosto animado (8 estados), relógio, WS com auth, testes | concluída |
| 3 Comunicação local | reconexão com backoff, heartbeat, indicador offline, regra de firewall | reconexão e heartbeat já feitos; falta validar com o LG |
| 4 Pipeline de voz | áudio do navegador → servidor; wake word; whisper.cpp; TTS; reprodução; teste com microfone real | captura (AudioWorklet → WS), wake word (openWakeWord `hey_luna`) e transcrição offline (whisper.cpp small-q8_0 no servidor, `docs/transcricao.md`) feitos e testados com microfone real nesta máquina; **4.1 (latência/precisão) feita**: transcrição 2,1 s → 0,85 s, fim da fala por Silero VAD, pré-buffer; **TTS feito** (Kokoro `pf_dora` no servidor, reprodução no navegador, `docs/voz.md`); teste no LG pendente |
| 5 Conversação | Ollama + modelo medido (RAM/VRAM/latência); memória SQLite apagável | Ollama `qwen2.5:7b` integrado e medido (`docs/ia-local.md`); **voz da Luna feita** (`docs/voz.md`): resposta → síntese local → áudio na cozinha, interrupção por "hey Luna"/Cancelar, falhas amigáveis; **memória local feita** (`docs/memoria.md`): SQLite em `data/memory.sqlite3`, "lembre que…/o que você lembra…/esqueça que…/apague todas as memórias" (com confirmação verbal), só memórias relevantes vão à IA como contexto, painel "Memória" na tela |
| 6 Ferramentas | clima Ouro Fino (Open-Meteo, cache), receitas SQLite, mídia local, Spotify opcional | pendente |
| 7 Operação | serviço no Windows, quiosque Lubuntu no LG, logs rotativos, health, docs | pendente |
| 8 Otimização | medições e correção de gargalos | pendente |

## 4. Riscos conhecidos

- RAM do servidor: 16 GB compartilhados com uso desktop; modelo + whisper + TTS
  devem caber em ~6 GB de RAM e 8 GB de VRAM. Medir antes de escolher.
- LG com HD mecânico e 4 GB: tempo de boot e Chromium em quiosque precisam ser
  medidos; Firefox é a alternativa.
- Microfone integrado do LG não validado; prever microfone USB barato.
- Spotify: conta gratuita não permite controle via API Web Playback; só
  controle de dispositivo existente com conta Premium. Será "indisponível"
  explicitamente quando não suportado.

## 5. IA local (adendo de 2026-10-11)

13. **Integração com o Ollama (implementada, sem Ollama instalado ainda):** cliente HTTP só com a
    biblioteca padrão (`server/services/llm.py`), leitura em fluxo para cancelar e mostrar a resposta
    progressivamente, `[llm]` em `config/settings.toml` (`url`, `model`, tempos, `system_prompt`).
    Só URLs da rede local são aceitas; o Ollama fica em 127.0.0.1 quando roda junto do servidor e,
    em outra máquina, em `OLLAMA_HOST=<ip-da-LAN>` com firewall só no perfil Privado. Sem memória,
    sem TTS. Modelo em uso na GTX 1080: `qwen2.5:7b` (Q4_K_M), medido em 2026-10-11 com Ollama
    0.40.3: 4,75 GB de VRAM (tudo na GPU), resposta curta em 1,2–3,0 s, fluxo completo
    "hey Luna" → resposta em ~3,8 s (`docs/ia-local.md`).

## 6. Fase 4.1 — latência e precisão do reconhecimento (adendo de 2026-10-11)

14. **Transcrição 2,4 s → ~0,85 s sem trocar de hardware nem de família de modelo.** Causa principal:
    o encoder do Whisper custa o mesmo para 2 s ou 30 s de fala (janela fixa de 1500 posições). Medido
    e adotado: `audio_ctx = 768` (sobe sozinho se o áudio precisar; mesmo texto em 18 amostras),
    12 threads em vez de 6 e `small-q8_0` em vez de `small` f16 (mesmo texto, −20 %, metade da RAM).
    Descartado após medir: beam search, flash attention na CPU, contexto menor que 768, silêncio de 1,0 s.
    GPU (CUDA) fica para a Fase 8: exige compilar o `pywhispercpp` com CUDA Toolkit 12 + MSVC.
15. **Fim da fala por voz, não por volume.** O Silero VAD (classe do openWakeWord, `silero_vad.onnx`,
    0,4 ms/quadro) decide se cada quadro tem fala; o limiar de energia anterior tinha um piso de ruído
    que a própria "hey Luna" inflava e cortava frases ou perdia fala baixa. Sem o arquivo, o limiar de
    energia continua como reserva, com piso robusto (percentil 10 de 3 s) e histerese. O estado do VAD
    é zerado na ativação e **não** aquecido com o pré-buffer (isso fazia ruído virar fala).
16. **Pré-buffer de 320 ms** antes da detecção entra na captura: a detecção dispara 80-160 ms após o fim
    da palavra e quem fala emendado perdia a primeira palavra. "Luna" não vaza para o texto.
17. **Cliente:** cancelamento de eco desligado até existir áudio de saída; reamostragem com filtro
    anti-aliasing quando o navegador recusa 16 kHz; microfone e formato em uso registrados no console.
    A transcrição **permanece no Ryzen**: o Celeron só roda o navegador e não é o gargalo.

## 7. Voz da Luna (adendo de 2026-10-11, Fase 5)

18. **Motor: Kokoro-82M via `kokoro-onnx` (CPU), voz `pf_dora`.** Confirmado em 2026-10-11 que as quatro vozes
    oficiais pt-BR do Piper (faber, cadu, edresson, jeff) são masculinas; o Kokoro tem a única voz feminina
    pt-BR de código aberto encontrada (Apache-2.0, 24 kHz). Medido no Ryzen: carga 1,9 s, RTF 0,26-0,30
    (13,3 s de áudio em 3,3 s), primeira frase pronta 0,86 s após a resposta final. O Piper (`piper-tts`,
    GPL-3.0, RTF 0,19) fica implementado como alternativa (`[tts] engine = "piper"`). Arquivos em
    `models/tts/` por `scripts/prepare_models.py kokoro` (325,5 MB + 28,2 MB, tamanhos conferidos antes).
19. **Síntese no servidor, reprodução no quiosque, pelo mesmo WebSocket.** Frase a frase: cada trecho PCM
    é enviado assim que fica pronto (`speak_start` → binários → `speak_end`), o navegador agenda os trechos
    sem lacunas no Web Audio e confirma o fim (`speak_done`). Nenhum arquivo de áudio, nenhuma porta nova,
    nada sai da LAN. O Celeron só decodifica int16 → float e toca.
20. **Nunca duas falas ao mesmo tempo; interrupção pela palavra de ativação.** Uma nova resposta, "hey Luna"
    durante a fala (`barge_in`) ou Cancelar/Esc mandam `speak_stop`. O cancelamento de eco do navegador
    continua desligado (decisão da Fase 4.1); se a voz da Luna gerar falsos positivos no LG, ligar o AEC ou
    desligar `barge_in`. Detalhes, medições e pendências em `docs/voz.md`.
