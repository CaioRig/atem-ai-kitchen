# YouTube sem chave

Decisão: abrir a pesquisa oficial em outra aba por toque, com tema preparado
pela voz ou interface. Manter o IFrame Player para links informados pelo usuário.
Não foi identificada uma API oficial de pesquisa programática sem credenciais.
Não há extração automatizada de páginas, downloads ou contorno de restrições.

| Alternativa | Chave e custo | Instalação / Windows e Linux | Estabilidade, arquitetura e termos |
| --- | --- | --- | --- |
| Invidious público | Em geral sem chave ou cobrança; depende da instância | HTTP compatível com ambos | API de terceiro com busca; instâncias podem fechar, bloquear ou mudar. Encaminha consultas a terceiros e não comprova autorização do YouTube para extração. Não adotado. |
| yt-dlp / ytsearch | Sem chave; acesso pode exigir autenticação em alguns casos | Dependência Python/binário em ambos | Extrator não oficial, sujeito a mudanças e bloqueios; exigiria manutenção no servidor. Gratuidade não implica autorização. Não adotado. |
| Página oficial | Busca pública sem API key; sem cobrança por requisição | Apenas navegador em ambos | Melhor ajuste para uso manual permitido. Consentimento, rede e restrições podem intervir. Resultados não ficam acessíveis programaticamente à Luna. Adotado via navegação, sem scraping. |
| IFrame Player API | Sem chave e sem cobrança por requisição | JavaScript no navegador em ambos | Reprodução e controle oficiais de vídeos conhecidos; não fornece pesquisa. Mantido; incorporação/autoplay podem ser bloqueados. |
| Link direto de vídeo | Sem chave/custo pela Luna | Navegador em ambos | Alternativa mínima para vídeo conhecido, sujeita à disponibilidade e restrições do conteúdo. Campo opcional para player ou abertura no site. |

Os termos do YouTube restringem acesso automatizado por robôs/scrapers salvo
exceções ou autorização prévia. Portanto, não foi implementada pesquisa por
extração via Invidious, yt-dlp ou HTML. O IFrame não substitui uma API de busca:
seu antigo modo de lista por pesquisa foi descontinuado.

Comandos: “procure vídeos de receitas de lasanha no YouTube”, “encontre um
tutorial de como fazer pão” preparam a pesquisa; o usuário toca para abrir a
aba. “Pause o vídeo” e “continue o vídeo” funcionam no player dentro da Luna.
“Abra o primeiro vídeo” não seleciona resultados da aba externa: informa essa
limitação. Nenhuma resposta ou lista de resultados é inventada.

Fontes consultadas:
- [Termos do YouTube](https://www.youtube.com/static?template=terms)
- [IFrame Player API](https://developers.google.com/youtube/iframe_api_reference)
- [API Invidious](https://docs.invidious.io/api/) e [instâncias](https://docs.invidious.io/instances/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)

Não é necessário configurar credenciais. Internet é necessária apenas para
pesquisa/reprodução online; o servidor não consulta serviços externos neste fluxo.
Sem integração programática, ausência de resultados e erros do site externo
ficam sob responsabilidade da página oficial, sem detecção pela Luna.
