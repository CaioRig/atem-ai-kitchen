# Luna com um comando

Na pasta do projeto, execute:

Windows: dê dois cliques em **start.bat**, ou digite `start.bat` no terminal.

Linux: execute `./start.sh` no terminal.

O mesmo inicializador prepara e inicia o servidor. Depois de reiniciar o computador,
execute o mesmo arquivo novamente. Mantenha o terminal aberto; Ctrl+C encerra a
Luna. Não é preciso executar os scripts de setup, modelos e build separadamente.

## Primeira execução

1. Confere Python, cria `.venv` e instala as dependências necessárias nesse ambiente.
2. Detecta CPU, RAM disponível, GPU, VRAM livre, arquitetura e disco do servidor.
3. Prepara Node/npm locais quando necessários e compila a interface usando o lock.
4. Reutiliza ou inicia Ollama local. Confere o catálogo instalado e escolhe um modelo.
5. Baixa apenas o modelo de IA necessário e os arquivos de ativação, ASR e voz.
6. Gera configuração com token aleatório e certificado HTTPS, se ainda não existem.
7. Faz uma avaliação pequena quando necessária, salva os resultados e inicia Luna.

Essa execução pode demorar bastante por causa dos downloads. Há necessidade de
internet nessa preparação. Interrompeu ou caiu a conexão? Execute o mesmo comando;
etapas concluídas são reaproveitadas. Downloads HTTP usam arquivos `.part`, ETag e
Range quando o servidor permite. Downloads Ollama usam a retomada do próprio motor.
O arquivo final só substitui o parcial após tamanho/checksum verificáveis passarem.

Nas execuções seguintes há uma verificação local rápida de dependências, arquivos,
hardware e catálogo Ollama. Sem mudanças, não há pip/npm install, build, downloads
ou novas avaliações de modelos. Os modelos de voz/transcrição são então carregados
normalmente pelo servidor, uma vez para atender as interações.

Abra a URL do servidor exibida por `server.main`/configuração (por exemplo,
`https://192.168.1.20:8800`). O IP e porta estão em `config/settings.toml`, seção
`[server]`. Para conectar a interface, abra essa URL com `/#token=SEU_TOKEN`, usando
o valor de `[auth] token` no arquivo. O token não é incluído nos logs do bootstrap.
O navegador pode pedir que você aceite o certificado local e permita o microfone.
Em outra máquina, use o IP do servidor; `localhost` refere-se à própria máquina.

## Compatibilidade e requisitos

O ambiente de desenvolvimento verificado é Windows 10 x64, Python 3.12 e Node já
instalado. O bootstrap aceita Python 3.11 ou 3.12. Windows x64 e Linux x64 são os
alvos principais; há seleção de pacotes Node/Ollama arm64, mas as wheels ASR/TTS e
drivers dessa arquitetura ainda precisam de validação real. Não há promessa de
compatibilidade com qualquer computador/distribuição.

O menor modelo do catálogo exige estimativa de 3 GiB de RAM **disponível** e duas
threads; isso não é garantia de boa velocidade. Para uso confortável, prefira pelo
menos 16 GB de RAM total, CPU com seis ou mais threads e SSD. GPU compatível pode
ajudar muito o LLM, mas ASR e as vozes existentes também podem funcionar em CPU.
Reserve aproximadamente 15 GB de disco para começar; o volume dos modelos precisa
comportar o download, armazenamento e margem. Modelos maiores podem exigir muito
mais espaço e memória. O bootstrap confere o espaço antes dos downloads.

Windows: Python ausente pode ser instalado pelo Winget após autorização no terminal.
Ollama também usa o pacote oficial do Winget, quando ausente. Esses instaladores
podem abrir UAC; a Luna não contorna essa autorização nem a política do PowerShell.
Sem Winget, o inicializador fornece a origem oficial para instalação manual e pode
ser executado novamente depois. O BAT não depende de executar scripts PowerShell.

Linux: Python/venv ausentes podem ser preparados via apt ou dnf, após autorização
e sudo quando necessário. Distribuições com outra organização de pacotes, ou cujo
Python padrão esteja fora de 3.11/3.12, podem exigir instalação manual de um runtime
compatível. Não são adicionados PPAs ou repositórios de terceiros automaticamente.
Node fica em `.tools`. Ollama pode ser extraído nessa mesma pasta a partir da
release oficial, com SHA256 do GitHub. Pacotes `.zst` exigem zstd; se faltar, sua
instalação pelo gerenciador do sistema pede autorização. Não se executa `curl | sh`.
Se a release não fornecer um digest verificável, a instalação automática para e
informa a origem oficial, em vez de executar um binário sem essa verificação.

Se pip precisar compilar uma dependência nativa e não houver compilador, corrija a
ferramenta indicada no erro e execute novamente. Em Debian/Ubuntu isso pode incluir
`build-essential`, `cmake`, `python3-dev` e o pacote venv da versão em uso; no Windows,
Microsoft C++ Build Tools. Esses conjuntos grandes não são instalados às cegas.

FFmpeg não é instalado: o fluxo atual usa PCM em memória e não chama FFmpeg.
espeak-ng vem pelo pacote usado pelo Kokoro; o bootstrap não instala um serviço
de voz externo. Piper só é instalado se esse motor estiver configurado.

## Escolha dos modelos

`config/model-catalog.json` tem versão de esquema, revisão e requisitos estimados.
Ele permite atualizar recomendações sem alterar o código do bootstrap. O catálogo
inicial contém Qwen 2.5 quantizado em 1.5B, 3B, 7B e 14B. Os tamanhos e requisitos
são estimativas conservadoras; os ranks de qualidade são heurísticas, não resultados
de um benchmark comparativo de português.

A seleção considera memória disponível, threads, VRAM utilizável, perfil solicitado,
quantização e alternativas multilíngues já instaladas. Reutiliza uma alternativa
compatível antes de baixar outra. Um modelo manual não é substituído. Quando o
modelo manual falta, seu nome é preservado; downloads automáticos dele só ocorrem
se seu tamanho estiver previsto no catálogo. Fora disso, o inicializador explica
como instalá-lo conscientemente.

Detectar NVIDIA não comprova CUDA funcional: a detecção também chama `cuInit` e
consulta dispositivos pela Driver API. O uso **pelo Ollama** é confirmado depois
pela memória em GPU informada em `/api/ps`. Uma geração mínima, local, avalia carga
e tokens/s. Velocidade muito baixa deve se repetir em duas avaliações curtas antes
de uma escolha automática mais leve. Essa escolha fica salva para não retestar o
modelo lento a cada execução. Não há troca de modelos escolhidos manualmente.
O ensaio é uma estimativa operacional, não uma prova de qualidade factual.

Em instalação automática nova, o perfil Econômico usa Whisper base multilíngue e
Kokoro int8/Dora; os demais preservam Whisper small-q8_0 e Kokoro fp32/Dora.
Transcrição e voz continuam essenciais. Threads e segmentos seguem os perfis já
existentes. Configurações manuais antigas de ASR/TTS são preservadas. A avaliação
valida o carregamento de ativação/VAD/ASR e gera uma frase TTS; os resultados ficam
salvos. ASR continua usando o backend CPU já suportado, sem presumir suporte GPU.

O bootstrap que inicia seu próprio Ollama limita-o a um modelo carregado e uma
requisição paralela. Ao encerrar, termina somente esse processo que ele iniciou.
Se já houver um serviço Ollama, não altera a configuração global nem encerra o
serviço de terceiros. Nessa situação, os limites globais continuam a cargo dele.

## Preferências e ajustes manuais

Na interface, Configurações → Desempenho permite escolher perfil, modelo instalado
e voz. Clique em Salvar e reinicie usando o mesmo inicializador. Restaurar automático
remove as escolhas desse painel; não apaga memórias nem altera segredos do TOML.

`config/settings.toml` continua sendo a configuração principal. Um arquivo existente
nunca é reescrito pelo bootstrap. Instalações novas recebem:

```toml
[llm]
automatic_model = true
model = "qwen2.5:7b"

[bootstrap]
managed = true
```

O bootstrap guarda suas escolhas em `data/bootstrap-auto.json`. A sobreposição só
aplica campos que ainda correspondem ao valor original no TOML. Ao editar o nome
do modelo no TOML, sua escolha passa a prevalecer. Para fixar explicitamente o
modelo, use `automatic_model = false` e `model = "nome:tag"`. `num_ctx = 8192`
permite informar contexto; zero mantém a política existente. Para desativar todas
as sobreposições do bootstrap, use `[bootstrap] managed = false`. Arquivos de
configuração anteriores, sem essas opções, são tratados como manuais.

Para recalcular as escolhas automáticas, sem apagar o TOML ou memórias, pare Luna,
renomeie `data/bootstrap-state.json` e `data/bootstrap-auto.json`, mantenha as opções
automáticas habilitadas e execute novamente. Preferências explícitas do painel
continuam tendo prioridade; restaure-as pelo próprio painel se desejar.

## Servidor de IA na LAN

Em `[llm]`, configure `url = "http://IP_DO_SERVIDOR:11434"` e o nome de um modelo
instalado nele. O bootstrap confere a conexão, mas não instala software nem baixa
modelos remotamente. Não usa o hardware do notebook nem do servidor de voz para
estimar o hardware dessa outra máquina. A seleção da IA remota permanece manual;
use o bootstrap na própria máquina de inferência para fazer sua preparação.
Permita acesso ao Ollama somente pela LAN, conforme sua configuração de rede.
Não inclua senhas na URL nem use endpoints de IA na nuvem.

## Diagnóstico e recuperação

Os mesmos argumentos servem para BAT e SH:

| Opção | Efeito |
| --- | --- |
| `--check` | Diagnóstico de hardware e dependências, sem instalar ou gravar estado |
| `--prepare-only` | Prepara e valida sem iniciar o servidor Luna |
| `--offline` | Proíbe instalações e downloads; usa os recursos preparados |
| `--reprobe` | Refaz a avaliação leve; preserva preferências |

Logs: `logs/bootstrap.log`, `logs/server.log` e, quando o processo Ollama foi iniciado
pelo bootstrap, `logs/ollama.log`. Versões, arquivos, perfil e avaliações sem texto
das conversas ficam em `data/bootstrap-state.json`. A interface continua mostrando
os estados e erros dos serviços. Não publique seu `config/settings.toml`.

Sem internet depois da preparação, ative `--offline` se quiser garantir que nenhuma
tentativa de instalar ou baixar seja feita. IA, voz, ativação e memória funcionam
localmente; YouTube e atualização do clima mantêm seu tratamento separado de rede.

Sem disco, permissão ou componente correto, o fluxo para com orientação e código
de saída diferente de zero. Não há loop de reinstalação. Se uma dependência nativa
ou modelo estiver corrompido, preserve/mova o arquivo ou ambiente afetado antes de
reinstalar; nenhum dado de memória é apagado. Um par TLS incompleto não é sobrescrito
automaticamente. Certificados autoassinados não são adicionados à confiança do
sistema sem ação do usuário.

## Validação realizada e limites

No Windows disponível, `start.bat --check` e a preparação completa com
`start.bat --prepare-only --offline` passaram usando os modelos existentes, incluindo
uma avaliação real de Ollama, carregamento ASR/VAD/ativação e síntese Kokoro. A
dependência cryptography foi instalada somente no `.venv`. Uma segunda preparação
sem mudanças levou aproximadamente 0,69–1,14 s, sem novo build/instalação/download ou
avaliação. Isso mede a etapa de preparação, não o tempo até toda a aplicação carregar.

Testes usam mocks para primeira instalação nos dois sistemas, reinicialização,
hardware fraco/GPU, alterações de hardware, falta de internet/dependências,
modelo inválido, preferências manuais, retomada, checksum, falta de disco e recusa
de autorização. Há teste real de criação/preservação de certificado em pasta
temporária. A sintaxe SH foi conferida pelo Bash. Não há lint configurado no projeto;
foram executados compilação Python, TypeScript, testes e checagem de whitespace.

Permanecem pendentes uma instalação limpa real de Windows e Linux, instalação real
de Python/Node/Ollama em máquinas vazias, ARM64, outras distribuições/GPUs e avaliação
extensa da qualidade dos modelos/voz em hardware modesto. Nenhuma instalação global
foi feita apenas para os testes.

Resultado final: 321 testes Python aprovados, três ignorados e um aviso de
depreciação já existente; 16 testes do cliente e build TypeScript aprovados.
Os 29 testes específicos de bootstrap cobrem preparação, cache e recuperação;
há ainda testes da política de desempenho, incluindo persistência de contexto.

Fontes dos mecanismos de instalação: [Python para Windows](https://www.python.org/downloads/windows/),
[distribuições e checksums Node](https://nodejs.org/dist/v22.22.0/),
[instalação Linux do Ollama](https://github.com/ollama/ollama/blob/main/docs/linux.mdx),
[API de download Ollama](https://docs.ollama.com/api/pull) e
[modelos em execução/VRAM](https://docs.ollama.com/api/ps).
