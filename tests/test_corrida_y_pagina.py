import hashlib
import json
from datetime import timedelta

from bs4 import BeautifulSoup

from mesa import corrida, importar, pagina
from mesa.dias import agregar_corrida, cargar_dias
from mesa.modelo import ErrorModelo
from mesa.util import RAIZ, leer_json

from .conftest import AHORA, DescargaFalsa, leer_fixture


def llamador_ok(sistema, mensaje, modelo_id, max_tokens):
    return json.dumps({
        "tarjetas": [{"ids": [1, 2], "clasificacion": "confirmado", "titulo": "Hecho del día", "resumen": "Resumen breve."}],
        "escena_latam": [{"ids": [3], "clasificacion": "confirmado", "titulo": "Fulano Inventado gana el CAP", "resumen": "Argentino campeón."}],
    }), {"tokens_entrada": 2000, "tokens_salida": 300}


def llamador_roto(*_):
    raise ErrorModelo("La API respondió con error 529.")


def _huella(carpeta):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(carpeta.glob("*.json"))}


def _dia_previo(raiz):
    agregar_corrida(raiz, AHORA.date() - timedelta(days=1), {
        "id": "importada-1", "titulo": "Corrida principal", "origen": "importada", "estado": "ok",
        "tarjetas": [{"clasificacion": "rumor", "fecha": "3 oct 2026", "titulo": "Previa", "resumen": "R",
                      "fuentes": [{"nombre": "X", "fecha": None, "url": "https://x.com/1"}]}], "notas": []})


def test_corrida_completa_guarda_dia_costos_y_vistos(raiz_temporal, descarga_ejemplo):
    _dia_previo(raiz_temporal)
    antes = _huella(raiz_temporal / "data" / "dias")
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", descarga_ejemplo, llamador_ok)

    assert c["estado"] == "ok"
    assert len(c["tarjetas"]) == 1 and len(c["escena_latam"]["tarjetas"]) == 1
    # Días anteriores intactos.
    despues = _huella(raiz_temporal / "data" / "dias")
    assert despues["2026-10-03.json"] == antes["2026-10-03.json"]
    assert "2026-10-04.json" in despues
    # Costos registrados.
    costos = leer_json(raiz_temporal / "data" / "costos.json")
    assert costos["corridas"][0]["tokens_entrada"] == 2000
    assert costos["corridas"][0]["costo_usd"] == round(2000 / 1e6 + 300 * 5 / 1e6, 6)
    # La segunda corrida no repite titulares.
    c2 = corrida.ejecutar(raiz_temporal, AHORA + timedelta(hours=2), "manual", descarga_ejemplo, llamador_ok)
    assert c2["estado"] == "sin_noticias"
    dia = leer_json(raiz_temporal / "data" / "dias" / "2026-10-04.json")
    assert len(dia["corridas"]) == 2


def test_fallo_del_modelo_queda_registrado_y_no_marca_vistos(raiz_temporal, descarga_ejemplo):
    _dia_previo(raiz_temporal)
    antes = _huella(raiz_temporal / "data" / "dias")
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", descarga_ejemplo, llamador_roto)
    assert c["estado"] == "fallida" and "529" in c["motivo"]
    assert leer_json(raiz_temporal / "data" / "vistos.json")["urls"] == {}
    assert _huella(raiz_temporal / "data" / "dias")["2026-10-03.json"] == antes["2026-10-03.json"]
    html = (raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8")
    assert "Corrida fallida: La API respondió con error 529." in html
    assert "Previa" in html  # el historial sigue en la página


def test_falta_la_clave_registra_corrida_fallida(raiz_temporal, descarga_ejemplo, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", descarga_ejemplo)
    assert c["estado"] == "fallida" and "ANTHROPIC_API_KEY" in c["motivo"]
    assert (raiz_temporal / "docs" / "index.html").exists()


def test_error_inesperado_no_rompe_la_pagina(raiz_temporal):
    def descarga_que_explota(url):
        raise RuntimeError("algo raro")
    # Un error en una fuente se registra como fallo de esa fuente, no rompe la corrida.
    c = corrida.ejecutar(raiz_temporal, AHORA, "automatica", descarga_que_explota, llamador_ok)
    assert c["estado"] == "sin_noticias"
    fuentes = leer_json(raiz_temporal / "config" / "fuentes.json")["fuentes"]
    assert all(f["fallos_consecutivos"] == 1 for f in fuentes)
    assert (raiz_temporal / "docs" / "index.html").exists()


def test_articulos_abiertos_alimentan_candidatas(raiz_temporal):
    portadas = "".join(
        f'<div><a href="/news/nota-numero-{i}-con-titulo-largo/">Una nota número {i} con un título bastante largo</a></div>'
        for i in range(8))
    articulo = leer_fixture("articulo.html")
    respuestas = {"https://portada-poker.com/news/": (200, f"<html><body>{portadas}</body></html>" + " " * 300)}
    for i in range(8):
        respuestas[f"https://portada-poker.com/news/nota-numero-{i}-con-titulo-largo/"] = (200, articulo)
    descarga = DescargaFalsa(respuestas)
    corrida.ejecutar(raiz_temporal, AHORA, "automatica", descarga, llamador_ok)
    abiertas = [u for u in descarga.pedidas if "/nota-numero-" in u]
    assert len(abiertas) == 6  # tope de artículos abiertos
    estado = leer_json(raiz_temporal / "data" / "candidatas.json")
    dominios = [c["dominio"] for c in estado["candidatas"]]
    assert dominios == ["legalsportsreport.com"]  # twitter y gipsyteam excluidos
    fuentes = leer_json(raiz_temporal / "config" / "fuentes.json")["fuentes"]
    assert "legalsportsreport" not in json.dumps(fuentes)  # nunca se agrega sola


def test_pagina_tres_dias_abiertos_resto_plegado(raiz_temporal):
    for n in range(5):
        _ = agregar_corrida(raiz_temporal, AHORA.date() - timedelta(days=n), {
            "id": f"x{n}", "origen": "importada", "titulo": "Corrida principal", "estado": "ok", "notas": [],
            "tarjetas": [{"clasificacion": "confirmado", "fecha": "1 oct 2026", "titulo": f"Tarjeta {n}",
                          "resumen": "Resumen", "fuentes": [
                              {"nombre": "A", "fecha": None, "url": "https://a.com/1"},
                              {"nombre": "B", "fecha": "2026-10-01", "url": "https://b.com/1"}]}]})
    fuentes = leer_json(raiz_temporal / "config" / "fuentes.json")
    fuentes["fuentes"][0].update(estado="en_pausa", motivo_pausa="bloqueo del sitio (HTTP 403)",
                                 pausada_desde="2026-10-01", ultimo_intento="2026-10-01")
    (raiz_temporal / "config" / "fuentes.json").write_text(json.dumps(fuentes), encoding="utf-8")

    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    sopa = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser")
    bloques = sopa.select("details.day-block")
    assert len(bloques) == 5
    assert [b.has_attr("open") for b in bloques] == [True, True, True, False, False]
    tarjeta = bloques[0].select_one("article.card")
    assert tarjeta.select_one(".badge.confirmado").get_text() == "Confirmado"
    assert {a["href"] for a in tarjeta.select("a.go")} == {"https://a.com/1", "https://b.com/1"}
    salud = sopa.select_one("section.health").get_text(" ")
    assert "Ejemplo" in salud and "HTTP 403" in salud
    assert (raiz_temporal / "docs" / "estilo.css").exists()


def test_pie_muestra_costos_del_mes(raiz_temporal):
    (raiz_temporal / "data").mkdir()
    (raiz_temporal / "data" / "costos.json").write_text(json.dumps({"corridas": [
        {"fecha": "2026-09-30", "modelo": "claude-haiku-4-5", "tokens_entrada": 5, "tokens_salida": 5, "costo_usd": 9.0},
        {"fecha": "2026-10-02", "modelo": "claude-haiku-4-5", "tokens_entrada": 10000, "tokens_salida": 2000, "costo_usd": 0.02},
        {"fecha": "2026-10-03", "modelo": "claude-haiku-4-5", "tokens_entrada": 12000, "tokens_salida": 2500, "costo_usd": 0.0245},
    ]}), encoding="utf-8")
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    pie = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser").footer.get_text(" ")
    assert "US$ 0.0245" in pie            # última corrida
    assert "2 llamadas" in pie and "US$ 0.0445" in pie  # solo octubre
    assert "22.000 tokens de entrada" in pie


def test_importa_el_historial_completo(tmp_path):
    historial = json.loads((RAIZ / "mesa_caliente_historial.json").read_text(encoding="utf-8"))
    resumen = importar.importar(tmp_path, historial)
    assert resumen == {"dias_creados": 13, "dias_omitidos": 0, "corridas": 21, "tarjetas": 159}
    dias = cargar_dias(tmp_path)
    tarjetas = [t for d in dias for c in d["corridas"] for t in c["tarjetas"]]
    originales = [t for d in historial["dias"] for t in d["tarjetas"]]
    assert sorted(json.dumps(t, sort_keys=True) for t in tarjetas) == sorted(json.dumps(t, sort_keys=True) for t in originales)
    notas = [n for d in dias for c in d["corridas"] for n in c["notas"]]
    assert notas == historial["dias"][9]["notas"]
    titulos = {c["titulo_original"] for d in dias for c in d["corridas"]}
    assert titulos == {d["titulo"] for d in historial["dias"]}
    cand = leer_json(tmp_path / "data" / "candidatas.json")["candidatas"]
    assert cand[0]["nombre"] == "Legal Sports Report" and cand[0]["dominio"] == "legalsportsreport.com"
    # Reimportar no pisa nada.
    assert importar.importar(tmp_path, historial)["dias_omitidos"] == 13


def test_datos_del_repositorio_contienen_todo_el_historial():
    historial = json.loads((RAIZ / "mesa_caliente_historial.json").read_text(encoding="utf-8"))
    originales = sorted(json.dumps(t, sort_keys=True) for d in historial["dias"] for t in d["tarjetas"])
    dias = cargar_dias(RAIZ)
    guardadas = [json.dumps(t, sort_keys=True) for d in dias for c in d["corridas"]
                 if c.get("origen") == "importada" for t in c["tarjetas"]]
    assert sorted(guardadas) == originales
