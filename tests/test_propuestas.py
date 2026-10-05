"""Propuestas de diseño y experimento de imágenes (sin red)."""

import importlib.util
import json
import re

from bs4 import BeautifulSoup

from mesa.dias import cargar_dias
from mesa.util import RAIZ


def _modulo(nombre):
    spec = importlib.util.spec_from_file_location(nombre, RAIZ / "scripts" / f"{nombre}.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


imagenes = _modulo("probar_imagenes")
propuestas = _modulo("generar_propuestas")


# ---------------------------------------------------------------- extracción de imágenes

def test_og_image_y_twitter_image():
    html = '<html><head><meta property="og:image" content="/fotos/nota.jpg"></head></html>'
    assert imagenes.imagen_de_pagina(html, "https://medio.com/nota") == ("https://medio.com/fotos/nota.jpg", "og:image")
    html = '<html><head><meta name="twitter:image" content="https://cdn.medio.com/a.png"></head></html>'
    assert imagenes.imagen_de_pagina(html, "https://medio.com/nota") == ("https://cdn.medio.com/a.png", "twitter:image")
    assert imagenes.imagen_de_pagina("<html></html>", "https://medio.com/nota") == (None, None)


def test_nunca_imagenes_de_gipsyteam():
    html = '<html><head><meta property="og:image" content="https://latam.gipsyteam.com/img/a.jpg"></head></html>'
    assert imagenes.imagen_de_pagina(html, "https://medio.com/nota") == (None, None)
    feed = """<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
      <item><title>Nota uno</title><link>https://medio.com/uno</link>
        <media:content url="https://gipsyteam.com.br/a.jpg" medium="image"/></item></channel></rss>"""
    assert imagenes.imagenes_de_feed(feed) == {}


def test_imagen_del_feed_media_content_y_enclosure():
    feed = """<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>
      <item><title>Nota uno</title><link>https://medio.com/uno?utm_source=rss</link>
        <media:content url="https://medio.com/uno.jpg" medium="image"/></item>
      <item><title>Nota dos</title><link>https://medio.com/dos</link>
        <enclosure url="https://medio.com/dos.png" type="image/png" length="1000"/></item>
      <item><title>Nota tres</title><link>https://medio.com/tres</link>
        <enclosure url="https://medio.com/tres.mp3" type="audio/mpeg" length="1000"/></item>
    </channel></rss>"""
    resultado = imagenes.imagenes_de_feed(feed)
    assert resultado == {"https://medio.com/uno": "https://medio.com/uno.jpg",
                         "https://medio.com/dos": "https://medio.com/dos.png"}


# ---------------------------------------------------------------- propuestas

def _generar(tmp_path, imagenes_extra=None):
    ultimo = cargar_dias(RAIZ)[0]
    t = ultimo["corridas"][-1]["tarjetas"][0]
    mapa = {(ultimo["fecha"], t["titulo"]): "https://medio.com/foto.jpg"}
    mapa.update(imagenes_extra or {})
    escritos = propuestas.generar(RAIZ, tmp_path, mapa)
    return escritos, t


def test_genera_solo_en_la_carpeta_de_propuestas(tmp_path):
    index = RAIZ / "docs" / "index.html"
    antes = index.read_bytes() if index.exists() else None
    escritos, _ = _generar(tmp_path)
    assert {p.name for p in escritos} == {"propuesta-a.html", "propuesta-b.html", "propuesta-a.css",
                                          "propuesta-b.css", "filtros.js"}
    assert all(p.parent == tmp_path for p in escritos)
    assert (index.read_bytes() if index.exists() else None) == antes


def test_todo_el_historial_con_todos_los_enlaces(tmp_path):
    _generar(tmp_path)
    originales = []
    for d in cargar_dias(RAIZ):
        for c in d["corridas"]:
            originales += c.get("tarjetas", []) + ((c.get("escena_latam") or {}).get("tarjetas") or [])
    for nombre in ("propuesta-a", "propuesta-b"):
        sopa = BeautifulSoup((tmp_path / f"{nombre}.html").read_text(encoding="utf-8"), "html.parser")
        tarjetas = sopa.select("[data-tarjeta]")
        assert len(tarjetas) == len(originales)
        enlaces = {a["href"] for a in sopa.select("[data-tarjeta] a")}
        for t in originales:
            for f in t["fuentes"]:
                assert f["url"] in enlaces
        clasifs = {t["data-clasif"] for t in tarjetas}
        assert clasifs <= {"confirmado", "discusion", "rumor"}
        # Clasificación visible como texto en cada tarjeta, no solo por color.
        for t in tarjetas:
            assert t.find(string=re.compile(r"Confirmado|En discusión|Rumor"))


def test_filtros_y_buscador_presentes(tmp_path):
    _generar(tmp_path)
    for nombre in ("propuesta-a", "propuesta-b"):
        sopa = BeautifulSoup((tmp_path / f"{nombre}.html").read_text(encoding="utf-8"), "html.parser")
        for id_ in ("f-buscar", "f-clasif", "f-fuente", "f-dia", "f-latam", "f-limpiar", "f-contador"):
            assert sopa.find(id=id_), id_
        assert [o["value"] for o in sopa.select("#f-clasif option")] == ["", "confirmado", "discusion", "rumor"]
        assert sopa.find("script", src="filtros.js")
        assert sopa.find(class_="destacado") or sopa.find(class_="grupo--latam")


def test_imagenes_con_reemplazo_y_sin_referrer(tmp_path):
    _, t = _generar(tmp_path, {("2026-10-04", "x"): "https://gipsyteam.com/a.jpg"})
    for nombre in ("propuesta-a", "propuesta-b"):
        texto = (tmp_path / f"{nombre}.html").read_text(encoding="utf-8")
        assert "gipsyteam" not in texto
        sopa = BeautifulSoup(texto, "html.parser")
        imgs = sopa.select("[data-tarjeta] img")
        assert [i["src"] for i in imgs] == ["https://medio.com/foto.jpg"]
        assert imgs[0]["referrerpolicy"] == "no-referrer" and imgs[0]["loading"] == "lazy"
        assert "sin-imagen" in imgs[0]["onerror"]


def test_css_con_variables_sin_modo_oscuro_ni_librerias():
    for nombre in ("propuesta-a.css", "propuesta-b.css"):
        css = (RAIZ / "templates" / "propuestas" / nombre).read_text(encoding="utf-8")
        assert css.split(":root", 1)[0].count("{") == 0  # las variables van primero
        assert "--fondo" in css and "--tinta" in css
        assert "prefers-color-scheme" not in css
        assert "url(" not in css and "@import" not in css
    for nombre in ("propuesta-a.html.j2", "propuesta-b.html.j2"):
        html = (RAIZ / "templates" / "propuestas" / nombre).read_text(encoding="utf-8")
        externos = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert all("fonts.googleapis.com" in u or "fonts.gstatic.com" in u for u in externos)


def test_texto_sin_voseo():
    voseo = re.compile(r"\b(probá|limpiá|buscá|elegí|tocá|mirá|tenés|podés|querés|sabés|hacé|usá|verificá)\b", re.I)
    for archivo in (RAIZ / "templates" / "propuestas").iterdir():
        assert not voseo.search(archivo.read_text(encoding="utf-8")), archivo.name


def test_informe_de_imagenes_sin_gipsyteam():
    ruta = RAIZ / "docs" / "propuestas" / "imagenes-prueba.json"
    if ruta.exists():
        informe = json.loads(ruta.read_text(encoding="utf-8"))
        assert informe["tokens_modelo"] == 0
        assert "gipsyteam" not in ruta.read_text(encoding="utf-8").lower()
