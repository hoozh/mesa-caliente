"""Filtro de avances de "día 1": solo se publican los de torneos relevantes."""

from __future__ import annotations

import re
from pathlib import Path

from .calidad import sin_tildes
from .util import leer_json

# "lidera el día 1", "Day 1A", "Día 1B", "Dia 1C", "chip leader tras el día 1", "Day One", "flight 1A"…
_DIA1 = re.compile(r"\b(d[ií]a|day|dia)\s*(1|one|uno)\s*[a-h]?\b|\bflight\s*1?[a-h]\b|\bprimer d[ií]a\b|\bfirst day\b",
                   re.I)
# Día 2 en adelante, mesas finales, resultados y ganadores pasan siempre.
_AVANZADO = re.compile(
    r"\b(d[ií]a|day|dia)\s*([2-9]|two|three|dos|tres|final)\b|\bmesa final\b|\bfinal table\b|\bheads-?up\b|"
    r"\bgan[aoóe]\w*|\bwins?\b|\bwon\b|\bcampe[oó]n\w*|\bchampion\w*|\bconquist\w*|\bvictoria\b|\bvictory\b|"
    r"\btakes? down\b|\bresultados?\b|\bresults?\b|\bse impon\w*|\bse llev\w*", re.I)


def cargar_relevantes(raiz: Path) -> list[dict]:
    datos = leer_json(Path(raiz) / "config" / "torneos_relevantes.json", {}) or {}
    return [t for grupo in ("internacionales", "regionales") for t in datos.get(grupo, [])]


def torneo_relevante(texto: str, relevantes: list[dict]) -> str | None:
    for t in relevantes:
        for clave in t.get("claves", []):
            # Siglas en mayúsculas exactas ("CAP"); nombres largos sin distinguir mayúsculas.
            if clave.isupper():
                if re.search(rf"\b{re.escape(clave)}\b", texto):
                    return t["nombre"]
            elif re.search(rf"\b{re.escape(sin_tildes(clave))}\b", sin_tildes(texto)):
                return t["nombre"]
    return None


def _torneo_mencionado(texto: str) -> str:
    """Nombre del torneo tal como aparece en el titular, para el registro."""
    palabra = r"[A-Z][\w'’&$-]*"
    m = re.search(rf"(?<!\w)((?:{palabra}\s+(?:(?:of|the|de|del|la)\s+)?){{0,5}}(?:Main Event|Series|Tour|Championship|Festival|Open|"
                  rf"Cup|Classic|Circuito|Serie|Liga|Copa|Campeonato|Evento Principal)\b"
                  rf"(?:\s+(?:(?:of|the|de|del|la)\s+)?{palabra}){{0,3}})", texto)
    if m:
        return m.group(1).strip()
    siglas = re.findall(r"\b[A-Z]{2,6}\b", texto)
    return " ".join(siglas[:2]) if siglas else "no identificado"


def descartador_dia1(relevantes: list[dict]):
    """Devuelve una función: None si la nota pasa, o el motivo del descarte."""
    def descartar(item: dict) -> dict | None:
        texto = f"{item.get('titulo') or ''}. {item.get('primera_linea') or ''}"
        if not _DIA1.search(texto) or _AVANZADO.search(texto):
            return None
        if torneo_relevante(texto, relevantes):
            return None
        return {"torneo": _torneo_mencionado(texto),
                "motivo": "avance de día 1 de un torneo que no está en config/torneos_relevantes.json"}
    return descartar
