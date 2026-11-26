"""«Crear tarjeta» desde «Consultar registros»: datos y fuentes del registro, una llamada, oculta, costo registrado."""

import hashlib
import json

from mesa import crear_tarjeta, registro
from mesa.util import leer_json

from .conftest import AHORA, DescargaFalsa

ARCHIVO = {"fecha": "2026-11-25", "corrida": "c1", "origen": "seleccion", "titulo": "Fulano Inventado gana el Ficticio Open",
           "fuentes": [{"nombre": "Medio Inventado", "url": "https://medio.com/n/1", "fecha": "2026-11-25",
                        "titular": "Fulano Inventado wins the Ficticio Open", "primera_linea": "Fulano Inventado won $50,000 from 370 entries."}]}
HTML = "<html><head><meta property='og:image' content='https://medio.com/f.jpg'></head><body><article><p>" + \
       "Fulano Inventado won $50,000 at the Ficticio Open in Lima after beating 370 entries. " * 3 + "</p></article></body></html>"


def _privado(tmp_path):
    p = tmp_path.parent / (tmp_path.name + "-priv")
    (p / "resultados").mkdir(parents=True, exist_ok=True)
    (p / "resultados" / "archivo.jsonl").write_text(json.dumps(ARCHIVO) + "\n", encoding="utf-8")
    return p


def _llamar(capturado, respuesta):
    def llamar(sistema, mensaje, modelo, max_tokens):
        capturado.append({"sistema": sistema, "mensaje": mensaje})
        return json.dumps(respuesta), {"tokens_entrada": 1000, "tokens_salida": 200}
    return llamar


def test_clave_del_archivo_igual_que_en_el_administrador():
    esperado = hashlib.sha1("2026-11-25|Fulano Inventado gana el Ficticio Open".encode()).hexdigest()[:12]
    assert crear_tarjeta.clave_archivo(ARCHIVO) == esperado


def test_large_desde_el_archivo_queda_oculta_con_fuentes_y_costo(raiz_temporal, tmp_path):
    llamadas = []
    resp = {"titulo": "Fulano Inventado gana el Ficticio Open", "resumen": "Fulano Inventado ganó $50,000 en Lima ante 370 entradas.",
            "cuerpo": "Fulano Inventado ganó el Ficticio Open en Lima.\n\nSuperó a un field de 370 entradas.\n\nAntes había ganado $900,000 en Madrid."}
    r = crear_tarjeta.crear(raiz_temporal, _privado(tmp_path), "archivo", crear_tarjeta.clave_archivo(ARCHIVO), "large",
                            _llamar(llamadas, resp), DescargaFalsa({"https://medio.com/n/1": (200, HTML)}), AHORA)
    assert len(llamadas) == 1 and "370 entries" in llamadas[0]["mensaje"]
    dia = leer_json(raiz_temporal / "data" / "dias" / f"{AHORA.date().isoformat()}.json")
    t = dia["corridas"][-1]["tarjetas"][0]
    assert t["fuentes"] == [{"nombre": "Medio Inventado", "fecha": "2026-11-25", "url": "https://medio.com/n/1"}]
    parrafos = t["cuerpo"].split("\n\n")
    assert len(parrafos) <= 2 and "900,000" not in t["cuerpo"] and "Lima" in t["cuerpo"]  # dato inventado: quitado
    assert t["imagen"] == "https://medio.com/f.jpg" and "tarjeta creada desde el registro" in t["revisar"]
    ed = leer_json(raiz_temporal / "data" / "ediciones.json")["tarjetas"][r["nid"]]
    assert ed["oculta"] is True
    costo = leer_json(raiz_temporal / "data" / "costos.json")["corridas"][-1]
    assert costo["tipo"] == "tarjeta_registro" and costo["tamano"] == "large" and costo["costo_usd"] > 0


def test_small_sin_cuerpo_y_titulo_inventado_vuelve_al_del_registro(raiz_temporal, tmp_path):
    resp = {"titulo": "Mengano Falso gana $900,000 en Madrid", "resumen": "Mengano Falso ganó $900,000.", "cuerpo": "Algo."}
    r = crear_tarjeta.crear(raiz_temporal, _privado(tmp_path), "archivo", crear_tarjeta.clave_archivo(ARCHIVO), "small",
                            _llamar([], resp), DescargaFalsa({}), AHORA)
    t = leer_json(raiz_temporal / "data" / "dias" / f"{AHORA.date().isoformat()}.json")["corridas"][-1]["tarjetas"][0]
    assert t["titulo"] == ARCHIVO["titulo"] and "cuerpo" not in t and r["avisos"]


def test_desde_un_hecho_usa_el_texto_publicado(raiz_temporal, tmp_path):
    from mesa.dias import agregar_corrida
    agregar_corrida(raiz_temporal, AHORA.date(), {"id": "c9", "origen": "manual", "estado": "ok", "notas": [],
        "tarjetas": [{"clasificacion": "confirmado", "titulo": "Stake se suma a WSOP Paradise", "resumen": "WSOP anunció a Stake como socio.",
                      "cuerpo": "Stake transmitirá el Super Main Event en KICK.", "fuentes": [{"nombre": "WSOP.com", "url": "https://wsop.com/x"}]}],
        "escena_latam": {"tarjetas": []}})
    nid = registro.id_hecho(AHORA.date().isoformat(), "c9", "Stake se suma a WSOP Paradise")
    (raiz_temporal / "data" / "registro").mkdir(parents=True, exist_ok=True)
    (raiz_temporal / "data" / "registro" / "hechos.jsonl").write_text(json.dumps({"id": nid, "fecha": AHORA.date().isoformat(),
        "titulo": "Stake se suma a WSOP Paradise", "tipo": "industria_polemica", "fuentes": [{"nombre": "WSOP.com", "url": "https://wsop.com/x"}]}) + "\n", encoding="utf-8")
    llamadas = []
    crear_tarjeta.crear(raiz_temporal, None, "hechos", nid, "medium",
                        _llamar(llamadas, {"titulo": "Stake se suma a WSOP Paradise", "resumen": "WSOP anunció a Stake como socio.",
                                           "cuerpo": "Stake transmitirá el Super Main Event en KICK."}), DescargaFalsa({}), AHORA)
    assert "KICK" in llamadas[0]["mensaje"] and "industria_polemica" in llamadas[0]["mensaje"]


def test_flujo_y_boton_del_administrador():
    from mesa.util import RAIZ
    flujo = (RAIZ / ".github" / "workflows" / "crear-tarjeta.yml").read_text(encoding="utf-8")
    assert "python -m mesa.crear_tarjeta" in flujo and "SEI_LECTURA_TOKEN" in flujo and "set +x" in flujo
    admin = (RAIZ / "docs" / "admin" / "index.html").read_text(encoding="utf-8")
    assert "crear-tarjeta.yml/dispatches" in admin and "data-crear" in admin
