"""Controles de estilo después de la redacción, sin modelo (pedido de Hoozh, 26 nov 2026).

No cambian el texto: si algo aparece, la tarjeta queda marcada para revisión en el administrador
(campo 'revisar' con los motivos).
- Comillas angulares « »: en GipsyTeam van comillas rectas dobles.
- Frases que hablan de la fuente en vez de la noticia ("la fuente no precisa", "según la información disponible").
- Citas que siguen en inglés.
"""

from __future__ import annotations

import re

_META = re.compile(
    r"\b(?:las?\s+fuentes?|la\s+nota|el\s+art[ií]culo|el\s+informe|el\s+reporte|el\s+texto)\s+(?:original\s+)?"
    r"(?:no\s+)?(?:precisa|precisan|indica|indican|detalla|detallan|especifica|especifican|menciona|mencionan|aclara|"
    r"aclaran|informa|informan|confirma|confirman|da\s+a\s+conocer|ofrece|brinda|incluye|revela)\b"
    r"|\bseg[uú]n\s+la\s+informaci[oó]n\s+disponible\b"
    r"|\b(?:con|de)\s+la\s+informaci[oó]n\s+disponible\b"
    r"|\bno\s+se\s+(?:precisa|precisan|especifica|especifican|detalla|detallan|indica|indican|menciona|mencionan|"
    r"informa|informan|aclara|aclaran|dispone\s+de|conoce|conocen)\b"
    r"|\bno\s+(?:hay|se\s+dispone\s+de|existen?)\s+(?:m[aá]s\s+)?(?:detalles|datos|informaci[oó]n)\b"
    r"|\b(?:sin|no\s+hay)\s+(?:m[aá]s\s+)?(?:detalles|datos|informaci[oó]n)\s+(?:adicional(?:es)?|disponibles?)\b",
    re.I)

_CITA = re.compile(r"\"([^\"]{12,})\"|“([^”]{12,})”|«([^»]{12,})»|‘([^’]{12,})’")
_EN = {"the", "and", "to", "of", "is", "i", "you", "it", "was", "that", "we", "my", "in", "for", "this", "have", "with",
       "but", "be", "are", "not", "they", "he", "she", "his", "her", "what", "just", "so", "on", "at", "about", "like",
       "me", "do", "don't", "it's", "i'm", "can", "will", "would", "there", "been", "were", "our", "your", "out", "get"}
_ES = {"el", "la", "los", "las", "de", "que", "y", "en", "un", "una", "es", "por", "con", "para", "no", "se", "lo",
       "mi", "me", "su", "fue", "pero", "como", "más", "muy", "ya", "yo", "este", "esta"}


def cita_en_ingles(texto: str) -> list[str]:
    """Citas (entre comillas) de 4 palabras o más que parecen estar en inglés."""
    salida = []
    for m in _CITA.finditer(texto or ""):
        cita = next(g for g in m.groups() if g)
        palabras = re.findall(r"[a-záéíóúñü']+", cita.lower())
        if len(palabras) < 4:
            continue
        en = sum(p in _EN for p in palabras)
        es = sum(p in _ES for p in palabras)
        if en >= 2 and en / len(palabras) >= 0.2 and en > es:
            salida.append(cita)
    return salida


def motivos(tarjeta: dict) -> list[str]:
    texto = "\n".join(str(tarjeta.get(k) or "") for k in ("titulo", "resumen", "cuerpo", "novedad"))
    salida = []
    if "«" in texto or "»" in texto:
        salida.append("comillas « » (van comillas rectas dobles)")
    meta = _META.search(texto)
    if meta:
        salida.append(f"habla de la fuente en vez de la noticia: «{meta.group(0)}»")
    for cita in cita_en_ingles(texto)[:2]:
        salida.append(f"cita en inglés sin traducir: «{cita[:80]}»")
    return salida


def marcar(tarjetas: list[dict]) -> list[dict]:
    """Agrega 'revisar' a las tarjetas con problemas. Devuelve los controles para el registro de la corrida."""
    controles = []
    for t in tarjetas:
        m = motivos(t)
        if m:
            t["revisar"] = m
            controles += [{"titulo": t.get("titulo", ""), "motivo": f"para revisar: {x}"} for x in m]
    return controles
