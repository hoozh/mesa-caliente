"""Pedido del 26 nov 2026 (02:18): el título abre una noticia dentro de Mesa Caliente que cierra con las fuentes;
las ediciones del administrador se aplican sin tocar el dato del día; búsquedas activas; excepción de figuras
históricas. Nombres de personas inventados."""

import json
from datetime import date

from bs4 import BeautifulSoup

from mesa import modelo, pagina, recoleccion, registro
from mesa.dias import agregar_corrida, ruta_dia
from mesa.util import leer_json

from .conftest import AHORA, RAIZ

TITULO = "Fulano Inventado cuestiona la nueva regla de tiempo del EPT Ficticio"


def _dia(raiz):
    agregar_corrida(raiz, AHORA.date(), {
        "id": "c1", "origen": "programada", "titulo": "Corrida principal", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "4 oct 2026", "titulo": TITULO,
                      "resumen": "Fulano Inventado criticó la regla.", "imagen": "https://medio.com/foto.jpg",
                      "fuentes": [{"nombre": "Medio Inventado", "fecha": "2026-10-04", "url": "https://medio.com/n/1"}]},
                     {"clasificacion": "confirmado", "fecha": "4 oct 2026", "titulo": "Otra noticia inventada",
                      "resumen": "Otra.", "fuentes": [{"nombre": "B", "fecha": None, "url": "https://b.com/1"}]}]})
    return registro.id_hecho(AHORA.date().isoformat(), "c1", TITULO)


def test_el_titulo_abre_la_noticia_interna_que_cierra_con_las_fuentes(raiz_temporal):
    nid = _dia(raiz_temporal)
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    sopa = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser")
    assert sopa.select_one("a.titulo-noticia")["href"] == f"n/{nid}.html"
    nota = BeautifulSoup((raiz_temporal / "docs" / "n" / f"{nid}.html").read_text(encoding="utf-8"), "html.parser")
    assert nota.h1.get_text() == TITULO
    assert "4 de octubre de 2026" in nota.select_one(".fecha").get_text()
    assert nota.select_one(".foto img")["src"] == "https://medio.com/foto.jpg"
    assert "Medio Inventado" in nota.select_one(".foto figcaption").get_text()
    assert [a["href"] for a in nota.select(".fuentes a")] == ["https://medio.com/n/1"]
    assert (raiz_temporal / "docs" / "noticia.css").exists()


def test_ediciones_del_administrador_se_aplican_y_guardan_el_original(raiz_temporal):
    nid = _dia(raiz_temporal)
    otra = registro.id_hecho(AHORA.date().isoformat(), "c1", "Otra noticia inventada")
    (raiz_temporal / "data" / "ediciones.json").write_text(json.dumps({"tarjetas": {
        nid: {"titulo": "Título corregido a mano", "cuerpo": "Primer párrafo.\nSegundo párrafo.", "destacada": True,
              "original": {"titulo": TITULO}, "historial": [{"motivo": "el nombre estaba mal"}]},
        otra: {"oculta": True}}}), encoding="utf-8")
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    sopa = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser")
    titulos = [a.get_text() for a in sopa.select("a.titulo-noticia")]
    assert titulos == ["Título corregido a mano"]  # la oculta no se muestra
    assert sopa.select_one("article.card-destacada")
    nota = BeautifulSoup((raiz_temporal / "docs" / "n" / f"{nid}.html").read_text(encoding="utf-8"), "html.parser")
    assert [p.get_text() for p in nota.select(".cuerpo p")] == ["Primer párrafo.", "Segundo párrafo."]
    assert not (raiz_temporal / "docs" / "n" / f"{otra}.html").exists()
    # El dato del día no se toca y el administrador ve el texto original, también de la oculta.
    dia = leer_json(ruta_dia(raiz_temporal, AHORA.date()))
    assert dia["corridas"][0]["tarjetas"][0]["titulo"] == TITULO
    datos = {t["id"]: t for t in leer_json(raiz_temporal / "docs" / "datos" / "tarjetas.json")["tarjetas"]}
    assert datos[nid]["titulo"] == TITULO and datos[otra]["oculta"] is True


def test_busquedas_activas_vigentes_se_vuelven_fuentes(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "busquedas.json").write_text(json.dumps({"busquedas": [
        {"consulta": "Fulano Inventado polémica", "hasta": "2026-10-10", "estado": "activa"},
        {"consulta": "vencida", "hasta": "2026-10-01", "estado": "activa"},
        {"consulta": "apagada", "hasta": "2026-10-10", "estado": "inactiva"}]}), encoding="utf-8")
    fuentes = recoleccion.fuentes_de_busquedas(tmp_path, date(2026, 10, 4))
    assert len(fuentes) == 1 and fuentes[0]["busqueda"] is True
    assert fuentes[0]["url"].startswith("https://news.google.com/rss/search?q=Fulano")


def test_figuras_historicas_y_records_merecen_tarjeta():
    historicas = ["Daniel Negreanu", "Phil Hellmuth"]
    assert modelo.resultado_excepcional({"titulo": "Phil Hellmuth gana su brazalete 18", "resumen": ""}, historicas)
    assert modelo.resultado_excepcional({"titulo": "Fulano Inventado supera a todos en la all-time money list",
                                         "resumen": ""}, historicas)
    assert not modelo.resultado_excepcional({"titulo": "Fulano Inventado gana un torneo", "resumen": ""}, historicas)


def test_administrador_y_archivos_de_configuracion():
    admin = (RAIZ / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
    for ruta in ("data/ediciones.json", "config/temas_calientes.json", "config/busquedas.json",
                 "config/personalidades.json", "data/registro/resultados_archivo.jsonl", "data/archivos/",
                 "barrido-diario.yml/dispatches"):
        assert ruta in admin
    assert 'name="robots" content="noindex' in admin
    # La clave no viaja en la dirección ni se guarda en el repositorio.
    assert "mc-admin-token" in admin and "location.hash = TOKEN" not in admin
    # La página pública no tiene botón para correr el barrido.
    assert "dispatches" not in (RAIZ / "templates" / "index.html.j2").read_text(encoding="utf-8")
    for p in leer_json(RAIZ / "config" / "personalidades.json")["personalidades"]:
        assert set(p) >= {"nombre", "alias", "historica", "perfil"}
