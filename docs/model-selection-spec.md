# Luna — Seleção automática de modelos Ollama

## Objetivo

Implemente no projeto Luna a detecção automática de hardware, seleção, download e validação de modelos de IA pelo `start.bat`.

**Regra principal:** preserve todas as funcionalidades atuais do `start.bat`. Reutilize código existente e faça apenas as alterações necessárias.

Execute a implementação, não apenas o planejamento.

## 1. Analisar o projeto

Antes de editar:

- Leia `start.bat` e os scripts relacionados.
- Localize a integração atual com Ollama e a seleção de modelos.
- Identifique configurações e testes existentes.
- Reutilize funcionalidades prontas.
- Não presuma nomes ou caminhos de arquivos.

Faça uma análise breve e prossiga com a implementação.

## 2. Detectar hardware

Implemente ou reutilize a detecção de:

- CPU, núcleos e threads.
- RAM total e disponível.
- GPU, VRAM e suporte à aceleração.
- Espaço livre em disco.
- Sistema operacional.

Considere também o consumo de memória do sistema, contexto e serviços da Luna.

Se algum dado não puder ser detectado, utilize valores conservadores.

## 3. Catálogo JSON de modelos

Crie ou adapte um catálogo JSON local contendo modelos Ollama adequados para conversação em português brasileiro.

Cada modelo deve possuir, quando disponível:

- ID e tag Ollama.
- Família, parâmetros e quantização.
- Tamanho do download.
- RAM e VRAM estimadas.
- Suporte a CPU/GPU.
- Contexto recomendado.
- Qualidade relativa e desempenho.
- Fonte oficial, licença e data de verificação.

Consulte documentação oficial e APIs públicas quando disponíveis.

Utilize as APIs Ollama `/api/tags`, `/api/show` e `/api/pull`.

Não invente requisitos ou compatibilidades. Se não conseguir verificar um dado, marque-o como desconhecido e use estimativas conservadoras.

O catálogo deve funcionar offline e permitir atualizações futuras.

## 4. Seleção automática

Implemente a seguinte lógica:

1. Detectar hardware.
2. Verificar modelos instalados.
3. Preservar modelos selecionados manualmente.
4. Reutilizar o último modelo validado, se ainda estiver adequado.
5. Filtrar modelos incompatíveis.
6. Priorizar qualidade, português brasileiro e estabilidade.
7. Preferir modelos adequados já instalados.
8. Se necessário, selecionar e baixar um candidato compatível.

Não utilize modelos base inadequados para conversação.

Não substitua configurações manuais válidas.

## 5. Download automático

Quando for necessário instalar um modelo:

- Verifique Ollama, internet e espaço livre.
- Confirme a disponibilidade e identificação do modelo.
- Informe modelo e tamanho aproximado.
- Baixe pela API oficial mostrando progresso.
- Trate interrupções e erros.
- Confirme a instalação.

Evite downloads repetidos, duplicados ou desnecessários.

Se estiver offline, utilize somente modelos locais previamente aprovados.

Se nenhum modelo adequado estiver disponível, informe o erro sem impedir desnecessariamente os demais serviços da Luna.

## 6. Validar qualidade das respostas

**Esta etapa é obrigatória.**

A Luna já apresentou respostas incoerentes com caracteres aleatórios, códigos e idiomas misturados.

Após selecionar um modelo novo, teste pela API Ollama usando o template e os parâmetros reais da aplicação.

Teste:

1. "Bom dia!" — saudação coerente em português.
2. "Quanto é 15 + 27?" — resultado 42.
3. "Qual é a capital do Brasil?" — Brasília.
4. "Responda apenas azul." — seguir a instrução.
5. "Como preparar ovos mexidos?" — explicação compreensível.
6. Duas mensagens consecutivas — preservar contexto.

Rejeite respostas manifestamente incoerentes, repetitivas ou inválidas.

Não considere HTTP 200 suficiente para aprovação.

Se falhar, teste outro candidato compatível, com limite de tentativas.

Preserve o último modelo aprovado. Nunca utilize um modelo reprovado como fallback.

## 7. Persistência

Salve configurações e resultados necessários para reutilização:

- Hardware detectado.
- Modelo selecionado.
- Origem da seleção: manual ou automática.
- Identificação do modelo.
- Parâmetros de inferência.
- Resultado e versão da validação.
- Versão do catálogo.

Nas próximas inicializações, execute apenas verificações rápidas.

Reavalie quando ocorrer alteração relevante no hardware, modelo ou configuração.

Fluxo inicial:

`start.bat → detectar → selecionar → baixar → validar → configurar → iniciar Luna`

Próximas execuções:

`start.bat → verificar configuração → iniciar Luna`

## 8. Testes

Crie testes para os cenários principais:

- Nenhum modelo instalado.
- Modelo adequado já instalado.
- Modelo incompatível.
- Computador sem GPU.
- Memória insuficiente.
- Sem internet.
- Falha no download.
- Falha na validação.
- Segunda inicialização.
- Modelo escolhido manualmente.

Simule os cenários que não puder reproduzir no hardware atual.

Não faça downloads reais nos testes automatizados. Use mocks quando adequado.

Não invente resultados de testes.

## 9. Integração e documentação

- Preserve a arquitetura existente.
- Use preferencialmente Python e bibliotecas já instaladas.
- Evite adicionar lógica complexa diretamente ao `.bat`.
- Mantenha compatibilidade com Windows e funcionamento offline.
- Não utilize APIs pagas.
- Não exclua modelos instalados.
- Não modifique configurações do usuário sem necessidade.
- Atualize o README com instalação, seleção, personalização e solução de problemas.

## 10. Método de execução

Implemente incrementalmente nesta ordem:

**Etapa A:** análise e detecção de hardware.

**Etapa B:** catálogo e seleção automática.

**Etapa C:** download, validação e persistência.

**Etapa D:** integração com start.bat, testes e documentação.

Em cada etapa:

1. Leia apenas os arquivos necessários.
2. Faça alterações pequenas.
3. Valide o código.
4. Corrija os erros identificados.
5. Preserve o funcionamento existente.

Evite refatorações não relacionadas.

Se o contexto ficar insuficiente, conclua a etapa atual e registre objetivamente o que falta, sem inventar resultados.

Não utilize subagentes ou serviços externos de IA.

## Entrega

Ao finalizar, informe brevemente:

- Arquivos criados ou modificados.
- Funcionalidades implementadas.
- Modelo selecionado no hardware atual, se realmente verificado.
- Testes executados e resultados.
- Pendências, se houver.
- Como iniciar a Luna.

**Prioridades:** funcionamento correto, preservação do projeto, qualidade das respostas e baixo consumo de recursos.

Respostas curtas. Não repita arquivos completos. Implemente diretamente no código.