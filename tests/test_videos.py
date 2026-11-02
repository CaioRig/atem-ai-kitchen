import json
import pytest
from server.services.videos import VideoSearch, video_query
from server.assistant.conversation import Conversation
from server.assistant.state import StateMachine, AssistantState
from tests.test_conversation import FakeLLM, settle

@pytest.fixture
def anyio_backend(): return "asyncio"

def test_explicit_query_only():
    assert video_query("Luna, pesquise um vídeo de receita de lasanha.") == "receita de lasanha"
    assert video_query("Luna, mostre um vídeo de como fazer pão") == "como fazer pao"
    for text in ("me ensine uma receita de pão", "não pesquise um vídeo de pão", "lembre que gosto de vídeos", "abra o primeiro vídeo"):
        assert video_query(text) is None

def test_no_key_is_honest_and_validates_results():
    event = VideoSearch("").search("receita de lasanha")
    assert event["items"] == [] and "não está configurada" in event["message"]
    assert VideoSearch.items({"items": [{"id": {"videoId": "javascript:alert(1)"}, "snippet": {}}]}) == []
    assert VideoSearch.items({"items": [{"id": {"videoId": "abcdefghijk"}, "snippet": {"title": "Pão &amp; bolo", "channelTitle": "Cozinha"}}]})[0]["title"] == "Pão & bolo"

@pytest.mark.parametrize("status,body", [(200, {"items": []}), (403, {}), (200, None)])
def test_source_empty_failure_and_no_sensitive_context(monkeypatch, status, body):
    requests = []
    class Connection:
        def __init__(self, host, timeout): assert host == "www.googleapis.com" and timeout == 10
        def request(self, method, path, headers): requests.append((method,path,headers))
        def getresponse(self): return self
        def read(self, limit): return json.dumps(body).encode()
        def close(self): pass
    Connection.status = status
    monkeypatch.setattr("server.services.videos.http.client.HTTPSConnection", Connection)
    event = VideoSearch("test-only-key").search("pao")
    assert event["items"] == [] and event["message"]
    assert "test-only-key" not in requests[0][1]
    assert "q=pao" in requests[0][1] and "videoEmbeddable=true" in requests[0][1]

def test_offline(monkeypatch):
    def fail(*args, **kwargs): raise OSError("offline")
    monkeypatch.setattr("server.services.videos.http.client.HTTPSConnection.request", fail)
    assert "internet" in VideoSearch("test").search("pao")["message"]

@pytest.mark.anyio
async def test_video_request_bypasses_llm():
    events=[]
    async def emit(event): events.append(event)
    llm=FakeLLM()
    conv=Conversation(StateMachine(AssistantState.IDLE), llm, on_screen=emit, videos=VideoSearch(""))
    await conv.ask("Luna, pesquise um vídeo de receita de lasanha")
    await settle(conv)
    assert not llm.calls and events[-1]["type"] == "videos"
    assert conv.machine.state == AssistantState.IDLE
