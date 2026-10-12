# IA local (Ollama) — Fase 4

## O que foi implementado (2026-10-11)
- **Servidor:** `server/services/llm.py` (cliente mínimo da API `/api/chat` do Ollama, só biblioteca
  padrão, leitura em fluxo) e `server/assistant/conversation.py` (ciclo pergunta → resposta, estados,
  cancelamento, nova tentativa). O `VoicePipeline` entrega a transcrição à `Conversation` uma única
  vez por fala (o número da transcrição identifica a pergunta); transcrições vazias nunca são enviadas.
- **Estados:** "Pensando…" (`thinking`) ao enviar → "Respondendo…" (`replying`) quando o primeiro trecho
  chega → "Pronta" (`idle`, some após `done_hold_s`). Falha: `idle` com "Não consegui responder" e a
  mensagem amigável no cartão "Resposta", rosto vermelho "Erro na resposta" e botão **Tentar novamente**
  (tecla **R**). **Cancelar**/Esc também interrompe a resposta (o Ollama para de gerar).
- **Interface:** a resposta aparece no cartão "Resposta" existente, em cinza itálico enquanto chega e
  normal quando completa. Em espera, "· IA indisponível" é acrescentado ao texto se a IA não respondeu.
  Nenhuma mudança no rosto pixel art.
- **Instrução de sistema:** `LUNA_SYSTEM_PROMPT` em `server/services/llm.py` (assistente de cozinha,
  acolhedora e objetiva, pt-BR, respostas curtas, sem Markdown). Pode ser substituída em
  `[llm] system_prompt`.
- **Sem memória, sem voz, sem clima, sem integrações externas** (fases seguintes). Cada pergunta é
  independente.

## Configuração (`config/settings.toml`, seção `[llm]`)
```toml
[llm]
enabled = true
url = "http://127.0.0.1:11434"   # Ollama na mesma máquina do servidor (padrão)
model = "qwen2.5:7b"             # precisa estar instalado: ollama pull qwen2.5:7b
connect_timeout_s = 5.0
timeout_s = 60.0
keep_alive = "10m"
temperature = 0.7
max_tokens = 300
system_prompt = ""               # vazio = instrução padrão da Luna
```
- `url`: só endereços da **rede local** são aceitos (loopback, 10.x, 172.16–31.x, 192.168.x ou nome de
  máquina sem domínio público). Qualquer outro é recusado na inicialização com `llm.url invalida`.
- `model`: nome exato como aparece em `ollama list`. Se não existir, a interface mostra
  "O modelo '…' não está instalado no Ollama. No servidor, execute: ollama pull …".
- `enabled = false`: desliga a IA; a transcrição continua como na fase anterior ("Transcrição concluída").
- Nenhuma chave ou credencial: o Ollama não usa autenticação e por isso **não pode ficar exposto**.

## Instalar o Ollama no servidor Ryzen (Windows 10)
1. Baixe o instalador em https://ollama.com/download (OllamaSetup.exe) e instale. Ele sobe como
   aplicativo na bandeja e escuta **apenas em 127.0.0.1:11434** por padrão.
2. Baixe o modelo (uma vez, com internet):
   ```powershell
   ollama pull qwen2.5:7b
   ollama list
   ```
   A GTX 1080 (8 GiB, Pascal) é suportada; o Ollama escolhe o runner CUDA 12 sozinho (`docs/plano.md`).
   Modelos de 7–8 B em Q4 (~4,5–5 GB) cabem na VRAM; 9 B (`gemma2:9b`, 5,4 GB) também, com menos folga.
   **Meça** com `scripts/check_llm.py` antes de fixar: RAM/VRAM e latência ainda não foram medidas
   neste projeto porque o Ollama não estava instalado quando esta fase foi escrita.
3. Verifique pelo projeto:
   ```powershell
   .\.venv\Scripts\python scripts\check_llm.py                             # servidor e modelo prontos?
   .\.venv\Scripts\python scripts\check_llm.py "Como faço arroz soltinho?" # pergunta de teste, com tempos
   ```

## Ollama em outra máquina da rede local
O servidor FastAPI e o Ollama normalmente rodam juntos no Ryzen. Se o Ollama ficar em **outro**
computador da LAN:
1. Nessa máquina, faça o Ollama escutar na interface da LAN (e **nunca** em uma interface pública):
   `OLLAMA_HOST=192.168.1.X:11434` como variável de ambiente do usuário (Windows: Configurações →
   Variáveis de ambiente; reinicie o Ollama). `0.0.0.0` também funciona, mas então a regra de firewall
   abaixo é obrigatória.
2. Libere a porta **só no perfil Privado** e, se possível, só para o IP do servidor:
   ```powershell
   New-NetFirewallRule -DisplayName "Ollama LAN" -Direction Inbound -Protocol TCP -LocalPort 11434 -Profile Private -RemoteAddress 192.168.1.6 -Action Allow
   ```
3. No servidor, `url = "http://192.168.1.X:11434"`. Não há TLS nem senha nesse tráfego: ele deve ficar
   restrito à rede doméstica. Não faça encaminhamento de porta no roteador.

## Fluxo e mensagens (resumo; detalhes em `docs/protocolo.md`)
"hey Luna" → "Ouvindo · pode falar" → "Transcrevendo…" → `transcript` → **"Pensando…"** →
**"Respondendo…"** (`reply` parcial, `final: false`) → `reply` final → **"Pronta"** → "Aguardando ativação".
Falha: `reply_error {code, message, retry: true}` → "Não consegui responder" → botão **Tentar novamente**
envia `retry` e a última pergunta é reenviada (uma vez por falha).

Códigos de erro: `unreachable` (Ollama desligado/host inacessível), `timeout`, `model_missing`,
`server` (erro do Ollama, p.ex. memória), `empty` (resposta vazia), `bad_response`, `internal`.

## Como testar
1. **Sem microfone:** `scripts/check_llm.py` (acima). Para simular falhas: pare o Ollama (ícone da
   bandeja → Quit) e repita; troque `model` por um nome inexistente e repita.
2. **Fluxo completo:** `.\scripts\run-server.ps1`, abra `https://<ip>:8800/#token=…`, diga **"hey Luna"**,
   faça uma pausa curta e pergunte, p.ex. "como faço arroz soltinho?". Esperado: texto em "Você disse",
   "Pensando…", "Respondendo…" com a resposta crescendo no cartão "Resposta", "Pronta", e de volta à
   espera. O log registra `resposta em X s: N caracteres` (não o texto).
3. **Falha de conexão:** com o Ollama parado, repita o passo 2. Esperado: "Não consegui responder",
   rosto vermelho "Erro na resposta", cartão "Resposta" com "Não consegui falar com a IA em
   127.0.0.1:11434. O Ollama está ligado?" e o botão **Tentar novamente**. Ligue o Ollama e clique
   (ou tecla R): a mesma pergunta é reenviada sem repetir a fala. A interface nunca trava: o áudio e
   o WebSocket continuam no loop principal; a chamada HTTP roda em uma thread.
4. **Transcrição vazia:** fale "hey Luna" e fique em silêncio, ou faça só ruído. Esperado: "Nada
   reconhecido" (ou volta direta à espera) e **nenhuma** chamada ao Ollama (`/health` → `replies`
   não muda).
5. **Automáticos:** `.\.venv\Scripts\python -m pytest -q` (inclui `tests/test_llm.py`, com um servidor
   Ollama **falso** em HTTP local, e `tests/test_conversation.py`, com o pipeline de voz) e
   `cd client; npm test`.
6. `GET /health` mostra `llm.available/reason/model`, `replies`, `reply_failures` e `last_reply_s`.

## Limitações conhecidas
- Medido em 2026-10-11 com Ollama 0.40.3 e `qwen2.5:7b` (Q4_K_M, 7,6 B) nesta máquina: modelo
  inteiro na VRAM (4,75 GB de 8 GB, `runner llamacpp`, contexto 4096); com o modelo carregado,
  primeiro trecho em 0,1–0,2 s e resposta curta completa em 1,2–3,0 s. Fluxo completo com os áudios
  de teste: "hey Luna" → transcrição (2,4 s) → "Pensando…" → "Respondendo…" (0,2 s depois) →
  "Pronta" em 3,8 s no total. O tempo de carga a frio (primeira pergunta após `keep_alive`) não foi
  medido.
- Sem histórico de conversa: "e com feijão?" não sabe do que se falou antes. A **memória persistente**
  (preferências, cozinha, instruções, lembretes pedidos explicitamente) existe desde a Fase 6
  (`docs/memoria.md`) e entra como contexto na instrução de sistema; o histórico da conversa em si, não.
- Uma pergunta por vez; uma fala que chegue enquanto a IA responde é ignorada (o microfone não
  reativa durante "Pensando…"/"Respondendo…").
- `timeout_s` cobre a resposta inteira; na primeira pergunta após o modelo descarregar (`keep_alive`),
  a carga na GPU entra nesse tempo.
