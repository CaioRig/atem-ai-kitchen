# Operacao e validacao na cozinha

## Preparacao do servidor

No PowerShell, em E:\Atem:

```powershell
cd client
npm ci --no-audit --no-fund
npm test
cd ..
.\.venv\Scripts\python -m pytest -p no:cacheprovider
.\.venv\Scripts\python scripts\check_llm.py
.\scripts\run-server.ps1
```

Ollama deve estar instalado, com o modelo configurado disponivel e inicializacao no logon habilitada.
Confira o endereco e os certificados em config/settings.toml. Nao compartilhe o token.
Logs rotativos ja existem em logs/server.log; /health informa o estado dos componentes.

## Inicializacao automatica (Windows)

Depois de validar a execucao manual:

```powershell
.\scripts\configure-autostart.ps1 -WhatIf
.\scripts\configure-autostart.ps1
Get-ScheduledTask -TaskName 'Luna Kitchen Server'
```

A tarefa inicia no logon do usuario atual, apos 30 segundos, sem privilegios elevados,
e tenta reiniciar ate tres vezes em caso de falha. Nao e um servico antes do logon.
Nao inicia imediatamente; encerre o servidor manual antes de testar a tarefa com
Start-ScheduledTask. Configure o Ollama para iniciar no mesmo logon.
Para remover: `.\scripts\configure-autostart.ps1 -Remove`.

## Navegador do computador da cozinha

Primeiro abra normalmente https://IP-DO-SERVIDOR:8800/#token=TOKEN.
Valide o certificado, permita o microfone e teste a reproducao de audio. O cliente
guarda o token no perfil do navegador e limpa o fragmento da URL.
No Windows, crie um atalho do Edge com argumentos
`--kiosk https://IP-DO-SERVIDOR:8800/ --edge-kiosk-type=fullscreen`
e teste se autenticacao e permissoes persistem nesse modo. Se nao persistirem,
use o perfil normal com tela cheia (F11). So depois coloque o atalho em shell:startup.
Nao inclua o token no atalho. Migracao para Lubuntu nao e requisito desta validacao.

## Aceite no LG (pendente de teste fisico)

- Reiniciar servidor e LG: interface abre e componentes ficam disponiveis.
- Dizer hey Luna dez vezes a distancia habitual; registrar acertos e falsos disparos.
- Perguntar algo e conferir transcricao, resposta e volume; testar interrupcao por hey Luna e Cancelar.
- Guardar uma preferencia de teste, reiniciar o servidor e consultar; apagar somente a memoria criada.
- Registrar ingredientes de teste, consultar, descontar e conferir quantidades.
- Pedir receita e adaptar porcoes; verificar texto completo e resumo falado.
- Consultar clima online; sem internet, mantendo a LAN, conferir cache e aviso de idade.
- Desconectar e reconectar a rede do cliente; conferir reconexao e retorno do microfone.
- Usar por pelo menos 30 minutos com ruido normal da cozinha e registrar latencias e erros.

Nao apague todas as memorias reais para testar. Use um banco temporario para esse cenario.
Testes automatizados nao substituem estes testes de microfone, alto-falante e rede fisicos.
