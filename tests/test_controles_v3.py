"""Controles revisados el 19 oct 2026 con los descartes reales de la página (7 al 19 de octubre):
falsos positivos por traducción y formato de cifras, oraciones cortadas por abreviaturas, títulos sin
traducir, marca de nacionalidad, duplicados entre secciones, Reddit como fuente de fuentes y filtro temático.
Los nombres de personas de los casos son los de las notas reales que se descartaron por error."""

import json
from datetime import timedelta

from mesa import calidad, control, corrida, modelo, recoleccion

from .conftest import AHORA, fuente


# ---------------------------------------------------------------- cifras

def test_cifras_con_multiplicador_o_formato_de_otro_idioma_se_rastrean():
    casos = [
        ("PokerNews Online Championship vuelve con casi US$ 800.000", "returns with nearly $800K in prizes"),
        ("La carrera reparte US$ 1 millón en premios", "with $1,000,000 in prizes"),
        ("La serie repartió US$ 410.000", "the series has paid out $410K so far"),
        ("GGPoker supera las 270.000 entradas", "GGPoker passa de 270 mil entradas"),
        ("Giselle54 gana US$ 2,2 millones", "Giselle54 wins $2.2 million in a week"),
        ("Antonius y el bote de $1,35 millones", "o pote de $1.35 milhão"),
        ("Fondo de €1.480.530", "prize pool of €1,480,530"),
    ]
    for tarjeta, fuente_texto in casos:
        assert calidad.no_rastreables(tarjeta, fuente_texto) == [], tarjeta


def test_cifra_inventada_sigue_detectandose():
    assert calidad.no_rastreables("Fulano cobró US$ 300.000", "Fulano won $30,000") == ["cifra «300.000»"]
    assert calidad.no_rastreables("Fulano cobró US$ 3 millones", "Fulano won $300K") == ["cifra «3»"]


def test_anio_en_curso_en_nombre_de_temporada(monkeypatch):
    monkeypatch.setattr(calidad, "_ANIO_ACTUAL", 2026)
    assert calidad.no_rastreables("WSOP Online Fall 2026 garantiza US$ 5 millones", "WSOP Online Fall guarantees $5M") == []
    assert calidad.no_rastreables("La final será en 2027", "The final is next year") == ["cifra «2027»"]


# ---------------------------------------------------------------- nombres propios y traducciones

def test_traducciones_habituales_y_signos_internos_no_son_nombres_inventados():
    casos = [
        ("ACR Poker realiza el primer Campeonato de Damas Online", "ACR Poker hosts first Ladies Online Championship"),
        ("La WSOP define su Jugador del Año en Paradise", "WSOP Player of the Year race ends at Paradise"),
        ("DENNO0211 gana el King's Million Closer", "DENNO0211 wins King’s Million Closer"),
        ("Joe Hachem impulsa el retorno del Mix-Max", "Joe Hachem leads Mix-Max return"),
        ("Policía irrumpe en un juego ilegal en Gales", "Police raid illegal game in Cardiff, Wales"),
    ]
    for tarjeta, fuente_texto in casos:
        assert calidad.no_rastreables(tarjeta, fuente_texto) == [], tarjeta


def test_nombre_propio_inventado_sigue_detectandose():
    faltan = calidad.no_rastreables("El torneo se jugó en el Casino Inventadísimo", "The event was played downtown")
    assert faltan == ["nombre propio «Inventadísimo»"]


def test_pais_traducido_no_se_toma_como_parte_de_un_nombre():
    tarjeta = {"titulo": "Nik (Rumania) gana el Super Main Event", "resumen": "Nik (Rumania) ganó el torneo."}
    conservar, cambios = control.verificar_nombres(tarjeta, "Nik (Romania) wins the Super Main Event")
    assert conservar and cambios == []


# ---------------------------------------------------------------- oraciones

def test_abreviaturas_no_cortan_la_oracion():
    texto = "Mike Hurley ganó el RGPS St. Louis Main Event con US$ 52.597. Fue su segundo título."
    assert calidad._oraciones(texto) == ["Mike Hurley ganó el RGPS St. Louis Main Event con US$ 52.597.",
                                         "Fue su segundo título."]
    resumen, quitadas = calidad.depurar_texto(texto, "Mike Hurley wins RGPS St. Louis Main Event for $52,597, "
                                                      "his second title")
    assert resumen == texto and quitadas == []


# ---------------------------------------------------------------- títulos y jerga

def test_titulo_con_terminos_en_ingles_no_se_descarta():
    medio = fuente("Medio Inventado", "https://medio-inventado.com/feed/", region="internacional", clase="medio")
    items = [recoleccion._item(medio, "https://medio-inventado.com/n/1", "Fulano Inventado wins the Big Event",
                               AHORA, "Fulano Inventado won the Big Event for $50,000.")]

    def llamar(*_):
        return json.dumps({"tarjetas": [{"ids": [1], "clasificacion": "confirmado",
                                         "titulo": "Fulano Inventado wins the Big Event for $50,000",
                                         "resumen": "Fulano Inventado ganó el Big Event y cobró US$ 50.000."}]}), {}
    r = modelo.resumir(items, {}, llamar, AHORA.date())
    assert [t["titulo"] for t in r["tarjetas"]] == ["Fulano Inventado wins the Big Event for $50,000"]
    assert "no los traduzca" in modelo.SISTEMA


# ---------------------------------------------------------------- nacionalidad

def test_marca_de_nacionalidad_no_va_sobre_torneos_ni_si_ya_hay_gentilicio():
    t = {"titulo": "Triton ONE en Chipre abre oportunidad para latinos", "resumen": "El circuito busca jugadores."}
    assert control.asegurar_nacionalidad(t, [{"titulo": "Triton ONE Cyprus", "primera_linea": ""}], True) is None
    assert "[nacionalidad a confirmar]" not in t["titulo"]
    t = {"titulo": "Adrián Mateos subcampeón en el Evento #2",
         "resumen": "Adrián Mateos, el español, terminó segundo y cobró US$ 151.200."}
    assert control.asegurar_nacionalidad(t, [{"titulo": "Mateos segundo", "primera_linea": ""}], True) is None
    t = {"titulo": "José Lesta gana el Super High Roller del CAP", "resumen": "José Lesta ganó el torneo."}
    assert control.asegurar_nacionalidad(t, [{"titulo": "José Lesta gana", "primera_linea": ""}], True) \
        == "marca de nacionalidad agregada"


# ---------------------------------------------------------------- duplicados entre secciones

def test_mismo_hecho_no_se_repite_en_la_seccion_latina():
    medio = fuente("Medio Inventado", "https://medio-inventado.com/feed/", region="internacional", clase="medio")
    latino = fuente("Medio Latino", "https://medio-latino.com/feed/", region="latam", clase="medio")
    texto = "Fulano Inventado ganó el WPT Ficticio Championship en Texas y cobró US$ 90.000."
    items = [recoleccion._item(medio, "https://medio-inventado.com/n/1", "Fulano Inventado gana el WPT Ficticio",
                               AHORA, texto),
             recoleccion._item(latino, "https://medio-latino.com/n/1", "Fulano Inventado vence el WPT Ficticio",
                               AHORA, texto)]

    def llamar(*_):
        tarjeta = {"clasificacion": "confirmado", "titulo": "Fulano Inventado gana el WPT Ficticio Championship",
                   "resumen": texto}
        return json.dumps({"tarjetas": [dict(tarjeta, ids=[1])], "escena_latam": [dict(tarjeta, ids=[2])]}), {}
    r = modelo.resumir(items, {}, llamar, AHORA.date())
    assert len(r["tarjetas"]) == 1 and r["escena_latam"] == []


# ---------------------------------------------------------------- Reddit y medios de juego general

def _rss(entradas):
    items = "".join(f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
                    f"<pubDate>{AHORA.strftime('%a, %d %b %Y %H:%M:%S +0000')}</pubDate></item>"
                    for t, u, d in entradas)
    return (f'<?xml version="1.0"?><rss version="2.0"><channel><title>x</title><link>https://x.com/</link>'
            f"<description>{' ' * 300}</description>{items}</channel></rss>")


def test_reddit_aporta_pocas_notas_y_los_sitios_que_comparte():
    reddit = fuente("Reddit", "https://www.reddit.com/r/poker/.rss", region="internacional", clase="comunidad",
                    uso="descubrimiento", max_titulares=3)
    entradas = [(f"Hilo número {n} sobre poker online", f"https://www.reddit.com/r/poker/comments/{n}/",
                 f'&lt;a href="https://medio-nuevo.com/nota-{n}"&gt;[link]&lt;/a&gt; '
                 f'&lt;a href="https://www.reddit.com/r/poker/comments/{n}/"&gt;[comments]&lt;/a&gt;')
                for n in range(8)]
    descarga = lambda url: recoleccion.Respuesta(200, _rss(entradas), url)
    leidos = []
    nuevos, informe, _ = recoleccion.recolectar([reddit], AHORA.date(), AHORA, set(), {}, descarga,
                                                AHORA - timedelta(hours=12), leidos=leidos)
    assert len(nuevos) == 3 and informe[0]["por_tope"] == 5
    assert nuevos[0]["enlaces"] == ["https://medio-nuevo.com/nota-0"]
    estado = {"menciones": []}
    assert corrida.registrar_enlaces_compartidos(leidos, estado, AHORA.date()) == 8
    assert {m["dominio"] for m in estado["menciones"]} == {"medio-nuevo.com"}


def test_reddit_tampoco_se_agota_con_temas_calientes():
    reddit = fuente("Reddit", "https://www.reddit.com/r/poker/.rss", region="internacional", clase="comunidad",
                    uso="descubrimiento", max_titulares=3)
    entradas = [(f"Caso Inventado: novedad {n}", f"https://www.reddit.com/r/poker/comments/{n}/", "x") for n in range(6)]
    descarga = lambda url: recoleccion.Respuesta(200, _rss(entradas), url)
    prioridad = lambda it: "Caso Inventado" if "Caso Inventado" in it["titulo"] else None
    nuevos, _, _ = recoleccion.recolectar([reddit], AHORA.date(), AHORA, set(), {}, descarga,
                                          AHORA - timedelta(hours=12), prioridad=prioridad)
    assert len(nuevos) == 3 and all(it["tema"] == "Caso Inventado" for it in nuevos)


def test_medio_de_juego_general_solo_aporta_notas_de_poker():
    juego = fuente("Medio de Juego", "https://juego-inventado.com/feed/", region="internacional", clase="medio",
                   tematica="juego")
    entradas = [("Casino Inventado abre un hotel nuevo en Macao", "https://juego-inventado.com/1", "Nuevo hotel."),
                ("Sportsbook Ficticio lanza apuestas de fútbol", "https://juego-inventado.com/2", "Apuestas."),
                ("Fulano Inventado gana el Main Event del WSOP Circuit", "https://juego-inventado.com/3", "Poker.")]
    descarga = lambda url: recoleccion.Respuesta(200, _rss(entradas), url)
    nuevos, informe, recogidos = recoleccion.recolectar([juego], AHORA.date(), AHORA, set(), {}, descarga,
                                                        AHORA - timedelta(hours=12))
    assert [it["url"] for it in nuevos] == ["https://juego-inventado.com/3"]
    assert informe[0]["por_tematica"] == 2 and len(recogidos) == 3  # lo descartado igual queda como visto


# ---------------------------------------------------------------- casos de la primera corrida (19 oct, 16:21 UTC)

def test_misma_persona_en_otro_hecho_no_es_repetida():
    assert control.parece_repetida("Aaron Barone gana Mystery Bounty en PokerNews Online Championship de 888",
                                   ["Aaron Barone multiplica bankroll en desafío de 888poker"]) is None
    assert control.parece_repetida("Jose Alonso gana Main Event de SIXERS Barcelona por €35.000",
                                   ["José Ignacio Alonso lidera High Roller récord en SIXERS Barcelona"]) is None
    previo = "Marius Conan triunfa en 888LIVE London Main Event por £91.170"
    assert control.parece_repetida("Marius Conan gana 888LIVE London Main Event por £91.170", [previo]) == previo


def test_nombres_no_cruzan_oraciones_ni_incluyen_montos_ni_la_palabra_inicial():
    t = {"titulo": "César García subcampeón del GGMillion$ PLO", "resumen": "Capturó US$ 196.384 en el torneo."}
    assert control.verificar_nombres(t, "César García, runner-up del GGMillion$ PLO por 196.384 $") == (True, [])
    t = {"titulo": "Shaun Deeb habla del Hall of Fame", "resumen": "Deeb habló del Poker Hall of Fame. También del POY."}
    assert control.verificar_nombres(t, "Shaun Deeb on WSOP POY Issues & Poker Hall of Fame Snub") == (True, [])
    assert t["resumen"] == "Deeb habló del Poker Hall of Fame. También del POY."
    # Un nombre de pila agregado al comienzo de una oración se sigue quitando.
    t = {"titulo": "Fulano gana", "resumen": "Mengano Fulano ganó el torneo."}
    conservar, cambios = control.verificar_nombres(t, "Fulano wins the event")
    assert conservar and cambios and t["resumen"] == "Fulano ganó el torneo."
