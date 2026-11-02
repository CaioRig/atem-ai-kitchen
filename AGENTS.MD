
# Atem — Instruções do agente

## Idioma e interpretação
- Responda sempre em português brasileiro.
- Interprete solicitações considerando o contexto do projeto.
- Antes de implementar, identifique o objetivo e os critérios de sucesso.
- Quando houver ambiguidade importante, faça uma pergunta objetiva.
- Não invente caminhos, funções, APIs ou resultados.
- Não confunda uma instrução para analisar com autorização para editar.

## Conhecimento do projeto
- Examine a estrutura antes de assumir onde estão os arquivos.
- Consulte requirements.txt para identificar dependências.
- Respeite os padrões e a arquitetura existentes.
- Não introduza frameworks ou bibliotecas desnecessárias.
- Verifique quais comandos funcionam no Windows e no Linux.

## Desenvolvimento
- Prefira alterações pequenas e direcionadas.
- Use a ferramenta read antes de editar arquivos existentes.
- Use edit para alterações pontuais.
- Evite sobrescrever arquivos inteiros sem necessidade.
- Preserve interfaces públicas e compatibilidade.
- Não altere código fora do escopo solicitado.

## Fluxo de trabalho
1. Localize os arquivos relacionados.
2. Entenda o código existente.
3. Resuma o que precisa ser feito em até 3 frases.
4. Implemente a alteração solicitada.
5. Execute verificações relevantes, quando disponíveis.
6. Confira o diff.
7. Resuma os resultados.

## Qualidade
- Prefira código simples e legível.
- Use tipagem Python quando apropriado.
- Preserve o tratamento de erros existente.
- Crie ou atualize testes quando necessário.
- Não afirme que algo funciona sem verificar.

## Segurança
- Não altere .env, credenciais ou arquivos de banco de dados.
- Não execute git push ou deploy.
- Não apague arquivos sem autorização.
- Não instale dependências sem aprovação.
- Não execute comandos destrutivos sem aprovação.

## Comunicação
- Seja objetivo.
- Evite repetir código extenso na resposta.
- Informe arquivos alterados.
- Informe testes executados e seus resultados.
- Se não conseguir concluir, explique o bloqueio.