"""Filtro por día, registro de vistos, descartes, caso Aido y nombres propios."""

import json
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from mesa import control, corrida, modelo
from mesa.dias import agregar_corrida
from mesa.recoleccion import filtrar_fuente, modo_de_lectura, recolectar
from mesa.util import leer_json, normalizar_url

from .conftest import AHORA, DescargaFalsa, fuente

AJUSTES = {"max_titulares_por_fuente": 10, "dias_maximos_atras": 3, "margen_horas": 3,
           "max_titulares_enviados": 120}
CORTE = AHORA - timedelta(hours=27)  # p. ej. última corrida exitosa ayer a las 12:45 menos 3 horas


def _item(n, fecha=None, fuente_="Prueba"):
    return {"fuente": fuente_, "url": f"https://prueba.com/nota-{n}", "titulo": f"Nota de prueba número {n}",
            "fecha": fecha, "primera_linea": "", "region": "internacional"}


def _rss(entradas):
    items = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>Texto de la nota de prueba.</description>"
        + (f"<pubDate>{f}</pubDate>" if f else "") + "</item>" for t, u, f in entradas)
    return f'<?xml version="1.0"?><rss version="2.0"><channel><title>X</title>{items}</channel></rss>' + " " * 300


# ---------------------------------------------------------------- modo de lectura

def test_modo_de_lectura():
    assert modo_de_lectura([_item(1, "2026-10-04T10:00:00+00:00"), _item(2, "2026-10-03T10:00:00+00:00")]) == "fecha"
    assert modo_de_lectura([_item(1), _item(2)]) == "orden"
    misma = "2026-10-04T18:59:27+00:00"
    assert modo_de_lectura([_item(n, misma) for n in range(5)]) == "orden"
    # Poker.org real: 68 notas a las 21:59:25 y 32 a las 21:59:26 del 4 de octubre.
    pokerorg = [_item(n, "2026-10-04T21:59:25+00:00") for n in range(68)] + \
               [_item(n, "2026-10-04T21:59:26+00:00") for n in range(68, 100)]
    assert modo_de_lectura(pokerorg) == "orden"
    assert modo_de_lectura([_item(1, "2026-10-04T10:00:00+00:00"), _item(2)]) == "orden"


def test_fuente_con_fechas_toma_solo_desde_el_corte():
    items = [_item(1, "2026-10-04T10:00:00+00:00"), _item(2, "2026-10-03T12:00:00+00:00"),
             _item(3, "2026-10-03T08:00:00+00:00"), _item(4, "2026-09-30T08:00:00+00:00")]
    vistos = {normalizar_url(items[1]["url"])}
    tomados, conteo = filtrar_fuente(items, vistos, CORTE, AHORA, 10, False)
    assert [t["url"] for t in tomados] == [items[0]["url"]]
    assert conteo["por_fecha"] == 2 and conteo["ya_vistos"] == 1 and conteo["por_tope"] == 0


def test_fuente_sin_fechas_corta_en_la_primera_url_vista():
    items = [_item(n) for n in range(1, 9)]
    vistos = {normalizar_url(items[3]["url"]), normalizar_url(items[6]["url"])}
    tomados, conteo = filtrar_fuente(items, vistos, CORTE, AHORA, 10, False)
    assert [t["url"] for t in tomados] == [i["url"] for i in items[:3]]
    assert conteo["modo"] == "orden" and conteo["por_orden"] == 5  # desde la nota 4 hacia abajo


def test_misma_fecha_en_todas_las_notas_usa_el_orden_y_no_muestra_esa_fecha():
    """Como el feed de Poker.org: todas las notas con la misma hora."""
    items = [_item(n, "2026-10-04T21:59:25+00:00" if n % 3 else "2026-10-04T21:59:26+00:00") for n in range(1, 31)]
    vistos = {normalizar_url(items[4]["url"])}
    tomados, conteo = filtrar_fuente(items, vistos, CORTE, AHORA, 10, False)
    assert [t["url"] for t in tomados] == [i["url"] for i in items[:4]]
    assert all(t["fecha"] is None for t in tomados)
    assert conteo["por_orden"] == 26 and conteo["por_fecha"] == 0


def test_fuente_vista_por_primera_vez_toma_como_maximo_diez():
    items = [_item(n) for n in range(1, 26)]
    tomados, conteo = filtrar_fuente(items, set(), CORTE, AHORA, 10, True)
    assert len(tomados) == 10 and conteo["por_tope"] == 15 and conteo["primera_vez"]


def test_recolectar_detecta_primera_vez_por_sitio():
    rss = _rss([(f"Nota de prueba número {n}", f"https://prueba.com/nota-{n}", None) for n in range(1, 16)])
    descarga = DescargaFalsa({"https://prueba.com/feed": (200, rss)})
    f = fuente("Prueba", "https://prueba.com/feed")
    _, informe, recogidos = recolectar([f], AHORA.date(), AHORA, set(), AJUSTES, descarga, CORTE)
    assert informe[0]["primera_vez"] and informe[0]["nuevos"] == 10 and len(recogidos) == 15
    vistos = {normalizar_url("https://prueba.com/nota-14")}
    _, informe, _ = recolectar([f], AHORA.date(), AHORA, vistos, AJUSTES, descarga, CORTE)
    assert not informe[0]["primera_vez"]
    assert informe[0]["nuevos"] == 10 and informe[0]["por_orden"] == 2  # notas 14 y 15


# ---------------------------------------------------------------- corte de la corrida

def _corrida_previa(raiz, momento, estado="ok"):
    agregar_corrida(raiz, momento.date(), {"id": momento.strftime("%Y-%m-%dT%H:%M:%SZ"), "origen": "automatica",
                                           "estado": estado, "notas": [], "tarjetas": []})


def test_corte_es_la_ultima_corrida_exitosa_menos_tres_horas(raiz_temporal):
    _corrida_previa(raiz_temporal, AHORA - timedelta(days=1))
    _corrida_previa(raiz_temporal, AHORA - timedelta(hours=5), estado="fallida")  # no cuenta
    corte, motivo = corrida.calcular_corte(raiz_temporal, AHORA, AJUSTES)
    assert corte == AHORA - timedelta(days=1, hours=3)
    assert "menos 3 horas" in motivo


def test_corte_no_va_mas_atras_de_tres_dias(raiz_temporal):
    _corrida_previa(raiz_temporal, AHORA - timedelta(days=6))
    corte, _ = corrida.calcular_corte(raiz_temporal, AHORA, AJUSTES)
    assert corte == AHORA - timedelta(days=3)
    corte, motivo = corrida.calcular_corte(raiz_temporal / "vacia", AHORA, AJUSTES)
    assert corte == AHORA - timedelta(days=3) and "sin corridas" in motivo


def test_las_corridas_importadas_no_cuentan_como_exitosas(raiz_temporal):
    agregar_corrida(raiz_temporal, AHORA.date() - timedelta(days=1),
                    {"id": "importada-2026-10-03-1", "origen": "importada", "estado": "ok", "tarjetas": []})
    assert corrida.ultima_corrida_exitosa(raiz_temporal, AHORA) is None


# ---------------------------------------------------------------- vistos y descartes

def _llamador(respuesta, capturado=None):
    def _llamar(sistema, mensaje, modelo_id, max_tokens):
        if capturado is not None:
            capturado.update(sistema=sistema, mensaje=mensaje)
        return json.dumps(respuesta), {"tokens_entrada": 100, "tokens_salida": 50}
    return _llamar


def _descarga_grande():
    fecha = "Sun, 04 Oct 2026 10:00:00 +0000"
    rss = _rss([(f"Nota de prueba número {n}", f"https://ejemplo-poker.com/noticias/nota-{n}", fecha if n % 2 else
                 "Sun, 04 Oct 2026 11:00:00 +0000") for n in range(1, 16)])
    portada = "".join(f'<div><a href="/news/nota-larga-numero-{n}/">Una nota de portada número {n} con título largo</a></div>'
                      for n in range(1, 6))
    return DescargaFalsa({"https://ejemplo-poker.com/feed/": (200, rss),
                          "https://portada-poker.com/news/": (200, f"<html><body>{portada}</body></html>" + " " * 300)})


def test_tras_corrida_exitosa_todo_lo_recogido_queda_visto(raiz_temporal):
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", _descarga_grande(), _llamador({"tarjetas": []}))
    vistos = leer_json(raiz_temporal / "data" / "vistos.json")["urls"]
    assert len(vistos) == 20  # 15 del feed (solo 10 elegidos) + 5 de la portada
    d = c["recoleccion"]["descartes"]
    assert d["por_fuente"]["Ejemplo"]["por_tope"] == 5 and d["por_fuente"]["Ejemplo"]["enviados"] == 10
    assert d["totales"]["por_tope"] == 5
    assert any(x.get("motivo") == "descartes" and x["fuente"] == "Ejemplo" for x in c["controles"])
    dia = leer_json(raiz_temporal / "data" / "dias" / "2026-10-04.json")
    assert dia["corridas"][-1]["recoleccion"]["corte"] == "2026-10-01T12:45:00Z"


def test_tras_corrida_fallida_no_se_marca_nada(raiz_temporal):
    def roto(*_):
        raise modelo.ErrorModelo("La API respondió con error 529.")
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", _descarga_grande(), roto)
    assert c["estado"] == "fallida"
    assert leer_json(raiz_temporal / "data" / "vistos.json")["urls"] == {}


def test_tope_total_se_cuenta_por_fuente(raiz_temporal):
    ajustes = leer_json(raiz_temporal / "config" / "ajustes.json")
    ajustes["max_titulares_enviados"] = 12
    (raiz_temporal / "config" / "ajustes.json").write_text(json.dumps(ajustes), encoding="utf-8")
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", _descarga_grande(), _llamador({"tarjetas": []}))
    d = c["recoleccion"]["descartes"]["por_fuente"]
    # Por turnos: 5 de la portada y 7 del feed; el feed pierde 3 más por el tope total.
    assert d["Portada"]["enviados"] == 5 and d["Ejemplo"]["enviados"] == 7
    assert d["Ejemplo"]["por_tope"] == 5 + 3
    assert c["recoleccion"]["titulares_enviados"] == 12


def test_pie_muestra_descartes(raiz_temporal):
    corrida.ejecutar(raiz_temporal, AHORA, "automatica", _descarga_grande(), _llamador({"tarjetas": []}))
    pie = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"),
                        "html.parser").footer.get_text(" ")
    assert "0 por fecha, 0 por orden de la lista, 5 por tope" in pie
    assert "2026-10-01 12:45:00 UTC" in pie
    assert "Ejemplo" in pie and "Portada" in pie


# ---------------------------------------------------------------- caso Aido

AIDO_RSS = _rss([("Sergio Aido Wins 2026 WSOP Online GGMillion$ High Rollers for $2,228,762, Claiming Third Bracelet",
                  "https://www.pokernewsdaily.com/sergio-aido-claims-third-wsop-bracelet-49065/",
                  "Wed, 30 Sep 2026 23:42:47 +0000"),
                 ("Nota nueva de prueba del cuatro de octubre", "https://www.pokernewsdaily.com/nota-nueva/",
                  "Sun, 04 Oct 2026 09:00:00 +0000")])
AIDO_PREVIO = "Sergio Aido gana el GGMillion$ de US$10.000 y suma su tercer brazalete WSOP"


def test_aido_queda_fuera_por_fecha_y_el_titulo_previo_llega_al_modelo(raiz_temporal):
    """La nota de Aido es del 30 de septiembre; con una corrida exitosa el 3 de octubre
    no entra en la ventana, y el título publicado el 30 de septiembre llega como previo."""
    agregar_corrida(raiz_temporal, datetime(2026, 9, 30).date(), {
        "id": "importada-2026-09-30-1", "origen": "importada", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "30 sep 2026", "titulo": AIDO_PREVIO,
                      "resumen": "R", "fuentes": []}]})
    _corrida_previa(raiz_temporal, datetime(2026, 10, 3, 12, 45, tzinfo=timezone.utc))
    descarga = DescargaFalsa({"https://ejemplo-poker.com/feed/": (200, AIDO_RSS)})
    capturado = {}
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", descarga, _llamador({"tarjetas": []}, capturado))
    assert c["recoleccion"]["corte"] == "2026-10-03T09:45:00Z"
    m = capturado["mensaje"]
    assert "Sergio Aido Wins" not in m.split("Títulos ya publicados")[0]
    assert "Nota nueva de prueba" in m
    assert f"] {AIDO_PREVIO}" in m  # tarjeta de hace 4 días, dentro de los 5 días
    assert c["recoleccion"]["descartes"]["por_fuente"]["Ejemplo"]["por_fecha"] == 1


def test_aido_repetido_se_descarta_aunque_entre_por_la_ventana():
    item = {"fuente": "PokerNewsDaily", "url": "https://www.pokernewsdaily.com/sergio-aido-claims-third-wsop-bracelet-49065/",
            "titulo": "Sergio Aido Wins 2026 WSOP Online GGMillion$ High Rollers for $2,228,762, Claiming Third Bracelet",
            "primera_linea": "", "fecha": "2026-09-30T23:42:47+00:00", "region": "internacional"}
    r = modelo.resumir([item], {"max_tarjetas": 12, "max_tarjetas_latam": 6, "precios_usd_por_millon": {}},
                       _llamador({"tarjetas": [{"ids": [1], "clasificacion": "confirmado",
                                                "titulo": "Sergio Aido gana el WSOP Online GGMillion$ High Rollers 2026",
                                                "resumen": "Sergio Aido ganó su tercer brazalete."}]}),
                       previos=[AIDO_PREVIO])
    assert r["tarjetas"] == [] and r["controles"][0]["previo"] == AIDO_PREVIO


# ---------------------------------------------------------------- nombres propios

PAPO = {"fuente": "PokerNews en español",
        "url": "https://es.pokernews.com/noticias/2026/10/papomc-conquista-el-main-event-medium-del-wcoop-y-firma-su-m-53922.htm",
        "titulo": "PapoMC conquista el Main Event Medium del WCOOP y firma su mayor premio online",
        "primera_linea": "", "fecha": "2026-10-01T10:00:00+00:00", "region": "latam"}
HAYWARD = {"fuente": "WSOP.com",
           "url": "https://www.wsop.com/news/connor-hayward-wins-the-horseshoe-council-bluffs-wsop-circuit-main-event/",
           "titulo": "CONNOR HAYWARD WINS THE HORSESHOE COUNCIL BLUFFS WSOP CIRCUIT MAIN EVENT",
           "primera_linea": "Connor Hayward wins his first WSOP Circuit ring in the Horseshoe Council Bluffs WSOP "
                            "Circuit Main Event, while Daniel Lowery and Ari Engel reach their 21st rings.",
           "fecha": None, "region": "internacional"}
AJ = {"max_tarjetas": 12, "max_tarjetas_latam": 6, "precios_usd_por_millon": {}}


def test_prompt_prohibe_completar_nombres():
    capturado = {}
    modelo.resumir([PAPO], AJ, _llamador({"tarjetas": []}, capturado))
    assert "Nunca complete, agregue ni invente nombres de pila, apellidos o alias" in capturado["sistema"]


def test_nombre_de_pila_agregado_a_un_alias_real_se_quita():
    """La fuente solo dice "PapoMC"; el modelo no puede agregar nombre ni apellido."""
    r = modelo.resumir([PAPO], AJ, _llamador({"escena_latam": [{
        "ids": [1], "clasificacion": "confirmado",
        "titulo": "Fulanito 'PapoMC' Menganez conquista el Main Event Medium del WCOOP",
        "resumen": "Fulanito 'PapoMC' Menganez ganó el Main Event Medium del WCOOP."}]}))
    t = r["escena_latam"][0]
    assert t["titulo"].startswith("PapoMC conquista el Main Event Medium del WCOOP")
    assert "Fulanito" not in t["titulo"] + t["resumen"] and "Menganez" not in t["titulo"] + t["resumen"]
    assert any("nombre corregido" in x["motivo"] for x in r["controles"])


def test_nombre_que_no_figura_en_ninguna_fuente_descarta_la_tarjeta():
    r = modelo.resumir([PAPO], AJ, _llamador({"escena_latam": [{
        "ids": [1], "clasificacion": "confirmado", "titulo": "Zutano Perengánez conquista el WCOOP",
        "resumen": "Zutano Perengánez ganó el Main Event Medium."}]}))
    assert r["escena_latam"] == []
    assert any("no presente en las fuentes" in x["motivo"] for x in r["controles"])


def test_nombres_reales_y_traducciones_no_se_tocan():
    tarjeta = {"titulo": "Connor Hayward gana el Evento Principal del WSOP Circuit en Council Bluffs",
               "resumen": "Daniel Lowery y Ari Engel suman 21 anillos. La Comisión de Juego de Nueva Jersey no intervino."}
    original = dict(tarjeta)
    conservar, cambios = control.verificar_nombres(tarjeta, modelo.texto_enviado([HAYWARD]))
    assert conservar and cambios == [] and tarjeta == original
