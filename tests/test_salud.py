from datetime import date, timedelta

from mesa import salud
from mesa.recoleccion import recolectar

from .conftest import AHORA, DescargaFalsa, fuente, leer_fixture

AJUSTES = {"ventana_horas": 96, "fallos_para_pausa": 3, "dias_pausa_reintento": 7}
D1 = date(2026, 10, 1)


def test_tres_dias_seguidos_de_fallo_pausan_la_fuente():
    f = fuente()
    salud.registrar_fallo(f, D1, "bloqueo del sitio (HTTP 403)")
    salud.registrar_fallo(f, D1 + timedelta(days=1), "bloqueo del sitio (HTTP 403)")
    assert f["estado"] == "activa" and f["fallos_consecutivos"] == 2
    salud.registrar_fallo(f, D1 + timedelta(days=2), "bloqueo del sitio (HTTP 403)")
    assert f["estado"] == "en_pausa"
    assert f["motivo_pausa"] == "bloqueo del sitio (HTTP 403)"
    assert f["pausada_desde"] == "2026-10-03"


def test_varias_corridas_el_mismo_dia_cuentan_un_solo_fallo():
    f = fuente()
    for _ in range(5):
        salud.registrar_fallo(f, D1, "error")
    assert f["fallos_consecutivos"] == 1 and f["estado"] == "activa"


def test_un_exito_reinicia_el_contador():
    f = fuente()
    salud.registrar_fallo(f, D1, "error")
    salud.registrar_fallo(f, D1 + timedelta(days=1), "error")
    salud.registrar_exito(f, D1 + timedelta(days=2), 10)
    assert f["fallos_consecutivos"] == 0 and f["ultimo_ok"] == "2026-10-03"


def test_pausada_se_reintenta_una_vez_por_semana_y_vuelve_si_responde():
    f = fuente(estado="en_pausa", ultimo_intento="2026-10-01", pausada_desde="2026-10-01",
               motivo_pausa="HTTP 403", fallos_consecutivos=3)
    assert not salud.debe_intentar(f, date(2026, 10, 7))
    assert salud.debe_intentar(f, date(2026, 10, 8))

    descarga = DescargaFalsa({f["url"]: (200, leer_fixture("feed.xml"))})
    # Antes de la semana: no se consulta.
    recolectar([f], date(2026, 10, 5), AHORA, set(), AJUSTES, descarga)
    assert descarga.pedidas == [] and f["estado"] == "en_pausa"
    # A la semana: se consulta y vuelve a activa.
    recolectar([f], date(2026, 10, 8), AHORA, set(), AJUSTES, descarga)
    assert descarga.pedidas == [f["url"]]
    assert f["estado"] == "activa" and f["fallos_consecutivos"] == 0
    assert "motivo_pausa" not in f


def test_pausada_que_sigue_fallando_espera_otra_semana():
    f = fuente(estado="en_pausa", ultimo_intento="2026-10-01", motivo_pausa="HTTP 403", fallos_consecutivos=3)
    descarga = DescargaFalsa({f["url"]: (503, "x" * 500)})
    recolectar([f], date(2026, 10, 8), AHORA, set(), AJUSTES, descarga)
    assert f["estado"] == "en_pausa"
    assert f["ultimo_intento"] == "2026-10-08"
    assert "503" in f["motivo_pausa"]
    assert not salud.debe_intentar(f, date(2026, 10, 9))


def test_recolectar_pausa_tras_tres_dias():
    f = fuente()
    descarga = DescargaFalsa({f["url"]: (403, "x" * 500)})
    for n in range(3):
        recolectar([f], D1 + timedelta(days=n), AHORA, set(), AJUSTES, descarga)
    assert f["estado"] == "en_pausa" and "403" in f["motivo_pausa"]
