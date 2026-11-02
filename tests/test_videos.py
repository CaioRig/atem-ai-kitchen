import pytest
from server.services.videos import VideoSearch, video_query
from server.assistant.conversation import Conversation
from server.assistant.state import StateMachine, AssistantState
from tests.test_conversation import FakeLLM, settle

@pytest.fixture
def anyio_backend(): return "asyncio"

def test_explicit_query_only():
    assert video_query("Luna, pesquise um vídeo de receita de lasanha.") == "receita de lasanha"
    assert video_query("Luna, mostre um vídeo de como fazer pão") == "como fazer pão"
    assert video_query("Luna, procure vídeos de receitas de lasanha no YouTube.") == "receitas de lasanha"
    assert video_query("Luna, encontre um tutorial de como fazer pão.") == "como fazer pão"
    for text in ("me ensine uma receita de pão", "não pesquise um vídeo de pão", "lembre que gosto de vídeos", "abra o primeiro vídeo"):
        assert video_query(text) is None

def test_search_needs_no_network_or_credentials(monkeypatch):
    import socket
    def fail(*args, **kwargs): raise AssertionError("network not allowed")
    monkeypatch.setattr(socket, "create_connection", fail)
    event = VideoSearch().search("receita de pão")
    assert event["query"] == "receita de pão"
    assert event["items"] == [] and "site oficial" in event["message"]

@pytest.mark.anyio
async def test_video_request_bypasses_llm():
    events=[]
    async def emit(event): events.append(event)
    llm=FakeLLM()
    conv=Conversation(StateMachine(AssistantState.IDLE), llm, on_screen=emit, videos=VideoSearch())
    await conv.ask("Luna, pesquise um vídeo de receita de lasanha")
    await settle(conv)
    assert not llm.calls and events[-1]["type"] == "videos"
    assert conv.machine.state == AssistantState.IDLE
