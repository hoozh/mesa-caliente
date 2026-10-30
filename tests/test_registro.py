"""Registro permanente (data/registro/), cambios en salas y barrido diario sin cambios de comportamiento.
Nombres de personas y medios inventados; ninguna prueba usa red ni la API."""

import json
import shutil
from datetime import timedelta
from email.utils import format_datetime

import yaml
from bs4 import BeautifulSoup

from mesa import corrida, modelo, pagina, registro
from mesa.dias import agregar_corrida
from mesa.util import RAIZ, leer_json

from .conftest import AHORA, DescargaFalsa, fuente

FEED = "https://medio-inventado.com/feed/"
URL_RESULTADO = "https://medio-inventado.com/n/resultado"
URL_PROMO = "https://medio-inventado.com/n/promo"
URL_RAKE = "https://medio-inventado.com/n/rake"

RESULTADO = ("Fulano Inventado wins the Ficticia Open Main Event", URL_RESULTADO,
             "Argentine pro Fulano Inventado won the Ficticia Open Main Event in Uruguay and collected $50,000 on GGPoker.")
PROMO = ("CoinPoker launches 50% rakeback weekend", URL_PROMO,
         "CoinPoker will pay 50% rakeback to all cash game players this weekend.")
RAKE = ("PokerStars cuts rake cap at micro stakes", URL_RAKE,
        "PokerStars said the rake cap at NL2 drops to $0.30 starting November 1.")

CAMPOS = ["fecha", "id", "titulo", "tipo", "clasificacion", "seccion", "salas", "jugadores", "torneo",
          "pais_evento", "temas_calientes", "fuentes"]


def _rss(notas):
    entradas = "".join(
        f"<item><title>{t}</title><link>{u}</link><description>{d}</description>"
        f"<pubDate>{format_datetime(AHORA - timedelta(hours=2))}</pubDate></item>" for t, u, d in notas)
    return (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>Feed</title>'
            f"<link>https://x.com/</link><description>{' ' * 300}</description>{entradas}</channel></rss>")


def _preparar(raiz, notas=(RESULTADO, PROMO, RAKE), con_salas=True):
    (raiz / "config" / "fuentes.json").write_text(json.dumps({"fuentes": [
        fuente("Medio Inventado", FEED, region="internacional", clase="medio")]}), encoding="utf-8")
    if con_salas:
        shutil.copy(RAIZ / "config" / "salas_vigiladas.json", raiz / "config" / "salas_vigiladas.json")
    agregar_corrida(raiz, AHORA.date(), {"id": (AHORA - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                         "origen": "automatica", "estado": "sin_noticias", "tarjetas": [],
                                         "escena_latam": {"tarjetas": []}, "notas": []})
    return DescargaFalsa({FEED: (200, _rss(notas))})


class Modelo:
    """Selección: el resultado y el recorte de rake van a «general», la promo a «salas».
    Redacción: tarjetas con (o sin) la ficha del registro y los cambios en salas que se le indiquen."""

    def __init__(self, con_registro=True, ficha=None, cambios=None):
        self.llamadas = []
        self.con_registro = con_registro
        self.ficha = ficha
        self.cambios = cambios

    @staticmethod
    def _n(mensaje, titulo):
        for linea in mensaje.splitlines():
            if titulo in linea and linea.startswith("["):
                return int(linea[1:linea.index("]")])
        return None

    def __call__(self, sistema, mensaje, modelo_id, max_tokens):
        self.llamadas.append({"sistema": sistema, "mensaje": mensaje})
        uso = {"tokens_entrada": 3000, "tokens_salida": 400}
        n_res, n_promo, n_rake = (self._n(mensaje, x[0]) for x in (RESULTADO, PROMO, RAKE))
        if "solo elige" in sistema:
            hechos = [{"ids": [n_res], "seccion": "general"}]
            if n_rake:
                hechos.append({"ids": [n_rake], "seccion": "general"})
            if n_promo:
                hechos.append({"ids": [n_promo], "seccion": "salas"})
            return json.dumps({"hechos": hechos}), uso
        tarjetas = [{"ids": [n_res], "clasificacion": "confirmado",
                     "titulo": "Fulano Inventado gana el Ficticia Open Main Event",
                     "resumen": "Fulano Inventado ganó el Ficticia Open Main Event y cobró $50,000."}]
        if n_rake:
            tarjetas.append({"ids": [n_rake], "clasificacion": "confirmado",
                             "titulo": "PokerStars baja el tope de rake en NL2",
                             "resumen": "PokerStars informó que el tope de rake en NL2 baja a $0.30."})
        # El modelo intenta además escribir una tarjeta con la promo: nunca debe publicarse.
        if n_promo:
            tarjetas.append({"ids": [n_promo], "clasificacion": "confirmado", "titulo": "CoinPoker paga 50% de rakeback",
                             "resumen": "CoinPoker pagará 50% de rakeback este fin de semana."})
        if self.con_registro:
            tarjetas[0]["registro"] = self.ficha or {
                "tipo": "resultado", "salas": ["GGPoker", "WPT Global"],
                "jugadores": [["Fulano Inventado", "Argentina"], ["Mengano Falso", "Chile"]],
                "torneo": "Ficticia Open Main Event", "pais_evento": "Uruguay"}
            if n_rake:
                tarjetas[1]["registro"] = {"tipo": "industria_polemica", "salas": ["PokerStars"]}
        datos = {"temas": [], "tarjetas": tarjetas, "escena_latam": []}
        if self.con_registro:
            datos["cambios_salas"] = self.cambios if self.cambios is not None else [
                {"ids": [n_promo], "sala": "CoinPoker", "tema": "bonos_promociones",
                 "dato": "CoinPoker will pay 50% rakeback to all cash game players this weekend."},
                {"ids": [n_rake], "sala": "PokerStars", "tema": "rake_rakeback",
                 "dato": "the rake cap at NL2 drops to $0.30 starting November 1"},
            ]
        return json.dumps(datos), uso


def _hechos(raiz):
    return registro.leer_jsonl(raiz / "data" / "registro" / "hechos.jsonl")


def _cambios(raiz):
    return registro.leer_jsonl(raiz / "data" / "registro" / "cambios_salas.jsonl")


# ---------------------------------------------------------------- formato del registro

def test_una_linea_por_tarjeta_publicada_con_todos_los_campos(raiz_temporal):
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), Modelo())
    hechos = _hechos(raiz_temporal)
    publicadas = [t["titulo"] for t in c["tarjetas"]]
    assert [h["titulo"] for h in hechos] == publicadas
    texto = (raiz_temporal / "data" / "registro" / "hechos.jsonl").read_text(encoding="utf-8")
    assert len(texto.splitlines()) == len(publicadas)  # JSON Lines: una línea por tarjeta
    for h in hechos:
        assert list(h) == CAMPOS
        assert h["fecha"] == AHORA.date().isoformat() and h["id"].startswith(h["fecha"] + "-")
        assert h["tipo"] in registro.TIPOS and h["clasificacion"] in ("confirmado", "discusion", "rumor")
        assert all(set(f) == {"nombre", "url", "clase"} and f["url"] for f in h["fuentes"])
    assert hechos[0]["fuentes"] == [{"nombre": "Medio Inventado", "url": URL_RESULTADO, "clase": "medio"}]
    assert c["registro"] == {"hechos": 2, "cambios_salas": 2}


def test_el_registro_no_inventa_datos(raiz_temporal):
    corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), Modelo())
    h = _hechos(raiz_temporal)[0]
    assert h["tipo"] == "resultado"
    # GGPoker figura en la nota; WPT Global no.
    assert h["salas"] == ["GGPoker"]
    # Mengano Falso no está en la nota: fuera. El país de Fulano sí lo dice la fuente («Argentine pro»).
    assert h["jugadores"] == [{"nombre": "Fulano Inventado", "pais": "Argentina"}]
    assert h["torneo"] == "Ficticia Open Main Event" and h["pais_evento"] == "Uruguay"


def test_pais_torneo_y_tipo_que_no_estan_en_la_fuente_quedan_vacios(raiz_temporal):
    ficha = {"tipo": "noticia rara", "jugadores": [["Fulano Inventado", "Brasil"]],
             "torneo": "Serie Imaginaria", "pais_evento": "Chile"}
    corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), Modelo(ficha=ficha))
    h = _hechos(raiz_temporal)[0]
    assert h["tipo"] == "otro"
    assert h["jugadores"] == [{"nombre": "Fulano Inventado", "pais": ""}]
    assert h["torneo"] == "" and h["pais_evento"] == ""


def test_pais_del_jugador_solo_si_la_fuente_lo_dice_de_esa_persona():
    texto = ("Zutano Imaginario took second place. Many players travelled to the event. " * 2
             + "Later, Argentine player Perengano Supuesto won the side event.")
    ficha = registro.verificar_ficha({"jugadores": [["Zutano Imaginario", "Argentina"], ["Perengano Supuesto", "Argentina"]]},
                                     texto, "", {"salas": [], "temas": {}})
    assert ficha["jugadores"] == [{"nombre": "Zutano Imaginario", "pais": ""},
                                  {"nombre": "Perengano Supuesto", "pais": "Argentina"}]


def test_temas_calientes_y_tarjetas_de_tema_entran_al_registro(raiz_temporal):
    (raiz_temporal / "config" / "temas_calientes.json").write_text(json.dumps({"temas": [
        {"nombre": "Caso Ficticio", "palabras_clave": ["Ficticia Open"], "estado": "activo"}]}), encoding="utf-8")

    class ConTema(Modelo):
        def __call__(self, sistema, mensaje, modelo_id, max_tokens):
            texto, uso = super().__call__(sistema, mensaje, modelo_id, max_tokens)
            datos = json.loads(texto)
            if "solo elige" in sistema:
                return texto, uso
            n = self._n(mensaje, RESULTADO[0])
            datos["temas"] = [{"tema": "Caso Ficticio", "linea_principal": "",
                               "novedades": [{"id": n, "clasificacion": "confirmado",
                                              "aporta": "Fulano Inventado ganó el Ficticia Open Main Event."}],
                               "registro": {"tipo": "resultado", "jugadores": [["Fulano Inventado", ""]]}}]
            return json.dumps(datos), uso
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), ConTema())
    assert c["temas"]
    tema = next(h for h in _hechos(raiz_temporal) if h["seccion"] == "tema")
    assert tema["titulo"] == "Caso Ficticio" and tema["temas_calientes"] == ["Caso Ficticio"]
    assert tema["jugadores"] == [{"nombre": "Fulano Inventado", "pais": ""}]
    assert tema["fuentes"][0]["url"] == URL_RESULTADO


def test_agregar_no_duplica_y_ordena_por_fecha(tmp_path):
    ruta = tmp_path / "h.jsonl"
    assert registro.agregar(ruta, [{"fecha": "2026-10-02", "id": "b"}], registro.clave_hecho) == 1
    assert registro.agregar(ruta, [{"fecha": "2026-10-01", "id": "a"}, {"fecha": "2026-10-02", "id": "b"}],
                            registro.clave_hecho) == 1
    assert [f["id"] for f in registro.leer_jsonl(ruta)] == ["a", "b"]


# ---------------------------------------------------------------- detección de sala y tema

SALAS = json.loads((RAIZ / "config" / "salas_vigiladas.json").read_text(encoding="utf-8"))


def test_salas_vigiladas_de_la_configuracion():
    nombres = [s["nombre"] for s in SALAS["salas"]]
    assert nombres == ["CoinPoker", "RedStar Poker", "WPT Global", "GGPoker", "PokerStars", "BC Poker",
                       "Phenom Poker", "YaPoker", "ACR", "PokerKing", "Ignition"]
    assert list(SALAS["temas"]) == ["bonos_promociones", "rake_rakeback", "hud_software", "pagos_retiros",
                                    "app_plataformas", "licencia_paises", "series_torneos"]


def test_detecta_salas_por_nombre_y_alias_como_palabra_completa():
    texto = "Americas Cardroom and GG Poker change their HUD policy; Poker Stars too. Ignition Poker adds tables."
    assert registro.detectar_salas(texto, SALAS) == ["GGPoker", "PokerStars", "ACR", "Ignition"]
    assert registro.detectar_salas("The WPT Global Championship and the WPT Prime", SALAS) == ["WPT Global"]
    assert registro.detectar_salas("Accra Poker Open in Ghana", SALAS) == []  # «ACR» no es parte de «Accra»
    assert registro.sala_vigilada("americas cardroom", SALAS)["nombre"] == "ACR"
    assert registro.sala_vigilada("PartyPoker", SALAS) is None


def test_tema_por_clave_o_por_etiqueta():
    assert registro.tema_sala("rake_rakeback", SALAS) == "rake_rakeback"
    assert registro.tema_sala("Pagos y retiros", SALAS) == "pagos_retiros"
    assert registro.tema_sala("HUD y software permitido", SALAS) == "hud_software"
    assert registro.tema_sala("noticias", SALAS) is None


NOTA_HUD = {"fuente": "Medio Inventado", "url": "https://medio-inventado.com/n/hud", "clase": "medio",
            "titulo": "GGPoker bans third-party HUDs", "primera_linea": "GGPoker will ban all third-party HUDs from December 1."}


def test_cambio_valido_copiado_tal_cual():
    cambio, motivo = registro.verificar_cambio(
        {"ids": [1], "sala": "GG Poker", "tema": "hud_software", "dato": "GGPoker will ban all third-party HUDs"},
        [NOTA_HUD], SALAS, "2026-10-04")
    assert motivo is None
    assert cambio == {"fecha": "2026-10-04", "sala": "GGPoker", "tema": "hud_software",
                      "dato": "GGPoker will ban all third-party HUDs",
                      "fuente": {"nombre": "Medio Inventado", "url": NOTA_HUD["url"], "clase": "medio"}}


def test_cambios_que_no_entran():
    casos = [
        ({"ids": [1], "sala": "GGPoker", "tema": "hud_software", "dato": "GGPoker prohibirá los HUD de terceros"},
         "tal cual"),  # traducido: no es el dato tal como lo dice la fuente
        ({"ids": [1], "sala": "PartyPoker", "tema": "hud_software", "dato": "will ban all third-party HUDs"},
         "no vigilada"),
        ({"ids": [1], "sala": "GGPoker", "tema": "noticias", "dato": "will ban all third-party HUDs"}, "tema"),
        ({"ids": [1], "sala": "PokerStars", "tema": "hud_software", "dato": "will ban all third-party HUDs"},
         "tal cual"),  # la sala no está en la nota
        ({"ids": [9], "sala": "GGPoker", "tema": "hud_software", "dato": "will ban all third-party HUDs"}, "nota"),
    ]
    for crudo, motivo_esperado in casos:
        cambio, motivo = registro.verificar_cambio(crudo, [NOTA_HUD], SALAS, "2026-10-04")
        assert cambio is None and motivo_esperado in motivo, (crudo, motivo)


def test_gipsyteam_nunca_entra_como_fuente_de_un_cambio():
    nota = dict(NOTA_HUD, url="https://latam.gipsyteam.com/news/gg")
    cambio, _ = registro.verificar_cambio({"ids": [1], "sala": "GGPoker", "tema": "hud_software",
                                           "dato": "GGPoker will ban all third-party HUDs"}, [nota], SALAS, "2026-10-04")
    assert cambio is None


def test_promo_de_sala_vigilada_entra_al_registro_pero_no_a_las_tarjetas(raiz_temporal):
    llamar = Modelo()
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), llamar)
    # La selección conoce las salas vigiladas y la sección «salas».
    assert "CoinPoker" in llamar.llamadas[0]["sistema"] and '"salas"' in llamar.llamadas[0]["sistema"]
    assert "solo cambios_salas (sin tarjeta)" in llamar.llamadas[1]["mensaje"]
    titulos = [t["titulo"] for t in c["tarjetas"]]
    assert "CoinPoker paga 50% de rakeback" not in titulos
    assert c["seleccion"]["hechos"] == 2 and c["seleccion"]["salas"] == 1
    cambios = _cambios(raiz_temporal)
    assert [(x["sala"], x["tema"]) for x in cambios] == [("CoinPoker", "bonos_promociones"),
                                                          ("PokerStars", "rake_rakeback")]
    assert cambios[0]["fuente"]["url"] == URL_PROMO and cambios[1]["fuente"]["url"] == URL_RAKE


def test_cambio_descartado_queda_anotado(raiz_temporal):
    malo = [{"ids": [1], "sala": "CoinPoker", "tema": "bonos_promociones", "dato": "CoinPoker regala dinero a todos"}]
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), Modelo(cambios=malo))
    assert _cambios(raiz_temporal) == []
    assert any(x["motivo"].startswith("cambio en sala descartado") for x in c["controles"])


def test_seccion_cambios_en_salas_ultimos_30_dias(raiz_temporal):
    shutil.copy(RAIZ / "config" / "salas_vigiladas.json", raiz_temporal / "config" / "salas_vigiladas.json")
    fuente_c = {"nombre": "Medio Inventado", "url": "https://medio-inventado.com/n/x", "clase": "medio"}
    registro.registrar(raiz_temporal, [], [
        {"fecha": "2026-10-03", "sala": "ACR", "tema": "pagos_retiros", "dato": "Withdrawals now take 24 hours", "fuente": fuente_c},
        {"fecha": "2026-08-01", "sala": "ACR", "tema": "pagos_retiros", "dato": "Dato viejo de agosto", "fuente": fuente_c},
    ])
    pagina.generar(raiz_temporal, AHORA.date(), AHORA)
    sopa = BeautifulSoup((raiz_temporal / "docs" / "index.html").read_text(encoding="utf-8"), "html.parser")
    seccion = sopa.find("section", class_="sec-cambios")
    assert seccion.h2.get_text() == "Cambios en salas"
    texto = seccion.get_text(" ")
    assert "Withdrawals now take 24 hours" in texto and "Pagos y retiros" in texto and "ACR" in texto
    assert "Dato viejo de agosto" not in texto
    assert (raiz_temporal / "docs" / "secciones.css").exists()


# ---------------------------------------------------------------- barrido diario sin cambios de comportamiento

def _sin_variables(dia):
    for c in dia["corridas"]:
        for k in ("registro", "controles", "seleccion"):
            c.pop(k, None)
    return dia


def test_las_tarjetas_no_cambian_con_o_sin_ficha_del_registro(tmp_path, raiz_temporal):
    otra = tmp_path / "otra"
    shutil.copytree(raiz_temporal, otra)
    a = corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), Modelo(con_registro=True))
    b = corrida.ejecutar(otra, AHORA, "manual", _preparar(otra, con_salas=False), Modelo(con_registro=False))
    assert a["tarjetas"] == b["tarjetas"] and a["escena_latam"] == b["escena_latam"]
    assert all("registro" not in t for t in a["tarjetas"])  # la ficha no se guarda en la tarjeta
    dia_a = _sin_variables(leer_json(raiz_temporal / "data" / "dias" / f"{AHORA.date()}.json"))
    dia_b = _sin_variables(leer_json(otra / "data" / "dias" / f"{AHORA.date()}.json"))
    assert dia_a == dia_b
    # Sin ficha, igual hay una línea por tarjeta (con los campos vacíos que no se pueden verificar).
    assert [h["titulo"] for h in _hechos(otra)] == [t["titulo"] for t in b["tarjetas"]]


def test_un_error_del_registro_no_rompe_la_corrida(raiz_temporal, monkeypatch):
    def roto(*_a, **_k):
        raise OSError("disco lleno")
    monkeypatch.setattr(registro, "registrar", roto)
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", _preparar(raiz_temporal), Modelo())
    assert c["estado"] == "ok" and c["tarjetas"]
    assert "disco lleno" in c["registro"]["error"]


def test_reglas_del_barrido_intactas_en_los_mensajes():
    for regla in ("REGLA MÁXIMA", "Notas puramente promocionales", "sin voseo", "nunca la deduzca"):
        assert regla in modelo.SISTEMA
    assert "Promociones, satélites, bonos" in modelo.SELECCION
    sistema = modelo.SISTEMA.format(max_tarjetas=14, max_latam=6, **modelo.textos_salas(SALAS))
    assert "CoinPoker" in sistema and "rake_rakeback" in sistema
    # Sin salas configuradas, la selección no pide la sección «salas».
    assert modelo.textos_salas(None) == {"salas": "ninguna", "temas_salas": "ninguno"}


def test_flujo_diario_sin_cambios_y_brief_en_su_propio_flujo():
    diario = yaml.safe_load((RAIZ / ".github" / "workflows" / "barrido-diario.yml").read_text(encoding="utf-8"))
    assert diario[True]["schedule"] == [{"cron": "45 12 * * *"}, {"cron": "25 13 * * *"}, {"cron": "45 15 * * *"}]
    brief = yaml.safe_load((RAIZ / ".github" / "workflows" / "brief-semanal.yml").read_text(encoding="utf-8"))
    assert brief[True]["schedule"] == [{"cron": "30 12 * * 1"}] and "workflow_dispatch" in brief[True]
    assert brief["permissions"] == {"contents": "write", "issues": "write"}
    assert brief["concurrency"]["group"] == diario["concurrency"]["group"]
    pasos = " ".join(p.get("run", "") for p in brief["jobs"]["brief"]["steps"])
    assert "python -m mesa.semanal" in pasos and 'gh issue create --title "$titulo"' in pasos
    assert 'titulo="Brief semanal ${SEMANA}"' in pasos
