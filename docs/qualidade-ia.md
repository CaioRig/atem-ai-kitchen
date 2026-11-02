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

## Aprovação obrigatória

`server/services/model_quality.py` executa oito casos: saudação, soma 15+27,
Brasília, dois turnos com o nome Ana, ovo mexido, resposta exata “azul”, informação
desconhecida e explicação de geladeira. As verificações são determinísticas, sem
usar outro LLM como avaliador. Também rejeitam texto manifestamente corrompido.
Uma recusa natural é válida; uma resposta que só pede pistas sobre uma senha não
substitui o reconhecimento de que não tem a informação.

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
local conhecido. Mudanças relevantes exigem nova aprovação. Uma aprovação salva
só é aceita com evidências completas dos oito casos; HTTP 200 não basta. Sem
mudanças, a inicialização reutiliza o resultado e não repete a bateria.

Um candidato reprovado não substitui a seleção aprovada. Na inicialização há no
máximo três candidatos, respeitando a seleção manual e os recursos disponíveis.
Na conversação, texto manifestamente corrompido não chega ao TTS. Pode haver uma
única tentativa com uma alternativa que tenha aprovação correspondente, sem nova
bateria de testes durante essa recuperação. Se não houver alternativa ou ela
falhar, a IA informa indisponibilidade; não simula uma resposta. Memória, voz,
transcrição, ferramentas e perfis continuam separados dessa disponibilidade.

## Inferência e contexto

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
