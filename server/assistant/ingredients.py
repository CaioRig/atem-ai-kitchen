"""Ingredientes disponíveis por voz (Fase 7): registrar, consultar, descontar e remover.

Regras determinísticas em pt-BR, sem passar pela IA, sobre a mesma memória SQLite (tipo
`ingredient`, ver `server/storage/memory.py`). Nada é inventado: quando a pessoa não diz a
quantidade, o item fica "sem quantidade informada"; quando o desconto não dá para calcular
(unidades diferentes, estoque desconhecido), a quantidade passa a desconhecida e a Luna avisa.

Só frases que claramente falam do estoque viram comandos ("tenho três ovos", "comprei um pacote de
arroz", "usei dois ovos", "acabou o leite", "quais ingredientes tenho?"). Um ingrediente apenas citado
em uma pergunta ("o que faço com ovos e arroz?") nunca entra no estoque.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from server.assistant.memory import (
    REPLY_STORAGE_FAILED,
    REPLY_UNAVAILABLE,
    MemoryOutcome,
    strip_polite_prefix,
    strip_wake_prefix,
)
from server.storage.memory import (
    Memory,
    MemoryStorageError,
    MemoryStore,
    fold,
    format_quantity,
    ingredient_key,
    normalize,
    parse_ingredient,
    plural_unit,
    singular,
)

log = logging.getLogger("jarvis.ingredients")

MAX_SPOKEN_ITEMS = 10  # acima disso, a lista falada resume e aponta para a tela
LIST_TOPIC = "ingredientes disponíveis"

_NUMBER_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8,
    "nove": 9, "dez": 10, "onze": 11, "doze": 12, "treze": 13, "quatorze": 14, "catorze": 14, "quinze": 15,
    "dezesseis": 16, "dezessete": 17, "dezoito": 18, "dezenove": 19, "vinte": 20, "trinta": 30, "quarenta": 40,
    "cinquenta": 50, "sessenta": 60, "setenta": 70, "oitenta": 80, "noventa": 90, "cem": 100, "duzentos": 200,
    "duzentas": 200, "trezentos": 300, "trezentas": 300, "quinhentos": 500, "quinhentas": 500, "mil": 1000,
    "meio": 0.5, "meia": 0.5,
}
_TENS = "vinte|trinta|quarenta|cinquenta|sessenta|setenta|oitenta|noventa|cem|duzent[oa]s|trezent[oa]s|quinhent[oa]s|mil"
_ONES = "um|uma|dois|duas|tres|quatro|cinco|seis|sete|oito|nove|dez|onze|doze|treze|quatorze|catorze|quinze|dezesseis|dezessete|dezoito|dezenove|vinte|trinta|quarenta|cinquenta"
_NUMWORD = "|".join(sorted(_NUMBER_WORDS, key=len, reverse=True))
_NUMBER = rf"\d+(?:[.,]\d+)?|(?:{_NUMWORD})(?![a-z])"

# Unidades (forma dobrada, singular) -> nome de exibição no singular.
_UNITS = {
    "unidade": "unidade", "pacote": "pacote", "pacotinho": "pacote", "pacotao": "pacote", "caixa": "caixa",
    "caixinha": "caixa", "lata": "lata", "latinha": "lata", "garrafa": "garrafa", "garrafinha": "garrafa",
    "litro": "litro", "l": "litro", "mililitro": "mililitro", "ml": "mililitro", "quilo": "quilo", "kg": "quilo",
    "kilo": "quilo", "quilograma": "quilo", "quilinho": "quilo", "grama": "grama", "g": "grama", "gr": "grama",
    "duzia": "dúzia", "xicara": "xícara", "colher": "colher", "saco": "saco", "saquinho": "saco", "pote": "pote",
    "potinho": "pote", "dente": "dente", "cabeca": "cabeça", "maco": "maço", "fatia": "fatia", "pedaco": "pedaço",
    "bandeja": "bandeja", "vidro": "vidro", "copo": "copo", "punhado": "punhado", "pe": "pé", "cacho": "cacho",
    "ramo": "ramo", "penca": "penca", "barra": "barra", "tablete": "tablete", "sache": "sachê", "envelope": "envelope",
    "rolo": "rolo", "pitada": "pitada", "embalagem": "embalagem", "galao": "galão", "bisnaga": "bisnaga",
    "tubo": "tubo", "frasco": "frasco", "fardo": "fardo", "bandejinha": "bandeja", "cartela": "cartela",
}
_FEMININE_UNITS = frozenset(
    "unidade caixa lata garrafa dúzia xícara colher cabeça fatia bandeja penca barra pitada embalagem bisnaga cartela".split()
)
_SPOON = r"colher(?:es)?\s+(?:de\s+)?(?:sopa|cha|sobremesa|cafe)"


def _unit_forms() -> list[str]:
    forms: set[str] = set()
    for unit in _UNITS:
        forms.add(unit)
        forms.add(fold(plural_unit(unit, 2)))
    return sorted(forms, key=len, reverse=True)


_UNIT = rf"(?:{_SPOON}|(?:{'|'.join(re.escape(u) for u in _unit_forms())})(?![a-z]))"
_VAGUE = r"um pouco de|um pouquinho de|um monte de|um tanto de|um bocado de|bastante|muit[oa]s?|pouc[oa]s?|vari[oa]s|algumas|alguns|uns|umas"
_FILLER = r"mais ou menos|cerca de|aproximadamente|quase|so|apenas|mais|ainda|tambem|outro|outra|outros|outras"
_TRAILING = (
    r"(?:\s+(?:na geladeira|no freezer|no congelador|na despensa|no armario|em casa|aqui em casa|aqui|guardad[oa]s?|"
    r"sobrando|ainda|disponive(?:l|is)|no estoque|na cozinha|la|de reserva|em estoque))*"
)
_ITEM = re.compile(
    rf"^(?:(?:{_FILLER})\s+)*"
    rf"(?:(?P<vague>{_VAGUE})\s+)?"
    rf"(?:(?P<qty>{_NUMBER})\s*)?"
    rf"(?:(?P<unit>{_UNIT})\s+(?:de\s+|d')?)?"
    rf"(?:(?P<vague2>{_VAGUE})\s+)?"
    rf"(?P<name>.+?)"
    rf"{_TRAILING}[.!?]*$"
)
# Os numeros compostos ("vinte e cinco", "um quilo e meio") ja foram resolvidos antes desta separacao.
_ITEM_SPLIT = re.compile(r"\s*(?:,|;|\s+e\s+|\s+mais\s+(?=\d|um\b|uma\b|dois\b|duas\b|tres\b))\s*")
_COMPOUND_NUMBER = re.compile(rf"\b(?P<a>{_TENS})\s+e\s+(?P<b>{_ONES})\b")
_AND_HALF = re.compile(rf"\b(?P<n>{_NUMBER})\s+(?:(?P<unit>{_UNIT})\s+)?e\s+mei[oa]\b")
_LEADING_ARTICLE = re.compile(r"^(?:o|a|os|as|de|do|da|dos|das|um|uma|uns|umas)\s+")
_FRACTION = re.compile(r"^(?:(?:o|a)\s+)?(?P<frac>resto|restante|tod[oa]s?|tudo|metade)\s+(?:d[oa]s?\s+|[oa]s?\s+)?(?P<name>.+)$")
_BAD_FIRST_WORDS = frozenset("que de se por como quando onde qual quais quanto quantos quantas para pra alguma algum algo mais menos tempo nenhum nenhuma".split())

# Palavras que mostram que "tenho X" não fala de comida (pessoas, tempo, saúde, sentimentos, equipamentos).
_NOT_INGREDIENT = frozenset(
    """filho filha filhos filhas duvida duvidas pergunta perguntas problema problemas ideia ideias hora horas minuto
    minutos tempo pressa fome sede vontade reuniao compromisso compromissos visita visitas convidado convidados
    convidada convidadas pessoa pessoas amigo amigos amiga amigas dinheiro real reais certeza medo dor dores febre gripe
    alergia alergias dieta aniversario festa trabalho prova consulta saudade sono razao interesse cuidado paciencia
    tudo nada algo isso isto aquilo ele ela voce mim ano anos idade pressao diabetes colesterol gastrite intolerancia
    receita receitas plano planos conta contas carro casa cachorro gato gatos cachorros bebe bebes crianca criancas
    marido esposa mulher namorado namorada mae pai avo avos irmao irma sogra sogro vizinho vizinha
    air airfryer fryer fritadeira fogao fogoes forno fornos microondas micro-ondas geladeira geladeiras freezer
    congelador liquidificador batedeira processador panela panelas frigideira frigideiras wok chaleira cafeteira
    sanduicheira torradeira grill churrasqueira balanca mixer espremedor faca facas tabua assadeira assadeiras forma
    formas pote potes utensilio utensilios eletrodomestico eletrodomesticos garrafa garrafas copo copos prato pratos
    talher talheres colher colheres xicara xicaras tigela tigelas""".split()
)
# Alimentos comuns (forma dobrada, singular). Permite aceitar "tenho arroz e feijão" sem quantidade; fora daqui,
# frases sem quantidade só entram com um contexto explícito ("tenho em casa", "na geladeira tem", "registre os
# ingredientes").
FOOD_LEXICON = frozenset(
    """ovo arroz feijao macarrao massa farinha trigo acucar sal oleo azeite manteiga margarina leite creme nata
    iogurte queijo requeijao presunto mussarela muçarela mortadela salsicha linguica calabresa paio bacon carne frango
    peito coxa sobrecoxa asa asinha peixe atum sardinha salmao tilapia bacalhau camarao bife patinho acem musculo
    costela picanha alcatra file moida porco lombo pernil bisteca carne-seca charque tomate cebola alho batata
    cenoura abobora abobrinha chuchu berinjela pimentao pepino alface rucula agriao repolho couve espinafre brocolis
    couve-flor vagem ervilha milho lentilha grao soja mandioca aipim macaxeira inhame beterraba nabo rabanete quiabo
    jilo maxixe salsinha salsa cebolinha coentro manjericao oregano louro pimenta cominho colorau paprica curry
    canela cravo noz-moscada gengibre acafrao curcuma limao laranja tangerina mexerica banana maca mamao manga
    abacaxi melancia melao uva morango pera pessego ameixa goiaba maracuja abacate coco amendoim castanha noz
    amendoa aveia granola pao bolacha biscoito torrada fermento bicarbonato chocolate cacau achocolatado cafe cha
    suco refrigerante agua cerveja vinho vinagre mostarda ketchup catchup maionese molho shoyu extrato polpa caldo
    tempero sopa pipoca fuba polvilho tapioca cuscuz farofa condensado gelatina mel geleia doce goiabada palmito
    azeitona champignon cogumelo tofu quinoa chia linhaca gergelim passa damasco tamara ricota cottage parmesao
    provolone cheddar gorgonzola brie cream-cheese nata kefir leite-condensado nozes castanhas banha toucinho
    hamburguer nugget empanado lasanha pizza esfiha pastel coxinha salgado bolo torta pudim brigadeiro sorvete
    acai granulado confeito amido maisena rosca bisnaguinha croissant wrap tortilha nachos fruta frutas legume
    legumes verdura verduras hortalica hortalicas carne cereal cereais sucrilhos leite-de-coco ervilhas lentilhas
    feijoes macarroes paes queijos iogurtes ovos tomates cebolas batatas cenouras bananas macas laranjas limoes
    mamoes mangas uvas morangos peras pessegos abacates cocos amendoins castanhas""".split()
)

# -- frases ---------------------------------------------------------------------------------------
_REMEMBER_PREFIX = re.compile(
    r"^(?:(?:me\s+)?(?:lembre|lembra|anote|anota|registre|registra|guarde|guarda|salve|salva|memorize|memoriza)(?:-se)?"
    r"(?:\s+(?:ai|para mim|pra mim|por favor))?\s+(?:de que|que|:)\s+)"
)
_PLACE = r"(?:em casa|aqui|aqui em casa|na geladeira|na despensa|no armario|no freezer|no estoque|na cozinha|guardad[oa]s?|disponive(?:l|is)|sobrando|de reserva)"
_SUBJECT = r"(?:(?:eu|a gente|nos)\s+)?"
_HAVE = re.compile(
    rf"^(?:(?P<subject>(?:eu|a gente|nos)\s+))?(?:(?:agora|ainda|hoje|so|apenas|ja)\s+)*(?P<verb>tenho|temos|tem|sobrou|sobraram|resta|restam)"
    rf"(?P<place>\s+{_PLACE})?\s+(?P<items>.+)$"
)
_PLACE_FIRST = re.compile(rf"^{_PLACE}\s+(?:eu\s+)?(?:tenho|temos|tem|ha|existe|existem)\s+(?P<items>.+)$")
_BOUGHT = re.compile(
    rf"^{_SUBJECT}(?:(?:hoje|agora|ja|acabei de)\s+)*(?:comprei|compramos|comprar|ganhei|ganhamos|chegou|chegaram|trouxe|trouxemos)"
    rf"(?:\s+(?:do mercado|da feira|no mercado|na feira|do sacolao))?\s+(?P<items>.+)$"
)
_ADD_TO = re.compile(
    r"^(?:adicione|adiciona|acrescente|acrescenta|coloque|coloca|inclua|inclui|bote|bota|ponha|poe|põe)\s+(?P<items>.+?)\s+"
    r"(?:na|no|nos|nas|a|à|em|ao)\s+(?:minha\s+|meu\s+|minhas\s+|meus\s+)?(?:despensa|estoque|geladeira|lista(?: de ingredientes)?|ingredientes|armario|cozinha)[.!?]*$"
)
_REGISTER_LIST = re.compile(
    r"^(?:registre|registra|anote|anota|guarde|guarda|salve|salva|atualize|atualiza|cadastre|cadastra)\s+"
    r"(?:[oa]s\s+)?(?:meus\s+|minhas\s+)?(?:ingredientes|estoque|despensa|mantimentos)(?:\s+(?:disponiveis|que tenho|que eu tenho|de casa))?[:,]?\s+(?P<items>.+)$"
)
_USED = re.compile(
    rf"^{_SUBJECT}(?:(?:ja|hoje|agora|acabei de)\s+)*(?:usei|usamos|usar|gastei|gastamos|gastar|comi|comemos|consumi|consumimos|"
    rf"peguei|pegamos|utilizei|utilizamos|utilizar)\s+(?P<items>.+?)(?:\s+(?:do estoque|da despensa|da geladeira|da lista|de la|dos ingredientes))?[.!?]*$"
)
_RAN_OUT = (
    re.compile(r"^(?:acabou|acabaram|acabou-se|acabaram-se|terminou|terminaram)\s+(?:[oa]s?\s+)?(?P<items>.+)$"),
    re.compile(r"^(?:[oa]s?\s+)?(?P<items>.+?)\s+(?:acabou|acabaram|terminou|terminaram)[.!?]*$"),
    re.compile(rf"^{_SUBJECT}(?:nao|não)\s+(?:tenho|temos|tem)\s+mais\s+(?P<items>.+)$"),
    re.compile(
        r"^(?:tire|tira|retire|retira|remova|remove|apague|apaga|exclua|exclui|risque|risca)\s+(?:[oa]s?\s+)?(?P<items>.+?)\s+"
        r"(?:d[aeo]s?\s+)(?:minha\s+|meu\s+|minhas\s+|meus\s+)?(?:lista(?: de ingredientes)?|despensa|estoque|ingredientes|geladeira|armario)[.!?]*$"
    ),
)
_CLEAR = re.compile(
    r"^(?:apague|apaga|limpe|limpa|zere|zera|remova|remove|esvazie|esvazia|exclua|exclui)\s+"
    r"(?:tod[oa]s\s+(?:[oa]s\s+)?)?(?:meus\s+|minhas\s+|a\s+|o\s+|os\s+|as\s+)?(?:ingredientes|despensa|estoque|lista de ingredientes|mantimentos)"
    r"(?:\s+(?:disponiveis|registrados|guardados|inteir[oa]|tod[oa]))?[.!?]*$"
)
_LIST = (
    re.compile(
        r"^(?:(?:me\s+)?(?:diga|diz|fala|fale|mostra|mostre|liste|lista|conta|conte)\s+)?(?:quais|que|o que|quais sao)\s+(?:sao\s+)?"
        r"(?:os\s+|todos os\s+)?(?:meus\s+)(?:ingredientes|alimentos|mantimentos)\b"
    ),
    re.compile(
        r"^(?:(?:me\s+)?(?:diga|diz|fala|fale|mostra|mostre|liste|lista|conta|conte)\s+)?(?:quais|que|o que|quais sao)\s+(?:sao\s+)?"
        r"(?:os\s+|todos os\s+)?(?:ingredientes|alimentos|mantimentos)\b.*\b(?:tenho|temos|disponive\w*|em casa|na despensa|na geladeira|"
        r"no armario|registrad\w*|anotad\w*|guardad\w*|sobrou|sobraram|sobrando|estoque|na lista)\b"
    ),
    re.compile(
        rf"^(?:o que|que)\s+(?:e que\s+)?(?:eu\s+|a gente\s+|nos\s+)?(?:tenho|temos|tem|ha|existe)\s+"
        rf"(?:de\s+(?:ingredientes?|comida|alimentos?|mantimentos)|{_PLACE}|na lista|registrado|anotado|guardado)"
    ),
    re.compile(
        r"^(?:me\s+)?(?:mostra|mostre|liste|lista|fala|fale|diz|diga|le|leia|confere|confira)\s+(?:a\s+|o\s+|os\s+)?(?:minha\s+|meus\s+|meu\s+)?"
        r"(?:lista de ingredientes|ingredientes|despensa|estoque|mantimentos)\b"
    ),
    re.compile(r"^(?:o que|que)\s+(?:tem|ha|existe)\s+(?:na (?:geladeira|despensa|cozinha)|no (?:armario|freezer|estoque)|em casa|de comida|para comer)\b"),
    re.compile(r"^(?:como\s+(?:esta|ta|anda)\s+)?(?:a\s+|o\s+|minha\s+|meu\s+)?(?:despensa|estoque|lista de ingredientes)\s*\??$"),
)
_CHECK = (
    re.compile(
        rf"^(?:quant[oa]s?)\s+(?P<name>.+?)\s+(?:(?:eu|a gente|nos)\s+)?(?:ainda\s+)?(?:tenho|temos|tem|sobrou|sobraram|resta|restam|ha)\b"
    ),
    re.compile(rf"^{_SUBJECT}(?:ainda\s+)?(?:tenho|temos|tem)\s+(?P<name>.+?)\s*\?$"),
    re.compile(rf"^(?:tem|ha|existe|sobrou|sobraram)\s+(?P<name>.+?)\s+{_PLACE}\s*\??$"),
    re.compile(rf"^{_SUBJECT}(?:ainda\s+)?(?:tenho|temos|tem)\s+(?P<name>.+?)\s+(?:ainda|{_PLACE}|em estoque)\s*\?$"),
)
_NOISE_SENTENCE = re.compile(
    r"^(?:e\s+)?(?:atualiz\w*|anot\w*|registr\w*|descont\w*|corrij\w*|corrig\w*|baix\w*|tir\w*|tire\w*|ajust\w*|guard\w*|"
    r"lembr\w*|por favor|obrigad[oa]|valeu|ta|ok|beleza|certo)\b.*$"
)


@dataclass
class Item:
    """Um ingrediente como dito: nome (como falado), quantidade (None = não informada) e unidade (None = unidade)."""

    name: str
    quantity: float | None = None
    unit: str | None = None
    explicit: bool = False  # havia quantidade, unidade ou quantificador vago na fala
    fraction: float | None = None  # "o resto do leite" = 1.0 (tudo), "metade do arroz" = 0.5 do que ha

    @property
    def key(self) -> str:
        return ingredient_key(self.name)


@dataclass
class StockIntent:
    name: str  # set | add | use | remove | list | check | clear
    items: list[Item] = field(default_factory=list)
    explicit_context: bool = False  # "tenho em casa", "registre os ingredientes", "adicione na despensa"


# -- interpretação --------------------------------------------------------------------------------


def _resolve_numbers(folded: str) -> str:
    """"vinte e cinco" -> "25"; "um quilo e meio" -> "1.5 quilo"; "dois e meio" -> "2.5"."""
    for _ in range(2):
        folded = _COMPOUND_NUMBER.sub(lambda m: str(_NUMBER_WORDS[m.group("a")] + _NUMBER_WORDS[m.group("b")]), folded)

    def half(m: re.Match) -> str:
        n = _to_number(m.group("n"))
        unit = m.group("unit")
        return f"{n + 0.5:g}{' ' + unit if unit else ''}"

    return _AND_HALF.sub(half, folded)


def _resolve_numbers_keeping_accents(original: str) -> str:
    """Mesma resolução, mas sobre o texto original: os trechos numéricos (sem acentos) são trocados e o
    resto fica intacto, para "vinte e cinco ovos" não ser dividido no " e " ao separar os itens."""
    folded = fold(original)
    out: list[str] = []
    pos = 0
    for m in re.finditer(rf"{_COMPOUND_NUMBER.pattern}|{_AND_HALF.pattern}", folded):
        out.append(original[pos:m.start()])
        out.append(_resolve_numbers(folded[m.start():m.end()]))
        pos = m.end()
    out.append(original[pos:])
    return "".join(out)


def _to_number(token: str) -> float:
    token = token.strip()
    if token in _NUMBER_WORDS:
        return float(_NUMBER_WORDS[token])
    return float(token.replace(",", "."))


def _canonical_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = " ".join(unit.split())
    if re.fullmatch(_SPOON, u):
        kind = u.split()[-1]
        return {"sopa": "colher de sopa", "cha": "colher de chá", "sobremesa": "colher de sobremesa", "cafe": "colher de café"}[kind]
    return _UNITS.get(fold(singular(u)))


def _looks_like_food(name: str) -> bool:
    return any(singular(w) in FOOD_LEXICON or w in FOOD_LEXICON for w in normalize(name).split())


def _excluded(name: str) -> bool:
    words = normalize(name).split()
    if not words or words[0] in _BAD_FIRST_WORDS:
        return True
    return any(w in _NOT_INGREDIENT or singular(w) in _NOT_INGREDIENT for w in words)


def parse_item(text: str, *, explicit_context: bool = False) -> Item | None:
    """"três ovos" -> Item("ovos", 3); "um pacote de arroz" -> Item("arroz", 1, "pacote"); "arroz" -> Item("arroz").
    `None` quando a frase não descreve um ingrediente (equipamento, pessoa, sentimento, nada reconhecível)."""
    original = " ".join(text.split()).lower()
    folded = _resolve_numbers(fold(original))
    fraction: float | None = None
    fm = _FRACTION.match(folded.strip(" .,;:!?"))
    if fm:
        fraction = 0.5 if fm.group("frac") == "metade" else 1.0
        folded = fm.group("name")
    m = _ITEM.match(folded)
    if not m:
        return None
    name = m.group("name").strip(" .,;:!?")
    name = _LEADING_ARTICLE.sub("", name).strip()
    if not name or _excluded(name) or _canonical_unit(name) is not None or name in _NUMBER_WORDS:
        return None
    quantity = _to_number(m.group("qty")) if m.group("qty") else None
    unit = _canonical_unit(m.group("unit"))
    if fraction is not None:
        # "metade do pacote de arroz": a unidade so qualifica o nome; o que vale e a fracao do estoque.
        quantity, unit = None, None
    if unit == "dúzia" and quantity is not None:
        quantity, unit = quantity * 12, None
    if unit == "unidade":
        unit = None
    explicit = quantity is not None or unit is not None or bool(m.group("vague") or m.group("vague2")) or fraction is not None
    # Sem contexto explicito de estoque, so entra o que tem cara de alimento: unidade de compra
    # ("dois pacotes de ...") ou nome conhecido. "tenho um presente", "tenho 3 bombons" ficam de fora.
    if not explicit_context and unit is None and fraction is None and not _looks_like_food(name):
        return None
    if not explicit_context and len(name.split()) > 4:
        return None
    if quantity is not None and quantity <= 0:
        return None
    # O nome e devolvido na forma original (com acentos): fold() preserva o comprimento.
    start = fold(original).rfind(name)
    display = original[start:start + len(name)] if start >= 0 else name
    return Item(display, quantity, unit, explicit, fraction)


def parse_items(text: str, *, explicit_context: bool = False) -> list[Item]:
    items: list[Item] = []
    seen: set[str] = set()
    for part in _ITEM_SPLIT.split(_resolve_numbers_keeping_accents(" ".join(text.split()).lower())):
        part = part.strip(" .,;:!?")
        if not part:
            continue
        item = parse_item(part, explicit_context=explicit_context)
        if item is None or item.key in seen:
            continue
        seen.add(item.key)
        items.append(item)
    return items


def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]


def parse(text: str) -> StockIntent | None:
    """Interpreta a transcrição. `None` = não é um comando de ingredientes."""
    text = strip_polite_prefix(strip_wake_prefix(text))
    folded = fold(text)
    m = _REMEMBER_PREFIX.match(folded)
    remembered = m is not None
    if m:
        text = text[m.end():]
        folded = fold(text)
    if not folded.strip():
        return None
    sentences = _split_sentences(text)
    first, rest = (sentences[0], sentences[1:]) if sentences else (text, [])
    if any(not _NOISE_SENTENCE.match(fold(s)) for s in rest):
        # Duas intenções na mesma fala ("usei dois ovos. Como faço omelete?"): deixa para a IA.
        return None
    whole = fold(text).strip()
    ffirst = fold(first).strip()
    if _CLEAR.match(whole):
        return StockIntent("clear")
    for pattern in _LIST:
        if pattern.search(whole):
            return StockIntent("list")
    for pattern in _CHECK:
        cm = pattern.match(whole)
        if cm:
            name = _LEADING_ARTICLE.sub("", cm.group("name").strip(" .,;:!?"))
            item = parse_item(first[cm.start("name"):cm.end("name")], explicit_context=True)
            if item is not None and not _canonical_unit(name):
                return StockIntent("check", [item])
    for pattern in _RAN_OUT:
        rm = pattern.match(ffirst)
        if rm:
            items = parse_items(first[rm.start("items"):rm.end("items")], explicit_context=True)
            if items:
                return StockIntent("remove", items, True)
    um = _USED.match(ffirst)
    if um:
        items = parse_items(first[um.start("items"):um.end("items")], explicit_context=True)
        if items:
            return StockIntent("use", items, True)
    rm = _REGISTER_LIST.match(ffirst)
    if rm:
        items = parse_items(first[rm.start("items"):rm.end("items")], explicit_context=True)
        return StockIntent("set", items, True) if items else None
    am = _ADD_TO.match(ffirst)
    if am:
        items = parse_items(first[am.start("items"):am.end("items")], explicit_context=True)
        return StockIntent("add", items, True) if items else None
    pm = _PLACE_FIRST.match(ffirst)
    if pm:
        items = parse_items(first[pm.start("items"):pm.end("items")], explicit_context=True)
        return StockIntent("set", items, True) if items else None
    bm = _BOUGHT.match(ffirst)
    if bm:
        items = parse_items(first[bm.start("items"):bm.end("items")], explicit_context=False)
        return StockIntent("add", items, False) if items else None
    hm = _HAVE.match(ffirst)
    if hm:
        explicit = bool(hm.group("place")) or remembered
        if ffirst.rstrip().endswith("?"):
            return None  # "tenho ovos?" sem bater em _CHECK: pergunta, nao registro
        if hm.group("verb") == "tem" and not (hm.group("subject") or hm.group("place")):
            return None  # "tem como fazer bolo sem ovo": "tem" sozinho nao e estoque
        items = parse_items(first[hm.start("items"):hm.end("items")], explicit_context=explicit)
        return StockIntent("set", items, explicit) if items else None
    return None


# -- fala de volta --------------------------------------------------------------------------------


def _number_phrase(quantity: float, unit: str | None) -> str:
    if quantity == 0.5:
        return "meia" if (unit or "unidade") in _FEMININE_UNITS else "meio"
    if float(quantity).is_integer():
        return str(int(quantity))
    return f"{quantity:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _singular_phrase(name: str) -> str:
    head, sep, tail = name.partition(" ")
    return f"{singular(head)}{sep}{tail}"


def _plural_phrase(name: str) -> str:
    head, sep, tail = name.partition(" ")
    return f"{plural_unit(head, 2)}{sep}{tail}"


def describe(name: str, quantity: float | None, unit: str | None) -> str:
    """"3 ovos", "1 ovo", "meio pacote de arroz", "500 gramas de carne moída", "tomate, sem quantidade informada"."""
    name = " ".join(name.split())
    if quantity is None:
        return f"{name}, sem quantidade informada"
    if unit == "unidade":
        unit = None
    number = _number_phrase(quantity, unit)
    if unit is None:
        if quantity == 1 or quantity == 0.5:
            return f"{number} {_singular_phrase(name)}"
        if _singular_phrase(name) == name:
            return f"{number} {_plural_phrase(name)}"
        return f"{number} {name}"
    return f"{number} {plural_unit(unit, quantity)} de {name}"


def describe_memory(mem: Memory) -> str:
    name, quantity, unit = parse_ingredient(mem.text)
    return describe(name, quantity, unit)


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " e " + items[-1]


def stock_summary(items: list[Memory], limit: int = MAX_SPOKEN_ITEMS) -> str:
    """Lista falada: quantidades conhecidas primeiro, depois os itens sem quantidade."""
    known = [describe_memory(m) for m in items if parse_ingredient(m.text)[1] is not None]
    unknown = [parse_ingredient(m.text)[0] for m in items if parse_ingredient(m.text)[1] is None]
    parts: list[str] = []
    shown = 0
    if known:
        take = known[:limit]
        shown += len(take)
        parts.append(f"Você tem: {_join(take)}.")
    if unknown and shown < limit:
        take = unknown[: limit - shown]
        shown += len(take)
        parts.append(f"Sem quantidade informada: {_join(take)}.")
    rest = len(items) - shown
    if rest > 0:
        parts.append(f"E mais {rest}; a lista completa está na tela.")
    return " ".join(parts)


class IngredientCommands:
    """Interpreta e executa comandos sobre os ingredientes disponíveis na `MemoryStore`.
    `store=None` significa memória indisponível: os comandos respondem isso."""

    def __init__(self, store: MemoryStore | None) -> None:
        self.store = store
        self.commands = 0
        self.failures = 0

    @property
    def available(self) -> bool:
        return self.store is not None

    def handle(self, text: str) -> MemoryOutcome | None:
        intent = parse(text)
        if intent is None:
            return None
        self.commands += 1
        name = f"stock_{intent.name}"
        if self.store is None:
            return MemoryOutcome(REPLY_UNAVAILABLE, {"action": "error", "message": REPLY_UNAVAILABLE}, name)
        try:
            return self._execute(intent)
        except MemoryStorageError as exc:
            self.failures += 1
            log.warning("comando de ingredientes (%s) falhou: %s", intent.name, exc)
            return MemoryOutcome(REPLY_STORAGE_FAILED, {"action": "error", "message": REPLY_STORAGE_FAILED}, name)
        except Exception as exc:  # erro de programacao: nunca derruba o servidor
            self.failures += 1
            log.exception("falha inesperada no comando de ingredientes (%s)", intent.name)
            return MemoryOutcome(REPLY_STORAGE_FAILED, {"action": "error", "message": type(exc).__name__}, name)

    def stock(self) -> list[Memory]:
        return self.store.ingredients() if self.store is not None else []

    # -- execução ----------------------------------------------------------------------------------

    def _execute(self, intent: StockIntent) -> MemoryOutcome:
        assert self.store is not None
        store = self.store
        name = f"stock_{intent.name}"
        if intent.name == "list":
            items = store.ingredients()
            event = {"action": "list", "items": [m.as_dict() for m in items], "count": store.count(), "topic": LIST_TOPIC, "spoken": True}
            if not items:
                return MemoryOutcome("Não tenho nenhum ingrediente registrado. Diga, por exemplo: tenho três ovos e um pacote de arroz.", event, name)
            return MemoryOutcome(stock_summary(items), event, name)
        if intent.name == "check":
            item = intent.items[0]
            mem = store.find_ingredient(item.name)
            found = [mem.as_dict()] if mem else []
            event = {"action": "list", "items": found, "count": store.count(), "topic": f"ingredientes · {item.name}", "spoken": True}
            if mem is None:
                return MemoryOutcome(f"Não encontrei {item.name} na lista de ingredientes. Se tiver, me diga quanto.", event, name)
            stored, qty, unit = parse_ingredient(mem.text)
            if qty is None:
                return MemoryOutcome(f"Tem {stored} registrado, mas sem quantidade. Me diga quanto tem e eu anoto.", event, name)
            return MemoryOutcome(f"Você tem {describe(stored, qty, unit)}.", event, name)
        if intent.name == "clear":
            n = store.clear_ingredients()
            if n == 0:
                return MemoryOutcome("A lista de ingredientes já estava vazia.", {"action": "stock_removed", "items": [], "count": store.count()}, name)
            return MemoryOutcome(f"Tirei {n} {'ingrediente' if n == 1 else 'ingredientes'} da lista.", {"action": "stock_removed", "items": [], "count": store.count()}, name)
        if intent.name == "remove":
            removed: list[Memory] = []
            missing: list[str] = []
            for item in intent.items:
                mem = store.remove_ingredient(item.name)
                (removed.append(mem) if mem else missing.append(item.name))
            parts: list[str] = []
            if removed:
                parts.append(f"Tirei da lista: {_join([parse_ingredient(m.text)[0] for m in removed])}.")
            if missing:
                parts.append(f"{_join(missing).capitalize()} não estava na lista.")
            return MemoryOutcome(" ".join(parts), {"action": "stock_removed", "items": [m.as_dict() for m in removed], "count": store.count()}, name)
        if intent.name == "use":
            return self._use(intent)
        return self._set(intent, increment=intent.name == "add")

    def _set(self, intent: StockIntent, *, increment: bool) -> MemoryOutcome:
        assert self.store is not None
        store = self.store
        created: list[str] = []
        updated: list[str] = []
        same: list[str] = []
        notes: list[str] = []
        changed: list[Memory] = []
        for item in intent.items:
            quantity, unit = item.quantity, item.unit
            previous = store.find_ingredient(item.name)
            noted = False
            if increment and previous is not None:
                _, old_qty, old_unit = parse_ingredient(previous.text)
                if quantity is None or old_qty is None:
                    quantity, unit = None, None
                    notes.append(f"não sei o total de {item.name} agora; me diga quanto tem")
                    noted = True
                elif old_unit != unit:
                    quantity, unit = None, None
                    notes.append(f"{item.name} estava em {plural_unit(old_unit or 'unidade', 2)}; deixei sem quantidade")
                    noted = True
                else:
                    quantity = old_qty + quantity
            mem, status, before = store.set_ingredient(_display_name(item.name, quantity, unit), quantity, unit)
            text = describe_memory(mem)
            if status == "created":
                created.append(text)
            elif status == "exists":
                if not noted:
                    same.append(text)
            else:
                old = f" (antes {format_quantity(*parse_ingredient(before.text)[1:])})" if before else ""
                updated.append(text + old)
            if status != "exists":
                changed.append(mem)
        parts: list[str] = []
        if created:
            parts.append(f"Anotei: {_join(created)}.")
        if updated:
            parts.append(f"Atualizei: {_join(updated)}.")
        if same:
            parts.append(f"Já estava anotado: {_join(same)}.")
        if notes:
            parts.append(_join(notes).capitalize() + ".")
        action = "stock_saved" if created and not updated else "stock_updated"
        return MemoryOutcome(" ".join(parts), {"action": action, "items": [m.as_dict() for m in changed], "count": store.count()}, f"stock_{intent.name}")

    def _use(self, intent: StockIntent) -> MemoryOutcome:
        assert self.store is not None
        store = self.store
        parts: list[str] = []
        changed: list[Memory] = []
        for item in intent.items:
            previous = store.find_ingredient(item.name)
            if previous is None:
                parts.append(f"{item.name.capitalize()} não estava na lista, então não descontei nada.")
                continue
            stored, old_qty, old_unit = parse_ingredient(previous.text)
            plural = _singular_phrase(stored) != stored
            if item.fraction == 1.0 or (item.fraction == 0.5 and old_qty is not None and old_qty <= 0.5):
                store.remove_ingredient(stored)
                changed.append(previous)
                parts.append(f"{stored.capitalize()} {'acabaram' if plural else 'acabou'}; tirei da lista.")
                continue
            if item.fraction == 0.5:
                if old_qty is None:
                    parts.append(f"Você usou metade, mas eu não sabia quanto tinha de {stored}; continua sem quantidade.")
                    continue
                mem, _, _ = store.set_ingredient(stored, round(old_qty / 2, 3), old_unit)
                changed.append(mem)
                parts.append(f"Sobrou {describe_memory(mem)}.")
                continue
            if item.quantity is None:
                mem, _, _ = store.set_ingredient(stored, None, None)
                changed.append(mem)
                parts.append(f"Anotei que você usou {stored}; não sei quanto sobrou, me diga depois.")
                continue
            if old_qty is None:
                parts.append(f"Você usou {describe(item.name, item.quantity, item.unit)}, mas eu não sabia quanto tinha; {stored} continua sem quantidade.")
                continue
            if (old_unit or None) != (item.unit or None):
                mem, _, _ = store.set_ingredient(stored, None, None)
                changed.append(mem)
                parts.append(
                    f"Não consigo descontar {describe(item.name, item.quantity, item.unit)} de {describe(stored, old_qty, old_unit)}; deixei {stored} sem quantidade."
                )
                continue
            remaining = round(old_qty - item.quantity, 3)
            if remaining <= 0:
                store.remove_ingredient(stored)
                changed.append(previous)
                parts.append(f"{stored.capitalize()} {'acabaram' if plural else 'acabou'}; tirei da lista.")
                continue
            mem, _, _ = store.set_ingredient(_display_name(item.name, remaining, old_unit), remaining, old_unit)
            changed.append(mem)
            verb = "Sobrou" if remaining <= 1 else "Sobraram"
            parts.append(f"{verb} {describe_memory(mem)}.")
        return MemoryOutcome(" ".join(parts), {"action": "stock_updated", "items": [m.as_dict() for m in changed], "count": store.count()}, "stock_use")


def _display_name(name: str, quantity: float | None, unit: str | None) -> str:
    """Nome guardado em concordância com a quantidade quando não há unidade: "1 ovo", "3 ovos"."""
    if quantity is None or unit is not None:
        return name
    if quantity == 1:
        return _singular_phrase(name)
    if quantity > 1 and _singular_phrase(name) == name:
        return _plural_phrase(name)
    return name


__all__ = [
    "FOOD_LEXICON",
    "IngredientCommands",
    "Item",
    "LIST_TOPIC",
    "StockIntent",
    "describe",
    "describe_memory",
    "parse",
    "parse_item",
    "parse_items",
    "stock_summary",
]
