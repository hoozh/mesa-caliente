"""Cambio 1: resultados de fuentes sin fecha y hora de las notas."""

import json
from datetime import date, datetime, timezone

from bs4 import BeautifulSoup

from mesa import modelo, pagina, recoleccion
from mesa.dias import agregar_corrida

from .conftest import AHORA, fuente

AJ = {"max_tarjetas": 12, "max_tarjetas_latam": 6, "precios_usd_por_millon": {}}
HOY = date(2026, 10, 6)


def _llamar(respuesta):
    return lambda *a: (json.dumps(respuesta), {"tokens_entrada": 1, "tokens_salida": 1})


def test_resultado_sin_fecha_de_oficial_o_medio_usa_la_fecha_de_la_barrida_y_puede_ser_confirmado():
    """Antes, un resultado de WSOP.com (sin fecha) quedaba EN DISCUSIÓN y decía "sin fecha en la fuente · vista el…"."""
    oficial = {"fuente": "WSOP.com", "url": "https://www.wsop.com/news/x/", "titulo": "Fulano Inventado wins the Example Poker Open Main Event",
               "primera_linea": "", "fecha": None, "hora": None, "region": "internacional", "clase": "oficial"}
    r = modelo.resumir([oficial], AJ, _llamar({"tarjetas": [{"ids": [1], "clasificacion": "confirmado",
        "titulo": "Fulano Inventado gana el Main Event del Example Poker Open", "resumen": "Ganó el Main Event del Example Poker Open."}]}), hoy=HOY)
    t = r["tarjetas"][0]
    assert t["clasificacion"] == "confirmado"
    assert t["fecha"] == "6 oct 2026"  # la fecha de la barrida, sin ningún texto agregado
    assert t["fuentes"][0]["fecha"] == "2026-10-06" and "hora" not in t["fuentes"][0]


def test_hora_solo_cuando_la_fuente_la_da(tmp_path):
    con_hora = recoleccion.parsear_rss(
        '<?xml version="1.0"?><rss version="2.0"><channel><item><title>Fulano Inventado gana</title>'
        '<link>https://a.com/1</link><pubDate>Mon, 05 Oct 2026 14:30:00 +0000</pubDate></item>'
        '<item><title>Mengano Ficticio gana</title><link>https://a.com/2</link><pubDate>Mon, 05 Oct 2026 00:00:00 +0000</pubDate></item>'
        '</channel></rss>', fuente())
    assert con_hora[0]["hora"] == "14:30" and con_hora[1]["hora"] is None

    agregar_corrida(tmp_path, HOY, {"id": "2026-10-06T12:45:00Z", "origen": "automatica", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "5 oct 2026", "titulo": "T", "resumen": "R", "fuentes": [
            {"nombre": "Con hora", "fecha": "2026-10-05", "hora": "14:30", "url": "https://a.com/1"},
            {"nombre": "Sin hora", "fecha": "2026-10-05", "url": "https://a.com/2"}]}],
        "escena_latam": {"tarjetas": [], "texto": None}})
    import shutil
    from mesa.util import RAIZ
    shutil.copytree(RAIZ / "templates", tmp_path / "templates")
    pagina.generar(tmp_path, HOY, AHORA)
    sopa = BeautifulSoup((tmp_path / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser")
    filas = [" ".join(li.get_text(" ").split()) for li in sopa.select("ul.sources-list li")]
    assert any("Con hora" in f and "14:30 UTC" in f for f in filas)
    assert any("Sin hora" in f and "UTC" not in f and "vista" not in f and "sin fecha" not in f for f in filas)
