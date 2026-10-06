"""Controles de calidad sobre las tarjetas que escribe el modelo.

Cada función revisa una regla editorial con el texto que realmente recibió el modelo
y corrige o descarta. Todas devuelven qué hicieron, para dejarlo anotado en "controles".
"""

from __future__ import annotations

import re
import unicodedata


def sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto or "")
                    if unicodedata.category(c) != "Mn").lower()


def plano(texto: str) -> str:
    """Sin tildes pero conservando mayúsculas (misma longitud que el original en NFC)."""
    return "".join(c for c in unicodedata.normalize("NFD", texto or "") if unicodedata.category(c) != "Mn")


def texto_items(items: list[dict]) -> str:
    """Texto enviado al modelo para estas notas, incluido el nombre de cada fuente (para poder escribir "según X")."""
    return " ".join(f"{it.get('fuente') or ''}. {it.get('titulo') or ''}. {it.get('primera_linea') or ''}. "
                    f"{it.get('extracto') or ''}" for it in items)


def _oraciones(texto: str) -> list[str]:
    return [o for o in re.split(r"(?<=[.!?])\s+", texto.strip()) if o]


def _quitar_oraciones(texto: str, patron: re.Pattern) -> tuple[str, int]:
    oraciones = _oraciones(texto)
    quedan = [o for o in oraciones if not patron.search(o)]
    return " ".join(quedan), len(oraciones) - len(quedan)


def _limpiar_espacios(texto: str) -> str:
    texto = re.sub(r"\s+([,.;:])", r"\1", texto)
    texto = re.sub(r",\s*,", ",", texto)
    texto = re.sub(r"\s{2,}", " ", texto).strip()
    return texto[:1].upper() + texto[1:] if texto else texto


# ---------------------------------------------------------------- relación con el poker

_POKER = re.compile(
    r"\b(poker|poquer|pokerstars|ggpoker|partypoker|888poker|winamax|coinpoker|natural8|acr|wpn|"
    r"wsop|wsopc|wpt|ept|apt|gukpt|rgps|bsop|clsop|cap|lapt|wcoop|scoop|pgt|triton|uktour|"
    r"hold'?em|holdem|texas|omaha|plo|nlh|mtt|bracelets?|brazaletes?|braceletes?|main event|high roller|"
    r"mesa final|final table|heads-?up|cash game|sit ?(&|and|n) ?go|spin ?(&|and) ?go|hustler|"
    r"gto|solver|fichas|chips|bounty|freezeout|rebuy|satelites?|satellites?|dealers?|dealing|crupier(es)?|croupiers?|"
    r"cartas|naipes|director(a)? de torneos?|tournament directors?|slide (dealing|pitching))\b")


def es_de_poker(tarjeta: dict, items: list[dict]) -> bool:
    """Nota de un medio dedicado solo al poker, o con palabras de poker en la tarjeta o su fuente.
    Los medios de juego en general (casino, apuestas) necesitan la palabra."""
    if any(it.get("tematica", "poker") == "poker" for it in items):
        return True
    texto = sin_tildes(f"{tarjeta['titulo']} {tarjeta['resumen']} {texto_items(items)}")
    return bool(_POKER.search(texto))


# ---------------------------------------------------------------- promocionales

_PROMO = re.compile(
    r"\b(satelites?|satellites?|promocion(es)?|promos?|ofertas?|bonos?|bonus|rakeback|freerolls?|"
    r"descuentos?|cashback|codigo de referido|deposito|recarga|duplica (las )?(oportunidades|chances)|"
    r"paquetes? (para|al|a la|del|de)|registrese|inscribase)\b")
_ANUNCIO_GARANTIA = re.compile(r"\b(garantizad[oa]s?|gtd|guaranteed?)\b")
_ANUNCIO_VERBO = re.compile(r"\b(anuncia|presenta|ofrece|lanza|calendario|cronograma|programa|agenda|vuelve con)\b")
_RESULTADO = re.compile(
    r"\b(gan[aoóe]|ganador[a]?|ganadores|campeon(a|es)?|conquist\w*|se impon\w*|se impuso|se llev[aoó]\w*|"
    r"se qued[aoó] con|venc[eií]\w*|triunf\w*|corona\w*|titulo|lidera\w*|elimin\w*)\b")


def es_promocional(tarjeta: dict) -> bool:
    texto = sin_tildes(f"{tarjeta['titulo']} {tarjeta['resumen']}")
    if _RESULTADO.search(texto):
        return False
    if _PROMO.search(texto):
        return True
    return bool(_ANUNCIO_GARANTIA.search(texto) and _ANUNCIO_VERBO.search(texto))


_SUPERLATIVOS = [
    (re.compile(r"\s+en circunstancias dramaticas", re.I), ""),
    (re.compile(r"\s+(el|la|los|las)\s+mas\s+\w+\s+(del|de la)\s+(mundo|planeta|historia|poker)", re.I), ""),
    (re.compile(r"\s+mas\s+(prestigios|emocionante|espectacular|impresionante|increible|legendari|famos|important|grande)\w*"
                r"(\s+(del|de la)\s+(mundo|planeta|historia|poker))?", re.I), ""),
    (re.compile(r"\s+(espectacular(es)?|increibles?|impresionantes?|epic[oa]s?|legendari[oa]s?|prestigios[oa]s?|"
                r"historic[oa]s?|sensacional(es)?|fenomenal(es)?|brillantes?|dramatic[oa]s?|asombros[oa]s?|"
                r"monumental(es)?|colosal(es)?|gigantesc[oa]s?|imparables?|arrolladora?s?|aplastantes?|"
                r"inolvidables?|memorables?|electrizantes?|estelar(es)?)\b", re.I), ""),
]


def quitar_superlativos(tarjeta: dict) -> list[str]:
    """Quita adjetivos de elogio copiados de la fuente ("el más prestigioso del mundo", "espectacular")."""
    cambios = []
    for campo in ("titulo", "resumen"):
        texto = tarjeta[campo]
        # Se busca sobre una versión sin tildes y se corta el original en las mismas posiciones.
        for patron, reemplazo in _SUPERLATIVOS:
            while True:
                sin_acentos = sin_tildes(texto)
                m = patron.search(sin_acentos)
                if not m:
                    break
                cambios.append(f"superlativo quitado: «{texto[m.start():m.end()].strip()}»")
                texto = texto[:m.start()] + reemplazo + texto[m.end():]
        tarjeta[campo] = _limpiar_espacios(texto)
    return cambios


# ---------------------------------------------------------------- personas y dato concreto

_NO_PERSONA = {
    "lake", "room", "cash", "game", "games", "beach", "city", "bay", "park", "resort", "hotel", "hall", "arena",
    "grand", "cup", "classic", "open", "series", "tour", "poker", "casino", "club", "festival", "world", "main",
    "event", "championship", "circuit", "high", "roller", "live", "online", "global", "super", "mega", "mini",
    "night", "nights", "day", "days", "weekend", "summer", "autumn", "winter", "spring", "edition", "kings",
    "queens", "masters", "legends", "stars", "paradise", "millions", "million", "series", "league", "players",
    "championships", "trophy", "ring", "bracelet", "wizard", "solver", "screen", "shield", "flip", "card", "nine",
    "second", "chance", "flash", "deepstack", "turbo", "bounty", "freezeout", "warm", "up", "medium", "low",
}

# Marcas y salas escritas con mayúsculas internas: no son alias de personas.
_MARCAS = {"ggpoker", "pokerstars", "coinpoker", "pokernews", "cardplayer", "pokergo", "partypoker", "winamax",
           "rungood", "pokerfuse", "pokerlistings", "wpt", "gtowizard", "youtube", "twitch", "draftkings",
           "fanduel", "betmgm", "betrivers", "bodog", "ignition", "americascardroom", "natural8", "888poker",
           "wsopc", "codigopoker", "pokernoticias", "screenshield", "ggmillion", "ggmillions"}


def personas(texto: str, texto_fuentes: str | None = None) -> list[str]:
    """Nombres de persona probables: dos o más palabras con mayúscula que no son de torneos,
    lugares ni palabras comunes, o un alias con mayúsculas internas o dígitos (p. ej. PapoMC)."""
    from .control import _SECUENCIA, _corridas_de_nombre, _limpio

    fuente = " " + re.sub(r"[^\w$]+", " ", sin_tildes(texto_fuentes)) + " " if texto_fuentes is not None else None
    encontrados = []
    # Un nombre no cruza signos de puntuación ("…Fulano Inventado. Texto…").
    for m in (m for parte in re.split(r"[.!?;:,()]\s+", texto) for m in _SECUENCIA.finditer(parte)):
        for tramo in _corridas_de_nombre(m.group(0)):
            limpios = [_limpio(p) for p in tramo]
            if len(tramo) >= 2 and not any(p in _NO_PERSONA for p in limpios):
                if fuente is None or all(f" {p} " in fuente for p in limpios):
                    encontrados.append(" ".join(p.strip("\"'‘’“”«»") for p in tramo))
    for m in re.finditer(r"\b[A-Za-z]*[a-z][A-Z][A-Za-z0-9]*\b|\b[A-Za-z]+\d+[A-Za-z0-9]*\b", texto):
        alias = m.group(0)
        if re.fullmatch(r"(US|R|MXN|ARS|COP|CLP|PEN|U)\$?\d*", alias) or alias.lower() in _NO_PERSONA | _MARCAS:
            continue
        siguiente = re.match(r"\s+([A-Z][\w$]*)", texto[m.end():])
        if siguiente and sin_tildes(siguiente.group(1)).strip("$") in _NO_PERSONA:
            continue  # parte del nombre de un torneo o una sala: "RunGood Poker Series"
        if fuente is None or f" {sin_tildes(alias)} " in fuente:
            encontrados.append(alias)
    return encontrados


_MONTO = re.compile(r"(US\$|U\$S|R\$|MXN\$?|ARS\$?|COP\$?|CLP\$?|A\$|C\$|\$|€|£|¥)\s?\d|\d[\d.,]*\s?"
                    r"(dolares|dólares|euros|libras|pesos|reales|USD|EUR|GBP|BRL|MXN|ARS)\b", re.I)
_EVENTO = re.compile(r"\b[A-Z]{2,6}\b|\b(Main Event|Series|Tour|Championship|Festival|Circuit|Open|Classic|Cup|Copa|"
                     r"Liga|Campeonato|Serie|Circuito|Torneo|Evento|High Roller)\b")


def tiene_dato_concreto(tarjeta: dict, texto_fuentes: str) -> tuple[bool, str | None]:
    """Un resultado necesita el nombre del ganador; cualquier otra tarjeta, al menos
    un nombre de persona, un monto o un evento con nombre."""
    texto = f"{tarjeta['titulo']} {tarjeta['resumen']}"
    hay_persona = bool(personas(texto, texto_fuentes))
    if _RESULTADO.search(sin_tildes(tarjeta["titulo"])) and re.search(
            r"\b(gan|campeon|conquist|se impon|se impuso|se llev|venc|triunf|decide campeon)", sin_tildes(tarjeta["titulo"])):
        return (True, None) if hay_persona else (False, "resultado sin nombre del ganador")
    if hay_persona or _MONTO.search(texto) or _EVENTO.search(texto):
        return True, None
    return False, "sin dato concreto (nombre, monto o evento)"


# ---------------------------------------------------------------- atributos de personas

# País -> (gentilicios en español para buscar en la tarjeta, formas aceptadas en las fuentes)
NACIONALIDADES = {
    "Argentina": ("argentin[oa]s?", r"argentin[oa]s?|argentine|argentinian|from argentina|argentina's"),
    "Brasil": ("brasilen[oa]s?", r"brasilen[oa]s?|brasileir[oa]s?|brazilian|from brazil|brazil's"),
    "Chile": ("chilen[oa]s?", r"chilen[oa]s?|chilean|from chile"),
    "Colombia": ("colombian[oa]s?", r"colombian[oa]?s?|from colombia"),
    "Perú": ("peruan[oa]s?", r"peruan[oa]s?|peruvian|from peru"),
    "Uruguay": ("uruguay[oa]s?", r"uruguay[oa]s?|uruguaian[oa]?s?|uruguayan|from uruguay"),
    "Paraguay": ("paraguay[oa]s?", r"paraguay[oa]s?|paraguaian[oa]?s?|paraguayan|from paraguay"),
    "Venezuela": ("venezolan[oa]s?", r"venezolan[oa]s?|venezuelan[oa]?s?|from venezuela"),
    "Ecuador": ("ecuatorian[oa]s?", r"ecuatorian[oa]s?|equatorian[oa]s?|ecuadorian|from ecuador"),
    "México": ("mexican[oa]s?", r"mexican[oa]?s?|from mexico"),
    "República Dominicana": ("dominican[oa]s?", r"dominican[oa]?s?|from the dominican republic"),
    "Costa Rica": ("costarricenses?", r"costarricenses?|costa rican|from costa rica"),
    "Panamá": ("panamen[oa]s?", r"panamen[oa]s?|panamenh[oa]s?|panamanian|from panama"),
    "Cuba": ("cuban[oa]s?", r"cuban[oa]?s?|from cuba"),
    "Bolivia": ("bolivian[oa]s?", r"bolivian[oa]?s?|from bolivia"),
    "Estados Unidos": ("estadounidenses?|norteamerican[oa]s?", r"estadounidenses?|norteamerican[oa]s?|american|norte-american[oa]s?|from the (us|usa|united states)"),
    "Canadá": ("canadienses?", r"canadienses?|canadian|canadense|from canada"),
    "Reino Unido": ("britanic[oa]s?|ingles(a|es|as)?|escoces(a|es|as)?|gales(a|es|as)?", r"britanic[oa]s?|british|english|scottish|welsh|from the uk|from england|from scotland|from wales|uk's"),
    "Irlanda": ("irlandes(a|es|as)?", r"irlandes(a|es|as)?|irish|from ireland"),
    "España": ("espanol(a|es|as)?", r"espanol(a|es|as)?|spanish|spaniard|espanhol|from spain"),
    "Francia": ("frances(a|es|as)?", r"frances(a|es|as)?|french|frenchman|from france"),
    "Alemania": ("aleman(a|es|as)?", r"aleman(a|es|as)?|german|alemao|from germany"),
    "Italia": ("italian[oa]s?", r"italian[oa]?s?|from italy"),
    "Portugal": ("portugues(a|es|as)?", r"portugues(a|es|as)?|portuguese|from portugal"),
    "Países Bajos": ("neerlandes(a|es|as)?|holandes(a|es|as)?", r"neerlandes|holandes|dutch|from the netherlands"),
    "Bélgica": ("belgas?", r"belgas?|belgian|from belgium"),
    "Austria": ("austriac[oa]s?", r"austriac[oa]s?|austrian|from austria"),
    "Suiza": ("suiz[oa]s?", r"suiz[oa]s?|swiss|from switzerland"),
    "Suecia": ("suec[oa]s?", r"suec[oa]s?|swedish|swede|from sweden"),
    "Noruega": ("norueg[oa]s?", r"noruegu?[oa]s?|norwegian|from norway"),
    "Finlandia": ("finlandes(a|es|as)?", r"finlandes|finnish|from finland"),
    "Dinamarca": ("danes(a|es|as)?", r"danes|danish|dane|from denmark"),
    "Polonia": ("polac[oa]s?", r"polac[oa]s?|polish|from poland"),
    "Rusia": ("rus[oa]s?", r"rus[oa]s?|russian|from russia"),
    "Ucrania": ("ucranian[oa]s?", r"ucranian[oa]s?|ukrainian|from ukraine"),
    "Lituania": ("lituan[oa]s?", r"lituan[oa]s?|lithuanian|from lithuania"),
    "Letonia": ("leton[oa]s?", r"leton[oa]s?|latvian|from latvia"),
    "Estonia": ("estoni[oa]s?", r"estoni[oa]s?|estonian|from estonia"),
    "Rumania": ("ruman[oa]s?", r"ruman[oa]s?|romanian|from romania"),
    "Bulgaria": ("bulgar[oa]s?", r"bulgar[oa]s?|bulgarian|from bulgaria"),
    "Hungría": ("hungar[oa]s?", r"hungar[oa]s?|hungarian|from hungary"),
    "República Checa": ("chec[oa]s?", r"chec[oa]s?|czech|from the czech republic"),
    "Croacia": ("croatas?", r"croatas?|croatian|from croatia"),
    "Serbia": ("serbi[oa]s?", r"serbi[oa]s?|serbian|from serbia"),
    "Grecia": ("grieg[oa]s?", r"grieg[oa]s?|greek|from greece"),
    "Turquía": ("turc[oa]s?", r"turc[oa]s?|turkish|from turkey"),
    "Israel": ("israelies|israeli", r"israel(i|ies)|from israel"),
    "China": ("chin[oa]s?", r"chin[oa]s?|chinese|from china"),
    "Japón": ("japones(a|es|as)?", r"japones|japanese|from japan"),
    "Corea del Sur": ("surcorean[oa]s?|corean[oa]s?", r"corean[oa]s?|korean|from (south )?korea"),
    "India": ("indi[oa]s?", r"indian|from india"),
    "Australia": ("australian[oa]s?", r"australian[oa]?s?|aussie|from australia"),
    "Nueva Zelanda": ("neozelandes(a|es|as)?", r"neozelandes|new zealander|kiwi|from new zealand"),
    "Sudáfrica": ("sudafrican[oa]s?", r"sudafrican[oa]s?|south african|from south africa|sul-african[oa]s?"),
    "Filipinas": ("filipin[oa]s?", r"filipin[oa]s?|from the philippines"),
    "Vietnam": ("vietnamitas?", r"vietnamitas?|vietnamese|from vietnam"),
    "Tailandia": ("tailandes(a|es|as)?", r"tailandes|thai|from thailand"),
    "Malasia": ("malasi[oa]s?", r"malasi[oa]s?|malaysian|from malaysia"),
    "Singapur": ("singapurenses?", r"singapurenses?|singaporean|from singapore"),
    "Mongolia": ("mongol(a|es|as)?", r"mongol|mongolian|from mongolia"),
    "Líbano": ("libanes(a|es|as)?", r"libanes|lebanese|from lebanon"),
    "Marruecos": ("marroquies|marroqui", r"marroqui|moroccan|from morocco"),
    "Tanzania": ("tanzan[oa]s?", r"tanzan[oa]s?|tanzanian|from tanzania"),
}

_ARTICULO = r"(?:(?P<art>(?i:el|la|los|las|al|del))\s+)?"
_ANTES_DE_NOMBRE = r"(?=\s+[A-ZÁÉÍÓÚÑ])"


def _patron_gentilicio(gentilicio: str) -> re.Pattern:
    # El gentilicio solo cuenta cuando califica a una persona: "el francés X", "X, francés,", "jugador francés".
    return re.compile(rf"\b{_ARTICULO}(?P<gen>{gentilicio})\b{_ANTES_DE_NOMBRE}"
                      rf"|,\s*(?P<gen2>{gentilicio})\s*,"
                      rf"|\b(?P<rol>(?i:jugador|jugadora|profesional|pro|grinder|campeon|campeona|ganador|ganadora))\s+(?P<gen3>{gentilicio})\b")


def _quitar_gentilicio(texto: str, patron: re.Pattern) -> str:
    def reemplazo(m):
        if m.group("gen"):
            art = (m.group("art") or "").lower()
            return {"al": "a", "del": "de"}.get(art, "")
        if m.group("gen2"):
            return ","
        return m.group("rol")
    # Se trabaja sobre la versión sin tildes para encontrar y se corta el original en las mismas posiciones.
    resultado, desplazamiento = texto, 0
    for m in list(patron.finditer(plano(texto))):
        nuevo = reemplazo(m)
        ini, fin = m.start() + desplazamiento, m.end() + desplazamiento
        original = resultado[ini:fin]
        if m.group("rol"):
            nuevo = original[:len(m.group("rol"))]
        resultado = resultado[:ini] + nuevo + resultado[fin:]
        desplazamiento += len(nuevo) - (fin - ini)
    return _limpiar_espacios(resultado)


_EDAD = re.compile(r"(,\s*)?\bde (\d{1,3}) anos\b(\s*de edad)?(,)?|\b(\d{1,3}) anos de edad\b|\ba los (\d{1,3}) anos\b|"
                   r"\bcumplir (\d{1,3}) anos\b|\b(\d{1,3}) anos\b(?=\s*(,|\)|y\b))")

# Cargos y posiciones: palabra en la tarjeta -> formas aceptadas en las fuentes
CARGOS = {
    r"base": r"point guard|\bguard\b|\bbase\b",
    r"escolta": r"shooting guard|\bguard\b|escolta",
    r"alero": r"forward|alero|ala-pivo",
    r"pivot|pivo": r"\bcenter\b|pivot|pivo",
    r"delantero|delantera": r"forward|striker|delanter|atacante",
    r"defensor|defensa central|lateral": r"defender|defensor|zagueiro|lateral",
    r"mediocampista|centrocampista|volante": r"midfielder|mediocampista|centrocampista|volante|meio-campista",
    r"arquero|portero|guardameta": r"goalkeeper|goalie|arquero|portero|goleiro",
    r"mariscal de campo|quarterback": r"quarterback|mariscal",
    r"entrenador|entrenadora|director tecnico": r"coach|trainer|entrenador|treinador|tecnico",
    r"director|directora": r"director|directora|diretor|diretora",
    r"director ejecutivo|ceo": r"\bceo\b|chief executive|director ejecutivo|diretor executivo",
    r"gerente": r"manager|gerente",
    r"presidente|presidenta": r"president|chairman|chairwoman|presidente|presidenta",
    r"vicepresidente|vicepresidenta": r"vice president|\bvp\b|vicepresidente|vice-presidente",
    r"fundador|fundadora|cofundador|cofundadora": r"founder|fundador|fundadora",
    r"propietario|propietaria|dueno|duena": r"owner|propietari|dueno|duena|dono",
    r"embajador|embajadora": r"ambassador|embajador|embaixador",
    r"comentarista": r"commentator|comentarista",
    r"presentador|presentadora|conductor|conductora": r"\bhost\b|presenter|presentador|apresentador|conductor",
    r"portavoz|vocero|vocera": r"spokes(person|man|woman)|portavoz|vocero|porta-voz",
    r"abogado|abogada": r"lawyer|attorney|abogad|advogad",
    r"juez|jueza": r"judge|juez|juiz",
    r"fiscal": r"prosecutor|fiscal|promotor",
    r"senador|senadora": r"senator|senador",
    r"diputado|diputada|congresista|asambleista": r"representative|congress(man|woman)|lawmaker|assembly(man|woman|member)|diputad|deputad|legislador",
    r"gobernador|gobernadora": r"governor|gobernador|governador",
    r"alcalde|alcaldesa": r"mayor|alcalde|prefeit",
    r"ministro|ministra": r"minister|ministr|secretary",
    r"rapero|rapera": r"rapper|rapero|rapper",
    r"actor|actriz": r"actor|actress|atriz|actriz",
    r"cantante": r"singer|cantante|cantor",
    r"comediante|humorista": r"comedian|comediante|humorista",
    r"empresario|empresaria": r"businessman|businesswoman|entrepreneur|empresari",
}


def verificar_atributos(tarjeta: dict, texto_enviado: str) -> tuple[bool, list[str]]:
    """Nacionalidades, edades y cargos de personas solo si figuran en lo enviado al modelo.
    Lo que no figura se quita; si no se puede quitar limpio, se quita la oración (o la
    tarjeta, si estaba en el título)."""
    fuente = sin_tildes(texto_enviado)
    cambios = []

    # Nacionalidades
    for pais, (gentilicio, aceptadas) in NACIONALIDADES.items():
        if re.search(rf"\b({aceptadas})\b", fuente):
            continue
        patron = _patron_gentilicio(gentilicio)
        for campo in ("titulo", "resumen"):
            if patron.search(plano(tarjeta[campo])):
                tarjeta[campo] = _quitar_gentilicio(tarjeta[campo], patron)
                cambios.append(f"nacionalidad no indicada por la fuente quitada ({pais})")

    # Edades
    for campo in ("titulo", "resumen"):
        for m in list(_EDAD.finditer(sin_tildes(tarjeta[campo]))):
            numero = next(g for g in (m.group(2), m.group(5), m.group(6), m.group(7), m.group(8)) if g)
            if re.search(rf"\b{numero}(-| )years?(-| )old\b|\baged? {numero}\b|\b{numero} (anos|anos de edad)\b|\({numero}\)", fuente):
                continue
            sin_acentos = sin_tildes(tarjeta[campo])
            frase = re.compile(rf"(,\s*)?\bde {numero} anos(\s*de edad)?(,)?")
            mm = frase.search(sin_acentos)
            if mm:
                tarjeta[campo] = _limpiar_espacios(tarjeta[campo][:mm.start()] + tarjeta[campo][mm.end():])
            elif campo == "resumen":
                tarjeta[campo], _ = _quitar_oraciones_plano(tarjeta[campo], re.compile(rf"\b{numero} anos\b"))
            else:
                cambios.append(f"edad no indicada por la fuente en el título ({numero} años)")
                return False, cambios
            cambios.append(f"edad no indicada por la fuente quitada ({numero} años)")

    # Cargos y posiciones
    for cargo, aceptadas in CARGOS.items():
        patron = re.compile(rf"\b({cargo})(es|s)?\b")
        if re.search(rf"({aceptadas})", fuente):
            continue
        if patron.search(sin_tildes(tarjeta["titulo"])):
            cambios.append(f"cargo no indicado por la fuente en el título ({cargo.split('|')[0]})")
            return False, cambios
        if patron.search(sin_tildes(tarjeta["resumen"])):
            tarjeta["resumen"], n = _quitar_oraciones_plano(tarjeta["resumen"], patron)
            cambios.append(f"cargo no indicado por la fuente quitado ({cargo.split('|')[0]}, {n} oración/es)")

    if not tarjeta["resumen"].strip():
        cambios.append("resumen vacío tras quitar datos no presentes en las fuentes")
        return False, cambios
    return True, cambios


def _quitar_oraciones_plano(texto: str, patron: re.Pattern) -> tuple[str, int]:
    oraciones = _oraciones(texto)
    quedan = [o for o in oraciones if not patron.search(sin_tildes(o))]
    return " ".join(quedan), len(oraciones) - len(quedan)


# ---------------------------------------------------------------- clasificación

def ajustar_clasificacion(tarjeta: dict, items: list[dict]) -> str | None:
    """CONFIRMADO exige al menos una fuente de clase oficial o medio. Las notas sin fecha usan la
    fecha de la barrida, así que no bajan por eso; lo que solo cuenta Reddit o un foro queda EN DISCUSIÓN."""
    if tarjeta["clasificacion"] != "confirmado":
        return None
    if any(it.get("clase", "medio") in ("oficial", "medio") for it in items):
        return None
    tarjeta["clasificacion"] = "discusion"
    return "confirmado → en discusión (solo fuentes de comunidad: Reddit o foros)"


# ---------------------------------------------------------------- monedas

REGIONES_LATINAS = {"latam", "argentina"}
_CALIFICA_MONEDA = re.compile(r"(US\$|U\$S|USD|R\$|MXN|ARS|COP|CLP|PEN|UYU|€|£|d[oó]lar|dollar|pesos?\b|reais|reales|euros?)", re.I)
MARCA_MONEDA = "(moneda a confirmar)"


def corregir_monedas(tarjeta: dict, items: list[dict]) -> list[str]:
    """Si todas las fuentes de la tarjeta son latinoamericanas y escriben solo "$",
    el monto queda como "$ N (moneda a confirmar)": no se supone que son dólares."""
    if not items or any(it.get("region") not in REGIONES_LATINAS for it in items):
        return []
    if _CALIFICA_MONEDA.search(texto_items(items)):
        return []
    cambios = []
    for campo in ("titulo", "resumen"):
        texto = tarjeta[campo]
        # "US$ 5.506" o "5.506 dólares" escrito por el modelo -> "$5.506 (moneda a confirmar)"
        numero = r"(?>\d(?:[.,]?\d)*)"  # grupo atómico: no retrocede para volver a encontrar un monto ya marcado
        texto = re.sub(rf"(?:US\$|U\$S|\bUSD)\s?({numero})", rf"$\1 {MARCA_MONEDA}", texto)
        texto = re.sub(rf"\b({numero})\s?(?:d[oó]lares|USD)\b", rf"$\1 {MARCA_MONEDA}", texto)
        texto = re.sub(rf"(?<![A-Za-z$])\$\s?({numero}(?:\s?(?:mil|millones|M|K)\b)?)(?!\s?\(moneda)",
                       rf"$\1 {MARCA_MONEDA}", texto)
        if texto != tarjeta[campo]:
            cambios.append("moneda a confirmar (la fuente solo indica $)")
            tarjeta[campo] = texto
    return cambios


# ---------------------------------------------------------------- novedad real

_VACIAS = {"confirma", "confirmo", "confirmado", "confirmada", "detalla", "detallan", "detalles", "revela",
           "revelan", "nuevo", "nueva", "nuevos", "nuevas", "especificas", "especificos", "acciones", "informa",
           "senala", "indica", "reportes", "reporte", "segun", "ahora", "ademas", "tambien", "sobre", "previamente",
           "informacion", "datos", "tras", "desde", "hasta", "entre", "fueron", "habian", "habia", "para", "como",
           "esta", "este", "estos", "estas", "otra", "otro", "otros", "otras", "mas", "caso", "hecho", "conoce"}


def _raices(texto: str) -> set[str]:
    palabras = re.findall(r"[a-z0-9]+", sin_tildes(texto))
    return {p[:5] for p in palabras if len(p) >= 4 and p not in _VACIAS and not p.isdigit()}


def _numeros(texto: str) -> set[str]:
    return {re.sub(r"\D", "", n) for n in re.findall(r"\d[\d.,]*", texto) if len(re.sub(r"\D", "", n)) >= 2}


def parecido(texto: str, otro: str) -> float:
    """Proporción de las ideas de `texto` que ya están en `otro` (0 a 1)."""
    propias = _raices(texto)
    return len(propias & _raices(otro)) / len(propias) if propias else 0.0


def misma_noticia(texto: str, otro: str, umbral: float = 0.5) -> bool:
    """Misma noticia: comparte la mayoría de las ideas y no nombra a personas distintas."""
    if parecido(texto, otro) < umbral:
        return False
    otro_plano = sin_tildes(otro)
    return all(sin_tildes(p) in otro_plano for p in personas(texto))


def es_novedad_real(novedad: str, previo_texto: str) -> bool:
    """La novedad debe aportar una cifra nueva o al menos dos ideas que no estaban en la tarjeta previa."""
    if _numeros(novedad) - _numeros(previo_texto):
        return True
    return len(_raices(novedad) - _raices(previo_texto)) >= 2


# ---------------------------------------------------------------- regla máxima: todo debe estar en la fuente

_DIAS_SEMANA = {
    "lunes": "lunes|monday|segunda", "martes": "martes|tuesday|terca", "miercoles": "miercoles|wednesday|quarta",
    "jueves": "jueves|thursday|quinta", "viernes": "viernes|friday|sexta", "sabado": "sabado|saturday",
    "domingo": "domingo|sunday",
}
_MESES = {
    "enero": "enero|january|janeiro|jan", "febrero": "febrero|february|fevereiro|feb", "marzo": "marzo|march|marco|mar",
    "abril": "abril|april|apr", "mayo": "mayo|may|maio", "junio": "junio|june|junho|jun", "julio": "julio|july|julho|jul",
    "agosto": "agosto|august|aug", "septiembre": "septiembre|setiembre|september|setembro|sept?",
    "octubre": "octubre|october|outubro|oct", "noviembre": "noviembre|november|novembro|nov",
    "diciembre": "diciembre|december|dezembro|dec",
}
# Lugares que suelen traducirse: forma en español -> formas aceptadas en la fuente.
_LUGARES = {
    "londres": "london|londres", "nueva york": "new york|nova york|nueva york", "lisboa": "lisbon|lisboa",
    "praga": "prague|praga", "viena": "vienna|viena", "montecarlo": "monte carlo|montecarlo|monaco",
    "seul": "seoul|seul", "pekin": "beijing|pekin|pequim", "moscu": "moscow|moscu", "atenas": "athens|atenas",
    "varsovia": "warsaw|varsovia", "bruselas": "brussels|bruselas", "ginebra": "geneva|ginebra",
    "estambul": "istanbul|estambul", "marruecos": "morocco|marruecos|marrocos", "sudafrica": "south africa|sudafrica",
    "estados unidos": "united states|usa|u\\.s\\.|estados unidos", "reino unido": "united kingdom|uk|reino unido",
    "paises bajos": "netherlands|holland|paises baixos", "corea del sur": "south korea|korea|corea",
    "alemania": "germany|alemania|alemanha", "francia": "france|francia|franca", "italia": "italy|italia",
    "espana": "spain|espana|espanha", "japon": "japan|japon|japao", "belgica": "belgium|belgica", "suiza": "switzerland|suiza|suica",
    "suecia": "sweden|suecia", "noruega": "norway|noruega", "dinamarca": "denmark|dinamarca", "polonia": "poland|polonia",
    "rusia": "russia|rusia|russia", "irlanda": "ireland|irlanda", "escocia": "scotland|escocia", "inglaterra": "england|inglaterra",
    "grecia": "greece|grecia", "turquia": "turkey|turquia", "chipre": "cyprus|chipre", "malta": "malta",
    "brasil": "brazil|brasil", "mexico": "mexico", "peru": "peru", "panama": "panama", "canada": "canada",
}
_PALABRAS_LUGARES = {w for clave in _LUGARES for w in clave.split() if len(w) > 3}
_CITA = re.compile(r"[“\"«]([^”\"»]{12,})[”\"»]")
_NUMERO = re.compile(r"\d+(?:[.,]\d+)*")


def _fuente_normalizada(texto: str) -> tuple[str, set[str], set[str]]:
    plano = " " + re.sub(r"[^\w$]+", " ", sin_tildes(texto)) + " "
    digitos = {re.sub(r"\D", "", n) for n in _NUMERO.findall(texto)}
    raices = {p[:5] for p in plano.split() if len(p) >= 5}
    return plano, digitos, raices


def _palabra_comun(base: str) -> bool:
    from .control import _GENERICAS, _NO_NOMBRES
    return base in _NO_NOMBRES or base in _GENERICAS or base in _NO_PERSONA


def no_rastreables(oracion: str, fuente_texto: str) -> list[str]:
    """Afirmaciones de una oración que no figuran en el texto de su fuente."""
    plano, digitos, raices = _fuente_normalizada(fuente_texto)
    oracion_sin_marcas = oracion.replace(MARCA_MONEDA, "").replace("[nacionalidad a confirmar]", "")
    faltan = []
    for n in _NUMERO.findall(oracion_sin_marcas):
        d = re.sub(r"\D", "", n)
        if d and d not in digitos:
            faltan.append(f"cifra «{n}»")
    for cita in _CITA.findall(oracion):
        if " " + re.sub(r"[^\w$]+", " ", sin_tildes(cita)).strip() + " " not in plano:
            faltan.append(f"cita «{cita[:40]}»")
    base_oracion = sin_tildes(oracion)
    for palabra, aceptadas in list(_DIAS_SEMANA.items()) + list(_MESES.items()):
        if re.search(rf"\b{palabra}\b", base_oracion) and not re.search(rf" ({aceptadas}) ", plano):
            faltan.append(f"fecha «{palabra}»")
    for lugar, aceptadas in _LUGARES.items():
        if re.search(rf"\b{lugar}\b", base_oracion) and not re.search(rf" ({aceptadas}) ", plano):
            faltan.append(f"lugar «{lugar}»")
    # Nombres propios (lugares, torneos, empresas) que no figuran: se omite la primera palabra de la oración.
    palabras = re.findall(r"(?<=\s)[A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü$'-]{2,}", " " + oracion_sin_marcas.split(" ", 1)[-1])
    for p in palabras:
        base = sin_tildes(p).strip("'$-")
        if re.search(r"\d", base) or re.fullmatch(r"(us|u|r|mxn|ars|cop|clp|pen|a|c)\$?", base):
            continue  # montos ("US$250"): las cifras ya se verificaron arriba
        if _palabra_comun(base) or base in _PALABRAS_LUGARES:
            continue  # palabras comunes y lugares traducidos (estos ya se verificaron arriba)
        if f" {base} " in plano or (len(base) >= 5 and base[:5] in raices):
            continue
        faltan.append(f"nombre propio «{p}»")
    return faltan


def depurar_texto(texto: str, fuente_texto: str) -> tuple[str, list[str]]:
    """Quita las oraciones con afirmaciones que no figuran en la fuente."""
    quedan, quitadas = [], []
    for o in _oraciones(texto):
        faltan = no_rastreables(o, fuente_texto)
        if faltan:
            quitadas.append(f"oración quitada ({', '.join(faltan)}): «{o[:60]}»")
        else:
            quedan.append(o)
    return " ".join(quedan), quitadas


def aplicar_regla_maxima(tarjeta: dict, fuente_texto: str) -> tuple[bool, list[str]]:
    """Regla máxima para una tarjeta común: título, resumen y "qué cambió" deben poder rastrearse
    al texto enviado para esa tarjeta. Si falla el título o el "qué cambió", o el resumen queda
    vacío, la tarjeta se descarta."""
    cambios = []
    conservar, atributos = verificar_atributos(tarjeta, fuente_texto)
    cambios += atributos
    if not conservar:
        return False, cambios
    faltan = no_rastreables(tarjeta["titulo"], fuente_texto)
    if faltan:
        return False, cambios + [f"título con datos que no están en la fuente ({', '.join(faltan)})"]
    if tarjeta.get("novedad"):
        faltan = no_rastreables(tarjeta["novedad"], fuente_texto)
        if faltan:
            return False, cambios + [f"«qué cambió» con datos que no están en la fuente ({', '.join(faltan)})"]
    tarjeta["resumen"], quitadas = depurar_texto(tarjeta["resumen"], fuente_texto)
    cambios += quitadas
    if not tarjeta["resumen"].strip():
        return False, cambios + ["resumen vacío tras quitar lo que no está en la fuente"]
    return True, cambios
