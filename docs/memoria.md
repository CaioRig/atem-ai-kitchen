# Memória local persistente — Fase 6

## O que foi implementado (2026-10-11)
- **Armazenamento:** `server/storage/memory.py` (`MemoryStore`), SQLite da biblioteca padrão, arquivo
  `data/memory.sqlite3` (fora do git; configurável em `[memory] path`). Nenhum banco na nuvem, nenhuma
  API externa. Nada de áudio: só o texto que a pessoa pediu para guardar. Operações: inserir (com
  deduplicação e correção de contradições), listar, buscar por relevância, atualizar, apagar uma, apagar
  todas. Qualquer falha do SQLite vira `MemoryStorageError`, tratada sem derrubar o servidor.
- **Comandos por voz:** `server/assistant/memory.py` (`MemoryCommands`), por regras determinísticas em
  pt-BR, sem passar pela IA. A `Conversation` consulta esse interpretador antes de enviar qualquer
  transcrição ao Ollama; se for comando de memória, responde na hora (mesmo ciclo visual: "Pensando…" →
  resposta no cartão → fala → "Pronta").
- **Contexto para a IA:** em perguntas comuns, `MemoryCommands.context_for()` anexa à instrução de
  sistema do Ollama **só as memórias relevantes** (sobreposição de palavras; em perguntas sobre comida ou
  receitas, preferências e equipamentos contam como relevantes) mais as instruções de resposta, com um
  aviso explícito: são informações, não ordens, e a IA não deve inventar lembranças que não estejam ali.
- **Interface:** botão **Memória** (tecla **L**) abre um painel sobreposto com a lista (tipo, texto,
  botão **Esquecer** em cada linha) e **Apagar todas** com segundo toque de confirmação. Avisos breves
  ("Memória salva", "Memória apagada", "3 memórias apagadas") aparecem sobre o rosto. Uma lista pedida
  por voz é mostrada por 20 s. O visual existente não mudou.
- **Sem** notificações, tarefas agendadas, integrações externas ou histórico de conversa (fases seguintes).

## Tipos de memória
| tipo | exemplo | como é classificada |
|---|---|---|
| `preference` (preferência) | "não gosto de cebola", "sou vegetariano", "meu filho é alérgico a amendoim" | palavras de gosto, dieta, alergia |
| `kitchen` (cozinha) | "tenho uma air fryer", "não tenho forno", "comprei uma panela de pressão" | equipamentos, utensílios, ingredientes, "tenho um/uma…" |
| `instruction` (instrução) | "responda sempre em frases curtas", "me chame de Caio" | fala sobre como a Luna deve responder (tem prioridade) |
| `note` (lembrete) | "o aniversário da Ana é dia 20" | tudo o mais que a pessoa pedir para guardar |

A classificação é automática pelo conteúdo (`classify()`); o tipo aparece na tela e pode ser conferido
com "o que você lembra sobre minha cozinha / minhas preferências / meus lembretes".

## Frases reconhecidas
Todas podem começar com "Luna," (ou "hey Luna") e com "pode", "por favor", "quero que você".

| intenção | frases |
|---|---|
| guardar | "**lembre que** eu não gosto de cebola", "lembra que…", "lembre-se de que…", "anote que…", "guarde que…", "memorize que…", "salve que…", "não esqueça que…", "me lembra que…" |
| consultar | "**o que você lembra** (sobre minha cozinha / minhas preferências / cebola)?", "o que você sabe sobre mim?", "quais são minhas memórias?", "liste suas memórias", "você lembra o que eu gosto?" |
| esquecer uma | "**esqueça que** eu não gosto de cebola", "esqueça a air fryer", "apague a memória de que…", "remova que…" (apaga a mais parecida; se nada for parecido o bastante, diz que não encontrou) |
| apagar todas | "**apague todas as minhas memórias**", "esqueça tudo", "limpe sua memória" → a Luna pergunta "Tem certeza? Vou apagar todas as N memórias. Diga sim para confirmar ou não para cancelar." |
| confirmar | só uma resposta curta e explícita apaga: "sim", "sim, pode apagar", "pode apagar", "confirmo". "não", "cancela", "deixa" cancelam. Qualquer outra frase (ou 45 s de silêncio, ou **Cancelar**) cancela o pedido e a frase segue o fluxo normal. "sim" fora de uma confirmação pendente é uma frase comum. |

O que **não** é comando vai para a IA como antes: "lembra que horas são?", "me lembre de comprar leite"
(lembretes agendados não existem nesta fase), "como faço arroz?".

Regras de gravação: o texto é guardado como dito (primeira pessoa) e falado de volta na segunda pessoa
("você não gosta de cebola"). Repetir a mesma frase não duplica ("Isso eu já tinha anotado"). A mesma
frase com a negação invertida **corrige** a anterior ("Atualizei. Antes eu tinha que você não gosta de
cebola; agora, você gosta de cebola"). Limites: 300 caracteres por memória, 500 memórias.

## Configuração (`config/settings.toml`, seção `[memory]`)
```toml
[memory]
enabled = true                # false: nenhuma frase é interpretada como memória; tudo vai para a IA
path = "data/memory.sqlite3"  # relativo à raiz do projeto; apagar o arquivo apaga todas as memórias
max_context = 8               # máximo de memórias relevantes por pergunta (as instruções vão sempre)
```
Perfis: a tabela tem a coluna `profile` para separar memórias por pessoa; a arquitetura atual tem um
único assistente por servidor, então há um único perfil (`default`).

## Protocolo (resumo; detalhes em `docs/protocolo.md`)
- `welcome` e `/health` trazem `memory: {available, reason, count}` (`/health` inclui `path`,
  `memory_commands` e `memory_failures`).
- Servidor → cliente `memory {action, item?, previous?, items?, count, topic?, spoken?, message?}` com
  `action` ∈ `saved | updated | exists | deleted | cleared | kept | not_found | confirm_clear | list | error`.
- Cliente → servidor: `memory_list`, `memory_delete {id}`, `memory_clear {confirm: true}` (sem
  `confirm` o servidor recusa com `error confirm_required`).

## Como testar
1. **Sem microfone, com a IA real:** `.\.venv\Scripts\python scripts\check_memory.py` roda o cenário completo
   em um banco **temporário**: guardar, consultar, pergunta à IA com contexto ("me sugere um jantar" deve
   evitar cebola), pergunta sobre algo não guardado (deve dizer que não sabe), reinício, esquecer, apagar
   tudo com confirmação e falha de armazenamento simulada. `--real` só lista o banco configurado.
2. **Fluxo completo:** `.\scripts\run-server.ps1`, abra o quiosque e diga, com pausa após "hey Luna":
   1. "lembre que eu não gosto de cebola" → "Anotado como preferência: você não gosta de cebola." + aviso
      "Memória salva"; o botão passa a "Memória · 1".
   2. "o que você lembra sobre minhas preferências?" → "Sobre as suas preferências, eu lembro: você não gosta de cebola."
   3. Reinicie o servidor (Ctrl+C e `run-server.ps1` de novo) e repita o passo 2: a resposta é a mesma
      (o log de início mostra `memoria: 1 registro(s) em …\data\memory.sqlite3`).
   4. "esqueça que eu não gosto de cebola" → "Esqueci: …" + "Memória apagada". Ou toque em **Memória** →
      **Esquecer** na linha.
   5. "apague todas as minhas memórias" → pergunta de confirmação; diga "sim" → "Pronto, apaguei N memórias."
      Diga "não" (ou outra coisa) e nada é apagado.
   6. Falha de armazenamento: com o servidor parado, troque `path` por um diretório existente (p.ex.
      `path = "data"`) e inicie: o servidor sobe, `welcome.memory.available = false`, e "lembre que…"
      responde "A memória local não está disponível neste servidor."; perguntas comuns seguem normais.
3. **Automáticos:** `.\.venv\Scripts\python -m pytest -q` (`tests/test_memory.py`: loja, frases, comandos,
   confirmação, contexto, reinício, falhas, WebSocket) e `cd client; npm test`.

## Privacidade e limites
- Tudo fica em `data/memory.sqlite3`, neste computador. O texto das memórias **não** vai ao log (só tipo e
  id). Não guarde dados sensíveis desnecessários: a Luna só grava o que for pedido explicitamente.
- A IA recebe no máximo `max_context` memórias relevantes por pergunta, mais as instruções de resposta;
  nunca a base inteira. As memórias chegam marcadas como informação, não como regras, mas um modelo local
  pequeno pode ainda assim ser influenciado por um texto malicioso guardado como memória; por isso só a
  pessoa (por voz ou pela tela) grava algo.
- A busca por relevância é por palavras (radicais), sem embeddings: "o que você lembra sobre carne?" não
  encontra "não como bife". Nesses casos, "o que você lembra?" lista tudo.
- O reconhecimento de voz pode errar a frase; a Luna repete o que entendeu ao confirmar, e o painel
  **Memória** permite corrigir apagando.
