"""Assistente de receitas (Fase 7): orientação para a IA local, estoque no contexto e fala resumida.

Nada aqui chama a IA nem acessa a rede: a geração continua sendo do Ollama local, pela
`Conversation`. Este módulo decide, por regras determinísticas em pt-BR:

- quando uma pergunta é sobre receita (sugerir pratos a partir de ingredientes, ensinar o preparo,
  adaptar porções, substituir ingredientes) e, nesse caso, qual orientação extra vai junto com a
  instrução de sistema (formato fácil de acompanhar, porções, substituições, segurança alimentar,
  respeito às preferências guardadas);
- o estoque registrado (`IngredientCommands`) a incluir no contexto, separado do que a pessoa só
  citou na pergunta, com a regra de não inventar quantidades;
- a última receita respondida, guardada só em memória e por pouco tempo, para "adapte essa receita
  para quatro pessoas" e "como substituo o leite nesta receita?" fazerem sentido mesmo sem histórico
  de conversa;
- o que é falado em voz alta quando a resposta é longa: o parágrafo de abertura, com os detalhes
  na tela.
"""
from __future__ import annotations

import logging
import re
import time

from server.assistant.ingredients import LIST_TOPIC, IngredientCommands, describe_memory
from server.assistant.memory import MemoryOutcome
from server.storage.memory import fold, is_food_question, parse_ingredient

log = logging.getLogger("jarvis.recipes")

DEFAULT_MAX_TOKENS = 600  # receitas completas não cabem nos 300 tokens das respostas curtas
DEFAULT_SPOKEN_MAX_CHARS = 420  # acima disso, uma receita é falada em resumo
DEFAULT_FOLLOWUP_TTL_S = 1800.0  # por quanto tempo "essa receita" se refere à última resposta
MAX_LAST_REPLY_CHARS = 1400
MAX_STOCK_IN_CONTEXT = 40
SPOKEN_DETAILS_HINT = "Os ingredientes e o passo a passo estão na tela."

_NUM = r"(?:\d+|uma?|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|doze|quinze|vinte)"
_RECIPE = re.compile(
    r"\breceitas?\b|\bcomo (?:eu |se |que se )?(?:faco|fazer|faz|preparo|preparar|prepara|cozinho|cozinhar|asso|assar|frito|fritar|monto|montar)\b|"
    r"\bme ensin[ae]\b|\bmodo de preparo\b|\bpasso a passo\b|\bo que (?:eu |a gente )?(?:posso|consigo|da pra|da para|poderia)\s+(?:fazer|cozinhar|preparar)\b|"
    r"\bo que (?:fazer|cozinhar|preparar)\b|\bda pra fazer\b|\bda para fazer\b|"
    r"\b(?:sugira|sugere|sugestao|sugestoes|ideias?|opcoes?|dica)\s+(?:de\s+|para\s+|pro\s+|pra\s+)?(?:um |uma |algum |alguma )?(?:prato|pratos|jantar|janta|almoco|lanche|cafe|sobremesa|receita|receitas|comida|refeicao)\b|"
    r"\b(?:jantar|janta|almoco|lanche|sobremesa)\s+(?:rapid\w*|facil|simples|pra hoje|para hoje|de hoje|leve)\b|"
    rf"\b(?:adapt\w*|ajust\w*)\b|\bsubstitu\w*\b|\bno lugar d[eoa]\b|\b(?:para|pra)\s+{_NUM}\s+(?:pessoas?|porcoes|porcao)\b|"
    r"\bdobr\w* a receita\b|\bmetade da receita\b|\brende\b|\bquanto tempo (?:leva|demora|assa|cozinha)\b|\bponto (?:do|da|de)\b"
)
_FOLLOWUP = re.compile(
    r"\b(?:ess[ae]|est[ae]|ness[ae]|nest[ae]|dess[ae]|dest[ae]|aquel[ae]|naquel[ae]|mesm[ao])\s+(?:receita|prato|bolo|massa|sopa|molho|opcao|sugestao|ideia|preparo|versao|torta|salada|omelete|panqueca)\b|"
    r"\b(?:a|o|na|no|da|do)\s+(?:primeir[ao]|segund[ao]|terceir[ao]|ultim[ao])\b|"
    rf"\b(?:adapt\w*|ajust\w*|convert\w*|transform\w*|refaz\w*|refac\w*|faz|faca|deixa|deixe|muda|mude)\b.*\b(?:para|pra)\s+{_NUM}\s+(?:pessoas?|porcoes|porcao)\b|"
    rf"^(?:e\s+)?(?:para|pra)\s+{_NUM}\s+(?:pessoas?|porcoes|porcao)\b|"
    r"\b(?:substitu\w*|no lugar d[eoa]|troc\w*)\b.*\b(?:nela|nele|nessa|nesse|nisso|aqui|na receita|da receita|dessa|desse|ai)\b|"
    r"\b(?:como|da pra|da para|posso|consigo)\s+(?:eu\s+)?substituir\s+(?:o|a|os|as)\s+\w+\s*\??$|"
    r"\bdobr\w*\b|\bmetade\b|\bsem\s+\w+\s+(?:nessa|nesse|nela|nele|da pra|da para|fica bom)\b|"
    r"\bme (?:ensina|ensine|explica|explique|mostra|mostre|detalha|detalhe|passa|passe)\s+(?:essa|esse|ela|ele|a primeira|a segunda|a terceira|a ultima|a melhor|direito|o resto|como faz essa|como faz esse)\b|"
    r"\b(?:quero|vou fazer|vamos de|pode ser|fico com|vou de|prefiro)\s+(?:a|o|essa|esse|aquela|aquele)\b|\bpasso a passo\b|"
    r"\b(?:repete|repita|de novo|outra vez|fala de novo)\b|\bqual (?:e|era|foi) o proximo passo\b|\bproximo passo\b|\bpasso (?:\d+|um|dois|tres|quatro|cinco|seis)\b|"
    r"\bquanto (?:tempo|de \w+)\b.*\b(?:nessa|nesse|nela|nele|ai|mesmo)\b"
)
_SAFETY = re.compile(
    r"\b(?:validade|vencid[oa]s?|venceu|vence|estrag\w*|azed\w*|mof\w*|cheiro|cheirando|gosm\w*|viscos\w*|escurec\w*|passad[oa]s?|"
    r"quanto tempo (?:dura|posso guardar|fica|aguenta|pode ficar)|dura quanto|pode comer|posso comer|ainda (?:da|serve|presta|pode)|seguro comer|"
    r"faz mal|descongel\w*|congel\w*|conserv\w*|guardar|armazenar|fora da geladeira|temperatura ambiente|cru[ao]?s?|mal passad[oa]|"
    r"ponto da carne|salmonela|intoxica\w*|estufad[oa]|amassad[oa]|podre|velh[oa]s?|requent\w*|esquent\w* de novo|sobras?)\b"
)
_STOCK_REFERENCE = re.compile(
    r"\bcom (?:o que|aquilo que|os ingredientes que|as coisas que|tudo que|tudo o que)\s+(?:eu\s+|a gente\s+|nos\s+)?(?:tenho|temos|tem|ha)\b|"
    r"\bcom o que (?:tem|ha)\s+(?:em casa|na geladeira|na despensa|aqui|ai)\b|"
    r"\bcom (?:os\s+)?(?:meus\s+)?ingredientes\s+(?:disponiveis|que tenho|que eu tenho|de casa|registrados|da lista)\b|"
    r"\bcom (?:o\s+)?(?:meu\s+)?estoque\b|\bdo que (?:eu\s+)?tenho\b|\bcom o que (?:eu\s+)?tenho\b|\bo que tenho (?:em casa|aqui)\b"
)
_SECTION_HEADER = re.compile(r"^\s*(?:ingredientes|preparo|modo de preparo|modo de fazer|passos?|como fazer|rendimento|tempo|voce (?:vai )?precisar|você (?:vai )?precisar)\b\s*:?", re.IGNORECASE)
_NUMBERED_LINE = re.compile(r"^\s*(?:\d+[.)]|passo\s+\d+)\s+", re.IGNORECASE)
_LIST_LINE = re.compile(r"^\s*[-•*▪◦]\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")
_SUGGESTION = re.compile(
    r"\bo que (?:eu |a gente )?(?:posso|consigo|da pra|da para|poderia|daria pra|daria para)\s+(?:fazer|cozinhar|preparar)\s+com\b|"
    r"\bo que (?:fazer|cozinhar|preparar)\s+com\b|\bda pra fazer (?:o que|algo|alguma coisa) com\b|"
    r"\b(?:sugira|sugere|sugestao|sugestoes|ideias?|opcoes?)\b.*\bcom\s+(?:o que|os ingredientes|esses|estes|isso)\b|"
    r"\b(?:alguma|uma)\s+(?:receita|ideia|sugestao)\s+com\b"
)

RECIPE_GUIDE = """Pedido sobre receita. Siga estas orientações, sempre em texto simples, sem Markdown:
- Comece com um parágrafo curto, de uma ou duas frases, dizendo o prato, para quantas porções, o tempo estimado e os ingredientes principais. Esse parágrafo é falado em voz alta; o resto fica na tela.
- Se a pessoa pediu o preparo, escreva depois uma linha "Ingredientes:" com um ingrediente por linha, com quantidade, e uma linha "Preparo:" com passos numerados (1., 2., 3.), um por linha, frases curtas, com tempos, temperaturas e pontos de cozimento quando importarem. Termine com uma frase breve de dica ou de segurança, se fizer sentido.
- Se a pessoa listou ingredientes e perguntou o que pode fazer: proponha até três pratos que usem principalmente esses ingredientes mais itens básicos (sal, óleo, água, temperos comuns), diga em uma frase o que cada um precisaria além do citado e pergunte qual ela quer em detalhe. Não escreva a receita completa ainda.
- Porções: se a pessoa não disse para quantas pessoas, assuma duas e deixe isso claro. Ao adaptar para outro número de porções, multiplique todas as quantidades proporcionalmente, informe o novo número de porções e repita a lista ajustada; avise se o tempo de cozimento ou o tamanho da panela mudam.
- Substituições: indique a proporção (por exemplo, 1 xícara de leite por 1 xícara de bebida vegetal), o efeito no sabor ou na textura e quando a troca não funciona. Prefira itens comuns em uma cozinha brasileira e, se houver ingredientes disponíveis registrados, prefira os que a pessoa tem.
- Quando faltar uma informação essencial para responder (o que a pessoa tem, qual prato ela quer, se há alguma restrição), faça uma única pergunta curta em vez de adivinhar.
- Respeite as preferências e restrições alimentares guardadas na memória: não proponha pratos que as contrariem; se a pessoa pedir algo que conflite, avise em uma frase e prossiga.
- Use só conhecimento geral de cozinha; você não tem acesso a sites, livros ou bancos de receitas. Não invente marcas, dados nutricionais exatos nem prazos de validade."""

SUGGESTION_GUIDE = (
    "A pessoa quer SUGESTÕES a partir dos ingredientes que citou, não o preparo: responda em no máximo quatro frases, "
    "com até três ideias de pratos que usem principalmente esses ingredientes mais itens básicos (sal, óleo, água, temperos "
    "comuns), dizendo o que faltaria além do citado, e termine perguntando qual ela quer em detalhe. Não escreva lista de "
    "ingredientes com quantidades nem passos agora."
)

SAFETY_SHORT = (
    "Segurança alimentar: lembre o cozimento seguro quando a receita levar carnes, frango, ovos, peixes ou frutos do mar "
    "(bem cozidos por dentro), e nunca recomende usar um ingrediente com sinais de deterioração."
)
SAFETY_GUIDE = """Segurança alimentar, com prioridade sobre o resto:
- Nunca recomende consumir um alimento com sinais de deterioração: cheiro ruim, mofo, textura viscosa ou gosmenta, cor alterada, embalagem estufada ou lata amassada. Na dúvida, oriente a pessoa a verificar o alimento e, se houver qualquer sinal, descartar.
- Não invente prazos de validade exatos. Só cite faixas gerais amplamente conhecidas, dizendo que dependem da conservação (temperatura, embalagem, tempo fora da geladeira) e que a pessoa deve confiar nos sinais do alimento e no rótulo.
- Para carnes, frango, ovos, peixes e frutos do mar, lembre o cozimento completo, o descongelamento na geladeira e não deixar à temperatura ambiente por muito tempo. Sobras: esfriar e refrigerar logo, reaquecer bem antes de comer.
- Se a pergunta envolver risco real (alimento possivelmente estragado, pessoa passando mal), seja clara e cautelosa, sem alarmismo, e sugira procurar um profissional de saúde se houver sintomas."""


def is_recipe_request(text: str) -> bool:
    return _RECIPE.search(fold(text)) is not None


def is_suggestion_request(text: str) -> bool:
    """"o que posso fazer com ovos, arroz e tomate?": ideias curtas, não o preparo."""
    return _SUGGESTION.search(fold(text)) is not None


def is_followup(text: str) -> bool:
    return _FOLLOWUP.search(fold(text)) is not None


def mentions_safety(text: str) -> bool:
    return _SAFETY.search(fold(text)) is not None


def refers_to_stock(text: str) -> bool:
    return _STOCK_REFERENCE.search(fold(text)) is not None


def looks_like_recipe(reply: str) -> bool:
    """Resposta estruturada: cabeçalho de ingredientes/preparo ou pelo menos três passos numerados ou itens de lista."""
    lines = [line for line in reply.splitlines() if line.strip()]
    if any(_SECTION_HEADER.match(line) for line in lines):
        return True
    return sum(1 for line in lines if _NUMBERED_LINE.match(line) or _LIST_LINE.match(line)) >= 3


def _is_detail_line(line: str) -> bool:
    stripped = line.strip()
    return bool(_SECTION_HEADER.match(line) or _NUMBERED_LINE.match(line) or _LIST_LINE.match(line) or stripped.endswith(":"))


def spoken_summary(reply: str, max_chars: int, hint: str = SPOKEN_DETAILS_HINT) -> str:
    """O que falar de uma resposta longa e estruturada: a abertura (antes do primeiro cabeçalho, item de lista
    ou passo), limitada a frases inteiras, mais um aviso de que os detalhes estão na tela. Respostas curtas ou
    sem estrutura são faladas por inteiro (o limite geral da voz continua valendo)."""
    if max_chars <= 0 or len(reply) <= max_chars or not looks_like_recipe(reply):
        return reply
    intro_lines: list[str] = []
    for line in reply.splitlines():
        if _is_detail_line(line):
            if line.strip().endswith(":") and not _SECTION_HEADER.match(line) and not _LIST_LINE.match(line):
                intro_lines.append(line.strip().rstrip(":") + ".")  # "Você vai precisar de:" -> fala e para
            break
        if line.strip():
            intro_lines.append(line.strip())
    intro = " ".join(intro_lines) or reply
    sentences = _SENTENCE_END.split(intro.strip())
    spoken = ""
    for sentence in sentences:
        candidate = f"{spoken} {sentence}".strip()
        if spoken and len(candidate) > max_chars:
            break
        spoken = candidate
        if len(spoken) >= max_chars // 2 and len(sentences) > 2:
            break
    spoken = spoken.strip()
    if not spoken:
        return reply
    if not spoken.endswith((".", "!", "?", "…")):
        spoken += "."
    return f"{spoken} {hint}"


class RecipeAssistant:
    """Orientação de receitas e estoque para a IA, comandos de ingredientes e fala resumida."""

    def __init__(
        self,
        ingredients: IngredientCommands | None,
        *,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        spoken_max_chars: int = DEFAULT_SPOKEN_MAX_CHARS,
        followup_ttl_s: float = DEFAULT_FOLLOWUP_TTL_S,
        clock=time.monotonic,
    ) -> None:
        self.ingredients = ingredients
        self.max_tokens = max_tokens
        self.spoken_max_chars = spoken_max_chars
        self.followup_ttl_s = followup_ttl_s
        self._clock = clock
        self._last: tuple[str, str, float] | None = None  # (pergunta, resposta, quando)
        self.recipe_requests = 0
        self.followups = 0
        self.summaries = 0

    # -- comandos locais (sem IA) --------------------------------------------------------------

    def handle(self, text: str) -> MemoryOutcome | None:
        """Comandos de ingredientes e o caso "o que faço com o que tenho?" sem nada registrado."""
        if self.ingredients is not None:
            outcome = self.ingredients.handle(text)
            if outcome is not None:
                return outcome
            if refers_to_stock(text) and self.ingredients.available and not self._stock_safe():
                return MemoryOutcome(
                    "Ainda não tenho nenhum ingrediente registrado, então não sei o que você tem em casa. "
                    "Me diga, por exemplo: tenho três ovos e um pacote de arroz.",
                    {"action": "list", "items": [], "count": self._count_safe(), "topic": LIST_TOPIC, "spoken": True},
                    "stock_empty",
                )
        return None

    # -- contexto para a IA -------------------------------------------------------------------

    def max_tokens_for(self, question: str) -> int | None:
        """Limite de tokens maior para receitas (e continuações); None mantém o padrão da IA."""
        if is_recipe_request(question) or self.followup_for(question) is not None:
            return self.max_tokens
        return None

    def followup_for(self, question: str) -> tuple[str, str] | None:
        """(pergunta anterior, resposta anterior) se `question` se refere à última receita e ela ainda vale."""
        if self._last is None:
            return None
        asked, replied, at = self._last
        if self._clock() - at > self.followup_ttl_s:
            self._last = None
            return None
        return (asked, replied) if is_followup(question) else None

    def context_for(self, question: str) -> str:
        parts: list[str] = []
        recipe = is_recipe_request(question)
        previous = self.followup_for(question)
        food = recipe or previous is not None or is_food_question(question) or refers_to_stock(question)
        if recipe or previous is not None:
            self.recipe_requests += 1
            if is_suggestion_request(question) and previous is None:
                parts.append(SUGGESTION_GUIDE)
            parts.append(RECIPE_GUIDE)
        if previous is not None:
            self.followups += 1
            asked, replied = previous
            parts.append(
                "Continuação: a pergunta atual se refere à sua resposta anterior, reproduzida abaixo. Trabalhe sobre ela "
                "(adaptar porções, substituir um ingrediente, detalhar um passo ou uma das opções sugeridas) em vez de propor outra receita.\n"
                f"Pergunta anterior: {asked}\n"
                f"Sua resposta anterior:\n{replied}"
            )
        if food and (self.ingredients is not None or recipe or previous is not None):
            # Sem memoria (estoque indisponivel), perguntas comuns de comida seguem como antes, sem contexto extra.
            # Em continuacoes ("adapte essa receita"), o estoque so entra se a pessoa falar dele: senao o modelo
            # tende a emendar sugestoes com o que ha em casa no fim da resposta.
            if previous is None or refers_to_stock(question):
                parts.append(self._stock_block())
        if mentions_safety(question):
            parts.append(SAFETY_GUIDE)
        elif recipe or previous is not None:
            parts.append(SAFETY_SHORT)
        return "\n\n".join(parts)

    def _stock_block(self) -> str:
        if self.ingredients is None or not self.ingredients.available:
            return (
                "Ingredientes disponíveis: não há registro de estoque neste servidor. Se a pessoa perguntar o que tem em casa, "
                "diga que não tem essa informação; não presuma nenhum ingrediente além dos que ela citar."
            )
        items = self._stock_safe()
        if not items:
            return (
                "Ingredientes disponíveis registrados pela pessoa: nenhum. Se ela perguntar o que tem em casa ou o que pode fazer "
                "com o que tem, diga que não há ingredientes registrados e peça para informar (por exemplo: tenho três ovos e um "
                "pacote de arroz). Ingredientes citados na pergunta são apenas o que ela quer usar agora."
            )
        lines = ["Ingredientes disponíveis registrados pela pessoa (estoque informado por ela, não deduzido):"]
        for mem in items[:MAX_STOCK_IN_CONTEXT]:
            lines.append(f"- {describe_memory(mem)}")
        if len(items) > MAX_STOCK_IN_CONTEXT:
            lines.append(f"- e mais {len(items) - MAX_STOCK_IN_CONTEXT} itens")
        lines.append(
            "Regras: só esses itens contam como disponíveis em casa; 'sem quantidade informada' significa que a quantidade é "
            "desconhecida, então não a invente. Ingredientes citados na pergunta são o que a pessoa quer usar, não necessariamente "
            "o que ela tem. Use o estoque quando ela pedir algo com o que tem ou quando precisar dizer o que falta; não comente a "
            "lista nem proponha outros pratos com ela se a pergunta não for sobre isso."
        )
        return "\n".join(lines)

    def _stock_safe(self) -> list:
        try:
            return self.ingredients.stock() if self.ingredients is not None else []
        except Exception as exc:  # falha de armazenamento nao impede a pergunta
            log.warning("estoque indisponivel ao montar o contexto: %s", exc)
            return []

    def _count_safe(self) -> int:
        try:
            store = self.ingredients.store if self.ingredients is not None else None
            return store.count() if store is not None else 0
        except Exception:
            return 0

    # -- depois da resposta -------------------------------------------------------------------

    def remember_reply(self, question: str, reply: str) -> None:
        """Guarda (só em memória, por `followup_ttl_s`) a última resposta sobre receita."""
        if not reply:
            return
        if is_recipe_request(question) or self.followup_for(question) is not None or looks_like_recipe(reply):
            self._last = (question[:300], reply[:MAX_LAST_REPLY_CHARS], self._clock())

    def spoken_text(self, reply: str, question: str = "") -> str:
        """O que falar: a resposta inteira, ou só a abertura quando é uma receita longa. Uma lista de sugestões
        ("o que posso fazer com...") é falada por inteiro: as opções são justamente o que a pessoa quer ouvir."""
        if question and is_suggestion_request(question) and self.followup_for(question) is None:
            return reply
        spoken = spoken_summary(reply, self.spoken_max_chars)
        if spoken != reply:
            self.summaries += 1
        return spoken

    @property
    def has_recent_recipe(self) -> bool:
        return self._last is not None and self._clock() - self._last[2] <= self.followup_ttl_s

    def forget_last(self) -> None:
        self._last = None


__all__ = [
    "RECIPE_GUIDE",
    "SAFETY_GUIDE",
    "SAFETY_SHORT",
    "SPOKEN_DETAILS_HINT",
    "SUGGESTION_GUIDE",
    "RecipeAssistant",
    "is_followup",
    "is_recipe_request",
    "is_suggestion_request",
    "looks_like_recipe",
    "mentions_safety",
    "refers_to_stock",
    "spoken_summary",
]
