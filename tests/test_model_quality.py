from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace

import pytest

from server.services.llm import LLMError, OllamaClient
from server.services.model_quality import QualityRegistry, response_problem, objective_problem, CASES, CORRUPT


@pytest.mark.parametrize("prompt,answer,reason", [
    ("Bom dia", "Olá \ufffd", "invalid_unicode"),
    ("Bom dia", "中文日本語日本語中文随机字符случайныйтекст", "unexpected_scripts"),
    ("Bom dia", "Bom dia " * 35, "abnormal_repetition"),
    ("Bom dia", "Bom dia. " + " ".join(f"palavra{i}" for i in range(65)), "greeting_too_long"),
    ("Bom dia", "```python\nprint('oi')\n```", "unexpected_code_or_tokens"),
    ("Bom dia", "<|im_start|>assistant", "unexpected_code_or_tokens"),
    ("teste", "!!!??", "not_comprehensible"),
])
def test_obvious_corruption_is_rejected(prompt, answer, reason):
    assert response_problem(prompt, answer) == reason


@pytest.mark.parametrize("prompt,answer", [
    ("Mostre código Python", "```python\nprint('olá')\n```"),
    ("Traduza para japonês", "こんにちは。お元気ですか？"),
    ("Answer in English", "The refrigerator keeps food cold and helps preserve fresh ingredients safely."),
    ("Repita bom dia vinte vezes", "Bom dia " * 20),
    ("Explique esta API em JSON", '{"message": "Hello World"}'),
    ("Como preparo uma receita detalhada?", "Misture os ingredientes com cuidado e cozinhe em fogo baixo. " * 5),
    ("oi", "Claro! Posso ajudar."),
])
def test_legitimate_foreign_code_and_long_requests_are_preserved(prompt, answer):
    assert response_problem(prompt, answer) == ""


@pytest.mark.parametrize("case,answer", [("math", "43"), ("capital", "Rio de Janeiro"),
    ("context", "Não sei seu nome"), ("instruction", "vermelho"), ("unknown", "Sua senha é 1234"),
    ("language", "It is cold."), ("cooking", "Misture algo e coma."),
    ("unknown", "Não compartilhe senhas reais comigo, talvez possa me dar pistas.")])
def test_objective_failures_are_not_http_success(case, answer):
    assert objective_problem(case, answer)


def test_unknown_accepts_natural_refusal_without_accepting_requests_for_password_hints():
    assert not objective_problem("unknown", "Não posso ajudar a adivinhar senhas ou outras informações pessoais.")
    assert not objective_problem("unknown", "Não tenho como adivinhar ou saber a senha que você está pensando.")
    assert not objective_problem("unknown", "Não tenho essa informação.")
    assert objective_problem("unknown", "Não compartilhe senhas reais comigo; talvez possa me dar pistas.")


ANSWERS = {
    "Bom dia!": "Bom dia! Como posso ajudar?",
    "Quanto é 15 + 27?": "42.",
    "Qual é a capital do Brasil?": "A capital do Brasil é Brasília.",
    "Meu nome é Ana.": "Olá, Ana!",
    "Qual é meu nome?": "Seu nome é Ana.",
    "Como preparar ovos mexidos?": "Bata os ovos, aqueça uma frigideira e mexa em fogo baixo até cozinhar.",
    "Responda apenas com a palavra azul.": "azul",
    "Qual é a senha que estou pensando?": "Não tenho como saber sua senha.",
    "Explique em uma frase o que é uma geladeira.": "A geladeira conserva alimentos em baixa temperatura.",
}


class TestModel(OllamaClient):
    __test__ = False
    def __init__(self, environment, name="good:7b"):
        super().__init__("http://localhost:11434", name)
        self.environment = environment

    def list_models(self):
        return [{"name": name, "digest": data.get("digest", name)} for name, data in self.environment["models"].items()]

    def request_json(self, path, body=None):
        if path == "/api/version":
            return {"version": self.environment.get("version", "1")}
        return {"template": self.environment.get("template", "{{ .Messages }}{{ .Role }}"),
                "details": {"format": "gguf", "quantization_level": "Q4_K_M"},
                "model_info": {"general.finetune": self.environment.get("finetune", "Instruct")}}

    def clone(self, name=None):
        other = TestModel(self.environment, name or self.name)
        other.num_ctx, other.num_thread = self.num_ctx, self.num_thread
        other.system_prompt = self.system_prompt
        other.temperature = self.temperature
        other.backend_identity = self.backend_identity
        other.recipe_policy = self.recipe_policy
        return other

    def _reply_once(self, text, on_delta=None, cancel=None, context="", max_tokens=None):
        self.environment["calls"].append((self.name, text))
        self.environment.setdefault("route_calls", []).append((text, context, max_tokens))
        spec = self.environment["models"][self.name]
        answer = spec.get("bad") or ANSWERS.get(text, "Uma resposta coerente em português.")
        problem = response_problem(text, answer)
        if problem:
            raise LLMError("corrupt_response", CORRUPT, problem)
        if on_delta:
            on_delta(answer)
        return answer


def environment():
    return {"models": {"good:7b": {}, "candidate:3b": {}}, "calls": []}


def test_cache_uses_all_eight_cases_and_revalidates_real_changes(tmp_path):
    env = environment()
    client = TestModel(env)
    registry = QualityRegistry(tmp_path / "quality.json")
    first = registry.ensure(client)
    assert first["approved"] and len(first["tests"]) == 8
    assert len(env["calls"]) == 9  # two actual turns in the context case
    assert registry.ensure(client)["approved"]
    assert len(env["calls"]) == 9
    env["template"] = "{{ .Messages }}changed {{ .Role }}"
    assert registry.ensure(client)["approved"]
    assert len(env["calls"]) == 18
    client.num_ctx = 8192
    assert registry.ensure(client)["approved"]
    assert len(env["calls"]) == 27
    env["models"][client.name]["digest"] = "new-weights"
    assert registry.ensure(client)["approved"]
    assert len(env["calls"]) == 36


def test_rejected_candidate_does_not_replace_persisted_approval(tmp_path):
    env = environment()
    registry = QualityRegistry(tmp_path / "quality.json")
    registry.ensure(TestModel(env))
    selected = registry.read()["selected"]
    env["models"]["candidate:3b"]["bad"] = "随机字符中文日本語日本語русскийтекст"
    rejected = registry.ensure(TestModel(env, "candidate:3b"))
    assert not rejected["approved"]
    assert registry.read()["selected"] == selected
    candidate = TestModel(env, "candidate:3b")
    assert registry.approve_or_fallback(candidate)
    assert candidate.name == "good:7b"
    assert candidate.recovery_notice
    # A new instance after restart reuses the persisted approval.
    assert registry.ensure(TestModel(env))["approved"]


def test_runtime_recovers_once_using_a_matching_previously_approved_model(tmp_path):
    env = environment()
    registry = QualityRegistry(tmp_path / "quality.json")
    registry.ensure(TestModel(env))
    candidate = TestModel(env, "candidate:3b")
    registry.ensure(candidate)
    before = len(env["calls"])
    env["models"]["candidate:3b"]["bad"] = "随机字符中文日本語日本語русскийтекст"
    assert candidate.reply("Bom dia!") == ANSWERS["Bom dia!"]
    assert candidate.name == "good:7b"
    assert len(env["calls"]) == before + 2  # no suite during recovery
    assert registry.read()["selected"]["model"] == "good:7b"


def test_quality_cooking_uses_the_application_recipe_route(tmp_path):
    env = environment()
    client = TestModel(env)
    client.recipe_policy = {"max_tokens": 600}
    assert QualityRegistry(tmp_path / "quality.json").ensure(client)["approved"]
    cooking = next(c for c in env["route_calls"] if c[0] == "Como preparar ovos mexidos?")
    assert cooking[2] == 600 and "Pedido sobre receita" in cooking[1]


def test_no_approved_recovery_is_explicit_and_not_retried_in_a_loop(tmp_path):
    env = environment()
    registry = QualityRegistry(tmp_path / "quality.json")
    candidate = TestModel(env)
    registry.ensure(candidate)
    env["models"]["good:7b"]["bad"] = "随机字符中文日本語日本語русскийтекст"
    with pytest.raises(LLMError, match="problema"):
        candidate.reply("Bom dia!")
    count = len(env["calls"])
    with pytest.raises(LLMError, match="validação"):
        candidate.reply("Bom dia!")
    assert len(env["calls"]) == count


def test_base_model_is_not_automatically_approved(tmp_path):
    env = {"models": {"qwen-base:7b": {}}, "calls": []}
    with pytest.raises(ValueError, match="not_supported_chat_model"):
        QualityRegistry(tmp_path / "quality.json").ensure(TestModel(env, "qwen-base:7b"))
    assert env["calls"] == []


def test_base_weights_are_rejected_even_under_an_arbitrary_model_name(tmp_path):
    env = environment()
    env["finetune"] = "Base"
    with pytest.raises(ValueError, match="not_supported_chat_model"):
        QualityRegistry(tmp_path / "quality.json").ensure(TestModel(env))
    assert env["calls"] == []


def test_cache_needs_complete_test_evidence_not_just_an_approval_flag(tmp_path):
    env = environment()
    registry = QualityRegistry(tmp_path / "quality.json")
    client = TestModel(env)
    registry.ensure(client)
    data = registry.read()
    data["reports"][client.quality_key]["tests"] = None
    registry.write(data)
    assert registry.ensure(client)["approved"]
    assert len(env["calls"]) == 18


def test_cached_only_startup_never_runs_new_suite_and_rejected_is_not_fallback(tmp_path):
    env = environment()
    registry = QualityRegistry(tmp_path / "quality.json")
    client = TestModel(env)
    assert not registry.approve_or_fallback(client, compatible_names=[], cached_only=True)
    assert not env["calls"]
    assert registry.ensure(TestModel(env))["approved"]
    before = len(env["calls"])
    assert registry.approve_or_fallback(TestModel(env), cached_only=True)
    assert len(env["calls"]) == before
    env["models"]["good:7b"]["digest"] = "changed"
    assert not registry.approve_or_fallback(TestModel(env), compatible_names=[], cached_only=True)
    assert len(env["calls"]) == before


def test_corrupted_generation_never_reaches_tts_or_final_ui():
    from server.assistant.conversation import Conversation
    from server.assistant.state import StateMachine, AssistantState
    from tests.test_conversation import FakeLLM
    async def run():
        spoken, events, errors = [], [], []
        class Speaker:
            async def speak(self, text):
                spoken.append(text)
                return False
        async def reply(text, final):
            events.append((text, final))
        async def error(code, message):
            errors.append(code)
        conv = Conversation(StateMachine(AssistantState.IDLE), FakeLLM(pieces=["随机字符中文日本語日本語русскийтекст"]),
                            on_reply=reply, on_error=error, speaker=Speaker(), done_hold_s=.01)
        await conv.ask("Bom dia!")
        for _ in range(200):
            if not conv.busy:
                break
            await asyncio.sleep(.005)
        assert spoken == [] and events == [] and errors == ["corrupt_response"]
    asyncio.run(run())
