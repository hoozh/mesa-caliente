"""Meta diaria de 10 notas: recuperación a 48 horas, segundo nivel acotado, reglas fijas intactas,
topes de costo y tokens, pie de página y entrada «origen» del flujo. Nombres de personas inventados."""

import json
import os
import stat
import subprocess
from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path

import yaml
from bs4 import BeautifulSoup

from mesa import corrida, modelo, pagina, recoleccion, torneos
from mesa.dias import agregar_corrida
from mesa.util import RAIZ, leer_json, normalizar_url

from .conftest import AHORA, DescargaFalsa, fuente

FEED = "https://medio-inventado.com/feed/"
FORO = "https://foro-inventado.com/feed/"


def _rss(notas):
    entradas = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
        f"<pubDate>{format_datetime(f)}</pubDate></item>" for t, u, d, f in notas)
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Feed</title>'
            f"<link>https://x.com/</link><description>{' ' * 300}</description>{entradas}</channel></rss>")


# Nota nueva (dentro de la ventana normal), nota de hace 20 horas (solo entra con la ventana de 48 h)
# y nota de hace 60 horas (fuera de toda ventana).
NUEVA = ("Fulano Inventado gana la Serie Ficticia", "https://medio-inventado.com/n/nueva",
         "Fulano Inventado ganó el Main Event de la Serie Ficticia y cobró US$ 50.000.", AHORA - timedelta(hours=2))
DE_AYER = ("Mengana Ficticia gana el High Roller Ficticio", "https://medio-inventado.com/n/ayer",
           "Mengana Ficticia ganó el High Roller Ficticio y cobró US$ 20.000.", AHORA - timedelta(hours=20))
VIEJA = ("Zutano Imaginario gana el Torneo Antiguo", "https://medio-inventado.com/n/vieja",
         "Zutano Imaginario ganó el Torneo Antiguo y cobró US$ 9.000.", AHORA - timedelta(hours=60))


def _preparar(raiz, notas=(NUEVA, DE_AYER, VIEJA), foro=(), ajustes_extra=None):
    fuentes = [fuente("Medio Inventado", FEED, region="internacional", clase="medio")]
    respuestas = {FEED: (200, _rss(notas))}
    if foro:
        fuentes.append(fuente("Foro Inventado", FORO, region="internacional", clase="comunidad"))
        respuestas[FORO] = (200, _rss(foro))
    (raiz / "config" / "fuentes.json").write_text(json.dumps({"fuentes": fuentes}), encoding="utf-8")
    if ajustes_extra:
        ajustes = leer_json(raiz / "config" / "ajustes.json")
        ajustes.update(ajustes_extra)
        (raiz / "config" / "ajustes.json").write_text(json.dumps(ajustes), encoding="utf-8")
    # Corrida exitosa hace 6 horas: la ventana normal empieza 9 horas atrás.
    agregar_corrida(raiz, AHORA.date(), {"id": (AHORA - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "origen": "automatica", "estado": "sin_noticias", "tarjetas": [],
                                         "escena_latam": {"tarjetas": []}, "notas": []})
    return DescargaFalsa(respuestas)


class Modelo:
    """Llamador falso: registra cada llamada y arma tarjetas copiando el titular y la primera línea."""

    def __init__(self, tokens_salida=(400, 400), extra=None):
        self.llamadas = []
        self.tokens_salida = list(tokens_salida)
        self.extra = extra or {}

    def __call__(self, sistema, mensaje, modelo_id, max_tokens):
        self.llamadas.append({"mensaje": mensaje, "max_tokens": max_tokens})
        tarjetas = []
        for nota in (NUEVA, DE_AYER, VIEJA):
            for linea in mensaje.splitlines():
                if nota[0] in linea and linea[:2].startswith("[") and linea[1:2].isdigit():
                    tarjetas.append({"ids": [int(linea[1:linea.index("]")])], "clasificacion": "confirmado",
                                     "titulo": nota[0], "resumen": nota[2]})
        tarjetas += self.extra.get(len(self.llamadas), [])
        salida = self.tokens_salida[min(len(self.llamadas), len(self.tokens_salida)) - 1]
        return json.dumps({"tarjetas": tarjetas, "escena_latam": []}), {"tokens_entrada": 3000,
                                                                         "tokens_salida": salida}


# ---------------------------------------------------------------- la meta se cuenta en el día

def test_meta_cuenta_todas_las_corridas_del_dia(raiz_temporal):
    tarjeta = lambda t: {"titulo": t, "resumen": "r", "fuentes": []}
    tema = {"tema": "Caso Inventado", "linea": "l", "novedades": [{"url": "https://a.com/1", "aporta": "x"}]}
    agregar_corrida(raiz_temporal, AHORA.date(), {"id": "a", "tarjetas": [tarjeta("A"), tarjeta("B")],
                                                  "escena_latam": {"tarjetas": [tarjeta("C")]}, "temas": [tema]})
    agregar_corrida(raiz_temporal, AHORA.date() - timedelta(days=1), {"id": "z", "tarjetas": [tarjeta("Z")] * 9})
    en_curso = {"id": "b", "tarjetas": [tarjeta("D")], "escena_latam": {"tarjetas": []}, "temas": [tema]}
    # 2 + 1 latam + 1 de la corrida en curso + el tema (una sola vez por día). Ayer no cuenta.
    assert corrida.tarjetas_publicadas_hoy(raiz_temporal, AHORA.date(), en_curso) == 5


def test_con_la_meta_alcanzada_no_hay_segunda_llamada(raiz_temporal):
    descarga = _preparar(raiz_temporal)
    agregar_corrida(raiz_temporal, AHORA.date(), {
        "id": "previa", "origen": "manual", "estado": "ok", "escena_latam": {"tarjetas": []},
        "tarjetas": [{"titulo": f"Tarjeta {n}", "resumen": "r", "fuentes": []} for n in range(10)]})
    llamar = Modelo()
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, llamar)
    assert len(llamar.llamadas) == 1
    assert c["meta"]["recuperacion"] is None and c["meta"]["publicadas"] == 11


# ---------------------------------------------------------------- (a) ventana de 48 horas

def test_recuperacion_amplia_la_ventana_a_48_horas(raiz_temporal):
    descarga = _preparar(raiz_temporal)
    llamar = Modelo()
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, llamar)
    assert len(llamar.llamadas) == 2
    principal, recuperacion = (x["mensaje"] for x in llamar.llamadas)
    assert NUEVA[0] in principal and DE_AYER[0] not in principal
    assert DE_AYER[0] in recuperacion and "CORRIDA DE RECUPERACIÓN" in recuperacion
    assert VIEJA[0] not in recuperacion          # más de 48 horas: nunca entra
    titulos = [t["titulo"] for t in c["tarjetas"]]
    assert titulos == [NUEVA[0], DE_AYER[0]]
    assert c["tarjetas"][1]["recuperada"] is True
    assert c["meta"]["publicadas"] == 2 and c["meta"]["recuperacion"]["tarjetas"] == 1
    # Auditoría: la recuperación queda registrada aparte.
    entradas = leer_json(raiz_temporal / "data" / "entradas" / f"{AHORA.date()}.json")["corridas"]
    assert entradas[-1]["tipo"] == "recuperacion"
    assert [n["url"] for n in entradas[-1]["notas"]] == [DE_AYER[1]]


def test_candidatas_excluyen_publicadas_enviadas_vistas_antiguas_y_dia1():
    medio = fuente("Medio Inventado", FEED, region="internacional")
    con_fecha = [recoleccion._item(medio, u, t, f, d) for t, u, d, f in (NUEVA, DE_AYER, VIEJA)]
    dia1 = recoleccion._item(medio, "https://medio-inventado.com/n/dia1", "Día 1A del Torneo Ficticio de Pueblo",
                             AHORA - timedelta(hours=10), "Fulano Inventado lidera el Día 1A con 80.000 fichas.")
    publicada = recoleccion._item(medio, "https://medio-inventado.com/n/publicada", "Perengana Supuesta gana",
                                  AHORA - timedelta(hours=30), "Perengana Supuesta ganó.")
    sin_fecha = [recoleccion._item(medio, f"https://otro-inventado.com/s/{n}", f"Nota sin fecha {n}", None, "")
                 for n in range(3)]
    leidos = [{"fuente": "Medio Inventado", "items": con_fecha + [dia1, publicada]},
              {"fuente": "Otro", "items": sin_fecha}]
    vistos = {normalizar_url(sin_fecha[1]["url"]): (AHORA.date() - timedelta(days=1)).isoformat(),  # dentro de 48 h
              normalizar_url(sin_fecha[2]["url"]): (AHORA.date() - timedelta(days=5)).isoformat()}  # antigua
    excluidas = {normalizar_url(NUEVA[1]), normalizar_url(publicada["url"])}
    elegidas = corrida.candidatas_recuperacion(leidos, AHORA, vistos, excluidas, 48, 10,
                                               descartar_dia1=torneos.descartador_dia1([]))
    assert [it["url"] for it in elegidas] == [DE_AYER[1], sin_fecha[0]["url"], sin_fecha[1]["url"]]


# ---------------------------------------------------------------- (b) segundo nivel

def _items_nivel2():
    medio = fuente("Medio Inventado", FEED, region="internacional", clase="medio")
    foro = fuente("Foro Inventado", FORO, region="internacional", clase="comunidad")
    return [recoleccion._item(medio, "https://medio-inventado.com/n/serie", "Perengana Supuesta gana un torneo menor",
                              AHORA, "Perengana Supuesta ganó el torneo de US$ 100 y cobró US$ 4.000."),
            recoleccion._item(foro, "https://foro-inventado.com/t/1", "Zutano Imaginario gana un torneo casero",
                              AHORA, "Zutano Imaginario ganó el torneo casero y cobró US$ 300.")]


def _llamar_nivel2(sistema, mensaje, modelo_id, max_tokens):
    return json.dumps({"tarjetas": [
        {"ids": [1], "nivel": 2, "clasificacion": "confirmado", "titulo": "Perengana Supuesta gana un torneo menor",
         "resumen": "Perengana Supuesta ganó el torneo de US$ 100 y cobró US$ 4.000."},
        {"ids": [2], "nivel": 2, "clasificacion": "confirmado", "titulo": "Zutano Imaginario gana un torneo casero",
         "resumen": "Zutano Imaginario ganó el torneo casero y cobró US$ 300."}]}), {"tokens_entrada": 10, "tokens_salida": 10}


def test_segundo_nivel_solo_en_recuperacion_y_de_fuentes_oficiales_o_medios():
    items = _items_nivel2()
    fuera = modelo.resumir(items, {}, _llamar_nivel2, AHORA.date())
    assert fuera["tarjetas"] == []
    assert all(x["motivo"] == "segundo nivel fuera de la recuperación" for x in fuera["controles"])
    dentro = modelo.resumir(items, {}, _llamar_nivel2, AHORA.date(),
                            recuperacion={"publicadas": 5, "meta": 10, "faltan": 5})
    assert [t["titulo"] for t in dentro["tarjetas"]] == ["Perengana Supuesta gana un torneo menor"]
    assert dentro["tarjetas"][0]["nivel"] == 2
    assert dentro["controles"][0]["motivo"].startswith("segundo nivel: solo se admiten fuentes oficiales o medios")
    assert dentro["controles"][0]["fuentes"] == ["Foro Inventado"]


def test_segundo_nivel_no_supera_lo_que_falta_para_la_meta():
    medio = fuente("Medio Inventado", FEED, region="internacional", clase="medio")
    items = [recoleccion._item(medio, f"https://medio-inventado.com/n/{n}", f"Perengana Supuesta {n} gana", AHORA,
                               f"Perengana Supuesta {n} ganó y cobró US$ {n}.000.") for n in (1, 2, 3)]

    def llamar(*_):
        return json.dumps({"tarjetas": [
            {"ids": [n], "nivel": 2, "clasificacion": "confirmado", "titulo": f"Perengana Supuesta {n} gana",
             "resumen": f"Perengana Supuesta {n} ganó y cobró US$ {n}.000."} for n in (1, 2, 3)]}), {}
    r = modelo.resumir(items, {}, llamar, AHORA.date(), recuperacion={"publicadas": 9, "meta": 10, "faltan": 1})
    assert len(r["tarjetas"]) == 1
    assert sum(1 for x in r["controles"] if x["motivo"] == "segundo nivel: la meta ya se alcanzó") == 2


# ---------------------------------------------------------------- reglas fijas, sin cambios en la recuperación

def test_reglas_fijas_no_se_relajan_en_la_recuperacion():
    medio = fuente("Medio Inventado", FEED, region="internacional", clase="medio")
    items = [
        recoleccion._item(medio, "https://medio-inventado.com/p/1", "Sala Inventada regala bonos de depósito",
                          AHORA, "Deposite hoy y reciba un bono del 100 % con el código PROMO."),
        recoleccion._item(medio, "https://medio-inventado.com/p/2", "Novedades en la sala",
                          AHORA, "La sala anunció cambios en su programa."),
        recoleccion._item(medio, "https://medio-inventado.com/p/3", "Fulano Inventado gana un torneo",
                          AHORA, "Fulano Inventado ganó el torneo."),
        recoleccion._item(medio, "https://medio-inventado.com/p/4", "Mengana Ficticia gana el High Roller Ficticio",
                          AHORA, "Mengana Ficticia ganó el High Roller Ficticio y cobró US$ 20.000."),
    ]

    def llamar(*_):
        return json.dumps({"tarjetas": [
            {"ids": [1], "nivel": 2, "clasificacion": "confirmado", "titulo": "Sala Inventada regala bonos de depósito",
             "resumen": "Deposite hoy y reciba un bono del 100 % con el código PROMO."},
            {"ids": [2], "nivel": 2, "clasificacion": "confirmado", "titulo": "Novedades en la sala",
             "resumen": "La sala anunció cambios en su programa."},
            {"ids": [3], "clasificacion": "confirmado", "titulo": "Fulano Inventado gana US$ 80.000",
             "resumen": "Fulano Inventado ganó el torneo y cobró US$ 80.000."},
            {"ids": [4], "clasificacion": "confirmado", "titulo": "Mengana Ficticia gana el High Roller Ficticio",
             "resumen": "Mengana Ficticia ganó el High Roller Ficticio y cobró US$ 20.000."}]}), {}
    previos = [{"titulo": "Mengana Ficticia gana el High Roller Ficticio",
                "resumen": "Mengana Ficticia ganó el High Roller Ficticio y cobró US$ 20.000.", "hoy": True}]
    r = modelo.resumir(items, {}, llamar, AHORA.date(), previos,
                       recuperacion={"publicadas": 2, "meta": 10, "faltan": 8})
    assert r["tarjetas"] == []
    categorias = sorted(modelo.categoria_descarte(x["motivo"]) for x in r["controles"])
    assert categorias == ["promociones", "repetidas", "sin_dato", "sin_respaldo"]


def test_recuperacion_nunca_completa_ni_inventa(raiz_temporal):
    """Si el modelo no encuentra más hechos, el día queda con lo que hay: sin tarjetas de relleno."""
    descarga = _preparar(raiz_temporal, notas=(NUEVA, VIEJA))

    def llamar(sistema, mensaje, modelo_id, max_tokens):
        if "CORRIDA DE RECUPERACIÓN" in mensaje:
            return json.dumps({"tarjetas": [], "escena_latam": []}), {"tokens_entrada": 10, "tokens_salida": 5}
        return Modelo()(sistema, mensaje, modelo_id, max_tokens)
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, llamar)
    assert [t["titulo"] for t in c["tarjetas"]] == [NUEVA[0]]
    assert c["meta"]["publicadas"] == 1


# ---------------------------------------------------------------- topes de costo, tokens y titulares

def test_recuperacion_respeta_el_tope_de_tokens_de_salida(raiz_temporal):
    descarga = _preparar(raiz_temporal)
    llamar = Modelo(tokens_salida=(1000, 400))
    corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, llamar)
    assert llamar.llamadas[1]["max_tokens"] == 2000  # 3000 por corrida menos los 1000 usados


def test_sin_presupuesto_de_salida_no_hay_segunda_llamada(raiz_temporal):
    descarga = _preparar(raiz_temporal)
    llamar = Modelo(tokens_salida=(2600,))
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, llamar)
    assert len(llamar.llamadas) == 1
    assert c["meta"]["recuperacion"]["omitida"] == "sin presupuesto de tokens de salida en esta corrida"


def test_recuperacion_respeta_el_tope_de_costo(raiz_temporal):
    descarga = _preparar(raiz_temporal, ajustes_extra={"max_costo_usd_por_corrida": 0.016})
    llamar = Modelo()
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, llamar)
    assert len(llamar.llamadas) == 1
    assert c["meta"]["recuperacion"]["omitida"] == "superaría el tope de costo por corrida"


def test_recuperacion_respeta_el_tope_de_titulares(raiz_temporal):
    descarga = _preparar(raiz_temporal, ajustes_extra={"max_titulares_enviados": 1})
    llamar = Modelo()
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, llamar)
    assert len(llamar.llamadas) == 1
    assert c["meta"]["recuperacion"]["omitida"] == "sin cupo de titulares en esta corrida"


def test_costo_extra_se_registra_aparte_y_se_muestra(raiz_temporal):
    descarga = _preparar(raiz_temporal)
    corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga, Modelo(tokens_salida=(1000, 400)))
    costos = leer_json(raiz_temporal / "data" / "costos.json")["corridas"]
    assert [c["tipo"] for c in costos] == ["principal", "recuperacion"]
    assert costos[1]["costo_usd"] == round(3000 / 1e6 + 400 * 5 / 1e6, 6)
    pie = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"),
                        "html.parser").footer.get_text(" ")
    assert "costo estimado US$ 0.0130" in pie  # 0,008 + 0,005
    assert "US$ 0.0050 corresponden a la llamada de recuperación" in pie


# ---------------------------------------------------------------- pie de página

def test_pie_muestra_n_notas_hoy_con_motivos_por_regla_y_por_fuente(raiz_temporal):
    agregar_corrida(raiz_temporal, AHORA.date(), {
        "id": "2026-10-04T12:45:00Z", "origen": "automatica", "estado": "ok", "escena_latam": {"tarjetas": []},
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "4 oct 2026", "titulo": f"Tarjeta {n}", "resumen": "r",
                      "fuentes": [{"nombre": "Medio Inventado", "fecha": None, "url": f"https://m.com/{n}"}]}
                     for n in range(5)],
        "recoleccion": {"titulares_enviados": 42, "descartes": {"totales": {
            "por_fecha": 161, "por_orden": 175, "por_tope": 1, "por_dia1": 0, "ya_vistos": 8}, "por_fuente": {}}},
        "controles": [{"titulo": "a", "motivo": "sin dato concreto (nombre, monto o evento)", "fuentes": ["Medio Inventado"]},
                      {"titulo": "b", "motivo": "sin dato concreto (nombre, monto o evento)", "fuentes": ["Medio Inventado"]},
                      {"titulo": "c", "motivo": "repetida: ya publicada como «x»", "fuentes": ["Foro Inventado"]},
                      {"titulo": "d", "motivo": "nacionalidad agregada"}],
        "meta": {"meta": 10, "publicadas": 5, "recuperacion": {"enviadas": 7, "tarjetas": 0, "segundo_nivel": 0,
                                                               "costo_usd": 0.004, "omitida": None}}})
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    pie = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"),
                        "html.parser").footer.get_text(" ")
    assert "5 notas hoy" in pie and "la meta diaria es 10" in pie
    assert "161 titulares por fecha" in pie and "175 por orden" in pie
    assert "sin dato concreto (2); repetidas sin dato nuevo (1)." in pie
    assert "Medio Inventado : sin dato concreto (2)" in pie and "Foro Inventado : repetidas sin dato nuevo (1)" in pie
    assert "7 notas revisadas, 0 tarjetas agregadas" in pie


def test_pie_con_meta_alcanzada_no_da_motivos(raiz_temporal):
    agregar_corrida(raiz_temporal, AHORA.date(), {
        "id": "x", "origen": "manual", "estado": "ok", "escena_latam": {"tarjetas": []},
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "", "titulo": f"T{n}", "resumen": "r", "fuentes": []}
                     for n in range(10)]})
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    pie = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"),
                        "html.parser").footer.get_text(" ")
    assert "10 notas hoy" in pie and "Motivo" not in pie


# ---------------------------------------------------------------- entrada «origen» del flujo

def test_origen_externo_se_omite_si_hoy_hubo_una_corrida_exitosa(raiz_temporal, monkeypatch):
    llamadas = []
    monkeypatch.setattr(corrida, "RAIZ", raiz_temporal)
    monkeypatch.setattr(corrida, "ahora_utc", lambda: AHORA)
    monkeypatch.setattr(corrida, "ejecutar", lambda raiz, ahora, origen: llamadas.append(origen) or
                        {"estado": "ok", "motivo": None, "tarjetas": [], "uso": None})
    # Sin corrida exitosa hoy: la externa corre.
    corrida.main(["--origen", "externo", "--omitir-si-hubo-exito-hoy"])
    assert llamadas == ["externo"]
    agregar_corrida(raiz_temporal, AHORA.date(), {"id": "y", "origen": "manual", "estado": "ok", "tarjetas": []})
    corrida.main(["--origen", "externo", "--omitir-si-hubo-exito-hoy"])
    assert llamadas == ["externo"]  # omitida: ni modelo ni cambios
    corrida.main(["--origen", "manual"])  # manual sin origen: siempre corre
    assert llamadas == ["externo", "manual"]


def _paso_del_flujo():
    flujo = yaml.safe_load((RAIZ / ".github" / "workflows" / "barrido-diario.yml").read_text(encoding="utf-8"))
    disparadores = flujo.get("on") or flujo.get(True)
    pasos = next(iter(flujo["jobs"].values()))["steps"]
    return disparadores, next(p for p in pasos if "mesa.corrida" in (p.get("run") or ""))


def test_flujo_tiene_la_entrada_origen_opcional():
    disparadores, paso = _paso_del_flujo()
    entrada = disparadores["workflow_dispatch"]["inputs"]["origen"]
    assert entrada["required"] is False and entrada["default"] == ""
    assert "${{ inputs.origen }}" not in paso["run"]  # se pasa por variable de entorno, nunca interpolado
    assert paso["env"]["ORIGEN_ENTRADA"] == "${{ inputs.origen }}"


def test_flujo_elige_los_argumentos_segun_evento_y_origen(tmp_path):
    _, paso = _paso_del_flujo()
    falso = tmp_path / "bin" / "python"
    falso.parent.mkdir()
    falso.write_text('#!/bin/sh\necho "$@"\n')
    falso.chmod(falso.stat().st_mode | stat.S_IEXEC)

    def correr(evento, origen):
        env = {"PATH": f"{falso.parent}:{os.environ['PATH']}", "EVENTO": evento, "ORIGEN_ENTRADA": origen}
        return subprocess.run(["bash", "-c", paso["run"]], env=env, capture_output=True, text=True).stdout.strip()
    assert correr("schedule", "") == "-m mesa.corrida --origen automatica --omitir-si-hubo-exito-hoy"
    assert correr("workflow_dispatch", " Externo ") == "-m mesa.corrida --origen externo --omitir-si-hubo-exito-hoy"
    assert correr("workflow_dispatch", "") == "-m mesa.corrida --origen manual"
    assert correr("workflow_dispatch", "otro; rm -rf /") == "-m mesa.corrida --origen manual"
