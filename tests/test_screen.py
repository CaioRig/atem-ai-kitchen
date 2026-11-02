import pytest
from server.assistant.screen import COMMANDS, parse_screen
from server.assistant.conversation import Conversation
from server.assistant.state import StateMachine, AssistantState
from tests.test_conversation import FakeLLM

@pytest.fixture
def anyio_backend():
    return "asyncio"

def test_exact_commands_and_no_collisions():
    for action, phrases in COMMANDS.items():
        for phrase in phrases:
            assert parse_screen(f"Luna, {phrase}.") == action
    for question in ("lembre que aumente a letra", "como faço arroz?", "como estará o clima amanhã?",
                     "não feche a janela", "o que você lembra sobre minha cozinha?", "repita a resposta e apague tudo"):
        assert parse_screen(question) is None

@pytest.mark.anyio
async def test_screen_commands_never_call_llm():
    llm = FakeLLM()
    events = []
    async def send(event): events.append(event)
    machine = StateMachine(AssistantState.TRANSCRIBING)
    conv = Conversation(machine, llm, on_screen=send)
    assert await conv.ask("Luna, abra as configurações", prompt_id=1)
    assert events == [{"type": "screen", "action": "settings"}]
    assert machine.state == AssistantState.IDLE
    assert not await conv.ask("Luna, abra as configurações", prompt_id=1)
    await conv.ask("Luna, repita a resposta")
    assert events[-1]["action"] == "notice" and "Não há" in events[-1]["message"]
    assert llm.calls == []

@pytest.mark.anyio
async def test_stop_has_priority_over_busy_response():
    events = []
    async def send(event): events.append(event)
    conv = Conversation(StateMachine(AssistantState.IDLE), FakeLLM(delay_s=1), on_screen=send)
    await conv.ask("como faço arroz?")
    assert conv.busy
    await conv.ask("pare de falar")
    assert not conv.busy and events[-1]["action"] == "stop"


@pytest.mark.anyio
async def test_music_commands_work_without_llm():
    from server.services.videos import VideoSearch
    from tests.test_conversation import settle
    events = []
    async def send(event): events.append(event)
    conv = Conversation(StateMachine(AssistantState.IDLE), None, on_screen=send, videos=VideoSearch())
    assert await conv.ask("Quero ouvir música.")
    await settle(conv)
    assert events[-1]["type"] == "videos" and events[-1]["query"] == "música"
    assert await conv.ask("Pare a música.")
    assert events[-1] == {"type": "screen", "action": "pause_video"}
