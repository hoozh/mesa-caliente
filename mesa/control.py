"""Controles del programa sobre lo que escribe el modelo.

Son una red de seguridad: el prompt ya pide estas reglas, pero el programa
las verifica con el texto de las fuentes y corrige lo que se pueda corregir.
"""

from __future__ import annotations

import re
import unicodedata

# ---------------------------------------------------------------- términos

# Premios de torneos. Las claves son la forma en español que se usa en las tarjetas.
TERMINOS = {
    "brazalete": {"singular": "brazalete", "plural": "brazaletes",
                  "fuente": r"\b(brazaletes?|bracelets?|braceletes?)\b"},
    "anillo": {"singular": "anillo", "plural": "anillos",
               "fuente": r"\b(anillos?|rings?|anel|an[eé]is)\b(?!\s+games?)"},
    "trofeo": {"singular": "trofeo", "plural": "trofeos",
               "fuente": r"\b(trofeos?|troph(?:y|ies)|trof[eé]us?)\b"},
}


def _texto_fuentes(items: list[dict]) -> str:
    partes = []
    for it in items:
        partes += [it.get("titulo") or "", it.get("primera_linea") or "", it.get("extracto") or ""]
    return " ".join(partes).lower()


def corregir_terminos(tarjeta: dict, items: list[dict]) -> list[str]:
    """Si la tarjeta usa un premio que las fuentes no mencionan (p. ej. "brazalete")
    y las fuentes usan otro (p. ej. "ring"), reemplaza por el término de la fuente.
    Devuelve la lista de cambios hechos."""
    fuentes = _texto_fuentes(items)
    en_fuente = {k for k, t in TERMINOS.items() if re.search(t["fuente"], fuentes, re.I)}
    cambios = []
    for clave, t in TERMINOS.items():
        patron = rf"\b({t['singular']}|{t['plural']})\b"
        texto = f"{tarjeta['titulo']} {tarjeta['resumen']}"
        if clave in en_fuente or not re.search(patron, texto, re.I):
            continue
        alternativas = [k for k in en_fuente if not re.search(
            rf"\b({TERMINOS[k]['singular']}|{TERMINOS[k]['plural']})\b", texto, re.I)]
        if len(alternativas) != 1:
            continue  # sin un reemplazo claro, no se toca
        nuevo = TERMINOS[alternativas[0]]

        def reemplazo(m, nuevo=nuevo, t=t):
            palabra = m.group(0)
            forma = nuevo["plural"] if palabra.lower() == t["plural"] else nuevo["singular"]
            return forma.capitalize() if palabra[0].isupper() else forma
        for campo in ("titulo", "resumen"):
            tarjeta[campo] = re.sub(patron, reemplazo, tarjeta[campo], flags=re.I)
        cambios.append(f"{t['singular']} → {nuevo['singular']}")
    return cambios


# ---------------------------------------------------------------- nacionalidad

MARCA_NACIONALIDAD = "[nacionalidad a confirmar]"

# País -> gentilicios en español, inglés y portugués (sin tildes, en minúscula).
PAISES = {
    "Argentina": ["argentin[oa]s?", "argentine", "argentinian"],
    "Brasil": ["brasilen[oa]s?", "brazilian", "brasileir[oa]s?"],
    "Chile": ["chilen[oa]s?", "chilean"],
    "Colombia": ["colombian[oa]?s?"],
    "Perú": ["peruan[oa]s?", "peruvian"],
    "Uruguay": ["uruguay[oa]s?", "uruguaian[oa]?s?", "uruguayan"],
    "Paraguay": ["paraguay[oa]s?", "paraguaian[oa]?s?", "paraguayan"],
    "Bolivia": ["bolivian[oa]?s?"],
    "Venezuela": ["venezolan[oa]s?", "venezuelan[oa]?s?"],
    "Ecuador": ["ecuatorian[oa]s?", "ecuadorian", "equatorian[oa]s?"],
    "México": ["mexican[oa]?s?"],
    "República Dominicana": ["dominican[oa]?s?"],
    "Costa Rica": ["costarricenses?", "costa rican"],
    "Panamá": ["panamen[oa]s?", "panamanian", "panamenh[oa]s?"],
    "Guatemala": ["guatemaltec[oa]s?", "guatemalan"],
    "Honduras": ["hondurenh?[oa]s?", "honduran"],
    "El Salvador": ["salvadoren[oa]s?", "salvadoran"],
    "Cuba": ["cuban[oa]?s?"],
    "Puerto Rico": ["puertorriquen[oa]s?", "puerto rican"],
    "Nicaragua": ["nicaraguenses?", "nicaraguan"],
}


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def paises_mencionados(texto: str, incluir_nombre_pais: bool = False) -> set[str]:
    plano = _sin_tildes(texto)
    encontrados = set()
    for pais, gentilicios in PAISES.items():
        patrones = list(gentilicios)
        if incluir_nombre_pais:
            patrones.append(re.escape(_sin_tildes(pais)))
        if re.search(r"\b(" + "|".join(patrones) + r")\b", plano):
            encontrados.add(pais)
    return encontrados


def asegurar_nacionalidad(tarjeta: dict, items: list[dict], es_latam: bool) -> str | None:
    """Si el titular o la primera línea de una fuente indica la nacionalidad y la
    tarjeta no la menciona, la agrega. En la escena latina, si nadie la indica,
    agrega la marca [nacionalidad a confirmar]. Devuelve el cambio hecho o None."""
    texto = f"{tarjeta['titulo']} {tarjeta['resumen']}"
    en_tarjeta = paises_mencionados(texto, incluir_nombre_pais=True)
    en_fuentes = set()
    for it in items:
        en_fuentes |= paises_mencionados(f"{it.get('titulo') or ''} {it.get('primera_linea') or ''}")
    faltan = sorted(en_fuentes - en_tarjeta)
    if faltan:
        tarjeta["resumen"] = tarjeta["resumen"].rstrip() + f" Nacionalidad indicada por la fuente: {', '.join(faltan)}."
        return "nacionalidad agregada"
    if es_latam and not en_tarjeta and not en_fuentes and MARCA_NACIONALIDAD not in texto:
        tarjeta["titulo"] = tarjeta["titulo"].rstrip() + f" {MARCA_NACIONALIDAD}"
        return "marca de nacionalidad agregada"
    return None


# ---------------------------------------------------------------- repetidos

# Palabras de torneos y salas que se repiten entre hechos distintos: no identifican un hecho.
_GENERICAS = {
    "wsop", "wpt", "ept", "wcoop", "scoop", "bsop", "clsop", "cap", "lapt", "pgt", "apt", "gopc",
    "main", "event", "evento", "principal", "circuit", "circuito", "series", "online", "live",
    "high", "roller", "rollers", "super", "championship", "campeonato", "festival", "tour", "open",
    "final", "mesa", "dia", "day", "pokerstars", "ggpoker", "ggmillion", "ggmillions", "acr", "wpn",
    "poker", "plo", "nlh", "holdem", "mystery", "bounty", "millions", "global", "world", "us",
    "medium", "low", "mini", "las", "vegas", "the", "los", "del", "con", "por", "para", "una",
}


def _claves(titulo: str) -> set[str]:
    """Palabras que empiezan con mayúscula (nombres propios), sin las genéricas."""
    palabras = re.findall(r"[A-ZÁÉÍÓÚÑ][\wÁÉÍÓÚÑáéíóúñ$'.-]+", titulo)
    claves = set()
    for p in palabras:
        p = _sin_tildes(p).strip("$.'-")
        if len(p) >= 3 and p not in _GENERICAS and not p.isdigit():
            claves.add(p)
    return claves


def parece_repetida(titulo: str, previos: list[str]) -> str | None:
    """Devuelve el título previo que cubre el mismo hecho, o None.
    Criterio prudente: comparten al menos 2 nombres propios no genéricos y esos
    nombres son la mayor parte (60 % o más) de los del título nuevo."""
    nuevas = _claves(titulo)
    if not nuevas:
        return None
    for previo in previos:
        comunes = nuevas & _claves(previo)
        if len(comunes) >= 2 and len(comunes) / len(nuevas) >= 0.6:
            return previo
    return None
