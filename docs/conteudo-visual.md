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

Não existia integração de vídeo. Foi adotada a API oficial YouTube Data API v3,
opcional, com até três resultados e filtro de vídeos incorporáveis. A aplicação
não ativa faturamento, não contrata serviços e não contorna cotas do provedor.
É necessário criar credencial própria, habilitar a API e respeitar a cota disponível.

A chave fica somente no servidor, na variável de ambiente `LUNA_YOUTUBE_API_KEY`.
Defina-a no ambiente do processo que inicia a Luna e reinicie o servidor. Não a
coloque na URL do quiosque, no código ou no Git. A chave não é enviada ao navegador
nem registrada nos logs. Em inicialização pelo Agendador, o ambiente do usuário
deve estar atualizado antes de iniciar a tarefa.

**Sem chave**, a Luna informa que a busca integrada não está configurada e oferece
**Pesquisar no YouTube**, um link que abre outra aba apenas após toque. Não há
resultados fabricados nem seleção automática nesse modo. O mesmo caminho fica
disponível em caso de erro de rede/cota ou resultados vazios.

**Com chave e resultados**, a seleção abre o player oficial IFrame API dentro da
área principal. IDs são validados e links são construídos apenas para YouTube.
Nenhum HTML do modelo ou da fonte é injetado. Scripts externos só são carregados
após selecionar um vídeo. A busca e a reprodução exigem internet e comunicam-se
com o Google/YouTube, incluindo os metadados normais da conexão do navegador.

O player inicia sem autoplay; use o botão nativo de reprodução ou peça para
continuar. Autoplay bloqueado, erro do player ou falta de carregamento produzem
uma mensagem e um link por toque para o vídeo. Pause/continue usam a API oficial.
Fechar/substituir destrói o player e interrompe o áudio. Iniciar vídeo interrompe
a fala da Luna; ativação de voz e nova fala pausam o vídeo. O vídeo não retoma
sozinho após uma pergunta. É necessário validar falsas ativações e eco no LG.

Fontes oficiais verificadas:
- [Busca de vídeos](https://developers.google.com/youtube/v3/docs/search/list)
- [Credenciais e configuração](https://developers.google.com/youtube/v3/getting-started)
- [Player, controle e erros](https://developers.google.com/youtube/iframe_api_reference)

## Verificação

- Python: `tests/test_videos.py` verifica reconhecimento explícito, ausência de
  chave, resultados validados, fonte vazia, erros e desvio do Ollama.
- `npm test` compila e testa o cliente.
- `node scripts/check_ui.cjs`: Chrome headless nas resoluções documentadas,
  toque emulado, textos longos, controles, painéis e preferências.
- `node scripts/check_stage.cjs`: resposta/receita, rolagem, prazo do clima,
  substituição sem prazo antigo, fechamento, resultados, seleção, pausa,
  autoplay/erros e destruição do player. Backend e player são simulados nesse teste.
  Usa a instalação isolada do Playwright descrita em `docs/interface.md`.

Nesta máquina não havia chave de busca configurada. A consulta real à API e a
reprodução de vídeos externos ainda precisam de validação com credencial e rede;
os testes não equivalem a reprodução real nem a voz/toque físicos no LG.
