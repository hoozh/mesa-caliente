"""Datos de ejemplo compartidos. Ninguna prueba usa red ni la API."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mesa.recoleccion import Respuesta

RAIZ = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
AHORA = datetime(2026, 10, 4, 12, 45, tzinfo=timezone.utc)


def leer_fixture(nombre: str) -> str:
    return (FIXTURES / nombre).read_text(encoding="utf-8")


def fuente(nombre="Ejemplo", url="https://ejemplo-poker.com/feed/", tipo="rss", region="latam", **extra):
    return {"nombre": nombre, "url": url, "tipo": tipo, "idioma": "es", "region": region,
            "estado": "activa", "fallos_consecutivos": 0, "ultimo_ok": None, **extra}


class DescargaFalsa:
    """Responde según un diccionario url -> (estado, texto) o excepción."""

    def __init__(self, respuestas: dict):
        self.respuestas = respuestas
        self.pedidas: list[str] = []

    def __call__(self, url: str) -> Respuesta:
        self.pedidas.append(url)
        r = self.respuestas.get(url)
        if r is None:
            return Respuesta(404, "", url)
        if isinstance(r, Exception):
            raise r
        estado, texto = r
        return Respuesta(estado, texto, url)


@pytest.fixture(autouse=True)
def sin_red(monkeypatch):
    """Cualquier intento de conexión real hace fallar la prueba."""
    import socket

    def prohibido(*args, **kwargs):
        raise AssertionError("Las pruebas no deben usar la red")
    monkeypatch.setattr(socket.socket, "connect", prohibido)
    monkeypatch.setattr(socket, "create_connection", prohibido)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def raiz_temporal(tmp_path: Path) -> Path:
    """Copia mínima del proyecto (config + plantillas) en una carpeta temporal."""
    (tmp_path / "config").mkdir()
    shutil.copy(RAIZ / "config" / "ajustes.json", tmp_path / "config" / "ajustes.json")
    shutil.copytree(RAIZ / "templates", tmp_path / "templates")
    fuentes = [
        fuente("Ejemplo", "https://ejemplo-poker.com/feed/"),
        fuente("Portada", "https://portada-poker.com/news/", tipo="html", region="internacional",
               incluir="/news/[^/]+/?$"),
    ]
    (tmp_path / "config" / "fuentes.json").write_text(
        json.dumps({"fuentes": fuentes}, ensure_ascii=False), encoding="utf-8")
    return tmp_path


@pytest.fixture
def descarga_ejemplo() -> DescargaFalsa:
    return DescargaFalsa({
        "https://ejemplo-poker.com/feed/": (200, leer_fixture("feed.xml")),
        "https://portada-poker.com/news/": (200, leer_fixture("portada.html")),
    })
