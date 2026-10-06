"""Controles de calidad pedidos tras la página del 5 de octubre de 2026.

Cada prueba reproduce un problema real de ese día con nombres claramente inventados.
"""

import json
from datetime import timedelta

from mesa import calidad, control, corrida, modelo
from mesa.dias import agregar_corrida
from mesa.util import leer_json

from .conftest import AHORA, DescargaFalsa, fuente

AJ = {"max_tarjetas": 12, "max_tarjetas_latam": 6, "max_tokens_salida": 3000, "precios_usd_por_millon": {}}


def item(n, titulo, primera="", fuente_="PokerNews", fecha="2026-10-05T10:00:00+00:00", region="internacional",
         clase="medio", extracto=None):
    it = {"fuente": fuente_, "url": f"https://ejemplo.com/nota-{n}", "titulo": titulo, "primera_linea": primera,
          "fecha": fecha, "region": region, "clase": clase}
    if extracto:
        it["extracto"] = extracto
    return it


def llamador(respuesta, capturado=None):
    def _llamar(sistema, mensaje, modelo_id, max_tokens):
        if capturado is not None:
            capturado.update(sistema=sistema, mensaje=mensaje)
        return json.dumps(respuesta), {"tokens_entrada": 100, "tokens_salida": 50}
    return _llamar


def tarjeta(ids, titulo, resumen, clasif="confirmado", **extra):
    return {"ids": ids, "clasificacion": clasif, "titulo": titulo, "resumen": resumen, **extra}


# ---------------------------------------------------------------- 1. tarjetas del mismo día

def test_el_modelo_recibe_las_tarjetas_de_hoy_con_su_resumen(raiz_temporal):
    agregar_corrida(raiz_temporal, AHORA.date(), {
        "id": "2026-10-04T12:45:00Z", "origen": "automatica", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "4 oct 2026", "titulo": "Fulana Ejemplar deja el programa de poker en vivo",
                      "resumen": "El programa investiga acusaciones de colusión contra Fulana Ejemplar.", "fuentes": []}],
        "escena_latam": {"tarjetas": []}})
    agregar_corrida(raiz_temporal, AHORA.date() - timedelta(days=2), {
        "id": "x", "origen": "importada", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "", "titulo": "Título de hace dos días",
                      "resumen": "Resumen viejo que no se envía.", "fuentes": []}]})
    previos = corrida.previos_recientes(raiz_temporal, AHORA.date(), 5)
    assert previos[0] == {"titulo": "Fulana Ejemplar deja el programa de poker en vivo",
                          "resumen": "El programa investiga acusaciones de colusión contra Fulana Ejemplar.", "hoy": True}
    m = modelo.construir_mensaje([], previos)
    assert "(publicada hoy) Fulana Ejemplar deja el programa de poker en vivo — El programa investiga" in m
    assert "Título de hace dos días" in m and "Resumen viejo" not in m


def test_hecho_de_hoy_repetido_sin_novedad_real_se_descarta():
    """Caso del malware del 5 de octubre: la segunda corrida lo volvió a publicar con una "novedad" vacía."""
    previo = {"titulo": "Escándalo de malware: dos salas fueron alertadas sobre la cuenta de Fulano Inventado",
              "resumen": "Dos salas de poker fueron alertadas previamente; una confiscó $100.000 y reembolsó a los afectados.",
              "hoy": True}
    items = [item(1, "Two poker sites were warned about Fulano Inventado account", "One site confiscated $100,000.")]
    r = modelo.resumir(items, AJ, llamador({"tarjetas": [tarjeta(
        [1], "Malware en el poker online: dos salas fueron advertidas sobre Fulano Inventado",
        "Dos salas de poker recibieron alertas sobre la cuenta de Fulano Inventado.",
        actualiza="P1", novedad="Se confirma que dos salas fueron advertidas previamente, con acciones de confiscación y reembolso")]}),
        previos=[previo])
    assert r["tarjetas"] == []
    assert "no agrega nada" in r["controles"][0]["motivo"]


def test_hecho_de_hoy_con_desarrollo_nuevo_pasa_y_dice_que_cambio():
    previo = {"titulo": "Escándalo de malware: dos salas fueron alertadas sobre la cuenta de Fulano Inventado",
              "resumen": "Dos salas de poker fueron alertadas previamente.", "hoy": True}
    items = [item(1, "Poker site refunds $250,000 after Fulano Inventado ban", "Players received refunds.")]
    r = modelo.resumir(items, AJ, llamador({"tarjetas": [tarjeta(
        [1], "La sala reembolsa US$250.000 tras banear a Fulano Inventado", "Los jugadores afectados recibieron reembolsos.",
        actualiza="P1", novedad="La sala devolvió US$250.000 a los jugadores afectados")]}), previos=[previo])
    assert r["tarjetas"][0]["novedad"].startswith("La sala devolvió")
    assert r["tarjetas"][0]["actualiza"] == previo["titulo"]


# ---------------------------------------------------------------- 2. clasificación

def test_solo_reddit_baja_a_discusion():
    reddit = item(1, "Fulano Inventado wins the Example Poker Open", fuente_="Reddit r/poker", clase="comunidad")
    con_fecha = item(3, "Fulano Inventado wins the Example Poker Open", fuente_="PokerNews")
    for items, esperado in (([reddit], "discusion"), ([reddit, con_fecha], "confirmado")):
        r = modelo.resumir(items, AJ, llamador({"tarjetas": [tarjeta(
            list(range(1, len(items) + 1)), "Fulano Inventado gana el Example Poker Open", "Ganó el torneo de poker.")]}))
        assert r["tarjetas"][0]["clasificacion"] == esperado, items
    assert "comunidad" in modelo.SISTEMA


# ---------------------------------------------------------------- 3. cargos, edades, nacionalidades; sin poker

def test_cargo_que_no_figura_en_la_fuente_y_nota_sin_poker_se_descartan():
    """Caso Wembanyama: el modelo agregó "base" y la nota no era de poker."""
    items = [item(1, "Fulano Inventado says he will never endorse a betting platform", fuente_="CardPlayer")]
    r = modelo.resumir(items, AJ, llamador({"tarjetas": [tarjeta(
        [1], "Estrella de la NBA rechaza respaldar plataformas de apuestas",
        "Fulano Inventado, base del equipo de ejemplo, respondió que nunca respaldaría una plataforma de apuestas.")]}))
    assert r["tarjetas"] == []
    assert any(c["motivo"] == "sin relación con el poker" for c in r["controles"])


def test_cargo_no_verificado_se_quita_de_una_nota_de_poker():
    t = {"titulo": "Fulano Inventado critica el slide dealing en el poker en vivo",
         "resumen": "Fulano Inventado, director de torneos, pidió cambios. Dijo que llevará tiempo."}
    conservar, cambios = calidad.verificar_atributos(t, "Fulano Inventado wants slide dealing in live poker")
    assert conservar and "director" not in t["resumen"] and t["resumen"] == "Dijo que llevará tiempo."
    t = {"titulo": "Fulano Inventado critica el slide dealing", "resumen": "Fulano Inventado, director de torneos, pidió cambios."}
    assert calidad.verificar_atributos(t, "tournament director Fulano Inventado")[0] and "director" in t["resumen"]


def test_edad_y_nacionalidad_solo_si_figuran():
    """Caso del African Poker Championship: "sudafricano", "británico" y la edad salieron de otra parte."""
    t = {"titulo": "Fulano Inventado gana el campeonato de ejemplo",
         "resumen": "El sudafricano Fulano Inventado, de 21 años, venció al británico Mengano Ficticio en heads-up."}
    calidad.verificar_atributos(t, "Fulano Inventado beat Mengano Ficticio heads-up to win the example championship.")
    assert t["resumen"] == "Fulano Inventado venció a Mengano Ficticio en heads-up."
    t = {"titulo": "Fulano Inventado gana", "resumen": "El sudafricano Fulano Inventado, de 21 años, ganó."}
    calidad.verificar_atributos(t, "South African Fulano Inventado, a 21-year-old, won.")
    assert t["resumen"] == "El sudafricano Fulano Inventado, de 21 años, ganó."


def test_estrella_de_la_nba_no_se_toma_como_nombre():
    t = {"titulo": "Estrella de la NBA rechaza respaldar plataformas de apuestas", "resumen": "Texto."}
    assert control.verificar_nombres(t, "NBA star rejects betting platform") == (True, [])
    assert t["titulo"].startswith("Estrella de la NBA")


# ---------------------------------------------------------------- 4. dato concreto

def test_sin_ganador_ni_dato_concreto_se_descarta():
    """Casos "campeón francés en Marrakesh" y "Nine Card Flip" del 5 de octubre."""
    items = [item(1, "French grinder wins in Marrakech after four days"),
             item(2, "Nine Card Flip Decides RGPS Lake Erie Main Event Champion", "The RunGood Poker Series title was decided.")]
    r = modelo.resumir(items, AJ, llamador({"tarjetas": [
        tarjeta([1], "Campeón francés se impone en torneo en Marrakesh tras cuatro días de competencia",
                "Un grinder francés consiguió la victoria tras cuatro días de poker."),
        tarjeta([2], "Nine Card Flip decide campeón del RGPS Lake Erie Main Event",
                "El primer título del RunGood Poker Series se decidió en una mano.")]}))
    assert r["tarjetas"] == []
    assert sum("ganador" in c["motivo"] or "dato concreto" in c["motivo"] for c in r["controles"]) == 2


def test_titular_vago_se_abre_primero():
    vago = item(1, "French grinder wins in Marrakech after four days", "A long first line that is long enough to skip opening it.")
    claro = item(2, "Fulano Inventado wins Main Event for $48,000")
    assert corrida.recoleccion.titular_vago(vago) and not corrida.recoleccion.titular_vago(claro)
    pedidas = []

    def descargar(url):
        pedidas.append(url)
        return corrida.recoleccion.Respuesta(200, "<article><p>" + "Fulano Inventado ganó el torneo de poker en Marrakech. " * 3 + "</p></article>", url)
    corrida._abrir_articulos([claro, vago], {"max_articulos_abiertos": 1}, descargar, {"menciones": []}, AHORA.date())
    assert pedidas == [vago["url"]] and "Fulano Inventado" in vago["extracto"]


# ---------------------------------------------------------------- 5. promocionales y superlativos

def test_promocionales_se_descartan():
    """Satélites del WPT, "duplica oportunidades" del BSOP y garantizados de Kings of Tallinn."""
    items = [item(1, "WPT Global satellites from $1.10 to WPT Seoul", region="latam"),
             item(2, "BSOP Millions doubles chances to win R$1 million in cash game promotion", region="latam"),
             item(3, "Kings of Tallinn Autumn Edition announces €500,000 guaranteed Main Event")]
    r = modelo.resumir(items, AJ, llamador({"tarjetas": [
        tarjeta([1], "WPT Global ofrece satélites desde $1,10 para el WPT Seoul", "Satélites con paquetes para el WPT Seoul."),
        tarjeta([2], "BSOP Millions duplica oportunidades de ganar R$ 1 millón en Cash Game", "Como parte de su promoción."),
        tarjeta([3], "Kings of Tallinn Autumn Edition anuncia Main Event con €500.000 garantizados",
                "Presenta su calendario de poker con €500.000 garantizados.")]}))
    assert r["tarjetas"] == []
    assert [c["motivo"] for c in r["controles"]].count("nota promocional") == 3


def test_superlativos_de_la_fuente_se_quitan():
    t = {"titulo": "WSOP Circuit regresa a Panamá por tercera ocasión",
         "resumen": "El circuito más prestigioso del mundo retorna a Panamá. Fulano Inventado logró una remontada espectacular."}
    cambios = calidad.quitar_superlativos(t)
    assert t["resumen"] == "El circuito retorna a Panamá. Fulano Inventado logró una remontada."
    assert len(cambios) == 2


# ---------------------------------------------------------------- 6. marca de nacionalidad

def test_marca_solo_con_persona_nombrada_y_junto_al_nombre():
    latam = item(1, "Fulano Inventado gana el Second Chance del festival de poker", region="latam")
    r = modelo.resumir([latam, item(2, "BSOP Millions anuncia novedades en el cash game de poker", region="latam")], AJ,
                       llamador({"escena_latam": [
                           tarjeta([1], "Fulano Inventado gana el Second Chance del festival", "Se llevó el primer premio del torneo de poker."),
                           tarjeta([2], "BSOP Millions anuncia novedades en el cash game", "El BSOP Millions presenta cambios en su cash game de poker.")]}))
    titulos = [t["titulo"] for t in r["escena_latam"]]
    assert titulos[0] == "Fulano Inventado [nacionalidad a confirmar] gana el Second Chance del festival"
    assert control.MARCA_NACIONALIDAD not in titulos[1] + r["escena_latam"][1]["resumen"]


# ---------------------------------------------------------------- 7. monedas

def test_moneda_a_confirmar_en_fuentes_latinas():
    latina = item(1, "Fulano Inventado gana el Flash de $200 del CAP", "Se llevó $2.946 en el torneo de poker.", region="argentina")
    r = modelo.resumir([latina], AJ, llamador({"escena_latam": [tarjeta(
        [1], "Fulano Inventado gana el Flash de $200 del CAP", "Se llevó 2.946 dólares en el torneo de poker.")]}))
    t = r["escena_latam"][0]
    assert "$200 (moneda a confirmar)" in t["titulo"]
    assert "$2.946 (moneda a confirmar)" in t["resumen"] and "dólares" not in t["resumen"]


def test_moneda_indicada_por_la_fuente_se_respeta():
    con_moneda = item(1, "Fulano Inventado gana US$5.506 en el torneo de poker", region="latam")
    t = {"titulo": "Fulano Inventado gana US$5.506", "resumen": "Premio de US$5.506 en el poker."}
    assert calidad.corregir_monedas(t, [con_moneda]) == [] and "moneda a confirmar" not in t["resumen"]
    ingles = item(2, "Fulano Inventado wins $48,000 poker title")
    t = {"titulo": "Fulano Inventado gana $48.000", "resumen": "Ganó $48.000."}
    assert calidad.corregir_monedas(t, [ingles]) == []
    assert "moneda a confirmar" in modelo.SISTEMA


# ---------------------------------------------------------------- horarios de respaldo

def test_respaldo_se_omite_si_hoy_ya_hubo_corrida_exitosa(raiz_temporal, monkeypatch):
    monkeypatch.setattr(corrida, "RAIZ", raiz_temporal)
    monkeypatch.setattr(corrida, "ahora_utc", lambda: AHORA)
    llamadas = []
    monkeypatch.setattr(corrida, "ejecutar", lambda *a, **k: llamadas.append(a) or {
        "estado": "ok", "motivo": None, "tarjetas": [], "uso": None, "recoleccion": {}})

    assert corrida.main(["--origen", "automatica", "--omitir-si-hubo-exito-hoy"]) == 0
    assert len(llamadas) == 1  # todavía no hubo corrida hoy: corre

    agregar_corrida(raiz_temporal, AHORA.date(), {"id": "2026-10-04T12:45:00Z", "origen": "automatica",
                                                  "estado": "fallida", "tarjetas": []})
    corrida.main(["--origen", "automatica", "--omitir-si-hubo-exito-hoy"])
    assert len(llamadas) == 2  # una fallida no cuenta: el respaldo corre

    agregar_corrida(raiz_temporal, AHORA.date(), {"id": "2026-10-04T13:25:00Z", "origen": "automatica",
                                                  "estado": "ok", "tarjetas": []})
    corrida.main(["--origen", "automatica", "--omitir-si-hubo-exito-hoy"])
    assert len(llamadas) == 2  # ya hubo una exitosa: se omite
    corrida.main(["--origen", "manual"])
    assert len(llamadas) == 3  # las manuales siempre corren


def test_flujo_tiene_los_tres_horarios():
    from mesa.util import RAIZ
    texto = (RAIZ / ".github" / "workflows" / "barrido-diario.yml").read_text(encoding="utf-8")
    for cron in ('"45 12 * * *"', '"25 13 * * *"', '"45 15 * * *"'):
        assert cron in texto
    assert "--omitir-si-hubo-exito-hoy" in texto and "workflow_dispatch" in texto


def test_misma_noticia_de_hoy_sin_actualiza_se_descarta_pero_no_otra_persona():
    """Segunda corrida del 5 de octubre: volvió el caso del malware con otro título."""
    previo = {"titulo": "Escándalo del superuser: Fulano Inventado y el malware afectan a varias salas",
              "resumen": "Dos salas de poker fueron alertadas previamente sobre la cuenta de Fulano Inventado; una confiscó "
                         "$100.000 y reembolsó a los afectados. Otra sala detectó y baneó a Fulano Inventado.",
              "hoy": True}
    items = [item(1, "Poker sites were warned about Fulano Inventado account"),
             item(2, "Mengano Ficticio leads the Day 2 of the poker festival")]
    r = modelo.resumir(items, AJ, llamador({"tarjetas": [
        tarjeta([1], "Malware en el poker online: dos salas fueron alertadas sobre Fulano Inventado",
                "Dos salas de poker recibieron alertas previas sobre la cuenta de Fulano Inventado; una sala baneó la cuenta, "
                "confiscó $100.000 y reembolsó a los afectados."),
        tarjeta([2], "Mengano Ficticio lidera el Día 2 del festival de poker",
                "Mengano Ficticio terminó el Día 2 del festival de poker como líder.")]}), previos=[previo])
    assert [t["titulo"] for t in r["tarjetas"]] == ["Mengano Ficticio lidera el Día 2 del festival de poker"]
    assert r["controles"][0]["motivo"] == "repetida"
