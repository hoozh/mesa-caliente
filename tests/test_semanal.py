"""Brief semanal (con modelo simulado) y carga del historial. Nada fuera del registro; sin red ni API."""

import json
import shutil
from datetime import date, datetime, timezone

from bs4 import BeautifulSoup

from mesa import historial_registro, pagina, registro, semanal
from mesa.dias import agregar_corrida
from mesa.util import RAIZ, leer_json

AHORA = datetime(2026, 10, 26, 12, 30, tzinfo=timezone.utc)  # lunes: el brief es de la semana 2026-W43


def _f(nombre, n):
    return {"nombre": nombre, "url": f"https://{nombre.lower().replace(' ', '-')}.com/n/{n}", "clase": "medio"}


def _hecho(fecha, titulo, fuentes, temas=(), seccion="general", jugadores=(), torneo="", salas=()):
    return {"fecha": fecha, "id": registro.id_hecho(fecha, "c", titulo), "titulo": titulo, "tipo": "resultado",
            "clasificacion": "confirmado", "seccion": seccion, "salas": list(salas), "jugadores": list(jugadores),
            "torneo": torneo, "pais_evento": "", "temas_calientes": list(temas), "fuentes": fuentes}


def _registro(raiz):
    shutil.copy(RAIZ / "config" / "salas_vigiladas.json", raiz / "config" / "salas_vigiladas.json")
    hechos = [
        # Semana anterior (W42): el tema ya estaba.
        _hecho("2026-10-14", "Caso Ficticio: primera denuncia", [_f("Medio Uno", 1)], temas=["Caso Ficticio"]),
        _hecho("2026-10-15", "Fuente vieja aporta", [_f("Medio Viejo", 2)]),
        # Semana del brief (W43).
        _hecho("2026-10-20", "Caso Ficticio: la sala responde", [_f("Medio Uno", 3), _f("Medio Dos", 4)],
               temas=["Caso Ficticio"]),
        _hecho("2026-10-21", "Fulano Inventado gana el Ficticia Open por US$ 50.000",
               [_f("Medio Uno", 5), _f("Medio Dos", 6), _f("Medio Nuevo", 7)], seccion="latam",
               jugadores=[{"nombre": "Fulano Inventado", "pais": "Argentina"}], torneo="Ficticia Open"),
        _hecho("2026-10-22", "Mengana Ficticia gana un High Roller", [_f("Medio Uno", 8)],
               jugadores=[{"nombre": "Mengana Ficticia", "pais": ""}]),
    ]
    cambios = [{"fecha": "2026-10-23", "sala": "GGPoker", "tema": "hud_software",
                "dato": "GGPoker will ban all third-party HUDs", "fuente": _f("Medio Dos", 9)}]
    registro.registrar(raiz, hechos, cambios)
    return hechos


class Modelo:
    def __init__(self, respuesta):
        self.respuesta = respuesta
        self.llamadas = []

    def __call__(self, sistema, mensaje, modelo_id, max_tokens):
        self.llamadas.append({"sistema": sistema, "mensaje": mensaje, "max_tokens": max_tokens})
        return json.dumps(self.respuesta), {"tokens_entrada": 2500, "tokens_salida": 600}


RESPUESTA = {
    "temas": [
        {"texto": "El Caso Ficticio sigue abierto: la sala respondió.", "ids": ["H1"]},
        # Inventa un nombre y una cifra que no están en el registro: esa oración se quita.
        {"texto": "Fulano Inventado ganó el Ficticia Open. Phil Inventadísimo cobró US$ 999.000 en Macao.",
         "ids": ["H2"]},
        {"texto": "Un tema sin referencias válidas.", "ids": ["H99"]},
    ],
    "cambios": [{"texto": "GGPoker anunció: «GGPoker will ban all third-party HUDs».", "ids": ["C1"]}],
    "latinos": [{"texto": "Fulano Inventado, de Argentina, ganó el Ficticia Open.", "ids": ["H2"]},
                {"texto": "Mengana Ficticia ganó un High Roller.", "ids": ["H3"]}],  # H3 no es latino
    "ideas": [{"id": "I1", "texto": "Cronología del Caso Ficticio."},
              {"id": "I9", "texto": "Idea que el programa no eligió."}],
}


def test_semana_anterior_completa():
    assert semanal.semana_de(None, date(2026, 10, 26)) == ("2026-W43", date(2026, 10, 19), date(2026, 10, 25))
    assert semanal.semana_de("2026-W40", date(2026, 10, 26))[1] == date(2026, 9, 28)


def test_una_sola_llamada_y_solo_con_el_registro_de_la_semana(raiz_temporal):
    _registro(raiz_temporal)
    llamar = Modelo(RESPUESTA)
    brief = semanal.generar_brief(raiz_temporal, AHORA.date(), AHORA, llamar)
    assert len(llamar.llamadas) == 1
    mensaje = llamar.llamadas[0]["mensaje"]
    assert "Caso Ficticio: la sala responde" in mensaje and "primera denuncia" not in mensaje  # W42 no va
    assert "GGPoker will ban all third-party HUDs" in mensaje
    assert "[H2] (latino)" in mensaje and "[H3] (latino)" not in mensaje
    assert "sin voseo" in llamar.llamadas[0]["sistema"]
    assert brief["semana"] == "2026-W43" and brief["hechos"] == 3 and brief["cambios_registrados"] == 1


def test_el_brief_no_trae_datos_fuera_del_registro(raiz_temporal):
    _registro(raiz_temporal)
    brief = semanal.generar_brief(raiz_temporal, AHORA.date(), AHORA, Modelo(RESPUESTA))
    publicado = {k: brief[k] for k in ("temas", "cambios", "latinos", "ideas", "fuentes_nuevas")}
    textos = json.dumps(publicado, ensure_ascii=False)
    assert "Phil Inventadísimo" not in textos and "999.000" not in textos and "Macao" not in textos
    assert [e["texto"] for e in brief["temas"]] == ["El Caso Ficticio sigue abierto: la sala respondió.",
                                                    "Fulano Inventado ganó el Ficticia Open."]
    assert any("oración quitada" in c and "Phil Inventadísimo" in c for c in brief["controles"])  # solo en la auditoría
    # Las fuentes las pone el programa, desde el registro.
    assert brief["temas"][1]["fuentes"][0]["url"] == "https://medio-uno.com/n/5"
    assert brief["cambios"][0]["fuentes"] == [{"nombre": "Medio Dos", "url": "https://medio-dos.com/n/9"}]
    # Solo hechos latinos en la sección latina.
    assert [e["texto"] for e in brief["latinos"]] == ["Fulano Inventado, de Argentina, ganó el Ficticia Open."]


def test_ideas_y_fuentes_nuevas_las_decide_el_programa(raiz_temporal):
    _registro(raiz_temporal)
    brief = semanal.generar_brief(raiz_temporal, AHORA.date(), AHORA, Modelo(RESPUESTA))
    ideas = {i["tema"]: i for i in brief["ideas"]}
    # Siguió 2 semanas seguidas (W42 y W43).
    assert ideas["Caso Ficticio"]["motivo"] == "tema caliente que siguió 2 semanas seguidas"
    assert ideas["Caso Ficticio"]["texto"] == "Cronología del Caso Ficticio."
    # 3 fuentes distintas.
    assert ideas["Fulano Inventado gana el Ficticia Open por US$ 50.000"]["motivo"] == "3 fuentes distintas"
    assert len(brief["ideas"]) == 2  # la idea I9 del modelo no existe
    # Fuente nueva: aparece por primera vez en el registro esta semana. Medio Viejo no rindió esta semana.
    assert [f["nombre"] for f in brief["fuentes_nuevas"]] == ["Medio Dos", "Medio Nuevo"]


def test_ideas_por_varios_dias_o_varias_fuentes_sin_esperar_dos_semanas():
    hechos = [
        # El mismo torneo en 2 días, con una fuente cada vez.
        _hecho("2026-10-20", "Comienza la Serie Ficticia en Lima", [_f("Medio Uno", 1)], torneo="Serie Ficticia"),
        _hecho("2026-10-22", "Fulano Inventado lidera la Serie Ficticia", [_f("Medio Uno", 2)], torneo="Serie Ficticia"),
        # El mismo hecho contado dos días seguidos (título casi igual).
        _hecho("2026-10-21", "Mengana Ficticia gana el High Roller Ficticio", [_f("Medio Uno", 3)]),
        _hecho("2026-10-22", "Mengana Ficticia gana el High Roller Ficticio por US$ 20.000", [_f("Medio Dos", 4)]),
        # Tres fuentes el mismo día.
        _hecho("2026-10-23", "Zutano Imaginario gana el Ficticia Cup",
               [_f("Medio Uno", 5), _f("Medio Dos", 6), _f("Medio Tres", 7)]),
        # Un hecho suelto, un solo día y una sola fuente: no es idea.
        _hecho("2026-10-24", "Perengano Supuesto cobra un premio menor", [_f("Medio Uno", 8)]),
    ]
    ideas = {i["tema"]: i["motivo"] for i in semanal.ideas_candidatas(hechos, date(2026, 10, 19), date(2026, 10, 25))}
    assert ideas == {
        "Serie Ficticia": "apareció 2 días (20 oct 2026, 22 oct 2026)",
        "Mengana Ficticia gana el High Roller Ficticio": "apareció 2 días (21 oct 2026, 22 oct 2026)",
        "Zutano Imaginario gana el Ficticia Cup": "3 fuentes distintas",
    }


def test_guarda_publica_y_registra_el_costo(raiz_temporal):
    _registro(raiz_temporal)
    (raiz_temporal / "data" / "costos.json").write_text(json.dumps({"corridas": [
        {"fecha": "2026-10-26", "id": "2026-10-26T12:00:00Z", "tipo": "redaccion", "modelo": "claude-haiku-4-5",
         "tokens_entrada": 1000, "tokens_salida": 100, "costo_usd": 0.0015, "estado": "ok"}]}), encoding="utf-8")
    brief = semanal.generar_brief(raiz_temporal, AHORA.date(), AHORA, Modelo(RESPUESTA))
    semanal.registrar_costo(raiz_temporal, brief, AHORA)
    rutas = semanal.escribir(raiz_temporal, brief)
    costos = leer_json(raiz_temporal / "data" / "costos.json")["corridas"]
    assert costos[-1]["tipo"] == "brief" and costos[-1]["semana"] == "2026-W43"
    assert costos[-1]["costo_usd"] == round(2500 / 1e6 + 600 * 5 / 1e6, 6)
    md = rutas["md"].read_text(encoding="utf-8")
    assert md.startswith("# Brief semanal 2026-W43")
    for titulo in ("## Temas de la semana", "## Cambios en salas", "## Jugadores y torneos latinos destacados",
                   "## Fuentes nuevas que rindieron", "## Ideas de artículos"):
        assert titulo in md
    assert "[Medio Dos](https://medio-dos.com/n/9)" in md and "vos " not in md
    assert (raiz_temporal / "data" / "semanal" / "2026-W43.json").exists()
    html = BeautifulSoup(rutas["html"].read_text(encoding="utf-8"), "html.parser")
    assert html.h2.get_text() == "Brief semanal 2026-W43"
    indice = (raiz_temporal / "docs" / "semanal" / "index.html").read_text(encoding="utf-8")
    assert 'href="2026-W43.html"' in indice
    # El brief no pasa a ser «la última corrida» del pie de la página.
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    assert pagina._costos(leer_json(raiz_temporal / "data" / "costos.json"), AHORA.date())["ultima"]["tipo"] == "redaccion"


def test_sin_registro_no_llama_al_modelo(raiz_temporal):
    llamar = Modelo(RESPUESTA)
    brief = semanal.generar_brief(raiz_temporal, AHORA.date(), AHORA, llamar)
    assert llamar.llamadas == [] and brief["uso"] is None and "no se consultó" in brief["nota"]


# ---------------------------------------------------------------- carga del historial

def _dia_historico(raiz):
    agregar_corrida(raiz, date(2026, 9, 21), {
        "id": "importada-2026-09-21-1", "origen": "importada", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "21 sep 2026", "titulo": "Zutano Imaginario gana el Ficticia Cup",
                      "resumen": "El argentino Zutano Imaginario ganó el Ficticia Cup en PokerStars.",
                      "fuentes": [{"nombre": "Medio Uno", "fecha": None, "url": "https://medio-uno.com/n/1"}]}]})
    agregar_corrida(raiz, date(2026, 9, 20), {
        "id": "importada-2026-09-20-1", "origen": "importada", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "fecha": "20 sep", "titulo": "Anterior al 21", "resumen": "r",
                      "fuentes": [{"nombre": "Medio Uno", "fecha": None, "url": "https://medio-uno.com/n/0"}]}]})


def test_historial_informa_el_costo_sin_llamar_al_modelo(raiz_temporal, capsys, monkeypatch):
    _dia_historico(raiz_temporal)
    monkeypatch.setattr(historial_registro, "RAIZ", raiz_temporal)
    assert historial_registro.main([]) == 0
    salida = capsys.readouterr().out
    assert "Tarjetas por cargar: 1 en 1 llamadas" in salida and "US$" in salida and "--confirmar" in salida
    assert not (raiz_temporal / "data" / "registro" / "hechos.jsonl").exists()


def test_historial_carga_verifica_y_no_duplica(raiz_temporal):
    _dia_historico(raiz_temporal)
    shutil.copy(RAIZ / "config" / "salas_vigiladas.json", raiz_temporal / "config" / "salas_vigiladas.json")
    respuesta = {"fichas": [{"n": 1, "registro": {"tipo": "resultado", "salas": ["PokerStars"],
                                                   "jugadores": [["Zutano Imaginario", "Argentina"], ["Nadie Real", "Chile"]],
                                                   "torneo": "Ficticia Cup", "pais_evento": "Brasil"},
                             "cambios_salas": [{"sala": "PokerStars", "tema": "series_torneos", "dato": "ganó el Ficticia Cup"}]}]}
    llamar = Modelo(respuesta)
    r = historial_registro.cargar(raiz_temporal, llamar)
    assert r["hechos"] == 1 and len(llamar.llamadas) == 1
    h = registro.leer_jsonl(raiz_temporal / "data" / "registro" / "hechos.jsonl")[0]
    assert h["fecha"] == "2026-09-21" and h["salas"] == ["PokerStars"] and h["torneo"] == "Ficticia Cup"
    # El país solo está en el resumen publicado, no en una nota de la fuente: queda pendiente.
    assert h["jugadores"] == [{"nombre": "Zutano Imaginario", "pais": "",
                               "pais_pendiente": {"valor": "Argentina",
                                                  "fuente": {"nombre": "Medio Uno", "url": "https://medio-uno.com/n/1"}}}]
    assert h["pais_evento"] == ""
    # Sin titular original (antes del 7 oct no hay data/entradas): el dato no sale de la fuente, no entra.
    assert r["cambios_salas"] == 0
    assert leer_json(raiz_temporal / "data" / "costos.json")["corridas"][-1]["tipo"] == "historial"
    # Segunda vez: nada que cargar.
    assert historial_registro.pendientes(raiz_temporal) == []
