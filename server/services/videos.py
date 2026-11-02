"""Prepara pesquisa no site oficial, sem rede, chave, scraping ou credenciais."""
import re
from server.assistant.memory import strip_polite_prefix, strip_wake_prefix
from server.storage.memory import fold


def video_query(text: str) -> str | None:
    text = strip_polite_prefix(strip_wake_prefix(text)).strip(" .!?")
    match = re.fullmatch(
        r"(?:pesquise|busque|procure|mostre|encontre)(?: para mim)? "
        r"(?:um |o |uns )?(?:videos?|tutorial|tutoriais) (?:de |sobre |para )?"
        r"(.{2,160}?)(?: no youtube)?", fold(text)
    )
    return text[match.start(1):match.end(1)] if match else None


class VideoSearch:
    def search(self, query: str) -> dict:
        return {
            "type": "videos", "query": query[:160], "items": [],
            "message": "Toque em Pesquisar no YouTube. Os resultados e a seleção ficam no site oficial, em outra aba. "
                       "A Luna não lê essa lista nem controla a outra aba. Requer internet; feche a aba para voltar à Luna.",
        }
