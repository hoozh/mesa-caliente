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


# ---------------------------------------------------------------- archivo privado, columnas, Gemini y gráficos en vivo (27 nov, 10:51)

MARCA_PRIVADA = "TITULAR-PRIVADO-SIN-PUBLICAR"


def _archivo():
    return [{"fecha": "2026-11-2%d" % i, "titulo": f"Jon Dough gana el torneo {MARCA_PRIVADA}", "fuentes": [{"nombre": "PokerNews", "url": "https://x"}]} for i in (3, 5, 6)] + \
           [{"fecha": "2026-11-24", "titulo": "Vladimir Minko cobra en Chipre", "fuentes": [{"nombre": "CardPlayer"}]},
            {"fecha": "2026-08-01", "titulo": "Viejo Campeón gana", "fuentes": []}]


def test_el_archivo_privado_suma_menciones_y_propone_nombres_que_ameritan_tarjeta(tmp_path):
    raiz = _raiz(tmp_path, HECHOS)
    pub = candidatos.calcular(raiz, HOY)
    priv = candidatos.calcular_privados(raiz, HOY, pub, _archivo())
    assert priv["menciones"]["vladimir minko"]["n"] == 1  # cuenta el resultado del archivo del 24 nov
    nuevos = {r["texto"]: r for r in priv["del_archivo"]}
    assert "Jon Dough" in nuevos and nuevos["Jon Dough"]["menciones"] == 3 and nuevos["Jon Dough"]["dias"] == 3
    assert nuevos["Jon Dough"]["ejemplos"][0]["clave"]  # clave para «Crear tarjeta» desde el archivo
    assert "Viejo Campeón" not in nuevos  # fuera de la ventana
    assert "Vladimir Minko" not in nuevos  # ya es candidato público


def test_lo_del_archivo_nunca_va_al_archivo_publico_de_candidatos(tmp_path):
    raiz = _raiz(tmp_path, HECHOS)
    archivo = tmp_path / "privado" / "resultados" / "archivo.jsonl"
    archivo.parent.mkdir(parents=True)
    archivo.write_text("\n".join(json.dumps(a) for a in _archivo()) + "\n", encoding="utf-8")
    candidatos.generar(raiz, HOY, archivo)
    publico = (raiz / "data" / "candidatos.json").read_text(encoding="utf-8")
    assert MARCA_PRIVADA not in publico and "Jon Dough" not in publico
    privado = (archivo.parent / "candidatos_privados.json").read_text(encoding="utf-8")
    assert "Jon Dough" in privado
    assert not (raiz / "data" / "candidatos_privados.json").exists()


def test_sin_repositorio_privado_el_barrido_no_publica_candidatos_del_archivo(raiz_temporal, descarga_ejemplo, monkeypatch):
    monkeypatch.delenv("MESA_DIR_PRIVADO", raising=False)
    corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga_ejemplo, llamador_ok)
    assert not list(raiz_temporal.rglob("candidatos_privados.json"))


def test_los_candidatos_traen_las_columnas_pedidas(tmp_path):
    c = candidatos.calcular(_raiz(tmp_path, HECHOS), HOY)
    r = next(x for x in c["personalidades"] if x["texto"] == "Vladimir Minko")
    assert {"apariciones", "primera", "ultima", "dias", "fuentes", "clave", "ejemplos"} <= set(r)
    assert r["primera"] == "2026-11-20" and r["ultima"] == "2026-11-22"


def test_administrador_gemini_ficha_y_controles_en_vivo():
    html = (RAIZ / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
    for texto in ("Empezó a notarse", "Menciones", "resultados/candidatos_privados.json", "cd-his", "cd-ali", "cd-res", "resumenGemini",
                  "x-goog-api-key", "google_search", "mc-gemini-clave", "Sugerir con Gemini", "Del archivo privado"):
        assert texto in html, texto
    for id_control in ("gc-dias", "gc-agr", "data-serie", "g-dias", "g-dim", "g-desg", "g-top"):
        assert id_control in html, id_control
    assert "/v1beta/interactions" in html and ":generateContent" in html  # segunda vía si la primera no responde
