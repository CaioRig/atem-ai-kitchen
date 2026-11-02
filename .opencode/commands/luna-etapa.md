
---
description: Executa uma etapa da implementação de modelos Ollama
agent: build
subagent: false
---

Execute exclusivamente a etapa $ARGUMENTS do projeto Luna.

## Instruções

1. Leia docs/model-selection-spec.md.
2. Leia docs/model-selection-progress.md.
3. Identifique os requisitos da etapa solicitada.
4. Analise somente os arquivos necessários.
5. Reutilize implementações existentes.
6. Implemente a etapa solicitada.
7. Execute verificações e testes relevantes.
8. Atualize model-selection-progress.md.

## Restrições

- Não execute outras etapas.
- Não reescreva start.bat desnecessariamente.
- Não altere funcionalidades não relacionadas.
- Não utilize subagentes.
- Não utilize APIs pagas.
- Não invente resultados ou arquivos.
- Solicite autorização antes de downloads reais.
- Preserve modelos e configurações manuais.

## Finalização

Informe brevemente:
- Arquivos modificados.
- Funcionalidades implementadas.
- Testes executados e resultados.
- Problemas encontrados.
- Próxima etapa.

Não avance automaticamente.

Economize tokens e execute as alterações diretamente.
