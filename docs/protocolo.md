# Protocolo cliente ↔ servidor (WebSocket, JSON)

Endpoint: `ws://<host>:<porta>/ws`. Mensagens de texto são JSON com campo
`type`. Mensagens **binárias** são áudio do microfone: PCM int16 little-endian,
mono, 16 kHz, em quadros de qualquer tamanho (o cliente envia 1280 amostras =
80 ms = 2560 bytes). O servidor acumula e processa em quadros de 80 ms.

## Handshake

1. O cliente conecta e **deve** enviar `hello` em até 5 s:
   `{"type":"hello","token":"<token>","client":"ui","version":1}`
2. O servidor responde `welcome` ou fecha com código **4401** (token inválido)
   ou **4400** (primeira mensagem não é `hello` ou demorou demais).
   `{"type":"welcome","server":"jarvis-kitchen","version":"0.3.0","state":"idle","detail":"","ts":...,"wake_word":"hey Luna","debug":false,"wake":{"available":true,"reason":"","phrase":"hey Luna","model":"hey_luna"},"transcription":{"available":true,"reason":"","model":"ggml-small","language":"pt"},"llm":{"available":true,"reason":"","model":"qwen2.5:7b"}}`

   `wake.available = false` significa que o servidor recebe áudio mas não detecta
   a palavra (biblioteca ou modelo ausente; `reason` explica). `transcription.available = false`
   significa que a fala é capturada mas não transcrita (`pywhispercpp` ou modelo ggml ausente).
   `llm.available = false` significa que a IA local não respondeu na última verificação ou está
   desativada (`reason` explica); a transcrição continua funcionando (`docs/ia-local.md`).
   `memory: {available, reason, count}` descreve a memória persistente (`docs/memoria.md`);
   `available = false` significa que o arquivo SQLite não abriu ou `[memory] enabled = false`.
   `weather: {enabled, available, location, summary, detail, updated_at, stale, status, message, source, attribution, …}`
   descreve a previsão do tempo a partir do cache local (`docs/clima.md`); o `welcome` nunca consulta a fonte.

## Cliente → servidor

| type | campos | descrição |
|---|---|---|
| `ping` | `t` (ms, opcional) | heartbeat; servidor responde `pong` com o mesmo `t` |
| `set_state` | `state`, `detail?` | **somente** se `debug_allow_state_override = true` na config; simula estados para testar a interface |
| `cancel` | — | interrompe a captura, a transcrição, a resposta da IA ou a **fala** em andamento e volta a `idle` (detalhe "Cancelado"); sempre permitido, ignorado se não há interação |
| `retry` | — | reenvia à IA a última transcrição cuja resposta falhou (uma vez por falha); ignorado se não há o que repetir ou se há interação em andamento |
| `speak_done` | `id`, `played` (bool), `error?` | o cliente terminou de reproduzir a fala `id` (`played: true`) ou não conseguiu (`played: false` + motivo); ignorado se `id` não é a fala atual |
| `replay` | — | fala de novo a última resposta, sem consultar a IA (botão "Tentar novamente" após uma falha da voz); só em `idle` |
| `say` | `text` | **somente** com `debug_allow_state_override = true`: mostra `text` no cartão "Resposta" e o fala (teste da voz sem microfone) |
| `memory_list` | — | pede a lista de memórias guardadas; resposta `memory` com `action: "list"` só para quem pediu (`error unavailable` se não há memória) |
| `memory_delete` | `id` | apaga a memória `id` (botão "Esquecer"); todos recebem `memory` com `action: "deleted"` (ou `not_found` só para quem pediu) |
| `memory_clear` | `confirm` (bool) | apaga **todas** as memórias; só com `confirm: true` (senão `error confirm_required`); todos recebem `memory` com `action: "cleared"` |
| (binário) | PCM int16 LE mono 16 kHz | áudio do microfone; só o primeiro cliente que enviar é aceito (os demais recebem `error busy`) |

## Servidor → cliente

| type | campos | descrição |
|---|---|---|
| `welcome` | ver acima | confirma autenticação e envia estado atual; inclui `tts: {available, reason, engine, voice, sample_rate}` |
| `state` | `state`, `detail`, `ts` | mudança de estado do assistente (broadcast) |
| `pong` | `t` | resposta ao ping |
| `transcript` | `text`, `final` | texto reconhecido pelo whisper.cpp local (sempre `final: true` nesta fase); a interface mostra em "Você disse" |
| `reply` | `text`, `final` | resposta da IA local; `final: false` enquanto o texto ainda está sendo gerado (texto parcial acumulado), `final: true` com o texto completo |
| `reply_error` | `code`, `message`, `retry` | a IA não respondeu: `message` é amigável (pt-BR) e vai para o cartão "Resposta"; `retry: true` habilita "Tentar novamente". Códigos: `unreachable`, `timeout`, `model_missing`, `server`, `empty`, `bad_response`, `internal` |
| `speak_start` | `id`, `sample_rate`, `channels` (1), `format` (`pcm_s16le`) | começa a fala `id`; em seguida chegam **quadros binários** PCM int16 LE mono em `sample_rate` (24 000 Hz no Kokoro, 22 050 no Piper), um por frase, conforme são sintetizados |
| (binário) | PCM int16 LE mono | trecho de áudio da fala em andamento; o cliente o toca em sequência, sem lacunas, e o descarta. Sem `speak_start` prévio na conexão, é ignorado |
| `speak_end` | `id`, `samples` | último trecho da fala `id` já foi enviado; o cliente responde `speak_done` quando o último trecho terminar de tocar |
| `speak_stop` | `id` | parar a reprodução imediatamente (nova interação, "hey Luna" durante a fala, Cancelar ou falha no meio) |
| `speak_error` | `code`, `message`, `retry` | a voz falhou: `message` amigável para a linha de estado (o texto da resposta continua no cartão); `retry: true` habilita "Tentar novamente" (`replay`). Códigos: `engine`, `empty`, `playback` (o próprio quiosque não conseguiu tocar), `internal` |
| `memory` | `action`, `count`, `item?`, `previous?`, `items?`, `topic?`, `spoken?`, `message?` | memória persistente (`docs/memoria.md`): `saved`, `updated` (com `previous`), `exists`, `deleted`, `cleared`, `kept`, `not_found`, `confirm_clear` (a Luna pediu confirmação por voz), `list` (`items`; `spoken: true` quando pedida por voz; `topic` quando filtrada) e `error` (`message`). Ingredientes disponíveis (`docs/receitas.md`): `stock_saved`, `stock_updated`, `stock_removed` (`items` alterados; `item.kind = "ingredient"`) e `list` com `topic: "ingredientes disponíveis"`. `count` é o total após a ação. Broadcast, exceto `list`/`not_found` pedidos pela tela |
| `weather` | `available`, `location`, `summary`, `detail`, `updated_at`, `observed_at`, `period_start`, `period_end`, `stale`, `status`, `message`, `source`, `attribution` | previsão do tempo (`docs/clima.md`) após cada pergunta sobre o tempo: `summary`/`detail` vão para o cartão "Clima"; `updated_at` é a hora (epoch) da consulta à fonte e `observed_at` a hora local a que as condições se referem; `stale: true` quando os dados passaram da validade longa; `status` ∈ `online` (fonte consultada agora), `cache` (dentro da validade), `offline` (fonte falhou, usando o cache; `message` explica), `none` (nada disponível), `cached_only` (pergunta sobre o próprio cache). Broadcast |
| `error` | `code`, `message` | erro não fatal (`bad_message`, `forbidden`, `busy`, `bad_audio`, `unavailable`, `confirm_required`) |

## Estados do assistente (servidor)

`booting`, `idle`, `wake`, `listening`, `transcribing`, `thinking`, `replying`,
`synthesizing`, `speaking`, `media`, `offline`, `error`.

Mapeamento para o rosto (cliente):

| servidor | rosto |
|---|---|
| booting | inicializando |
| idle | em espera |
| wake, listening | escutando |
| transcribing, thinking, synthesizing | processando (em `synthesizing`, indicador "Preparando") |
| replying | processando (indicador "Respondendo") |
| speaking | falando (a boca acompanha o nível do áudio reproduzido) |
| media | reproduzindo mídia |
| offline, ou sem conexão com o servidor há mais de 1,5 s | offline |
| error | erro |

## Ciclo de ativação por voz (servidor)

1. `idle`: cada quadro de 80 ms passa pelo openWakeWord; score ≥ `wake.threshold`
   → `wake` (detalhe "Palavra detectada"), com `cooldown_s` entre ativações.
2. `wake` → `listening` após 0,4 s; o áudio é acumulado em memória.
3. `listening` termina por silêncio (`listen_silence_s` após fala), ausência de
   fala (4 s: volta a `idle` sem transcrever) ou duração máxima (`listen_max_s`).
4. `transcribing` (detalhe "Transcrevendo…"): o whisper.cpp roda em uma thread
   separada. Ao terminar, o servidor envia `transcript`. Sem IA configurada, volta a `idle`
   com o detalhe "Transcrição concluída" (ou "Nada reconhecido" / "Falha na transcrição"),
   que some após `transcription.done_hold_s`. Sem transcritor carregado, volta a `idle` direto.
5. Com IA configurada (`[llm]`), a transcrição não vazia vai **uma única vez** ao Ollama:
   `thinking` (detalhe "Pensando…") → `replying` ("Respondendo…") a partir do primeiro trecho,
   com mensagens `reply` parciais → `reply` final. **Sem voz**: `idle` com o detalhe "Pronta" (some após
   `done_hold_s`). Em falha: `reply_error` e `idle` com "Não consegui responder"; o cliente pode
   enviar `retry`. "Nada reconhecido" nunca chega à IA.
   **Comandos de memória** ("lembre que…", "o que você lembra…", "esqueça que…", "apague todas as
   memórias" e o "sim"/"não" da confirmação) e **de ingredientes** ("tenho três ovos", "usei dois ovos",
   "quais ingredientes tenho?") são resolvidos no servidor sem a IA: `thinking` → `reply`
   final + `memory` → fala (se houver voz) → `idle` "Pronta". As demais perguntas recebem as memórias
   relevantes como contexto na instrução de sistema (`docs/memoria.md`); perguntas sobre receitas recebem
   também a orientação de receitas, o estoque e a última receita (`docs/receitas.md`), e receitas longas são
   faladas em resumo (o texto completo vai no `reply`).
   **Perguntas sobre o tempo** ("como está o tempo?", "vai chover hoje?", "como estará o tempo amanhã?", "previsão para os
   próximos três dias", "você tem uma previsão salva?", "quando o tempo foi atualizado?") também não vão à IA: `thinking` →
   consulta à Open-Meteo em uma thread (só se o cache venceu) → `reply` final + `weather` → fala curta → `idle` "Pronta". Sem
   internet, a resposta sai do cache com a idade dos dados (`docs/clima.md`); nunca gera `reply_error`.
6. **Com voz** (`[tts]`), após o `reply` final: `synthesizing` ("Preparando a voz…") → `speak_start` +
   primeiro trecho binário → `speaking` ("Falando") → demais trechos → `speak_end` → o cliente toca tudo
   e responde `speak_done` → `idle` com "Pronta". Sem `speak_done` em (duração do áudio + 3 s), volta a
   `idle` do mesmo jeito. Falha da voz: `speak_error` (e `speak_stop`, se já havia áudio) → `idle` com
   "Não consegui falar"; o texto fica na tela e `replay` fala de novo.
7. `cancel` em `wake`, `listening`, `transcribing`, `thinking`, `replying`, `synthesizing` ou `speaking`
   descarta o áudio, aborta o whisper.cpp, a geração do Ollama ou a síntese (com `speak_stop`) e volta a
   `idle` com o detalhe "Cancelado".
8. **Interrupção pela voz** (`tts.barge_in = true`): em `speaking` o detector continua ouvindo; "hey Luna"
   envia `speak_stop` e segue para `wake` → `listening` normalmente. Nunca há duas falas ao mesmo tempo:
   uma nova resposta sempre interrompe a anterior.

O tempo é medido em áudio recebido (quadros), não em relógio. Nenhum áudio é
gravado em disco nem sai da rede local: o microfone sobe e a voz da Luna desce pelo mesmo
WebSocket, só em memória.

## Heartbeat e reconexão (cliente)

- `ping` a cada 10 s; sem `pong` em 5 s o cliente fecha e reconecta.
- Reconexão com backoff exponencial 1 s → 30 s com jitter de até 30 %.
- Código 4401 (token recusado) mantém tentativas apenas no intervalo máximo
  e exibe instrução para informar o token.
