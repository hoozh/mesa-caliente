import json

import pytest

from mesa import modelo

AJUSTES = {"max_tarjetas": 12, "max_tarjetas_latam": 6, "max_tokens_salida": 3000,
           "modelo_por_defecto": "claude-haiku-4-5",
           "precios_usd_por_millon": {"claude-haiku-4-5": {"entrada": 1.0, "salida": 5.0}}}

# Datos de ejemplo con nombres claramente inventados.
ITEMS = [
    {"fuente": "PokerNews", "url": "https://www.pokernews.com/a", "titulo": "Fulano Inventado wins the Example Poker Open Main Event",
     "fecha": "2026-10-03T10:00:00+00:00", "primera_linea": "", "region": "internacional", "clase": "medio"},
    {"fuente": "CardPlayer", "url": "https://www.cardplayer.com/a", "titulo": "Fulano Inventado takes down Example Poker Open",
     "fecha": "2026-10-04T10:00:00+00:00", "primera_linea": "", "region": "internacional", "clase": "medio"},
    {"fuente": "Código Poker", "url": "https://codigopoker.com/b", "titulo": "Mengano Ficticio gana el torneo de poker de prueba",
     "fecha": None, "primera_linea": "", "region": "latam", "clase": "medio"},
]
T_A = "Fulano Inventado gana el Main Event del Example Poker Open"
T_LATAM = "Mengano Ficticio gana el torneo de prueba"


def llamador(respuesta: dict | str, uso=(1000, 200)):
    def _llamar(sistema, mensaje, modelo_id, max_tokens):
        assert max_tokens == 3000
        assert "Nunca invente" in sistema
        texto = respuesta if isinstance(respuesta, str) else json.dumps(respuesta)
        return texto, {"tokens_entrada": uso[0], "tokens_salida": uso[1]}
    return _llamar


def test_agrupa_fuentes_y_toma_urls_y_fechas_del_programa():
    r = modelo.resumir(ITEMS, AJUSTES, llamador({
        "tarjetas": [{"ids": [1, 2], "clasificacion": "confirmado", "titulo": T_A, "resumen": "Se impuso en el torneo de poker.",
                      "url": "https://inventada.com"}],
        "escena_latam": [{"ids": [3], "clasificacion": "rumor", "titulo": T_LATAM, "resumen": "Ganó el torneo de poker."}],
    }))
    t = r["tarjetas"][0]
    assert [f["url"] for f in t["fuentes"]] == ["https://www.pokernews.com/a", "https://www.cardplayer.com/a"]
    assert t["fecha"] == "3-4 oct 2026"
    assert "inventada" not in json.dumps(r)
    assert r["escena_latam"][0]["fuentes"][0]["nombre"] == "Código Poker"
    assert r["escena_latam"][0]["fecha"].startswith("sin fecha")


def test_descarta_ids_inexistentes_y_clasificaciones_invalidas():
    r = modelo.resumir(ITEMS, AJUSTES, llamador({"tarjetas": [
        {"ids": [99], "clasificacion": "confirmado", "titulo": T_A, "resumen": "Texto de poker."},
        {"ids": [1], "clasificacion": "inventada", "titulo": T_A, "resumen": "Texto de poker."},
        {"ids": ["2"], "clasificacion": "En discusión", "titulo": T_A, "resumen": "Texto de poker."},
    ], "escena_latam": []}))
    assert [t["titulo"] for t in r["tarjetas"]] == [T_A]
    assert r["tarjetas"][0]["clasificacion"] == "discusion"


def test_respeta_tope_de_tarjetas():
    muchas = [{"ids": [1], "clasificacion": "confirmado", "titulo": f"Jugador{i} gana el Main Event número {i}",
               "resumen": "Resultado de poker."} for i in range(20)]
    items = [dict(ITEMS[0], url=f"https://x.com/{i}", titulo=f"Jugador{i} wins Main Event {i}") for i in range(20)]
    for i, m in enumerate(muchas):
        m["ids"] = [i + 1]
    r = modelo.resumir(items, AJUSTES, llamador({"tarjetas": muchas}))
    assert len(r["tarjetas"]) == 12


def test_json_entre_texto_y_bloque_de_codigo():
    texto = 'Aquí está:\n```json\n{"tarjetas": [{"ids": [1], "clasificacion": "rumor", "titulo": "Fulano Inventado gana el Example Poker Open", "resumen": "Poker."}]}\n```'
    r = modelo.resumir(ITEMS, AJUSTES, llamador(texto))
    assert len(r["tarjetas"]) == 1


def test_respuesta_invalida_lanza_error_con_uso():
    with pytest.raises(modelo.ErrorModelo) as e:
        modelo.resumir(ITEMS, AJUSTES, llamador("no es json"))
    assert e.value.uso["tokens_entrada"] == 1000


def test_costo_estimado_y_modelo_configurable(monkeypatch):
    assert modelo.costo_estimado("claude-haiku-4-5", 1_000_000, 100_000, AJUSTES) == 1.5
    monkeypatch.setenv("MESA_MODELO", "claude-otro")
    assert modelo.modelo_configurado(AJUSTES) == "claude-otro"
    monkeypatch.setenv("MESA_MODELO", "")
    assert modelo.modelo_configurado(AJUSTES) == "claude-haiku-4-5"


def test_mensaje_incluye_solo_datos_basicos():
    items = [dict(ITEMS[0], primera_linea="Primera línea.", extracto="Texto abierto.")]
    m = modelo.construir_mensaje(items)
    assert "[1] PokerNews (internacional, medio) | 2026-10-03 | Fulano Inventado wins the Example Poker Open Main Event | Primera línea." in m
    assert "Extracto: Texto abierto." in m
    assert "https://" not in m  # el modelo no recibe URLs, así no puede copiarlas mal


def test_sin_clave_no_llama(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(modelo.ErrorModelo, match="Falta la clave"):
        modelo.llamar_anthropic("s", "m", "claude-haiku-4-5", 10)
