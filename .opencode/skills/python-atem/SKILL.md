
---
name: python-atem
description: Use para implementar, refatorar e validar código Python, FastAPI, serviços e integrações do projeto Atem.
---

# Desenvolvimento Python no Atem

## Procedimento
1. Localize o código relevante usando glob e grep.
2. Leia os arquivos antes de modificar.
3. Identifique interfaces e dependências envolvidas.
4. Implemente a menor alteração que resolva a tarefa.
5. Preserve a arquitetura e o comportamento existente.
6. Verifique o resultado e revise o diff.

## Convenções
- Prefira código Python simples e tipado.
- Use funções pequenas com responsabilidades claras.
- Preserve rotas e contratos do FastAPI.
- Evite dependências novas sem necessidade.
- Não altere módulos de áudio ou IA sem relação com a tarefa.

## Validação
- Identifique o ambiente Python correto.
- Execute testes relevantes com pytest, se disponível.
- Use ferramentas de lint existentes no projeto.
- Não afirme que testes passaram sem executá-los.

## Resposta
Informe o que mudou, quais arquivos foram afetados
e quais verificações foram executadas.
  