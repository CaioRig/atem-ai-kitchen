# Instalação do servidor (Windows 10 Pro)

## Pré-requisitos já verificados nesta máquina
- Python 3.12 via gerenciador oficial (`py -3.12`).
- Node.js 22 (apenas para compilar a interface; não roda em produção).
- Driver NVIDIA 582.66 com CUDA funcional (verificado via `nvcuda.dll`).

> Armadilha: no Git Bash o gerenciador `py` instala em `<diretório atual>\Python`.
> Para instalar/atualizar interpretadores use o PowerShell ou execute a partir
> de `%LocalAppData%`. O projeto usa o interpretador em
> `%LocalAppData%\Python\pythoncore-3.12-64`.

## Passos
```powershell
cd E:\Atem
.\scripts\setup-server.ps1     # .venv + dependências + config/settings.toml com token + certificado TLS
.\scripts\build-client.ps1     # npm install + tsc
.\scripts\run-server.ps1       # http://192.168.1.6:8800/
```

Edite `config/settings.toml`:
- `bind_host`: IP da LAN do servidor (0.0.0.0 é rejeitado).
- `token`: gerado automaticamente; copie para a URL do quiosque como `#token=…`.
- `tls_certfile` / `tls_keyfile`: certificado e chave PEM gerados por
  `.\scripts\make-cert.ps1` em `config/certs/`. Obrigatórios para o microfone funcionar
  fora de `localhost` (o navegador exige HTTPS). Regere ao mudar `bind_host`.
  O navegador do quiosque mostra um aviso de certificado autoassinado na primeira visita;
  aceite uma vez ou importe `server.crt` como autoridade confiável. Veja `docs/microfone.md`.
- `debug_allow_state_override = true` apenas durante o desenvolvimento
  (habilita as teclas 1–9/0 na interface para simular estados).

## Firewall (apenas perfil Privado, apenas a porta do servidor)
```powershell
New-NetFirewallRule -DisplayName "Jarvis Kitchen" -Direction Inbound -Protocol TCP -LocalPort 8800 -Profile Private -Action Allow
```
Requer PowerShell como administrador. Não exponha a porta na internet.

## Verificação de saúde
`GET https://<ip>:8800/health` → `{"status":"ok","state":"idle",...}`

## Logs
`logs/server.log`, rotativo (5 × 2 MB). Nenhum token, áudio ou conversa é
registrado. O log de acesso HTTP está desativado.

## Testes
```powershell
.\.venv\Scripts\python -m pytest
cd client; npm test
```
