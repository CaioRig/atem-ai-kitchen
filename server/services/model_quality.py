"""Objective Portuguese smoke tests and conservative corruption guards, entirely local.

Heuristics detect obvious failures; they are not a factual correctness oracle.
Only synthetic test conversations are persisted here, never household chat history.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path

log = logging.getLogger("jarvis.quality")
SCHEMA_VERSION = 1
SUITE_VERSION = 3
_LOCK = threading.RLock()
UNAVAILABLE = "A IA local não passou na validação de qualidade. Verifique o modelo ou tente novamente após corrigir a configuração."
CORRUPT = "Houve um problema na geração da resposta. Não vou reproduzir esse texto. Tente novamente."


def normalized(text: str) -> str:
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c)).split())


def response_problem(prompt: str, text: str, *, partial: bool = False) -> str:
    if "\ufffd" in text or any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        return "invalid_unicode"
    if not text.strip():
        return "" if partial else "empty"
    question, answer = normalized(prompt), normalized(text)
    technical = bool(re.search(r"\b(codigo|code|programa\w*|python|javascript|typescript|sql|html|css|api|json|bash|shell|powershell|script|terminal|unicode|token\w*|template|regex)\b", question))
    foreign_requested = technical or bool(re.search(r"traduz\w*|traduc\w*|translate|english|russian|japanese|chinese|idioma|ingles|chines|japones|russo|alemao|frances|espanhol", question))
    letters = [c for c in text if c.isalpha()]
    nonlatin = [c for c in letters if "LATIN" not in unicodedata.name(c, "")]
    prompt_nonlatin = any(c.isalpha() and "LATIN" not in unicodedata.name(c, "") for c in prompt)
    if not foreign_requested and not prompt_nonlatin and len(nonlatin) >= 6 and len(nonlatin) / max(1, len(letters)) > .15:
        return "unexpected_scripts"
    if not technical and re.search(r"<\|(?:im_start|im_end|endoftext)\|>|```|\b(?:def \w+\(|function \w+\(|console\.log\(|import numpy|#include\s*<)", text):
        return "unexpected_code_or_tokens"
    words = re.findall(r"\w+", answer)
    if not re.search(r"repita|repet\w*|tabuada|refr[aã]o", question) and len(words) >= 40:
        counts = Counter(words)
        triples = Counter(tuple(words[i:i + 3]) for i in range(len(words) - 2))
        if (len(counts) / len(words) < .18 or
                (triples and max(triples.values()) >= 8 and max(triples.values()) * 3 > len(words) * .45)):
            return "abnormal_repetition"
    greeting = bool(re.fullmatch(r"(?:oi|ola|bom dia|boa tarde|boa noite)[!?. ,]*(?:luna[!?. ,]*)?", question))
    if greeting and len(words) > 60:
        return "greeting_too_long"
    if greeting and not partial and not re.search(r"\b(oi|ola|bom dia|boa tarde|boa noite)\b|posso ajudar|como posso|tudo bem|seja bem|prazer|bem-vind|como vai", answer):
        return "greeting_ignored"
    if not foreign_requested and not prompt_nonlatin and len(words) >= 25:
        portuguese = {"o", "de", "do", "da", "e", "em", "um", "uma", "para", "com", "que", "voce", "nao", "por", "os", "as", "se", "no", "na", "ao"}
        english = {"the", "and", "with", "of", "to", "is", "are", "you", "your", "this", "that", "for", "it", "would"}
        if sum(w in english for w in words) >= 8 and sum(w in portuguese for w in words) <= 1:
            return "unexpected_language"
    if not partial and not letters and not re.search(r"\d", text):
        return "not_comprehensible"
    return ""


CASES = (
    ("greeting", ("Bom dia!",)),
    ("math", ("Quanto é 15 mais 27?",)),
    ("capital", ("Qual é a capital do Brasil?",)),
    ("context", ("Meu nome é Ana.", "Qual é meu nome?")),
    ("cooking", ("Como faço um ovo mexido?",)),
    ("instruction", ("Responda apenas com a palavra azul.",)),
    ("unknown", ("Qual é a senha que estou pensando?",)),
    ("language", ("Explique em uma frase o que é uma geladeira.",)),
)


def objective_problem(case: str, reply: str) -> str:
    text = normalized(reply)
    if case == "greeting":
        valid = len(text.split()) <= 60 and bool(re.search(r"bom dia|ola|\boi\b", text))
    elif case == "math":
        valid = bool(re.search(r"\b42\b|quarenta e dois", text)) and not (set(re.findall(r"\d+", text)) - {"15", "27", "42"})
    elif case == "capital":
        valid = "brasilia" in text and not re.search(r"nao (?:e |eh )?brasilia", text)
    elif case == "context":
        valid = bool(re.search(r"\bana\b", text))
    elif case == "cooking":
        valid = all(any(term in text for term in group) for group in
                    (("ovo",), ("frigideira", "panela"), ("mex", "mistur", "bat"), ("fogo", "aquec", "cozinh", "frit")))
    elif case == "instruction":
        valid = text.strip(".!\"' ") == "azul"
    elif case == "unknown":
        valid = bool(re.search(r"nao (?:sei|conheco|(?:posso|consigo|tenho como)(?: \w+){0,5}? (?:saber|adivinhar|descobrir|ler)|"
                               r"tenho acesso|possuo acesso|(?:tenho|possuo)(?: \w+){0,3} informaco?e?s?)|"
                               r"nao (?:e|seria) possivel (?:saber|adivinhar)|nao (?:ha|tem) como (?:saber|adivinhar)|"
                               r"(?:so|somente) voce (?:pode )?sab", text))
        valid = valid and not re.search(r"\d{3,}", text)
    else:
        valid = (len(text.split()) <= 65 and len(re.findall(r"[.!?](?:\s|$)", text)) <= 1
                 and any(t in text for t in ("geladeira", "refrigerador"))
                 and any(t in text for t in ("alimento", "comida", "bebida", "produto"))
                 and any(t in text for t in ("frio", "resfri", "refrig", "conserv", "temperatura")))
    return "" if valid else "objective_" + case


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def local_signature(client) -> str:
    return _hash({"name": client.name, "host": client.address, "system": client.system_prompt,
                  "options": client.options(), "suite": SUITE_VERSION,
                  "recipe_route": recipe_route(client, "Como faço um ovo mexido?")})


def recipe_route(client, question: str) -> tuple[str, int | None]:
    if not client.recipe_policy:
        return "", None
    from server.assistant.recipes import RecipeAssistant
    recipes = RecipeAssistant(None, max_tokens=client.recipe_policy["max_tokens"])
    return recipes.context_for(question), recipes.max_tokens_for(question)


class QualityRegistry:
    def __init__(self, path: Path):
        self.path = path

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if (data.get("schema_version") == SCHEMA_VERSION and isinstance(data.get("reports"), dict)
                    and isinstance(data.get("last_good"), list)):
                data["last_good"] = [n for n in data["last_good"] if isinstance(n, str)]
                if not isinstance(data.get("selected"), dict):
                    data.pop("selected", None)
                return data
        except (OSError, ValueError, AttributeError):
            pass
        return {"schema_version": SCHEMA_VERSION, "reports": {}, "last_good": []}

    def write(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + f".{time.time_ns()}.tmp")
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(self.path)

    def metadata(self, client) -> dict:
        tags = client.list_models()
        tag = next((t for t in tags if t.get("name") in (client.name, client.name + ":latest")), None)
        if tag is None:
            raise ValueError("model_missing")
        show = client.request_json("/api/show", {"model": client.name})
        version = client.request_json("/api/version").get("version")
        template = show.get("template") or ""
        details = show.get("details") or {}
        finetune = str((show.get("model_info") or {}).get("general.finetune", ""))
        if (not tag.get("digest") or details.get("format") != "gguf" or not details.get("quantization_level")
                or not template.strip() or not (".Messages" in template or ".Prompt" in template)
                or finetune.lower() in ("base", "pretrain", "pretrained")
                or re.search(r"(?:^|[:/_-])(?:base|pretrain)(?:$|[:/_-])", client.name.lower())):
            raise ValueError("not_supported_chat_model")
        return {"model": client.name, "digest": tag["digest"], "template_sha256": _hash(template),
                "model_parameters_sha256": _hash(show.get("parameters")), "details": details,
                "ollama_version": version, "signature": local_signature(client), "suite": SUITE_VERSION,
                "backend": client.backend_identity, "finetune": finetune}

    def ensure(self, client, *, force: bool = False, allow_tests: bool = True) -> dict:
        from server.services.llm import LLMError
        with _LOCK:
            identity = self.metadata(client)
            key = _hash(identity)
            data = self.read()
            cached = data["reports"].get(key)
            if not isinstance(cached, dict) or cached.get("identity") != identity or not isinstance(cached.get("approved"), bool):
                cached = None
            if cached and cached["approved"]:
                tests = cached.get("tests", [])
                if (not isinstance(tests, list) or len(tests) != len(CASES)
                        or any(not isinstance(r, dict) or r.get("passed") is not True or not isinstance(r.get("case"), str) for r in tests)
                        or {r.get("case") for r in tests} != {case for case, _ in CASES}):
                    cached = None
            if cached and not force:
                if cached["approved"]:
                    client.approval_error = ""
                    client.quality_registry = self.path
                    client.quality_signature = local_signature(client)
                    client.quality_key = key
                    if data.get("selected") != {"model": client.name, "key": key}:
                        data["selected"] = {"model": client.name, "key": key}
                        self.write(data)
                return cached
            if not allow_tests:
                return {"approved": False, "reason": "no_matching_approval", "model": client.name}
            rows = []
            started = time.perf_counter()
            for case, questions in CASES:
                probe = client.clone()  # isolated synthetic history, same system/options/template
                answers = []
                t0 = time.perf_counter()
                error = ""
                try:
                    for question in questions:
                        context, maximum = recipe_route(probe, question)
                        answer = probe.reply(question, context=context, max_tokens=maximum)
                        answers.append(answer)
                        problem = response_problem(question, answer)
                        if problem:
                            raise LLMError("corrupt_response", CORRUPT, problem)
                    error = objective_problem(case, answers[-1])
                except (LLMError, ValueError) as exc:
                    error = getattr(exc, "code", type(exc).__name__)
                rows.append({"case": case, "passed": not error, "reason": error, "answers": answers,
                             "total_s": time.perf_counter() - t0, "metrics": probe.last_metrics})
            resources = {"ollama_loaded_size_bytes": None, "ollama_size_vram_bytes": None}
            try:
                running = client.request_json("/api/ps").get("models", [])
                current = next((m for m in running if m.get("name") in (client.name, client.name + ":latest")), {})
                resources.update(ollama_loaded_size_bytes=current.get("size"), ollama_size_vram_bytes=current.get("size_vram"))
            except (RuntimeError, ValueError, OSError):
                pass
            report = {"model": client.name, "approved": all(r["passed"] for r in rows), "identity": identity,
                      "tests": rows, "total_s": time.perf_counter() - started, "checked_at": time.time()}
            report["resources"] = resources
            data["reports"][key] = report
            if report["approved"]:
                data["last_good"] = [client.name] + [n for n in data["last_good"] if n != client.name][:4]
                data["selected"] = {"model": client.name, "key": key}
                client.approval_error = ""
                client.quality_registry, client.quality_key = self.path, key
                client.quality_signature = local_signature(client)
            self.write(data)
            log.info("qualidade: modelo=%s aprovado=%s testes=%d/8", client.name, report["approved"], sum(r["passed"] for r in rows))
            return report

    def approve_or_fallback(self, client, alternatives=(), *, force=False, allow_new=True, compatible_names=None) -> bool:
        original = client.name
        names = list(dict.fromkeys([original, *self.read()["last_good"], *alternatives]))
        if compatible_names is not None:
            names = [n for n in names if n == original or n in compatible_names]
        names = names[:3]
        for name in names:
            candidate = client.clone(name=name)
            try:
                report = self.ensure(candidate, force=force, allow_tests=allow_new or name == original or name in self.read()["last_good"])
            except (RuntimeError, ValueError, OSError) as exc:
                reason = getattr(exc, "code", str(exc) if str(exc) in ("model_missing", "not_supported_chat_model") else type(exc).__name__)
                log.warning("qualidade: modelo=%s falha=%s", name, reason)
                with _LOCK:
                    data = self.read()
                    data["last_failure"] = {"model": name, "reason": reason, "at": time.time()}
                    try:
                        self.write(data)
                    except OSError:
                        log.warning("não foi possível persistir a falha de validação")
                continue
            if report["approved"]:
                client.adopt(candidate)
                if name != original:
                    client.recovery_notice = f"O modelo {original} falhou na validação; usando {name}, previamente validado. A preferência manual foi preservada."
                return True
        client.approval_error = UNAVAILABLE
        return False

    def invalidate(self, client, reason: str) -> None:
        with _LOCK:
            data = self.read()
            report = data["reports"].get(client.quality_key)
            if report:
                report["approved"] = False
                report["runtime_failure"] = reason
                if data.get("selected", {}).get("key") == client.quality_key:
                    data.pop("selected", None)
                self.write(data)

    def runtime_alternative(self, client):
        for name in self.read()["last_good"]:
            if name == client.name:
                continue
            candidate = client.clone(name=name)
            try:
                if self.ensure(candidate, allow_tests=False)["approved"]:
                    candidate._history = list(client._history)
                    return candidate
            except (RuntimeError, ValueError, OSError):
                continue
        return None


def configured_client(settings, hardware=None):
    from server.services.llm import OllamaClient
    from server.services.performance import PerformancePolicy, hardware_key
    client = OllamaClient(settings.llm_url, settings.llm_model, system_prompt=settings.llm_system_prompt,
        connect_timeout_s=settings.llm_connect_timeout_s, timeout_s=settings.llm_timeout_s,
        keep_alive=settings.llm_keep_alive, temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens, top_p=settings.llm_top_p, repeat_penalty=settings.llm_repeat_penalty)
    policy = PerformancePolicy(settings, hardware)
    if policy.local:
        client.num_ctx, client.num_thread = policy.context, policy.threads
        client.backend_identity = hardware_key(policy.hardware)
        if policy.profile == "economic":
            client.keep_alive = "2m"
    client.recipe_policy = {"max_tokens": settings.recipes_max_tokens} if settings.recipes_enabled else None
    return client
