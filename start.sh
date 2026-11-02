#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
for candidate in .venv/bin/python python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(not ((3,11) <= sys.version_info[:2] < (3,13)))' 2>/dev/null; then
        exec "$candidate" scripts/bootstrap.py "$@"
    fi
done
case " $* " in *" --check "*|*" --offline "*) echo "Python 3.11/3.12 ausente. Instale-o e execute ./start.sh novamente."; exit 1;; esac
echo "A Luna precisa de Python 3.11/3.12 e venv. Instalar pelo gerenciador do sistema?"
printf 'Autoriza essa alteração (poderá pedir sudo)? [s/N] '
read -r answer
case "$answer" in s|S|sim) ;; *) exit 1;; esac
if command -v apt-get >/dev/null 2>&1; then
    if [ "$(id -u)" = 0 ]; then
        apt-get update && apt-get install -y python3 python3-venv
    else
        sudo apt-get update && sudo apt-get install -y python3 python3-venv
    fi
elif command -v dnf >/dev/null 2>&1; then
    if [ "$(id -u)" = 0 ]; then dnf install -y python3.12; else sudo dnf install -y python3.12; fi
else
    echo "Instale Python 3.11/3.12 com venv usando o gerenciador da sua distribuição e execute novamente."
    exit 1
fi
for candidate in python3.12 python3.11 python3; do
    if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(not ((3,11) <= sys.version_info[:2] < (3,13)))'; then
        exec "$candidate" scripts/bootstrap.py "$@"
    fi
done
echo "A distribuição não forneceu Python 3.11/3.12. Instale uma dessas versões e execute novamente."
exit 1
