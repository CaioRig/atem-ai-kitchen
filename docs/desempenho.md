# Desempenho adaptativo da Luna

A interface (Canvas/TypeScript) envia áudio por WebSocket ao servidor FastAPI.
O servidor mantém openWakeWord/Silero, Whisper.cpp, uma voz Kokoro/Piper e SQLite
carregados. O Ollama pode rodar neste servidor ou em outra máquina da LAN.
Memória, estoque, receitas, clima e comandos de tela continuam no fluxo existente.

Antes desta alteração já existiam transcrição fora da interface, streaming de texto,
TTS em uma thread por frases, aquecimento da voz e reutilização dos modelos. Os
pontos corrigidos são frases sem pontuação excessivamente longas no TTS, falta de
limites de threads ONNX e ausência de política de seleção/modelos/contexto. A fala
continua após a resposta completa do LLM: isso mantém o resumo de receitas e evita
disputar CPU entre geração e síntese. O primeiro áudio não aguarda a síntese inteira.

## Perfis e controles

| Perfil | Contexto Ollama local | Máximo de threads por inferência | Segmento Kokoro |
| --- | ---: | ---: | ---: |
| Econômico | 4096 | 4 | 90 caracteres |
| Equilibrado | 8192 | 8 | 120 caracteres |
| Desempenho | 16384 | 12 | 160 caracteres |
| Automático (padrão) | seleciona um dos anteriores | limitado à afinidade disponível, reservando uma thread quando possível | conforme perfil |

Pouca RAM disponível, menos de quatro threads ou RAM desconhecida levam ao perfil
econômico. Desempenho exige pelo menos 12 GiB livres, oito threads e 6 GiB de VRAM;
os demais casos usam equilibrado. A detecção usa biblioteca padrão, afinidade de
processo, APIs Windows, `/proc`/cgroup v2 no Linux e `nvidia-smi` com timeout.
GPU/VRAM AMD/Intel não identificadas permanecem desconhecidas. A API CUDA é
inicializada para verificar o driver; isso não confirma uso da GPU pelo Ollama,
verificado separadamente pelo bootstrap através de `/api/ps`.

O navegador escolhe separadamente 8 ou 20 fps usando os limites expostos pelo
navegador; desativa efeitos opcionais no cliente modesto e mantém o microfone.
Esses dados nunca determinam o modelo no servidor. Para Ollama fora de loopback,
o hardware de inferência fica desconhecido: preservam-se o modelo instalado
configurado e as opções remotas de contexto/threads. Um endereço LAN da própria
máquina também é tratado conservadoramente como remoto. Não existe uma API de
inventário de hardware remoto instalada por esta alteração.

Configurações → Desempenho permite escolher perfil, modelo instalado e voz Kokoro,
visualizar hardware e restaurar automático. Preferências do servidor ficam em
`data/performance.json` (caminho configurável em `[performance]`). Salvar informa
que é necessário reiniciar o servidor. A API exige o mesmo token da conexão, via
Bearer, nunca na URL. Restaurar automático limpa as escolhas do painel; respeita
os caminhos e motor originalmente configurados no TOML.

O catálogo `/api/tags` fornece tamanho, família e quantização. A seleção reserva
memória para contexto, ASR/TTS e sistema, usa a VRAM NVIDIA detectada quando
disponível e favorece famílias multilíngues conhecidas/quantização. Essa é uma
estimativa, não um benchmark de qualidade em português. Se nenhum modelo cabe,
usa o menor instalado e apresenta aviso/recomendação explícita de instalação.
O módulo de desempenho não solicita downloads. O novo inicializador pode preparar
um modelo quando necessário; ver [inicialização com um comando](inicializacao.md).
Configurações antigas de modelo são manuais: a seleção automática requer
`[llm] automatic_model = true` ou uma instalação nova pelo bootstrap. Preferências
explícitas de modelo não são substituídas quando o modelo fica indisponível.
Sem catálogo acessível, mantém a configuração original. Ollama continua gerindo
GPU/offload; não forçamos `num_gpu` nem presumimos aceleração pelo tamanho da RAM.

Kokoro preserva Dora, feminina pt-BR, com recursos reutilizados e um pool de síntese.
Piper configurado no TOML continua suportado. Se Kokoro não carregar e o Piper padrão
já estiver instalado, tenta essa alternativa e informa explicitamente que a voz é
masculina. Não reduzimos os limites existentes de tokens, o contexto de áudio do
Whisper, a seleção de memórias nem as instruções de segurança/precisão.

## Medições e adaptação

`GET /api/performance`, autenticado, fornece tempos separados de detecção, captura,
transcrição, primeiro token, geração completa, primeiro áudio, síntese completa e
interação desde ativação até espera (inclui reprodução quando confirmada).
Métricas de geração incluem carga e tokens/s informados pelo Ollama. O uso de CPU
do processo é a média desde a consulta anterior, normalizada pelas threads
disponíveis; RAM é o working set/RSS do servidor Python, sem somar o processo
Ollama. Uso da GPU NVIDIA é consultado sob demanda, sem polling de inferência.
Valores indisponíveis são nulos. Os dados de hardware representam a inicialização.

Após três respostas completas com primeiro token acima de oito segundos,
descontada a carga informada pelo Ollama, Automático pode reduzir contexto pela
metade, nunca abaixo de 4096, com cinco minutos entre ajustes. Não troca o modelo
durante conversas nem alterna perfis para cima/baixo. Salva as últimas vinte
medições sem textos e o contexto escolhido; reaproveita o contexto quando hardware,
URL e modelo coincidem. Não há benchmark de inicialização. Preferências manuais
não sofrem esse ajuste. A política de contexto ainda requer validação em pedidos
muito longos e hardwares modestos reais.

## Verificação local em 02/11/2026

Windows 10, Ryzen 5 5600GT (6/12), 15,87 GiB de RAM total, aproximadamente 5,9 GiB
disponíveis durante o ensaio, GTX 1080 com 8 GiB. Único modelo instalado:
`qwen2.5:7b`, 4.683.087.332 bytes, Q4_K_M. Seleção inicial: equilibrado, oito
threads, contexto 8192, Kokoro Dora e segmentos de 120 caracteres; Whisper
small-q8_0 preservado. São observações desta máquina, não resultados em outros PCs.

Comparação TTS usou a implementação original extraída do HEAD anterior às alterações
e a atual, sequencialmente no mesmo processo. Mesmo texto em pt-BR com 220
caracteres, sem pontuação intermediária; três repetições após aquecimento por
configuração. O comprimento de áudio variou ligeiramente com a segmentação.

| Mediana | Antes | Depois |
| --- | ---: | ---: |
| Primeiro áudio | 3,457 s | 1,568 s |
| Síntese completa | 3,457 s | 3,279 s |

Redução do primeiro áudio: 54,6%; síntese total: 5,1%. Working set observado nas
repetições aquecidas: aproximadamente 912 MB antes e 665 MB depois. Não são picos
isolados nem uma medição do sistema inteiro; a ordem e os alocadores podem influir.
Resultados brutos locais em `data/performance-benchmark.json`.

LLM: mesma GPU/modelo, temperatura zero, aquecimento por configuração e três
perguntas (cálculo 12×13, contaminação cruzada, preferência por evitar coentro
fornecida como contexto de memória). Respostas foram idênticas antes/depois:
156, separação/higienização de utensílios e evitar coentro. Primeiro token entre
0,052–0,082 s antes e 0,058–0,082 s depois; totais entre 0,361–2,384 s antes e
0,367–2,377 s depois. Não há ganho relevante de LLM demonstrado neste ambiente.
Não constitui avaliação abrangente de factualidade ou receitas longas. Dados em
`data/llm-performance-benchmark.json`; sem memórias reais do usuário nos ensaios.

Testes unitários simulam recursos fracos/intermediários/GPU, pouca RAM, GPU ausente,
hardware desconhecido, Ollama remoto, falta de alternativas, preferências inválidas,
persistência, autenticação e histerese. Pendentes: execução real em Linux/cgroups,
notebook fraco conectado ao servidor, PCs sem GPU, auditoria de qualidade em muitas
perguntas, reprodução e qualidade da voz em navegador e latência ponta a ponta com
microfone. Não se extrapolam números de desempenho para esses cenários.

Validação automatizada final: 291 testes Python aprovados e três ignorados;
18 dos aprovados cobrem a nova política/API. Os 16 testes do cliente e o build
TypeScript passaram. `git diff --check` passou. O aviso de depreciação do
TestClient/httpx já existia antes das alterações.
