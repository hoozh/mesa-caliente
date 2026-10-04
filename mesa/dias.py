"""Archivos de datos por día: data/dias/AAAA-MM-DD.json.

Cada archivo guarda la lista de corridas de ese día. Una corrida solo puede
agregar al archivo de su propia fecha; nunca toca días anteriores.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .util import fecha_larga, leer_json, escribir_json


def carpeta_dias(raiz: Path) -> Path:
    return Path(raiz) / "data" / "dias"


def ruta_dia(raiz: Path, dia: date) -> Path:
    return carpeta_dias(raiz) / f"{dia.isoformat()}.json"


def agregar_corrida(raiz: Path, dia: date, corrida: dict) -> Path:
    ruta = ruta_dia(raiz, dia)
    datos = leer_json(ruta) or {"fecha": dia.isoformat(), "titulo": fecha_larga(dia), "corridas": []}
    datos.setdefault("corridas", []).append(corrida)
    escribir_json(ruta, datos)
    return ruta


def cargar_dias(raiz: Path) -> list[dict]:
    """Todos los días, del más reciente al más antiguo."""
    dias = []
    for ruta in sorted(carpeta_dias(raiz).glob("*.json"), reverse=True):
        datos = leer_json(ruta)
        if isinstance(datos, dict) and datos.get("fecha"):
            dias.append(datos)
    dias.sort(key=lambda d: d["fecha"], reverse=True)
    return dias
