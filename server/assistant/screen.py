"""Comandos de interface: correspondencia integral, sem IA ou codigo arbitrario."""
from server.assistant.memory import strip_polite_prefix, strip_wake_prefix
from server.storage.memory import fold

COMMANDS = {
    "settings": ("abra as configuracoes", "abrir configuracoes", "mostre as configuracoes"),
    "memory": ("mostre minhas memorias", "abra minhas memorias", "abra a memoria"),
    "close": ("feche a janela", "fechar janela", "feche o painel"),
    "home": ("volte para a tela principal", "voltar para a tela principal", "tela principal"),
    "down": ("role para baixo", "rolar para baixo"),
    "up": ("role para cima", "rolar para cima"),
    "larger": ("aumente o tamanho do texto", "aumente a letra"),
    "smaller": ("diminua o tamanho do texto", "diminua a letra"),
    "replay": ("repita a resposta", "repita a ultima resposta"),
    "stop": ("pare de falar", "parar de falar", "cancele", "cancelar"),
}


def parse_screen(text: str) -> str | None:
    text = fold(strip_polite_prefix(strip_wake_prefix(text))).strip(" .!?,;")
    return next((action for action, phrases in COMMANDS.items() if text in phrases), None)
