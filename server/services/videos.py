"""Busca opcional pela API oficial do YouTube. Nunca usa o modelo ou dados pessoais."""
import html
import http.client
import json
import os
import re
from urllib.parse import urlencode

from server.assistant.memory import strip_polite_prefix, strip_wake_prefix
from server.storage.memory import fold


def video_query(text: str) -> str | None:
    text = fold(strip_polite_prefix(strip_wake_prefix(text))).strip(" .!?")
    match = re.fullmatch(r"(?:pesquise|busque|procure|mostre|encontre)(?: para mim)? (?:um |o |uns )?videos? (?:de |sobre |para )?(.{2,160})", text)
    return match.group(1) if match else None


class VideoSearch:
    def __init__(self, key: str | None = None):
        self.key = os.environ.get("LUNA_YOUTUBE_API_KEY", "") if key is None else key

    def search(self, query: str) -> dict:
        event = {"type": "videos", "query": query[:160], "items": [], "message": ""}
        if not self.key:
            event["message"] = "A busca integrada de vídeos não está configurada. Toque em Pesquisar no YouTube para procurar no site."
            return event
        connection = http.client.HTTPSConnection("www.googleapis.com", timeout=10)
        try:
            params = urlencode({"part": "snippet", "type": "video", "q": query[:160],
                                "maxResults": 3, "videoEmbeddable": "true", "relevanceLanguage": "pt", "safeSearch": "moderate"})
            connection.request("GET", "/youtube/v3/search?" + params, headers={"X-Goog-Api-Key": self.key})
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError("source unavailable")
            body = response.read(262145)
            if len(body) > 262144:
                raise ValueError("response too large")
            event["items"] = self.items(json.loads(body))
            if not event["items"]:
                event["message"] = "Não encontrei vídeos disponíveis para incorporar. Você pode pesquisar no YouTube."
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
            event["message"] = "Não consegui consultar os vídeos. Verifique a internet ou tente mais tarde; a fonte pode estar indisponível ou sem cota."
        finally:
            connection.close()
        return event

    @staticmethod
    def items(data: dict) -> list[dict]:
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("invalid response")
        result = []
        for item in data["items"][:3]:
            if not isinstance(item, dict):
                continue
            ident, snippet = item.get("id"), item.get("snippet")
            if not isinstance(ident, dict) or not isinstance(snippet, dict):
                continue
            video_id = ident.get("videoId", "")
            if not isinstance(video_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{11}", video_id):
                continue
            result.append({"id": video_id, "title": html.unescape(str(snippet.get("title", "Vídeo")))[:200],
                           "source": html.unescape(str(snippet.get("channelTitle", "YouTube")))[:100]})
        return result
