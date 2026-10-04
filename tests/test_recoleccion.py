import json

import pytest

from mesa import recoleccion
from mesa.recoleccion import ErrorDescarga, Respuesta, leer_fuente, recolectar, seleccionar
from mesa.util import RAIZ, esta_bloqueado, normalizar_url

from .conftest import AHORA, DescargaFalsa, fuente, leer_fixture

AJUSTES = {"dias_maximos_atras": 3, "max_titulares_por_fuente": 10}


def test_rss_extrae_titulo_fecha_y_primera_linea():
    items = recoleccion.parsear_rss(leer_fixture("feed.xml"), fuente())
    primero = items[0]
    assert primero["titulo"] == "Fulano Inventado gana el Main Event del CAP en Buenos Aires"
    assert primero["fecha"].startswith("2026-10-04")
    assert primero["primera_linea"].startswith("El jugador argentino")
    assert primero["region"] == "latam"


def test_html_con_enlaces_por_nota():
    f = fuente("Portada", "https://portada-poker.com/news/", tipo="html", incluir="/news/[^/]+/?$")
    items = recoleccion.parsear_html(leer_fixture("portada.html"), f["url"], f)
    titulos = [i["titulo"] for i in items]
    assert "Mengana Ficticia gana su primer título en el BSOP Millions" in titulos
    assert "Anuncian las fechas del CLSOP 2027 en Lima y Bogotá" in titulos
    assert all("otro-sitio.com" not in i["url"] for i in items)  # enlaces externos fuera
    assert all("privacy" not in i["url"] and "category" not in i["url"] for i in items)
    assert len(items) == 2
    mengana = next(i for i in items if "Mengana" in i["titulo"])
    assert mengana["primera_linea"].startswith("La jugadora brasileña")


def test_html_solo_titulares_usa_parrafo_siguiente():
    f = fuente("Liga", "https://liga.com.ar/noticias.html", tipo="html")
    items = recoleccion.parsear_html(leer_fixture("titulares.html"), f["url"], f)
    assert len(items) == 2
    assert items[0]["titulo"] == "¡MARIO GUARINO GANA LA LPP EN BARILOCHE!"
    assert items[0]["url"].startswith("https://liga.com.ar/noticias.html#t-")
    assert "171 entradas" in items[0]["primera_linea"]


def test_recolectar_aplica_ventana_y_bloqueo_gipsyteam():
    descarga = DescargaFalsa({"https://ejemplo-poker.com/feed/": (200, leer_fixture("feed.xml"))})
    nuevos, informe, recogidos = recolectar([fuente()], AHORA.date(), AHORA, set(), AJUSTES, descarga)
    urls = [i["url"] for i in nuevos]
    assert len(nuevos) == 2
    assert not any("nota-vieja" in u for u in urls)
    assert not any("gipsyteam" in u for u in urls)
    assert informe[0]["resultado"] == "ok"
    assert informe[0]["por_fecha"] == 1  # la nota vieja
    assert len(recogidos) == 3  # todo lo recogido, aunque no se elija (gipsyteam nunca)


def test_descarta_lo_ya_visto_por_url():
    descarga = DescargaFalsa({"https://ejemplo-poker.com/feed/": (200, leer_fixture("feed.xml"))})
    # La URL vista se guardó sin parámetros utm: igual debe reconocerse.
    vistos = {normalizar_url("https://www.ejemplo-poker.com/noticias/fulano-inventado-gana-cap/")}
    nuevos, _, _ = recolectar([fuente()], AHORA.date(), AHORA, vistos, AJUSTES, descarga)
    assert [i["titulo"] for i in nuevos] == ["Nuevo récord de inscriptos en el WCOOP"]


@pytest.mark.parametrize("respuesta, motivo", [
    ((403, "x" * 500), "bloqueo"),
    ((404, "x" * 500), "404"),
    ((200, ""), "sin contenido"),
    ((200, "<html><body>" + "<p>nada</p>" * 60 + "</body></html>"), "sin contenido"),
    (ErrorDescarga("error de conexión (Timeout)"), "conexión"),
])
def test_fallos_se_informan_con_motivo(respuesta, motivo):
    f = fuente("Rota", "https://rota.com/", tipo="html")
    with pytest.raises(ErrorDescarga, match=motivo):
        leer_fuente(f, DescargaFalsa({"https://rota.com/": respuesta}))


def test_fuente_gipsyteam_nunca_se_consulta():
    descarga = DescargaFalsa({})
    for url in ["https://gipsyteam.com/feed", "https://gipsyteam.com.br/", "https://gipsyteam.ru/news",
                "https://latam.gipsyteam.com/", "https://cdn.latam.gipsyteam.com/x"]:
        assert esta_bloqueado(url)
        nuevos, informe, _ = recolectar([fuente("GT", url)], AHORA.date(), AHORA, set(), AJUSTES, descarga)
        assert nuevos == [] and informe[0]["resultado"] == "omitida"
    assert descarga.pedidas == []
    assert not esta_bloqueado("https://www.pokernews.com/")


def test_configuracion_real_sin_gipsyteam_y_completa():
    datos = json.loads((RAIZ / "config" / "fuentes.json").read_text(encoding="utf-8"))
    fuentes = datos["fuentes"]
    assert len(fuentes) == 21
    for f in fuentes:
        assert not esta_bloqueado(f["url"]) and not esta_bloqueado(f.get("portada", ""))
        assert f["tipo"] in ("rss", "html")
        for campo in ("nombre", "url", "idioma", "region", "estado", "fallos_consecutivos", "ultimo_ok"):
            assert campo in f


def test_seleccion_reparte_entre_fuentes():
    items = [{"fuente": "A", "fecha": f"2026-10-04T0{i}:00:00+00:00", "url": f"a{i}"} for i in range(8)]
    items += [{"fuente": "B", "fecha": None, "url": "b1"}]
    elegidos = seleccionar(items, 3)
    assert len(elegidos) == 3
    assert {"A", "B"} == {e["fuente"] for e in elegidos}


def test_extraer_articulo_da_extracto_y_enlaces_del_cuerpo():
    extracto, enlaces = recoleccion.extraer_articulo(leer_fixture("articulo.html"), "https://medio.com/nota", 300)
    assert extracto.startswith("El regulador de Michigan")
    assert "https://www.legalsportsreport.com/nota-1/" in enlaces
    assert not any("facebook" in e or "medio-de-pie" in e for e in enlaces)  # fuera del cuerpo


def test_urls_sin_parametros_de_rastreo():
    items = recoleccion.parsear_rss(leer_fixture("feed.xml"), fuente())
    assert items[0]["url"] == "https://ejemplo-poker.com/noticias/fulano-inventado-gana-cap"


def test_poker_red_toma_solo_notas_del_listado():
    fuentes = json.loads((RAIZ / "config" / "fuentes.json").read_text(encoding="utf-8"))["fuentes"]
    f = next(x for x in fuentes if x["nombre"] == "Poker Red")
    assert f["tipo"] == "html" and "gipsyteam" not in f["url"]
    html = """<html><body>
      <a href="/noticias/coinpoker-detecto-y-baneo-a-paul-gregg-en-menos-de-una-semana">CoinPoker detectó y baneó a Paul Gregg en menos de una semana</a>
      <a href="/noticias/tags/ppm">Todas las noticias etiquetadas con PPM y otras más</a>
      <a href="/foros/general/un-hilo-del-foro-con-titulo-largo">Un hilo del foro con un título bastante largo</a>
    </body></html>"""
    items = recoleccion.parsear_html(html, f["url"], f)
    assert [i["url"] for i in items] == [
        "https://www.poker-red.com/noticias/coinpoker-detecto-y-baneo-a-paul-gregg-en-menos-de-una-semana"]
