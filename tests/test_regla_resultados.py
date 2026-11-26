"""Pedido del 26 nov 2026: regla de resultados (solo definiciones latinoamericanas y mesas finales de
latinoamericanos en series de marca mundial), archivo de resultados no publicados, etiqueta Europa,
ofertas de salas, personalidades, foto de la nota y respaldos atrasados. Nombres de personas inventados."""

import json

from mesa import corrida, modelo, recoleccion
from mesa.util import leer_json

from .conftest import AHORA, RAIZ, fuente

MEDIO = fuente("Medio Inventado", "https://medio-inventado.com/feed/", region="internacional", clase="medio")


def _items():
    textos = [
        ("Fulano Inventado wins the Ficticio Open Main Event", "Fulano Inventado won $50,000 at the Ficticio Open."),
        ("Mengana Falsa debate la nueva regla de tiempo en el EPT Ficticio", "Mengana Falsa criticó la regla de tiempo del EPT Ficticio."),
        ("Sala Ficticia ofrece un bono del 100% hasta $500", "Sala Ficticia ofrece un bono del 100% hasta $500 para nuevos jugadores."),
        ("Sala Ficticia regala satélites al Main Event Ficticio", "Sala Ficticia regala satélites al Main Event Ficticio."),
    ]
    return [recoleccion._item(MEDIO, f"https://medio-inventado.com/n/{n}", t, AHORA, p) for n, (t, p) in enumerate(textos, 1)]


def test_seleccion_conoce_la_regla_las_secciones_y_las_personalidades():
    capturado = {}

    def llamar(sistema, mensaje, modelo_id, max_tokens):
        capturado["sistema"] = sistema
        return json.dumps({"hechos": [{"ids": [1], "seccion": "archivo"}, {"ids": [2], "seccion": "europa"},
                                      {"ids": [3], "seccion": "general"}]}), {}
    sel = modelo.seleccionar_hechos(_items(), {}, llamar, personalidades=["Persona Famosa Inventada"])
    assert [(h["ids"], h["seccion"]) for h in sel["hechos"]] == [([1], "archivo"), ([2], "europa"), ([3], "general")]
    s = capturado["sistema"]
    assert "Regla de resultados" in s and "Triton" in s and '"archivo"' in s and '"europa"' in s
    assert "Persona Famosa Inventada" in s and "ofertas de salas de poker" in s


def test_ofertas_se_publican_y_los_satelites_no():
    items = _items()

    def llamar(*_):
        return json.dumps({"tarjetas": [
            {"ids": [3], "clasificacion": "confirmado", "titulo": "Sala Ficticia ofrece un bono del 100% hasta $500",
             "resumen": "Sala Ficticia ofrece un bono del 100% hasta $500 para nuevos jugadores.", "registro": {"tipo": "oferta"}},
            {"ids": [4], "clasificacion": "confirmado", "titulo": "Sala Ficticia regala satélites al Main Event Ficticio",
             "resumen": "Sala Ficticia regala satélites al Main Event Ficticio.", "registro": {"tipo": "oferta"}}]}), {}
    r = modelo.resumir(items, {}, llamar, AHORA.date())
    assert [t["titulo"] for t in r["tarjetas"]] == ["Sala Ficticia ofrece un bono del 100% hasta $500"]
    assert any(x["motivo"] == "nota promocional" for x in r["controles"])


def test_resultado_fuera_de_la_escena_latina_va_al_archivo_y_europa_se_etiqueta():
    items = _items()

    def llamar(*_):
        return json.dumps({"tarjetas": [
            {"ids": [1], "clasificacion": "confirmado", "titulo": "Fulano Inventado gana el Ficticio Open Main Event",
             "resumen": "Fulano Inventado ganó el Ficticio Open Main Event y cobró $50,000.", "registro": {"tipo": "resultado"}},
            {"ids": [2], "clasificacion": "discusion", "titulo": "Mengana Falsa debate la nueva regla de tiempo en el EPT Ficticio",
             "resumen": "Mengana Falsa criticó la regla de tiempo del EPT Ficticio.", "registro": {"tipo": "industria_polemica"}}]}), {}
    grupos = [{"ids": [1], "seccion": "general"}, {"ids": [2], "seccion": "europa"}]
    r = modelo.resumir(items, {}, llamar, AHORA.date(), grupos=grupos)
    assert [t["titulo"] for t in r["tarjetas"]] == ["Mengana Falsa debate la nueva regla de tiempo en el EPT Ficticio"]
    assert r["tarjetas"][0]["region"] == "europa"
    assert [a["titulo"] for a in r["archivo"]] == ["Fulano Inventado gana el Ficticio Open Main Event"]
    assert [f["seccion"] for f in r["fichas"]] == ["europa"]
    assert modelo.categoria_descarte("resultado fuera de la regla de resultados (al archivo)") == "archivo"


def test_archivo_de_resultados_guarda_titulos_y_fuentes(raiz_temporal):
    notas = _items()[:1]
    assert corrida.archivar_resultados(raiz_temporal, AHORA.date(), "c1", [{"notas": notas, "motivo": "seleccion"}]) == 1
    linea = json.loads((raiz_temporal / "data" / "registro" / "resultados_archivo.jsonl").read_text(encoding="utf-8"))
    assert linea["titulo"] == notas[0]["titulo"] and linea["origen"] == "seleccion"
    assert linea["fuentes"][0]["url"] == notas[0]["url"] and linea["fuentes"][0]["nombre"] == "Medio Inventado"


def test_foto_de_la_nota():
    html = ('<html><head><meta property="og:image" content="//cdn.medio-inventado.com/foto.jpg"></head>'
            '<body><article><p>' + "Texto de la nota con suficiente largo para el extracto. " * 3 + '</p></article></body></html>')
    extracto, enlaces, imagen = recoleccion.extraer_articulo(html, "https://medio-inventado.com/n/1", 500, con_imagen=True)
    assert imagen == "https://cdn.medio-inventado.com/foto.jpg" and extracto
    assert len(recoleccion.extraer_articulo(html, "https://medio-inventado.com/n/1", 500)) == 2  # uso anterior intacto


def test_personalidades_editables():
    datos = leer_json(RAIZ / "config" / "personalidades.json")
    assert datos["personalidades"] and all(p.get("nombre") for p in datos["personalidades"])


def test_respaldo_atrasado_no_cuenta_como_barrido_del_dia_siguiente():
    flujo = (RAIZ / ".github" / "workflows" / "barrido-diario.yml").read_text(encoding="utf-8")
    assert '"$(date -u +%H)" -lt 12' in flujo and "exit 0" in flujo
