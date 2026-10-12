# Previsão do tempo com cache local e funcionamento offline — Fase 8

## O que foi implementado (2026-10-12)
- **Fonte:** [Open-Meteo](https://open-meteo.com) (`server/services/weather.py`, `OpenMeteoClient`), gratuita para uso
  não comercial, sem chave nem cadastro (os termos citam explicitamente automação residencial pessoal). Dados sob
  CC BY 4.0, por isso a Luna informa a atribuição no protocolo (`attribution`). Limites do plano gratuito em 2026-10:
  600 chamadas/min, 5 000/h, 10 000/dia; a Luna faz **uma** chamada só quando alguém pergunta e o cache venceu.
  Cliente HTTP só com a biblioteca padrão (como o do Ollama), com timeouts de conexão e de resposta.
- **Local:** Ouro Fino, MG, por coordenadas da geocodificação da própria Open-Meteo (município, 32 094 habitantes,
  924 m): latitude **-22.28306**, longitude **-46.36889**. Fuso **America/Sao_Paulo**: a fonte já devolve os horários
  nesse fuso; a hora da consulta é convertida com o banco de fusos do sistema ou, se ele faltar (Windows sem o pacote
  `tzdata`), com o deslocamento informado pela fonte (-03:00; o Brasil não tem horário de verão).
- **Dados:** condições atuais (temperatura, sensação, umidade, chuva na última hora, vento, código do tempo, dia/noite),
  previsão por hora (probabilidade e volume de chuva, temperatura, código) e previsão diária para `forecast_days` dias
  (código mais severo do dia, mínima/máxima, chuva acumulada, probabilidade máxima, vento máximo, nascer/pôr do sol),
  em °C, mm e km/h. Códigos WMO interpretados em pt-BR (sol, poucas nuvens, nublado, nevoeiro, garoa, chuva fraca a
  forte, pancadas, neve, tempestade com e sem granizo); código desconhecido é dito como desconhecido, nunca adivinhado.
- **Cache persistente:** `server/storage/weather.py` (`WeatherCache`), tabela `weather_cache` **no mesmo SQLite da
  memória** (`data/memory.sqlite3`, configurável em `[weather] cache_path`; com `[memory] enabled = false`, um arquivo
  próprio `data/weather.sqlite3`). Uma linha por local: local, hora da consulta à fonte (`fetched_at`), período coberto
  (`period_start`..`period_end`), dados recebidos (JSON) e resultado da última tentativa (`last_status` ok/failed,
  `last_error`, `last_attempt_at`). Uma falha ou resposta vazia **nunca** substitui dados válidos: só anota a tentativa.
- **Comandos por voz:** `server/assistant/weather.py` (`WeatherAssistant`), por regras determinísticas em pt-BR, sem IA.
  A `Conversation` consulta esse interpretador depois dos comandos de memória e ingredientes e antes de enviar qualquer
  coisa ao Ollama. A consulta à fonte roda em uma thread: a interface, o áudio e o WebSocket não travam.
- **Interface:** o cartão **Clima · Ouro Fino, MG** (já existia, vazio) passa a mostrar o resumo (temperatura, condição,
  dia a que a previsão se refere, mínima/máxima, chance de chuva, vento), a linha "Atualizado às HH:MM" (hora da consulta à fonte, não a hora da previsão),
  em amarelo com "Dados antigos" quando o cache passou de `stale_after_s`, e "sem internet, usando dados salvos" quando a
  última resposta veio do cache por falha. O `welcome` traz o estado do cache ao conectar; o localStorage guarda uma cópia
  só para o quiosque mostrar algo enquanto o servidor não responde.
- **Sem** atualização em segundo plano, agendamentos, outras cidades ou dependências novas (fases seguintes).

## Frases reconhecidas
Todas podem começar com "Luna," / "hey Luna" e com "pode", "por favor".

| intenção | frases | resposta |
|---|---|---|
| condições atuais | "**como está o tempo** (em Ouro Fino)?", "que tempo faz?", "está frio lá fora?", "qual a temperatura agora?", "quantos graus faz hoje?" | "Agora em Ouro Fino, MG, segunda-feira, 12 de outubro de 2026, às 14:30: 23 graus, parcialmente nublado, vento de 12 km/h, umidade de 61% e sem chuva no momento. Para hoje, segunda-feira, 12 de outubro de 2026: … mínima de 15 e máxima de 27 graus, chance de chuva de 70% …" (a data e a hora são as da leitura informada pela fonte) |
| chuva | "**vai chover hoje?**", "vai chover?", "tá chovendo?", "vai chover amanhã / no fim de semana?", "preciso de guarda-chuva?", "tem chance de chuva?" | "Hoje, segunda-feira, 12 de outubro de 2026, é bem provável que chova em Ouro Fino, MG: a chance chega a 70% (70% à tarde; 40% à noite), com cerca de 5 mm previstos. Vale deixar um guarda-chuva por perto. Previsão é estimativa, não certeza." Só as horas que ainda faltam do dia contam; sem probabilidade na fonte, a Luna diz isso em vez de inventar |
| previsão de um dia | "**como estará o tempo amanhã?**", "previsão para hoje / depois de amanhã / na quinta / no sábado", "vai fazer sol amanhã?" | "Amanhã, terça-feira, 13 de outubro de 2026: chuva fraca, mínima de 14 e máxima de 24 graus, chance de chuva de 60% (cerca de 4 mm) e vento de até 18 km/h." |
| vários dias | "**qual é a previsão para os próximos três dias?**", "previsão da semana", "previsão para o fim de semana" | uma linha por dia na tela, cada uma com o nome do dia e a data completa ("Hoje, segunda-feira, 12 de outubro de 2026: …", "Amanhã, terça-feira, 13 de outubro de 2026: …", "Depois de amanhã, quarta-feira, 14 de outubro de 2026: …"); a voz fala até três dias e manda ver o resto na tela. Dias além do que a fonte deu: "Não tenho dados para …: a previsão salva vai até quarta-feira (14/10)." |
| cache | "**Luna, você tem uma previsão salva?**", "tem previsão guardada?" | "Sim, tenho uma previsão salva de Ouro Fino, MG, atualizada hoje às 14:32 (há 1 hora), cobrindo de hoje até depois de amanhã (quarta-feira)." / "Não tenho nenhuma previsão salva…" — **nunca** consulta a fonte |
| última atualização | "**quando o tempo foi atualizado pela última vez?**", "quando você atualizou a previsão?", "de quando é essa previsão?" | "A última atualização do tempo de Ouro Fino, MG foi hoje às 14:32, há 1 hora." + a última tentativa que falhou, se houver — **nunca** consulta a fonte |

Toda resposta termina dizendo **de quando são os dados**: "Dados atualizados agora, às 14:32." (fonte consultada),
"Dados atualizados hoje às 14:32." (cache válido) ou, em falha, "Não consegui acessar a internet. Estou usando os dados
salvos, atualizados ontem às 14:32 (há 20 horas). Atenção: esses dados estão desatualizados e a previsão pode não refletir as
condições atuais." A hora da consulta nunca é confundida com a data a que a previsão se refere: cada dia é identificado pelo
nome e pela data completa vindas do campo `time` da fonte (já no fuso local), e as condições atuais trazem a data e a hora da
leitura; ao usar o cache, essas datas são mantidas como estavam, e só a frase "atualizados …" muda.

O que **não** é pergunta sobre o tempo vai para os outros comandos ou para a IA como antes: "quanto tempo leva para
cozinhar arroz?", "tempo de forno", "temperatura do forno", "clima de festa", "lembre que…", "tenho três ovos".

## Política de validade do cache
| situação | o que a Luna faz |
|---|---|
| cache com menos de `current_ttl_s` (30 min) | responde do cache, sem consultar a fonte (condições atuais e chuva de hoje) |
| cache com menos de `forecast_ttl_s` (3 h) e a pergunta é sobre a previsão (amanhã, próximos dias, chuva em outro dia) | responde do cache, sem consultar a fonte |
| cache vencido ou inexistente | consulta a fonte **uma vez**; sucesso → substitui o cache e responde "atualizados agora" |
| consulta falhou (DNS, conexão, timeout, HTTP 4xx/5xx, 429, JSON inválido) e há cache | responde do cache, com o motivo e a idade dos dados; anota a falha sem apagar os dados |
| cache com mais de `stale_after_s` (6 h) usado em uma falha | acrescenta "Atenção: esses dados estão desatualizados e a previsão pode não refletir as condições atuais." (e o cartão mostra "Dados antigos") |
| cache não cobre o dia pedido | "Minha previsão salva vai até quarta-feira (14/10); não tenho dados para amanhã." |
| falhou há menos de `retry_cooldown_s` (60 s) | não insiste na fonte; usa o cache (ou diz que não há dados) |
| consulta falhou e não há cache | "Não consegui acessar a internet. Não tenho nenhuma previsão salva, então não consigo informar o tempo sem internet." |

As validades seguem a frequência da fonte: as condições atuais vêm de dados de 15 min a 1 h; os modelos de previsão
são atualizados a cada 1 a 6 h. Em condições atuais vindas do cache depois de `current_ttl_s`, a Luna diz "Na última
leitura, segunda-feira, 12 de outubro de 2026, às 14:30, Ouro Fino tinha…" em vez de "Agora faz…".

## Funcionamento sem internet
A indisponibilidade da internet nunca trava nada: a consulta roda em uma thread com timeouts curtos (DNS falha na hora;
conexão 5 s; resposta 10 s), e a interface, a transcrição, o Ollama, a memória, as receitas e a voz continuam iguais.
Uma falha do tempo **não** conta como falha da IA (sem "Tentar novamente", sem rosto de erro): a Luna responde o que
tem e volta a "Pronta". A Luna nunca afirma o tempo atual sem dados: sem cache, diz que não consegue sem internet.

## Configuração (`config/settings.toml`, seção `[weather]`)
```toml
[weather]
enabled = true
latitude = -22.28306            # Ouro Fino, MG (geocodificação da Open-Meteo)
longitude = -46.36889
location_name = "Ouro Fino, MG"
timezone = "America/Sao_Paulo"
forecast_days = 5               # dias pedidos à fonte (1..16)
connect_timeout_s = 5.0
timeout_s = 10.0
current_ttl_s = 1800            # condições atuais valem 30 min
forecast_ttl_s = 10800          # previsão dos próximos dias vale 3 h
stale_after_s = 21600           # acima de 6 h o cache é anunciado como desatualizado
retry_cooldown_s = 60           # espera após uma falha antes de tentar a fonte de novo
cache_path = ""                 # vazio = mesmo arquivo de [memory] path
```
Para outra cidade, troque as coordenadas e o nome (a geocodificação gratuita da Open-Meteo:
`https://geocoding-api.open-meteo.com/v1/search?name=<cidade>&count=5&language=pt`).

## Protocolo (resumo; detalhes em `docs/protocolo.md`)
- `welcome` e `/health` trazem `weather: {enabled, available, location, summary, detail, updated_at, stale, status,
  message, source, attribution, cache, questions, fetches, fetch_failures, timezone}` a partir do cache (nunca consultam a
  fonte); `/health` inclui `cache_path`, `cache_failures`, as validades e `weather_replies`.
- Servidor → cliente `weather {available, location, summary, detail, updated_at, observed_at, period_start, period_end,
  stale, status, message, source, attribution}` depois de cada resposta sobre o tempo (broadcast). `status` ∈ `online |
  cache | offline | none | cached_only`. `updated_at` é a hora da consulta à fonte (epoch); `observed_at` é a hora local
  a que as condições atuais se referem.

## Como testar
1. **Sem microfone, com a fonte real:** `.\.venv\Scripts\python scripts\check_weather.py` roda o cenário em um cache
   **temporário** com **uma** consulta real: online, perguntas do cache, reinício, falha simulada com cache (idade dos
   dados), cache antigo (aviso) e falha sem cache. `--offline` não consulta a fonte; `--real` só mostra o cache configurado.
2. **Fluxo completo:** `.\scripts\run-server.ps1`, abra o quiosque e diga, com pausa após "hey Luna":
   1. "como está o tempo em Ouro Fino?" → resposta falada curta, texto completo em "Resposta", cartão **Clima** com o
      resumo e "Atualizado às HH:MM".
   2. "vai chover hoje?" e "como estará o tempo amanhã?" → do cache (o log não mostra nova consulta).
   3. Reinicie o servidor: o cartão já vem preenchido no `welcome`; "você tem uma previsão salva?" responde a hora.
   4. Desligue a internet (cabo/Wi-Fi do servidor) e pergunte de novo: a resposta vem do cache com a idade dos dados;
      "como faço arroz?" e "lembre que…" continuam funcionando. Com o cache apagado (`DELETE FROM weather_cache`
      ou arquivo novo), a Luna diz que não consegue informar o tempo sem internet.
3. **Automáticos:** `.\.venv\Scripts\python -m pytest -q` (`tests/test_weather.py`: fonte, códigos, cliente HTTP contra
   um servidor local falso, cache, frases, respostas online/cache/offline/antigo, conversa, WebSocket, configuração) e
   `cd client; npm test`. `LUNA_WEATHER_ONLINE=1` liga um teste com uma chamada real à Open-Meteo.

## Privacidade e limites
- A única coisa enviada à Open-Meteo é a requisição com as coordenadas configuradas e as variáveis pedidas; nenhuma
  conversa, memória, áudio ou identificação. A Open-Meteo informa guardar logs (com coordenadas) por 90 dias.
- Nenhum serviço local é exposto à internet: a consulta é de saída, HTTPS, e nada escuta em portas novas.
- A previsão é estimativa: as frases preservam probabilidades e condicionais; a Luna não transforma 70% em "vai chover".
- Sem o pacote `tzdata` no Windows, a conversão da hora da consulta usa -03:00 fixo (correto enquanto o Brasil não tiver
  horário de verão). Para instalar o banco de fusos: `pip install tzdata` (opcional).
