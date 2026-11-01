# Interface por toque e voz

Atualizada em 2026-11-01. O corte vinha de `overflow: hidden` no corpo,
grade com altura fixa e cartoes comprimidos sem area de rolagem suficiente.
Agora o conteudo tem rolagem independente e os controles ficam em uma area
propria. Em telas muito baixas, o rosto cede espaco ao conteudo e aos controles.
Os paineis respeitam a altura dos controles. Em telas extremas, a propria barra
de controles tambem permite rolagem por toque.

## Ajustes

Toque em **Configuracoes**: tamanho do texto de 100% a 150%, duracao da janela
de clima, tela cheia e restauracao do visual padrao. As preferencias ficam no
navegador; o padrao e 100% e 15 segundos. Tela cheia exige toque e suporte do
navegador. O botao Microfone permanece acessivel quando a voz esta desligada.

## Comandos

Diga **hey Luna**, depois uma das frases:

- Abra as configuracoes.
- Mostre minhas memorias.
- Feche a janela.
- Volte para a tela principal.
- Role para baixo / role para cima.
- Aumente / diminua o tamanho do texto.
- Repita a resposta.
- Pare de falar.

Os comandos sao reconhecidos por correspondencia integral, antes do Ollama.
O servidor envia apenas acoes permitidas no evento `screen`; o cliente usa
uma lista fechada, nunca eval ou codigo recebido. Acoes visuais nao apagam a
resposta nem geram fala. Repetir usa a sintese existente; indisponibilidade e
informada na tela. Para interromper por voz durante a fala, a ativacao hey Luna
continua usando o barge-in existente; com microfone desligado, use o toque.

Ha apenas um painel aberto por vez: configuracoes, memoria ou previsao.
Fechar atua nesse painel. Rolar atua no painel aberto (lista, no caso de memoria)
ou no conteudo principal. Tela principal fecha os paineis e volta ao inicio do
conteudo. Confirmacoes de exclusao de memorias permanecem existentes.
As telas conectadas ao mesmo assistente recebem os mesmos eventos de voz.

## Verificacoes

`npm test` em client compila TypeScript e executa os testes de logica.
`python -m pytest -p no:cacheprovider` usa o Python da .venv.
Nao ha comando de lint dedicado configurado; o compilador verifica tipos e
simbolos nao utilizados. `git diff --check` verifica espacos nas alteracoes.

Para repetir a verificacao de navegador (Chrome instalado, sem backend real):

```powershell
npm install --prefix data/ui-check --no-save --package-lock=false --no-audit --no-fund playwright
node scripts/check_ui.cjs
```

O script usa um servidor HTTP temporario, WebSocket simulado e Chrome headless,
com toque emulado. Confere 360x800, 768x1024, 1024x768, 1366x768, 1920x1080 e
800x360; textos longos, paineis, rolagem, tamanho minimo dos controles,
persistencia/restauracao e erros JavaScript. Capturas ficam em data/ui-check.
Inclui viewport CSS equivalente ao zoom de 200% de 1366x768; isso nao e teste
do gesto fisico ou do menu de zoom do navegador. Os testes de comandos usam
transcricoes simuladas. Ainda e necessario validar microfone, alto-falante,
toque fisico e zoom do navegador no LG.
