from datetime import date, timedelta

from mesa import candidatas as cand

from .conftest import fuente

HOY = date(2026, 10, 4)
FUENTES = [fuente("PokerNews", "https://www.pokernews.com/rss.php")]


def _mencionar(estado, dominio_url, n_articulos, dia=HOY):
    for i in range(n_articulos):
        cand.registrar_menciones(estado, f"https://medio{i}.com/nota-{i}", [dominio_url], dia)


def test_tres_articulos_distintos_generan_candidata():
    estado = {"candidatas": [], "menciones": []}
    _mencionar(estado, "https://www.nuevo-medio.com/nota", 2)
    assert cand.actualizar(estado, FUENTES, HOY) == []
    _mencionar(estado, "https://nuevo-medio.com/otra", 3)
    nuevas = cand.actualizar(estado, FUENTES, HOY)
    assert [c["dominio"] for c in nuevas] == ["nuevo-medio.com"]
    assert nuevas[0]["ejemplo"].startswith("https://www.nuevo-medio.com/")


def test_el_mismo_articulo_no_cuenta_dos_veces():
    estado = {"candidatas": [], "menciones": []}
    for _ in range(5):
        cand.registrar_menciones(estado, "https://medio.com/unica", ["https://repetido.com/a"], HOY)
    assert cand.actualizar(estado, FUENTES, HOY) == []


def test_excluye_redes_fuentes_existentes_y_gipsyteam():
    estado = {"candidatas": [], "menciones": []}
    for url in ["https://twitter.com/x", "https://www.youtube.com/watch?v=1", "https://es.pokernews.com/x",
                "https://gipsyteam.com/news/1", "https://latam.gipsyteam.com/a", "https://gipsyteam.com.br/b"]:
        _mencionar(estado, url, 4)
    assert cand.actualizar(estado, FUENTES, HOY) == []
    assert estado["candidatas"] == []


def test_menciones_de_mas_de_14_dias_no_cuentan():
    estado = {"candidatas": [], "menciones": []}
    _mencionar(estado, "https://viejo.com/a", 2, HOY - timedelta(days=20))
    _mencionar(estado, "https://viejo.com/a", 1, HOY)
    assert cand.actualizar(estado, FUENTES, HOY) == []
    assert all(m["fecha"] == HOY.isoformat() for m in estado["menciones"])


def test_candidatas_no_se_borran_solas():
    estado = {"candidatas": [{"nombre": "Legal Sports Report", "dominio": "legalsportsreport.com",
                              "detectada": "2026-10-02", "ejemplo": "https://www.legalsportsreport.com/x"}],
              "menciones": []}
    cand.actualizar(estado, FUENTES, HOY + timedelta(days=60))
    assert [c["dominio"] for c in estado["candidatas"]] == ["legalsportsreport.com"]


def test_enlaces_de_afiliados_no_cuentan():
    estado = {"candidatas": [], "menciones": []}
    _mencionar(estado, "https://go.wpnaffiliates.com/visit/?bta=13597&brand=acr", 4)
    _mencionar(estado, "https://www.sala.com/registro?btag=123", 4)
    assert cand.actualizar(estado, FUENTES, HOY) == []
