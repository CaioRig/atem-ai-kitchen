# Receitas e ingredientes disponíveis — Fase 7

## O que foi implementado (2026-10-12)
- **Assistente de receitas:** `server/assistant/recipes.py` (`RecipeAssistant`). Não chama nada novo: quando a
  pergunta é sobre receita (sugerir pratos a partir de ingredientes, ensinar o preparo, adaptar porções,
  substituir um ingrediente), a `Conversation` anexa à instrução de sistema do **mesmo Ollama local** uma
  orientação de resposta (formato fácil de acompanhar, porções, substituições com proporção, pergunta curta
  quando falta informação essencial, respeito às preferências guardadas), o **estoque registrado** e, em
  continuações, a **última receita respondida**. Pedidos de receita também usam um limite de tokens maior
  (`[recipes] max_tokens`, padrão 600), porque os 300 das respostas curtas não cabem uma receita completa.
- **Ingredientes disponíveis:** `server/assistant/ingredients.py` (`IngredientCommands`), por regras
  determinísticas em pt-BR, sem IA, **na mesma memória SQLite** (`data/memory.sqlite3`), como memórias do tipo
  `ingredient` com texto `"nome: quantidade"` ("ovos: 3 unidades", "arroz: 1 pacote", "tomate: quantidade não
  informada"). Bancos criados antes desta fase são migrados automaticamente ao abrir (a tabela é recriada com o
  tipo novo, preservando ids e dados). O painel **Memória** mostra o estoque com o rótulo "Ingrediente" e o botão
  **Esquecer** remove um item; "apague todas as memórias" (com confirmação) também apaga o estoque.
- **Continuação sem histórico:** cada pergunta continua independente, mas a última resposta sobre receita fica
  guardada **só em memória do processo**, por `followup_ttl_s` (padrão 30 min). "Adapte essa receita para quatro
  pessoas" e "como posso substituir o leite nesta receita?" recebem essa resposta como contexto; uma pergunta
  nova ("como faço arroz soltinho?") não.
- **Voz:** o texto completo vai para o cartão "Resposta" (que agora preserva quebras de linha e rola quando não
  cabe; o visual não mudou). Se a resposta é uma receita estruturada maior que `spoken_max_chars` (padrão 420),
  a Luna **fala só o parágrafo de abertura** (prato, porções, tempo, ingredientes principais) seguido de "Os
  ingredientes e o passo a passo estão na tela." Respostas curtas ou sem estrutura são faladas por inteiro, como
  antes. `spoken_max_chars = 0` desliga o resumo.
- **Segurança alimentar:** orientação permanente em pedidos de receita (cozimento seguro de carnes, frango,
  ovos, peixes; nunca usar ingrediente com sinais de deterioração) e orientação completa quando a pergunta fala de
  validade, conservação, cheiro, mofo, sobras etc. (não inventar prazos exatos; na dúvida, verificar/descartar).
- **Offline:** nenhuma API externa, nenhum banco de receitas; só o Ollama local. A Luna não afirma que algo está
  atualizado. Sem clima, Spotify ou outras integrações (fases seguintes).

## Frases reconhecidas (ingredientes, sem IA)
Todas podem começar com "Luna," e com "pode", "por favor". Quantidades em palavras ou números ("três", "3",
"meia dúzia", "vinte e cinco", "um quilo e meio", "500 g", "1,5 litro"); unidades comuns (pacote, lata, caixa,
litro, ml, quilo, grama, dúzia, xícara, colher de sopa/chá, dente, maço, pote, bandeja, saco, fatia…).

| intenção | frases | efeito |
|---|---|---|
| registrar (estado atual) | "**tenho** três ovos e um pacote de arroz", "tenho em casa macarrão, molho de tomate e queijo", "na geladeira tem um pote de iogurte", "lembre que tenho duas colheres de sopa de manteiga", "registre os ingredientes: farinha, açúcar e fermento", "sobrou meio pacote de arroz" | define a quantidade (substitui a anterior); sem quantidade → "sem quantidade informada" |
| acrescentar | "**comprei** dois litros de leite", "ganhei uma dúzia de ovos", "adicione duas latas de milho na despensa" | soma ao que havia; se o anterior era desconhecido ou a unidade é outra, fica "sem quantidade" e a Luna avisa |
| consultar | "**quais ingredientes tenho** (disponíveis)?", "o que tenho na geladeira/despensa/em casa?", "mostra minha lista de ingredientes", "quantos ovos tenho?", "tenho ovos?", "tem leite na geladeira?" | fala a lista (quantidades conhecidas primeiro, depois "sem quantidade") e abre o painel por 20 s |
| descontar | "**usei** dois ovos", "gastei 200 gramas de carne moída", "comi duas bananas", "usei metade do arroz", "usei o resto do leite" | subtrai; zera → tira da lista; "usei ovos" sem número → fica "sem quantidade" ("não sei quanto sobrou") |
| remover | "**acabou** o arroz", "os ovos acabaram", "não tenho mais leite", "tire o queijo da lista", "apague todos os ingredientes" | remove da lista |

Regras: a Luna **não inventa quantidades**. Se não souber, diz. Um ingrediente apenas **citado** em uma
pergunta ("o que faço com ovos e arroz?") nunca entra no estoque; para a IA, o estoque vai marcado como
"informado pela pessoa" e os ingredientes citados como "o que ela quer usar agora". Frases como "tenho uma air
fryer", "tenho fome", "tenho que comprar ovos", "tenho um filho alérgico…" não são estoque (equipamentos,
pessoas, sentimentos e saúde são excluídos; "lembre que tenho uma air fryer" continua memória de cozinha).
Sem quantidade nem contexto explícito ("tenho X"), só nomes de alimentos comuns são aceitos; "tenho 3 bombons"
precisa de contexto ("tenho em casa 3 bombons", "adicione 3 bombons na despensa"). Duas intenções na mesma fala
("usei dois ovos. Como faço omelete?") vão para a IA sem atualizar o estoque.

"O que posso fazer com o que tenho?" com o estoque vazio é respondido sem a IA ("Ainda não tenho nenhum
ingrediente registrado…"); com estoque, a IA responde usando só o que está registrado.

## Exemplos (IA local)
- "Luna, o que posso fazer com ovos, arroz e tomate?" → até três ideias curtas e a pergunta de qual detalhar.
- "Me ensine a fazer panqueca para duas pessoas." → abertura falada + "Ingredientes:" e "Preparo:" numerado na tela.
- "Adapte essa receita para quatro pessoas." → quantidades dobradas sobre a receita anterior.
- "Como posso substituir o leite nesta receita?" → substituição com proporção e efeito na textura.
- "me sugere um jantar rápido com o que tenho?" → usa o estoque e respeita "não gosto de cebola" guardado.

## Configuração (`config/settings.toml`, seção `[recipes]`)
```toml
[recipes]
enabled = true          # false: nada desta fase (nem estoque, nem orientação, nem resumo falado)
max_tokens = 600        # limite da resposta em pedidos de receita (o padrão [llm] max_tokens continua nas demais)
spoken_max_chars = 420  # receitas maiores que isso são faladas em resumo; 0 = falar tudo
followup_ttl_s = 1800   # por quanto tempo "essa receita" se refere à última resposta
```
O estoque depende de `[memory] enabled = true` (mesmo banco); com a memória desativada, as frases de estoque vão
para a IA como qualquer outra.

## Protocolo (resumo; detalhes em `docs/protocolo.md`)
- Evento `memory` com `action` ∈ `stock_saved | stock_updated | stock_removed` (`items` alterados, `count` total)
  e `list` com `topic: "ingredientes disponíveis"` e `spoken: true` nas consultas por voz. `item.kind` pode ser
  `ingredient`. `memory_list`/`memory_delete` da tela funcionam para o estoque sem mudança.
- `/health` traz `recipes: {enabled, ingredients, recipe_requests, followups, spoken_summaries, recent_recipe,
  stock_commands, stock_failures}`. O `welcome` não mudou.

## Como testar
1. **Sem microfone, com a IA real:** `.\.venv\Scripts\python scripts\check_recipes.py` roda o cenário completo
   em um banco **temporário**: estoque vazio, registrar/consultar/descontar, sugestão a partir de ingredientes
   informados, panqueca para duas pessoas (mostra o que seria falado), adaptação para quatro, substituição do
   leite, jantar com o estoque e a preferência guardada, segurança alimentar. `--no-llm` só mostra os comandos e
   o contexto montado.
2. **Fluxo completo:** `.\scripts\run-server.ps1`, abra o quiosque e diga, com pausa após "hey Luna":
   1. "tenho três ovos e um pacote de arroz" → "Anotei: 3 ovos e 1 pacote de arroz." + aviso "Ingredientes
      registrados"; o botão passa a "Memória · 2".
   2. "quais ingredientes tenho disponíveis?" → "Você tem: 3 ovos e 1 pacote de arroz." e o painel abre.
   3. "usei dois ovos. Atualize o que ainda tenho." → "Sobrou 1 ovo."
   4. "me ensine a fazer panqueca para duas pessoas" → receita na tela; a voz fala só a abertura.
   5. "adapte essa receita para quatro pessoas" → quantidades ajustadas sobre a mesma receita.
   6. "lembre que eu não gosto de cebola" e depois "me sugere um jantar com o que tenho" → sem cebola, só com o estoque.
3. **Automáticos:** `.\.venv\Scripts\python -m pytest -q` (`tests/test_recipes.py`: migração do banco, frases,
   comandos, quantidades desconhecidas, detectores, resumo falado, contexto, continuação, conversa, WebSocket,
   configuração) e `cd client; npm test`.

## Limitações
- Reconhecimento por regras: nomes de ingredientes fora do léxico interno precisam de quantidade ou de contexto
  explícito; o reconhecimento de voz pode errar números ("dois" vs "dez"), e a Luna sempre repete o que anotou.
- A chave de um ingrediente é o nome no singular, sem acentos ("ovos" = "ovo"); "tomate" e "tomate cereja" são
  itens diferentes. Não há conversão entre unidades (200 g de um pacote → "sem quantidade", com aviso).
- A "última receita" vive só no processo do servidor (reiniciar apaga) e vale por `followup_ttl_s`.
- A qualidade das receitas é a do modelo local (qwen2.5:7b): ele às vezes usa marcadores "-" apesar da instrução
  (a voz os ignora), pode citar um item do estoque ou da memória sem necessidade (p.ex. lembrar da air fryer em
  uma panqueca) e pode dar a receita completa onde bastariam sugestões. Nada é buscado fora da máquina.
