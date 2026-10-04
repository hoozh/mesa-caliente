"""Términos exactos, nacionalidad y hechos repetidos (casos reales del 4 de octubre de 2026)."""

import json
from datetime import date, timedelta

from mesa import control, corrida, modelo
from mesa.dias import agregar_corrida

from .conftest import AHORA

AJUSTES = {"max_tarjetas": 12, "max_tarjetas_latam": 6, "max_tokens_salida": 3000,
           "modelo_por_defecto": "claude-haiku-4-5",
           "precios_usd_por_millon": {"claude-haiku-4-5": {"entrada": 1.0, "salida": 5.0}}}

HAYWARD = {
    "fuente": "WSOP.com", "url": "https://www.wsop.com/news/connor-hayward-wins-the-horseshoe-council-bluffs-wsop-circuit-main-event/",
    "titulo": "CONNOR HAYWARD WINS THE HORSESHOE COUNCIL BLUFFS WSOP CIRCUIT MAIN EVENT", "fecha": None, "region": "internacional",
    "primera_linea": "Connor Hayward wins his first WSOP Circuit ring in the Horseshoe Council Bluffs WSOP Circuit Main Event, "
                     "while Daniel Lowery and Ari Engel reach their 21st rings.",
}
PAPO = {
    "fuente": "PokerNews en español", "url": "https://es.pokernews.com/noticias/2026/10/papomc-conquista-el-main-event-medium-del-wcoop-y-firma-su-m-53922.htm",
    "titulo": "PapoMC conquista el Main Event Medium del WCOOP y firma su mayor premio online",
    "primera_linea": "El jugador se llevó 343.242 dólares.", "fecha": "2026-10-01T10:00:00+00:00", "region": "latam",
}
AIDO = {
    "fuente": "PokerNewsDaily", "url": "https://www.pokernewsdaily.com/sergio-aido-claims-third-wsop-bracelet-49065/",
    "titulo": "Sergio Aido Wins 2026 WSOP Online GGMillion$ High Rollers for $2,228,762, Claiming Third Bracelet",
    "primera_linea": "Sergio Aido captured the 2026 WSOP Online $10,000 GGMillion$ High Rollers title on GGPoker.",
    "fecha": "2026-09-30T23:42:47+00:00", "region": "internacional",
}
AIDO_PREVIO = "Sergio Aido gana el GGMillion$ de US$10.000 y suma su tercer brazalete WSOP"


def llamador(respuesta: dict, capturado: dict | None = None):
    def _llamar(sistema, mensaje, modelo_id, max_tokens):
        if capturado is not None:
            capturado.update(sistema=sistema, mensaje=mensaje)
        return json.dumps(respuesta), {"tokens_entrada": 100, "tokens_salida": 50}
    return _llamar


# ---------------------------------------------------------------- 1. términos

def test_prompt_exige_terminos_exactos():
    capturado = {}
    modelo.resumir([HAYWARD], AJUSTES, llamador({"tarjetas": []}, capturado))
    sistema = capturado["sistema"]
    assert '"ring" es "anillo"' in sistema and '"Bracelet" es "brazalete"' in sistema
    assert 'si la fuente dice "ring", no escriba "brazalete"' in sistema


def test_hayward_anillo_no_brazalete():
    """El 4 de octubre el modelo escribió "brazalete" aunque WSOP.com decía "ring"."""
    r = modelo.resumir([HAYWARD], AJUSTES, llamador({"tarjetas": [{
        "ids": [1], "clasificacion": "confirmado",
        "titulo": "Connor Hayward gana su primer brazalete WSOP Circuit",
        "resumen": "Connor Hayward ganó su primer brazalete en el Main Event del WSOP Circuit; "
                   "Daniel Lowery y Ari Engel alcanzaron sus 21 brazaletes."}]}))
    t = r["tarjetas"][0]
    assert t["titulo"] == "Connor Hayward gana su primer anillo WSOP Circuit"
    assert "brazalete" not in (t["titulo"] + t["resumen"]).lower()
    assert "21 anillos" in t["resumen"]
    assert any("brazalete → anillo" in c["motivo"] for c in r["controles"])


def test_brazalete_correcto_no_se_toca():
    tarjeta = {"titulo": "Sergio Aido suma su tercer brazalete", "resumen": "Ganó el brazalete del GGMillion$."}
    assert control.corregir_terminos(tarjeta, [AIDO]) == []
    assert tarjeta["titulo"] == "Sergio Aido suma su tercer brazalete"


def test_ring_game_no_cuenta_como_anillo():
    item = dict(HAYWARD, titulo="New ring games at the casino", primera_linea="Cash ring game tables open.")
    tarjeta = {"titulo": "Nuevo brazalete en juego", "resumen": "Texto."}
    assert control.corregir_terminos(tarjeta, [item]) == []


# ---------------------------------------------------------------- 2. nacionalidad

def test_prompt_exige_nacionalidad():
    capturado = {}
    modelo.resumir([PAPO], AJUSTES, llamador({"tarjetas": []}, capturado))
    assert "[nacionalidad a confirmar]" in capturado["sistema"]
    assert "nunca la deduzca del nombre" in capturado["sistema"]


def test_papomc_sin_nacionalidad_en_la_fuente_lleva_marca():
    """El 4 de octubre la tarjeta de PapoMC salió sin nacionalidad y sin marca."""
    r = modelo.resumir([PAPO], AJUSTES, llamador({"escena_latam": [{
        "ids": [1], "clasificacion": "confirmado", "titulo": "PapoMC conquista el Main Event Medium del WCOOP 2026",
        "resumen": "PapoMC ganó el Main Event Medium del WCOOP y se llevó 343.242 dólares."}]}))
    t = r["escena_latam"][0]
    assert control.MARCA_NACIONALIDAD in t["titulo"]


def test_papomc_con_nacionalidad_en_la_fuente_la_incluye():
    item = dict(PAPO, primera_linea="El argentino Patricio 'PapoMC' Lococo se llevó 343.242 dólares.")
    r = modelo.resumir([item], AJUSTES, llamador({"escena_latam": [{
        "ids": [1], "clasificacion": "confirmado", "titulo": "PapoMC conquista el Main Event Medium del WCOOP 2026",
        "resumen": "PapoMC ganó el Main Event Medium del WCOOP y se llevó 343.242 dólares."}]}))
    t = r["escena_latam"][0]
    assert "Argentina" in t["resumen"]
    assert control.MARCA_NACIONALIDAD not in t["titulo"]


def test_nacionalidad_ya_escrita_no_se_duplica():
    item = dict(PAPO, primera_linea="El argentino Patricio 'PapoMC' Lococo se llevó 343.242 dólares.")
    tarjeta = {"titulo": "El argentino PapoMC gana", "resumen": "Ganó 343.242 dólares."}
    assert control.asegurar_nacionalidad(tarjeta, [item], es_latam=True) is None
    assert tarjeta["resumen"] == "Ganó 343.242 dólares."


def test_tarjeta_principal_sin_nacionalidad_no_lleva_marca():
    tarjeta = {"titulo": "Connor Hayward gana", "resumen": "Texto."}
    assert control.asegurar_nacionalidad(tarjeta, [HAYWARD], es_latam=False) is None


# ---------------------------------------------------------------- 3. repetidos

def test_mensaje_incluye_solo_titulos_previos():
    capturado = {}
    modelo.resumir([AIDO], AJUSTES, llamador({"tarjetas": []}, capturado), previos=[AIDO_PREVIO])
    assert f"[P1] {AIDO_PREVIO}" in capturado["mensaje"]
    assert "No cree una tarjeta para un hecho ya cubierto" in capturado["sistema"]


def test_aido_repetido_sin_novedad_se_descarta():
    """El 4 de octubre se publicó otra vez el triunfo de Sergio Aido, ya cubierto el 30 de septiembre."""
    r = modelo.resumir([AIDO], AJUSTES, llamador({"tarjetas": [{
        "ids": [1], "clasificacion": "confirmado",
        "titulo": "Sergio Aido gana el WSOP Online GGMillion$ High Rollers 2026",
        "resumen": "Sergio Aido ganó el GGMillion$ High Rollers y su tercer brazalete."}]}), previos=[AIDO_PREVIO])
    assert r["tarjetas"] == []
    assert r["controles"][0]["motivo"] == "repetida" and r["controles"][0]["previo"] == AIDO_PREVIO


def test_aido_declarado_como_repetido_sin_novedad_se_descarta():
    r = modelo.resumir([AIDO], AJUSTES, llamador({"tarjetas": [{
        "ids": [1], "clasificacion": "confirmado", "titulo": "Sergio Aido gana", "resumen": "Texto.",
        "actualiza": "P1"}]}), previos=[AIDO_PREVIO])
    assert r["tarjetas"] == []


def test_aido_con_desarrollo_nuevo_dice_que_cambio():
    r = modelo.resumir([AIDO], AJUSTES, llamador({"tarjetas": [{
        "ids": [1], "clasificacion": "confirmado",
        "titulo": "Sergio Aido: se confirma el premio de US$2.228.762",
        "resumen": "PokerNewsDaily confirmó el premio final de Sergio Aido.",
        "actualiza": "P1", "novedad": "Se conoce el premio final: US$2.228.762."}]}), previos=[AIDO_PREVIO])
    t = r["tarjetas"][0]
    assert t["actualiza"] == AIDO_PREVIO and t["novedad"].startswith("Se conoce el premio")


def test_hechos_distintos_del_mismo_circuito_no_se_confunden():
    previos = ["Eric Chang gana el Main Event del WSOP Circuit en Texas Card House",
               "Liga Patagónica: Diego Visosa gana el Mini Main Event"]
    assert control.parece_repetida("Connor Hayward gana el Main Event del WSOP Circuit Council Bluffs", previos) is None
    assert control.parece_repetida("Liga Patagónica: Mario Guarino gana la LPP en Bariloche", previos) is None


def test_corrida_envia_titulos_de_los_ultimos_tres_dias(raiz_temporal, descarga_ejemplo):
    def dia(n, titulo):
        agregar_corrida(raiz_temporal, AHORA.date() - timedelta(days=n), {
            "id": f"x{n}", "origen": "importada", "estado": "ok", "notas": [],
            "tarjetas": [{"clasificacion": "confirmado", "fecha": "", "titulo": titulo, "resumen": "R", "fuentes": []}],
            "escena_latam": {"tarjetas": [{"clasificacion": "confirmado", "fecha": "", "titulo": f"Latam {n}",
                                           "resumen": "R", "fuentes": []}]}})
    dia(1, "Título de ayer")
    dia(3, "Título de hace tres días")
    dia(4, "Título de hace cuatro días")
    capturado = {}
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga_ejemplo, llamador({"tarjetas": []}, capturado))
    assert c["estado"] == "sin_noticias"
    m = capturado["mensaje"]
    assert "Título de ayer" in m and "Latam 1" in m and "Título de hace tres días" in m
    assert "Título de hace cuatro días" not in m
    assert "https://" not in m.split("Títulos ya publicados")[1]  # solo títulos


def test_pagina_muestra_que_cambio(raiz_temporal):
    from bs4 import BeautifulSoup
    from mesa import pagina
    agregar_corrida(raiz_temporal, date(2026, 10, 4), {
        "id": "2026-10-04T12:45:00Z", "origen": "automatica", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "4 oct 2026", "titulo": "T", "resumen": "R",
                      "actualiza": AIDO_PREVIO, "novedad": "Se conoce el premio final.",
                      "fuentes": [{"nombre": "A", "fecha": None, "url": "https://a.com"}]}],
        "escena_latam": {"tarjetas": [], "texto": None}})
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    sopa = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser")
    nota = sopa.select_one(".update-note").get_text(" ")
    assert "Qué cambió:" in nota and "Se conoce el premio final." in nota and AIDO_PREVIO in nota
