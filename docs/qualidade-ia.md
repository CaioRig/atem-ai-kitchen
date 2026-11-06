# Qualidade da IA local

## Diagnóstico em 02/11/2026

O modelo encontrado foi `qwen2.5:7b`, Instruct (confirmado em `general.finetune`), GGUF Q4_K_M, digest
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.
Os logs anteriores à otimização também registram esse modelo. O Ollama mostrou
template ChatML com papéis de sistema/usuário/assistente; o pedido real enviava o
system prompt da Luna em `/api/chat`, sem substituir template ou stops.

A saída com caracteres aleatórios descrita pelo usuário **não foi reproduzida**.
Antes das alterações, duas saudações (API direta e cliente Python) responderam
coerentemente. Também não foi identificado nos logs inspecionados um erro fatal
que permita atribuir o episódio a pesos, GPU, quantização ou template.

Foram comprovadas duas lacunas: o bootstrap aprovava qualquer texto não vazio em
uma geração de oito tokens sem o system prompt, diferente da configuração real;
e perguntas independentes não preservavam o nome informado no turno anterior.
Essas lacunas foram corrigidas. Não são uma prova da causa específica do episódio
intermitente de caracteres estrangeiros.

## Validação na inicialização

Um modelo escolhido manualmente faz uma geração curta em português com as
opções de conversa em uso, inclusive em modo offline quando já está instalado.
O catálogo e a detecção de hardware informam avisos e alternativas. A decisão
de manter ou trocar o modelo fica com o usuário. Erros de execução aparecem
no status da IA, enquanto a preferência de habilitação vale nas próximas
inicializações. Para trocar ou desativar, use **Configurações → Servidor, IA e
voz**, salve e reinicie o servidor.

A seleção automática usa a suíte v7 para separar compatibilidade, qualidade
e desempenho. No perfil econômico,
quatro gerações verificam saudação, identidade, soma simples e uma resposta em
português. O quinto caso usa o reconhecedor local de música. Os outros perfis
mantêm os casos adicionais de contexto, receitas, instruções e conversa.

- `approved` indica que o modelo completou a suíte e atendeu aos critérios sem
  sinais de lentidão.
- `limited` permite o uso normal quando há evidência de conversa básica, mesmo
  com erros pontuais ou lentidão. A primeira resposta a partir de 20 s e a
  geração abaixo de 3 tokens/s sinalizam lentidão.
- `failed` impede o uso diante de erros críticos de execução ou de qualidade
  insuficiente para uma conversa básica. O mínimo de qualidade exige respostas
  coerentes e distintas em pelo menos dois casos, e coerência em pelo menos metade dos casos de LLM
  executados, com pelo menos um acerto de conversa. Uma conversa bem-sucedida
  seguida apenas de timeout pode ser usada em modo limitado. Os comandos
  determinísticos não contam para esse mínimo.

Cada conversa de teste começa vazia, com o mesmo prompt, opções e template do
modelo usado pela aplicação. O caso de cozinha reutiliza o guia e o limite de
tokens de `RecipeAssistant`, com estoque vazio e sem memórias pessoais. Os demais
casos usam o limite comum. Relatórios não contêm histórico real das pessoas.

Antes de aprovar, são conferidos digest, formato/quantização e template disponível.
Modelos explicitamente base/pretrain são recusados. Os resultados ficam em
`data/model-quality.json`, incluindo respostas sintéticas, motivos, latências e
memória carregada/VRAM informada pelo Ollama quando disponível.

A chave da aprovação inclui digest, versão do Ollama, template e parâmetros do
modelo, prompt da Luna, opções de inferência, política de receitas e hardware
local conhecido. Mudanças relevantes, inclusive a migração para a suíte v7,
exigem nova validação. Sem mudanças, a inicialização reutiliza a evidência
salva para `approved` ou `limited` e testa uma geração curta. Uma reavaliação que só encontra timeout
preserva a validação anterior com a mesma identidade.

Um candidato automático reprovado não substitui a seleção aprovada. A seleção
automática tenta no máximo três candidatos e usa estimativas para ordená-los.
Na conversação, texto manifestamente corrompido é bloqueado antes do TTS.
Para modelos automáticos, duas falhas de saída consecutivas invalidam a
aprovação e permitem tentar uma alternativa validada.
Memória, voz, transcrição, ferramentas e perfis continuam disponíveis.

## Inferência e contexto

`llm.connect_timeout_s` limita a conexão. `llm.timeout_s` limita a espera pela
primeira saída e a inatividade durante a geração. No perfil econômico esse
intervalo é de pelo menos 180 s para acomodar a carga do modelo. Cada trecho
gerado renova o intervalo de inatividade até o limite total configurável
`llm.generation_limit_s`, de 600 s por padrão.

Temperatura 0,7 e limites existentes de 300/600 tokens foram preservados. Para
Qwen 2.5 7B/14B, os valores automáticos são `top_p=0.8` e `repeat_penalty=1.05`;
para 1.5B, penalidade 1.1. São valores específicos da família, conforme as
[configurações oficiais do 7B](https://huggingface.co/Qwen/Qwen2.5-7B-Instruct/blob/main/generation_config.json)
e [1.5B](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct/blob/main/generation_config.json).
Outras famílias mantêm seus defaults. Valores explícitos em `[llm] top_p` e
`repeat_penalty` prevalecem. Nenhum stop ou template arbitrário é injetado.

O prompt reforça respostas diretas e o reconhecimento de informações inacessíveis.
Há até três turnos recentes somente em RAM, por no máximo 30 minutos e com orçamento
conservador de caracteres conforme contexto. Não se grava essa conversa no SQLite.
Excluir/esquecer memórias também limpa esse contexto recente. As otimizações de
hardware, streaming, Whisper e Kokoro continuam em uso.

## Reproduzir a validação

```powershell
.\.venv\Scripts\python scripts\check_quality.py --force --show-answers
.\.venv\Scripts\python scripts\check_quality.py --flow
```

Linux: use `.venv/bin/python` e caminhos com `/`. `--model nome:tag` testa um modelo
já instalado, sem download. `--force` refaz a avaliação; reinicie a Luna depois de
corrigir um modelo que tenha sido colocado em quarentena durante a conversa.
`scripts/check_llm.py` também exige aprovação, além de verificar a conexão.

`--flow` gera uma saudação sintética e usa Whisper, Ollama, eventos WebSocket e
síntese Kokoro reais. Compara o stream recebido do Ollama, o texto final da interface
e o texto efetivamente enviado ao sintetizador; grava `data/quality-flow.json`.
Ativação e confirmação de reprodução são simuladas. O teste existente
`node scripts/check_stage.cjs`, quando esse relatório existe, verifica no Chrome
headless que o texto real capturado aparece integralmente na interface.

## Resultados e limites

O único modelo disponível, Qwen 2.5 7B Q4_K_M, passou nos oito critérios da suíte
final com contexto 4096 e oito threads. Os testes também demonstraram rejeição de
modelos falsos incoerentes, preservação da última aprovação, fallback, retomada da
aprovação após reinicialização, histórico limitado e bloqueio do TTS.

Antes das alterações, a saudação levou 4,60 s com carga de modelo e 0,45 s aquecida.
Depois, duas saudações aquecidas levaram 0,64 s e 0,43 s. O relatório correspondente
está em `data/quality-after.json`; a referência anterior, em `data/quality-before.json`.
Uma primeira verificação integrada posterior mediu ASR 0,97 s, LLM 4,30 s
(3,41 s de carga) e primeiro áudio 0,75 s após o texto. Esses cenários não são uma
comparação controlada de velocidade; não foi demonstrado um ganho de latência.
Os números de cada execução podem ser consultados nos relatórios locais.

Critérios lexicais e testes curtos não garantem correção factual ou boa resposta
para toda pergunta. Não foi feita avaliação real de outros modelos, nem validado
o episódio intermitente original, o microfone físico ou a reprodução nos alto-falantes.
Essas verificações permanecem pendentes.

A suíte completa passou com 355 testes Python aprovados e três ignorados; os 16
testes do cliente, build TypeScript, compilação Python e checagem de whitespace
passaram. O teste de Chrome headless confirmou a igualdade do texto real capturado.
O inicializador `start.bat --prepare-only --offline` reutilizou a aprovação persistida
sem repetir os oito testes. No JSON, `selected.key` aponta para o relatório ativo;
relatórios antigos também são conservados para diagnóstico.

Arquivos desta alteração: `server/services/llm.py`, `model_quality.py` e
`performance.py`; `server/storage/config.py`; `server/assistant/conversation.py`;
`server/api/app.py`; `scripts/bootstrap.py`, `check_quality.py`, `check_llm.py` e
`check_stage.cjs`; `tests/test_model_quality.py`, `test_llm.py` e `test_bootstrap.py`;
`config/settings.example.toml`, `README.md` e este guia. Nenhuma dependência nova
ou download de modelo foi necessário.
