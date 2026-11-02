# Área visual da Luna — 2026-11-02

A área do rosto agora tem um controlador único (`client/src/stage.ts`) com os modos
rosto, resposta, receita, previsão, resultados de vídeos e reprodução. Respostas
e receitas permanecem até serem fechadas ou substituídas. O cartão duplicado de
resposta foi removido. Receitas preservam quantidades e texto do modelo e destacam
título, ingredientes e preparo quando essas seções estiverem presentes; não são
inventadas seções ou quantidades ausentes.

Previsões de outros dias aparecem nessa área por 15 segundos, ou pelo prazo
escolhido em Configurações → Previsão na tela. Cada troca de modo invalida o prazo
anterior. O cartão lateral continua mostrando hoje. O conteúdo também permanece
utilizável em telas pequenas e em paisagem com pouca altura.

## Voz e toque

- “Hey Luna, feche esta tela”: fecha primeiro configurações/memória, se abertos;
  caso contrário, fecha o conteúdo e retorna ao rosto.
- “Volte para o rosto” ou “volte para a tela principal”: fecha painéis e conteúdo.
- “Role para baixo/cima”: rola o painel aberto ou o conteúdo principal.
- Botão **Voltar para Luna**: fecha o conteúdo, inclusive um vídeo em reprodução.
- “Pesquise um vídeo de receita de lasanha” ou “mostre um vídeo de como fazer pão”.
- Com resultados na tela: “abra o primeiro vídeo”, “abra o segundo” ou “abra o terceiro vídeo”.
- Com player pronto: “pause o vídeo” e “continue o vídeo”. Sem player disponível,
  a interface informa a limitação. Frases ambíguas fora de uma lista não selecionam vídeos.

Esses comandos não passam pelo Ollama. A busca de vídeos recebe apenas o tema
capturado do pedido explícito, sem memória, estoque, histórico ou áudio.

## Busca e reprodução

A busca abre a página oficial do YouTube em outra aba, após toque em
**Pesquisar no YouTube**. O botão **Vídeos no YouTube** permite iniciar pela
interface; pedidos por voz preenchem o tema, incluindo acentos. Não há chave,
conta de desenvolvedor, assinatura ou cobrança por requisição pela Luna.

Os resultados e sua seleção ficam no site oficial. A Luna não lê essa página:
“abra o primeiro vídeo” informa a limitação quando não há uma lista local.
Pause/continue não controlam a outra aba. Feche essa aba para voltar à Luna.
Opcionalmente, cole um link HTTPS do YouTube no campo **Assistir aqui** para
usar o player oficial incorporado e seus controles por voz. Links inválidos,
domínios diferentes e IDs malformados são recusados. Vídeos com restrições de
incorporação continuam dependendo do site oficial.

A busca não faz chamadas de rede no servidor e não envia histórico, memória ou
áudio ao YouTube. O navegador envia o tema ao abrir o formulário. Sem conexão
indicada pelo navegador, a Luna impede a abertura e informa o problema. Outras
falhas de rede e ausência de resultados são mostradas pelo próprio YouTube;
a Luna não consegue diagnosticar o conteúdo da outra aba. Funções offline
continuam independentes. Nenhum vídeo é baixado nem proxy público é utilizado.

O player inicia sem autoplay; use o botão nativo de reprodução ou peça para
continuar. Autoplay bloqueado, erro do player ou falta de carregamento produzem
uma mensagem e um link por toque para o vídeo. Pause/continue usam a API oficial.
Fechar/substituir destrói o player e interrompe o áudio. Iniciar vídeo interrompe
a fala da Luna; ativação de voz e nova fala pausam o vídeo. O vídeo não retoma
sozinho após uma pergunta. É necessário validar falsas ativações e eco no LG.

Alternativas, termos e limitações: [YouTube sem chave](youtube-sem-chave.md).

## Verificação

- Python: reconhecimento explícito em português, preparação sem rede/credenciais
  e desvio do Ollama em `tests/test_videos.py`.
- `npm test`: compilação TypeScript e testes, incluindo validação de URLs.
- `node scripts/check_ui.cjs`: Chrome headless em seis resoluções, toque emulado,
  textos longos, controles, painéis e preferências.
- `node scripts/check_stage.cjs`: navegação da busca em português, toque,
  desconexão, link inválido/válido, seleção, pausa, erro e destruição do player,
  além de resposta/receita e clima. Backend, página externa e player simulados.

Reprodução real, disponibilidade do YouTube, voz física e toque no computador
da cozinha exigem validação no equipamento com internet. Nenhum teste simulado
comprova esses aspectos.
