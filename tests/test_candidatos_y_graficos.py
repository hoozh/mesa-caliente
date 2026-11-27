"""Candidatos diarios de Personalidades y palabras clave, y gráficos del administrador (pedido de Hoozh, 27 nov 2026)."""

import json
from datetime import date

from mesa import candidatos, corrida
from mesa.util import RAIZ, leer_json

from .conftest import AHORA
from .test_corrida_y_pagina import llamador_ok

HOY = date(2026, 11, 27)


def _hecho(n, dia, titulo, jugadores=(), torneo="", fuente="PokerNews", seccion="general"):
    return {"fecha": dia, "id": f"{dia}-{n}", "titulo": titulo, "tipo": "resultado", "seccion": seccion, "salas": [],
            "jugadores": [{"nombre": j, "pais": ""} for j in jugadores], "torneo": torneo, "temas_calientes": [],
            "fuentes": [{"nombre": fuente, "url": f"https://x/{n}", "clase": "medio"}]}


def _raiz(tmp_path, hechos, personalidades=(), temas=(), descartados=None):
    (tmp_path / "config").mkdir()
    (tmp_path / "data" / "registro").mkdir(parents=True)
    (tmp_path / "data" / "registro" / "hechos.jsonl").write_text("\n".join(json.dumps(h) for h in hechos) + "\n", encoding="utf-8")
    (tmp_path / "config" / "personalidades.json").write_text(json.dumps({"personalidades": [
        {"id": "a", "nombre": n, "alias": [], "historica": False, "resumen": ""} for n in personalidades]}), encoding="utf-8")
    (tmp_path / "config" / "temas_calientes.json").write_text(json.dumps({"temas": [
        {"nombre": t, "palabras_clave": [t], "alta": "2026-11-01", "estado": "activo"} for t in temas]}), encoding="utf-8")
    (tmp_path / "config" / "salas_vigiladas.json").write_text(json.dumps({"salas": [{"nombre": "GGPoker", "alias": []}]}), encoding="utf-8")
    (tmp_path / "config" / "temas_generales.json").write_text(json.dumps({"juegos_formatos": ["Omaha"]}), encoding="utf-8")
    if descartados:
        (tmp_path / "config" / "candidatos_descartados.json").write_text(json.dumps(descartados), encoding="utf-8")
    return tmp_path


HECHOS = [
    _hecho(1, "2026-11-20", "Vladimir Minko gana el GGMillion$ Festival", ["Vladimir Minko"], "GGMillion$ Festival", "PokerNews"),
    _hecho(2, "2026-11-22", "Minko vuelve a ganar", ["Vladimir Minko"], "GGMillion$ Festival", "CardPlayer"),
    _hecho(3, "2026-11-26", "Daniel Negreanu critica a GGPoker", ["Daniel Negreanu"], "", "PokerNews"),
    _hecho(4, "2026-11-26", "Daniel Negreanu critica de nuevo", ["Daniel Negreanu"], "", "CardPlayer"),
    _hecho(5, "2026-11-27", "Jugador Suelto gana un Main Event", ["Jugador Suelto"], "Main Event", "PokerNews"),
    _hecho(6, "2026-09-01", "Viejo Campeón gana", ["Viejo Campeón"], "", "PokerNews"),
]


def test_propone_personalidades_y_palabras_clave_que_se_repiten(tmp_path):
    c = candidatos.calcular(_raiz(tmp_path, HECHOS), HOY)
    nombres = [r["texto"] for r in c["personalidades"]]
    assert "Vladimir Minko" in nombres and "Daniel Negreanu" in nombres
    assert "Jugador Suelto" not in nombres  # una sola aparición y una sola fuente
    assert "Viejo Campeón" not in nombres   # fuera de la ventana de 30 días
    minko = next(r for r in c["personalidades"] if r["texto"] == "Vladimir Minko")
    assert minko["apariciones"] == 2 and minko["dias"] == 2 and minko["fuentes"] == 2 and minko["ejemplos"]
    claves = [r["texto"] for r in c["palabras_clave"]]
    assert "GGMillion$ Festival" in claves
    assert "Main Event" not in claves  # solo palabras de formato


def test_no_propone_lo_que_ya_esta_en_las_listas_ni_lo_descartado(tmp_path):
    raiz = _raiz(tmp_path, HECHOS, personalidades=["Daniel Negreanu"], temas=["GGMillion$ Festival"])
    c = candidatos.calcular(raiz, HOY)
    assert [r["texto"] for r in c["personalidades"]] == ["Vladimir Minko"]
    assert "GGMillion$ Festival" not in [r["texto"] for r in c["palabras_clave"]]
    (raiz / "config" / "candidatos_descartados.json").write_text(json.dumps({"personalidades": ["vladimir minko"], "palabras_clave": []}), encoding="utf-8")
    assert candidatos.calcular(raiz, HOY)["personalidades"] == []


def test_las_noticias_de_tema_y_las_salas_no_cuentan_como_candidatos(tmp_path):
    hechos = [_hecho(i, "2026-11-2%d" % i, "Tema: Triton North Cyprus", [], "", seccion="tema") for i in range(3)] + \
             [_hecho(10 + i, "2026-11-2%d" % i, "GGPoker lanza una promoción de GGPoker", [], "") for i in range(3)]
    c = candidatos.calcular(_raiz(tmp_path, hechos), HOY)
    assert not any("Triton" in r["texto"] or "GGPoker" in r["texto"] for r in c["palabras_clave"])


def test_el_barrido_guarda_los_candidatos_sin_costo(raiz_temporal, descarga_ejemplo):
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga_ejemplo, llamador_ok)
    assert c["estado"] == "ok"
    datos = leer_json(raiz_temporal / "data" / "candidatos.json")
    assert {"fecha", "personalidades", "palabras_clave", "ventana_dias"} <= set(datos)


def test_administrador_con_candidatos_y_graficos():
    html = (RAIZ / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
    assert 'data-v="candidatos"' in html and "VISTAS.candidatos" in html and "candidatos_descartados.json" in html
    assert "graficoDias" in html and "graficoCategorias" in html
    assert html.count("graficoDias(") >= 5 and "d-graficos" in html  # Costos (4) y Consultar registros
    assert "chart.js" not in html.lower() and "d3js" not in html.lower()  # SVG propio, sin librerías de gráficos
