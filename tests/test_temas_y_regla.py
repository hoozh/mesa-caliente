"""Regla máxima, filtro de "día 1" y temas calientes (sin red ni API)."""

import json
import shutil
from datetime import date, datetime, timedelta, timezone

import yaml
from bs4 import BeautifulSoup

from mesa import calidad, corrida, modelo, pagina, temas, torneos
from mesa.dias import agregar_corrida
from mesa.recoleccion import filtrar_fuente, seleccionar
from mesa.util import RAIZ, leer_json

from .conftest import AHORA, DescargaFalsa, fuente

AJ = {"max_tarjetas": 12, "max_tarjetas_latam": 6, "precios_usd_por_millon": {}}
HOY = date(2026, 10, 6)


def _llamar(respuesta, capturado=None):
    def llamar(sistema, mensaje, *_):
        if capturado is not None:
            capturado.update(sistema=sistema, mensaje=mensaje)
        return json.dumps(respuesta), {"tokens_entrada": 1, "tokens_salida": 1}
    return llamar


def _item(n, titulo, primera="", tema=None, fuente_="Poker Red", fecha="2026-10-06T10:00:00+00:00", clase="medio"):
    it = {"fuente": fuente_, "url": f"https://ejemplo.com/{n}", "titulo": titulo, "primera_linea": primera,
          "fecha": fecha, "hora": "10:00" if fecha else None, "region": "espana", "clase": clase}
    if tema:
        it["tema"] = tema
    return it


# ---------------------------------------------------------------- regla máxima

def test_regla_maxima_quita_lo_que_no_esta_y_descarta_si_falta_el_dato_principal():
    item = _item(1, "Fulano Inventado wins the Example Poker Open for $48,000", fuente_="PokerNews")
    otro = _item(2, "Fulano Inventado wins the Example Poker Open for $48,000", fuente_="CardPlayer")
    r = modelo.resumir([item, otro], AJ, _llamar({"tarjetas": [
        {"ids": [1], "clasificacion": "confirmado", "titulo": "Fulano Inventado gana el Example Poker Open por $48.000",
         "resumen": "Fulano Inventado ganó el Example Poker Open. Se jugó el domingo en Londres ante 527 jugadores. "
                    "Dijo: “fue el mejor día de mi vida”."},
        {"ids": [2], "clasificacion": "confirmado", "titulo": "Fulano Inventado gana $95.000 en el Example Poker Open",
         "resumen": "Fulano Inventado ganó."}]}), hoy=HOY)
    t = r["tarjetas"][0]
    assert t["resumen"] == "Fulano Inventado ganó el Example Poker Open."
    motivos = " ".join(c["motivo"] for c in r["controles"])
    assert "cifra «527»" in motivos and "fecha «domingo»" in motivos and "lugar «londres»" in motivos and "cita" in motivos
    assert len(r["tarjetas"]) == 1 and "título con datos que no están en la fuente (cifra «95.000»)" in motivos


def test_que_cambio_tambien_debe_estar_en_la_fuente():
    previo = {"titulo": "Fulano Inventado deja la sala de ejemplo", "resumen": "La sala de poker investiga a Fulano Inventado.", "hoy": True}
    item = _item(1, "Example poker room bans Fulano Inventado", fuente_="PokerNews")
    r = modelo.resumir([item], AJ, _llamar({"tarjetas": [{"ids": [1], "clasificacion": "confirmado",
        "titulo": "La sala de poker banea a Fulano Inventado", "resumen": "La sala baneó a Fulano Inventado.",
        "actualiza": "P1", "novedad": "La sala lo baneó y le confiscó US$300.000"}]}), previos=[previo], hoy=HOY)
    assert r["tarjetas"] == []
    assert "«qué cambió» con datos que no están en la fuente" in r["controles"][-1]["motivo"]
    assert "la fuente no precisa" in modelo.SISTEMA


def test_entradas_del_dia_para_auditar(raiz_temporal):
    larga = "Primera frase muy larga " * 20
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel>'
           f'<item><title>Fulano Inventado wins the Example Poker Open</title><link>https://ejemplo-poker.com/n1</link>'
           f'<description>{larga}.</description><pubDate>Sun, 04 Oct 2026 10:00:00 +0000</pubDate></item>'
           '</channel></rss>' + " " * 300)
    corrida.ejecutar(raiz_temporal, AHORA, "manual", DescargaFalsa({"https://ejemplo-poker.com/feed/": (200, rss)}),
                     _llamar({"tarjetas": []}), generar_pagina=False)
    entradas = leer_json(raiz_temporal / "data" / "entradas" / "2026-10-04.json")
    nota = entradas["corridas"][0]["notas"][0]
    assert nota["fuente"] == "Ejemplo" and nota["url"] == "https://ejemplo-poker.com/n1"
    assert nota["titular"] == "Fulano Inventado wins the Example Poker Open"
    assert len(nota["primera_frase"]) <= 200


# ---------------------------------------------------------------- cambio 2: avances de día 1

def test_dia1_solo_de_torneos_relevantes(tmp_path):
    relevantes = torneos.cargar_relevantes(RAIZ)
    descartar = torneos.descartador_dia1(relevantes)
    assert descartar(_item(1, "Fulano Inventado leads Day 1A of the Example Poker Open"))["torneo"] == "Example Poker Open"
    assert descartar(_item(2, "Mengano Ficticio lidera el Día 1B del Festival de Ejemplo"))["torneo"] == "Festival de Ejemplo"
    assert descartar(_item(3, "Chip leader after Day 1 at Kings of Tallinn Main Event"))
    for pasa in ("Fulano Inventado lidera el Día 1C del Main Event del WPT Global Festival",  # WPT: relevante
                 "CAP Buenos Aires: Fulano Inventado lidera el día 1",                       # regional relevante
                 "Fulano Inventado leads Day 2 of the Example Poker Open",                   # día 2 pasa siempre
                 "Fulano Inventado wins the Example Poker Open"):                            # resultado
        assert descartar(_item(9, pasa)) is None, pasa
    # El archivo es editable: agregar un torneo lo vuelve relevante.
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "torneos_relevantes.json").write_text(json.dumps(
        {"regionales": [{"nombre": "Example Poker Open", "claves": ["Example Poker Open"]}]}), encoding="utf-8")
    assert torneos.descartador_dia1(torneos.cargar_relevantes(tmp_path))(
        _item(1, "Fulano Inventado leads Day 1A of the Example Poker Open")) is None


def test_dia1_queda_anotado_en_controles(raiz_temporal):
    rss = ('<?xml version="1.0"?><rss version="2.0"><channel>'
           '<item><title>Fulano Inventado leads Day 1A of the Example Poker Open</title><link>https://ejemplo-poker.com/d1</link>'
           '<pubDate>Sun, 04 Oct 2026 10:00:00 +0000</pubDate></item>'
           '<item><title>Mengano Ficticio wins the Example Poker Open</title><link>https://ejemplo-poker.com/res</link>'
           '<pubDate>Sun, 04 Oct 2026 11:00:00 +0000</pubDate></item></channel></rss>' + " " * 300)
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", DescargaFalsa({"https://ejemplo-poker.com/feed/": (200, rss)}),
                         _llamar({"tarjetas": []}), generar_pagina=False)
    d1 = [x for x in c["controles"] if "día 1" in x.get("motivo", "")]
    assert d1 == [{"fuente": "Ejemplo", "titulo": "Fulano Inventado leads Day 1A of the Example Poker Open",
                   "url": "https://ejemplo-poker.com/d1", "torneo": "Example Poker Open",
                   "motivo": "avance de día 1 de un torneo que no está en config/torneos_relevantes.json"}]
    assert c["recoleccion"]["descartes"]["totales"]["por_dia1"] == 1


# ---------------------------------------------------------------- cambio 3a: lista manual y botón

def test_gestionar_tema_agregar_desactivar_quitar(tmp_path):
    (tmp_path / "config").mkdir()
    assert "agregado" in temas.gestionar(tmp_path, "agregar", "Caso Fulano", "Fulano Inventado, superusuario", HOY)
    datos = leer_json(tmp_path / "config" / "temas_calientes.json")
    assert datos["temas"][0] == {"nombre": "Caso Fulano", "palabras_clave": ["Fulano Inventado", "superusuario"],
                                 "alta": "2026-10-06", "estado": "activo"}
    temas.gestionar(tmp_path, "desactivar", "caso fulano", "", HOY)
    assert leer_json(tmp_path / "config" / "temas_calientes.json")["temas"][0]["estado"] == "inactivo"
    assert temas.activos(tmp_path) == []
    temas.gestionar(tmp_path, "quitar", "Caso Fulano", "", HOY)
    assert leer_json(tmp_path / "config" / "temas_calientes.json")["temas"] == []


def test_flujo_gestionar_tema_caliente():
    flujo = yaml.safe_load((RAIZ / ".github" / "workflows" / "gestionar-tema.yml").read_text(encoding="utf-8"))
    entradas = flujo[True]["workflow_dispatch"]["inputs"]
    assert set(entradas) == {"accion", "nombre", "palabras_clave"}
    assert entradas["accion"]["options"] == ["agregar", "quitar", "desactivar"]
    pasos = flujo["jobs"]["tema"]["steps"]
    for paso in pasos:  # lo que escribe el usuario nunca se pega directo en un comando
        assert "${{ inputs" not in paso.get("run", "")
    assert any("python -m mesa.temas" in p.get("run", "") for p in pasos)
    assert any("git push" in p.get("run", "") for p in pasos)
    manual = leer_json(RAIZ / "config" / "temas_calientes.json")["temas"]
    assert {"Paul Gregg", "MeshAgent", "superuser"} <= set(manual[0]["palabras_clave"])


# ---------------------------------------------------------------- cambio 3b: detección automática

def _tarjeta_dia(raiz, dia, titulo, url):
    agregar_corrida(raiz, dia, {"id": f"{dia}T12:00:00Z", "origen": "automatica", "estado": "ok", "notas": [],
                                "tarjetas": [{"clasificacion": "confirmado", "fecha": "", "titulo": titulo,
                                              "resumen": "Texto de la nota de poker.", "fuentes": [{"nombre": "x", "url": url}]}],
                                "escena_latam": {"tarjetas": []}})


def test_tema_automatico_con_tres_fuentes_y_apagado_a_los_siete_dias(tmp_path):
    (tmp_path / "config").mkdir()
    _tarjeta_dia(tmp_path, HOY - timedelta(days=2), "Fulano Inventado acusado de colusión", "https://medio-uno.com/a")
    _tarjeta_dia(tmp_path, HOY - timedelta(days=1), "Fulano Inventado responde a la acusación", "https://medio-dos.com/b")
    _tarjeta_dia(tmp_path, HOY - timedelta(days=1), "Mengano Ficticio gana un torneo", "https://medio-uno.com/c")
    temas.detectar_auto(tmp_path, HOY)
    assert leer_json(tmp_path / "data" / "temas_auto.json")["temas"] == []  # solo 2 fuentes
    _tarjeta_dia(tmp_path, HOY, "La sala suspende a Fulano Inventado", "https://medio-tres.com/d")
    datos = temas.detectar_auto(tmp_path, HOY)
    tema = datos["temas"][0]
    assert tema["palabras_clave"] == ["Fulano Inventado"] and tema["estado"] == "activo"
    assert tema["fuentes"] == ["medio-dos.com", "medio-tres.com", "medio-uno.com"]
    assert [t["nombre"] for t in temas.activos(tmp_path)] == [tema["nombre"]]
    datos = temas.detectar_auto(tmp_path, HOY + timedelta(days=8))  # 8 días sin novedades
    assert datos["temas"][0]["estado"] == "inactivo"


# ---------------------------------------------------------------- cambio 3c y 3f: tarjeta de tema y vigilancia

def test_una_tarjeta_por_tema_y_dia_con_sus_novedades(tmp_path):
    shutil.copytree(RAIZ / "templates", tmp_path / "templates")
    (tmp_path / "config").mkdir()
    shutil.copy(RAIZ / "config" / "temas_calientes.json", tmp_path / "config" / "temas_calientes.json")
    tema = "Caso Paul Gregg (malware MeshAgent)"
    for hora, n in (("12:45", 1), ("15:45", 2)):
        agregar_corrida(tmp_path, HOY, {"id": f"2026-10-06T{hora}:00Z", "origen": "automatica", "estado": "ok",
            "notas": [], "tarjetas": [], "escena_latam": {"tarjetas": []},
            "temas": [{"tema": tema, "linea": f"Línea {n}.", "novedades": [
                {"aporta": f"Novedad {n}.", "clasificacion": "confirmado" if n == 1 else "discusion", "fuente": "Poker Red",
                 "fecha": "2026-10-06", "hora": "10:00" if n == 1 else None, "url": f"https://ejemplo.com/{n}"}]}]})
    pagina.generar(tmp_path, HOY, datetime(2026, 10, 6, 16, tzinfo=timezone.utc))
    sopa = BeautifulSoup((tmp_path / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser")
    tarjetas = sopa.select("article.card-tema")
    assert len(tarjetas) == 1
    texto = " ".join(tarjetas[0].get_text(" ").split())
    assert "Tema caliente" in texto and tema in texto and "Línea 2." in texto
    novedades = tarjetas[0].select("ul.tema-novedades li")
    assert len(novedades) == 2
    assert "10:00 UTC" in novedades[0].get_text() and "UTC" not in novedades[1].get_text()
    assert novedades[0].select_one(".badge.confirmado") and novedades[1].select_one(".badge.discusion")
    assert [a["href"] for a in tarjetas[0].select("a.go")] == ["https://ejemplo.com/1", "https://ejemplo.com/2"]
    vigilancia = sopa.select_one("section.vigilancia")
    assert "Temas en vigilancia" in vigilancia.get_text() and tema in vigilancia.get_text()


# ---------------------------------------------------------------- cambio 3d: prioridad

def test_notas_de_tema_entran_siempre_primero_y_fuera_de_topes_y_filtros():
    detector = temas.detector([{"nombre": "Caso Fulano", "palabras_clave": ["Fulano Inventado"]}])
    descartar = torneos.descartador_dia1([])
    items = [_item(n, f"Nota común número {n} de poker") for n in range(1, 6)]
    items += [_item(10, "Fulano Inventado leads Day 1A of the Example Poker Open"),   # día 1: igual entra
              _item(11, "Fulano Inventado: nueva cuenta detectada")]
    tomados, conteo = filtrar_fuente(items, set(), AHORA - timedelta(days=3), datetime(2026, 10, 6, 12, tzinfo=timezone.utc),
                                     2, False, detector, descartar)
    assert [t["url"] for t in tomados[:2]] == ["https://ejemplo.com/10", "https://ejemplo.com/11"]
    assert all(t["tema"] == "Caso Fulano" for t in tomados[:2])
    assert len(tomados) == 4 and conteo["por_tope"] == 3 and conteo["por_dia1"] == 0  # tope 2 solo para las comunes
    elegidos = seleccionar(tomados, 1)
    assert [e.get("tema") for e in elegidos] == ["Caso Fulano", "Caso Fulano", None]  # tope total 1 + las del tema
    m = modelo.construir_mensaje(elegidos)
    assert m.index("[tema: Caso Fulano]") < m.index("Nota común")


# ---------------------------------------------------------------- cambio 3e: repetido informativo (caso Paul Gregg)

TEMA_GREGG = "Caso Paul Gregg (malware MeshAgent)"
# Resumen real de la tarjeta publicada el 5 de octubre de 2026.
PUBLICADO_GREGG = ("Escándalo del 'superuser': Paul Gregg y el ataque de malware afectan múltiples salas. "
                   "El escándalo del 'superuser' involucra al jugador canadiense Paul Gregg, quien habría utilizado malware "
                   "para acceder a las cartas privadas de rivales en línea. Dos salas de poker fueron alertadas previamente "
                   "sobre la cuenta sospechosa; una confiscó $100.000 y reembolsó a los afectados. CoinPoker detectó y "
                   "baneó a Gregg en menos de una semana; ACR Poker implementó nuevas medidas de seguridad como Screen "
                   "Shield en respuesta.")


def test_paul_gregg_mismas_personas_datos_nuevos_entran_y_sin_datos_nuevos_no():
    # Titulares reales publicados por Poker Red el 4 y 5 de octubre de 2026.
    items = [
        _item(1, "GGPoker ignoró un informe de Patrick Howard sobre Paul Gregg", tema=TEMA_GREGG),
        _item(2, "Paul Gregg utilizaba historiales de manos para elegir a quién infectar con mesh agent", tema=TEMA_GREGG,
              fecha=None),
        _item(3, "CoinPoker detectó y baneó a Paul Gregg en menos de una semana", tema=TEMA_GREGG),
    ]
    r = modelo.resumir(items, AJ, _llamar({"temas": [{"tema": TEMA_GREGG, "linea_principal": "Nuevos datos sobre Paul Gregg.",
        "novedades": [
            {"id": 1, "clasificacion": "confirmado", "aporta": "Según Poker Red, GGPoker ignoró un informe de Patrick Howard sobre Paul Gregg."},
            {"id": 2, "clasificacion": "confirmado", "aporta": "Paul Gregg utilizaba historiales de manos para elegir a quién infectar."},
            {"id": 3, "clasificacion": "confirmado", "aporta": "CoinPoker detectó y baneó a Paul Gregg en menos de una semana."}]}]}),
        hoy=HOY, publicado_temas={TEMA_GREGG: PUBLICADO_GREGG})
    tarjeta = r["temas"][0]
    assert [n["url"] for n in tarjeta["novedades"]] == ["https://ejemplo.com/1", "https://ejemplo.com/2"]
    assert tarjeta["novedades"][0]["hora"] == "10:00" and "hora" not in tarjeta["novedades"][1]
    assert tarjeta["novedades"][1]["fecha"] == "2026-10-06"  # sin fecha en la fuente: la de la barrida
    assert {"url": "https://ejemplo.com/3", "tema": TEMA_GREGG,
            "motivo": "repetida: no aporta ningún dato nuevo al tema"} in r["controles"]
    assert r["tarjetas"] == [] and r["escena_latam"] == []


def test_novedad_de_tema_con_dato_inventado_se_quita():
    items = [_item(1, "GGPoker ignoró un informe de Patrick Howard sobre Paul Gregg", tema=TEMA_GREGG)]
    r = modelo.resumir(items, AJ, _llamar({"temas": [{"tema": TEMA_GREGG, "linea_principal": "",
        "novedades": [{"id": 1, "clasificacion": "confirmado",
                       "aporta": "GGPoker ignoró un informe de Patrick Howard. La sala pagó US$2 millones a los afectados."}]}]}),
        hoy=HOY, publicado_temas={TEMA_GREGG: PUBLICADO_GREGG})
    assert r["temas"][0]["novedades"][0]["aporta"] == "GGPoker ignoró un informe de Patrick Howard."
