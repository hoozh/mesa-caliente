"""Documentos vigentes de SEI en la redacción (pedido de Hoozh del 26 nov 2026).

Se descargan en cada corrida desde hoozh/SEO-Articles con SEI_LECTURA_TOKEN, van a la llamada de redacción con
caché de prompt y su contenido nunca aparece en los registros ni en el repositorio público. Además, controles de
estilo sin modelo que marcan tarjetas para revisión."""

import json

import yaml

from mesa import corrida, modelo, revision, sei
from mesa.util import RAIZ, leer_json

from .conftest import AHORA
from .test_corrida_y_pagina import llamador_ok

MARCA = "CONTENIDO-PRIVADO-DE-SEI-7f3a91"


def _docs(tmp_path, nombres=sei.DOCUMENTOS):
    carpeta = tmp_path.parent / (tmp_path.name + "-sei")  # fuera de la raíz del repositorio de prueba
    carpeta.mkdir(exist_ok=True)
    for n in nombres:
        (carpeta / n).write_text(f"# {n}\nRegla interna {MARCA}: usar comillas rectas.\n" + "Texto largo. " * 400, encoding="utf-8")
    return carpeta


# ---------------------------------------------------------------- descarga (flujo) y carga

def test_flujo_descarga_con_secreto_de_solo_lectura_y_sin_imprimir():
    flujo = yaml.safe_load((RAIZ / ".github" / "workflows" / "barrido-diario.yml").read_text(encoding="utf-8"))
    pasos = flujo["jobs"]["barrido"]["steps"]
    paso = next(p for p in pasos if p.get("name") == "Descargar los documentos de SEI")
    assert paso["if"] == "env.TIENE_SEI == 'true'"  # sin secreto el paso no corre y el barrido sigue
    assert "secrets.SEI_LECTURA_TOKEN" in paso["env"]["SEI_LECTURA_TOKEN"]
    script = paso["run"]
    assert "$RUNNER_TEMP/sei" in script and "hoozh/SEO-Articles/contents/docs/estandares/" in script
    for nombre in sei.DOCUMENTOS:
        assert nombre in script
    assert "set +x" in script and "curl -sf -o" in script
    for prohibido in ("cat ", "head ", "tail ", "tee ", "$GITHUB_WORKSPACE", "--verbose", " -v ", "cp ", "mv "):
        assert prohibido not in script
    assert "-o \"$carpeta/$f\"" in script  # se guarda solo en la carpeta temporal, nunca en docs/ del repositorio
    barrido = next(p for p in pasos if p.get("name") == "Correr el barrido")
    assert "runner.temp" in barrido["env"]["MESA_DIR_SEI"] and "MESA_MEDIR_SEI" in barrido["env"]


def test_carga_completa_incompleta_y_sin_secreto(tmp_path, monkeypatch):
    monkeypatch.delenv("MESA_DIR_SEI", raising=False)
    sin = sei.cargar()
    assert sin["estado"] == "sin_secreto" and sin["texto"] == "" and "SEI_LECTURA_TOKEN" in sei.aviso(sin)
    completos = sei.cargar(_docs(tmp_path))
    assert completos["estado"] == "completos" and sei.aviso(completos) == "" and MARCA in completos["texto"]
    carpeta = _docs(tmp_path)
    (carpeta / "guia-traduccion-poker.md").unlink()
    parcial = sei.cargar(carpeta)
    assert parcial["estado"] == "incompletos" and parcial["faltan"] == ["guia-traduccion-poker.md"]
    assert "guia-traduccion-poker.md" in sei.aviso(parcial)
    assert MARCA not in json.dumps(sei.resumen_estado(parcial))


# ---------------------------------------------------------------- llamada y privacidad

def _llamador_que_anota(llamadas):
    def _llamar(sistema, mensaje, modelo_id, max_tokens):
        llamadas.append(sistema)
        return llamador_ok(sistema, mensaje, modelo_id, max_tokens)
    return _llamar


def test_solo_la_redaccion_recibe_los_documentos_con_cache(raiz_temporal, descarga_ejemplo, tmp_path, monkeypatch):
    monkeypatch.setenv("MESA_DIR_SEI", str(_docs(tmp_path)))
    llamadas = []
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga_ejemplo, _llamador_que_anota(llamadas))
    assert c["estado"] == "ok"
    con_docs = [s for s in llamadas if isinstance(s, list)]
    assert len(con_docs) == 1  # una sola llamada (la redacción) lleva los documentos
    assert con_docs[0][0]["cache_control"] == {"type": "ephemeral"} and MARCA in con_docs[0][0]["text"]
    assert all(MARCA not in s for s in llamadas if isinstance(s, str))  # la selección no los recibe
    assert c["documentos_sei"]["estado"] == "completos" and not c["documentos_sei"]["aviso"]


def test_el_contenido_nunca_sale_a_registros_ni_al_repositorio(raiz_temporal, descarga_ejemplo, tmp_path, monkeypatch, capfd):
    monkeypatch.setenv("MESA_DIR_SEI", str(_docs(tmp_path)))
    corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga_ejemplo, _llamador_que_anota([]))
    salida = capfd.readouterr()
    assert MARCA not in salida.out and MARCA not in salida.err
    for archivo in raiz_temporal.rglob("*"):
        if archivo.is_file():
            assert MARCA not in archivo.read_text(encoding="utf-8", errors="ignore"), archivo


def test_sin_secreto_el_barrido_sigue_y_avisa(raiz_temporal, descarga_ejemplo, monkeypatch):
    monkeypatch.delenv("MESA_DIR_SEI", raising=False)
    llamadas = []
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga_ejemplo, _llamador_que_anota(llamadas))
    assert c["estado"] == "ok" and all(isinstance(s, str) for s in llamadas)
    costos = leer_json(raiz_temporal / "data" / "costos.json")["corridas"]
    red = next(x for x in costos if x["tipo"] == "redaccion")
    assert red["documentos_sei"] == "sin_secreto" and "SEI_LECTURA_TOKEN" in red["aviso"]
    pie = (raiz_temporal / "docs" / "historial.html").read_text(encoding="utf-8")
    assert "SEI_LECTURA_TOKEN" in pie


def test_medicion_redacta_sin_y_con_documentos(raiz_temporal, descarga_ejemplo, tmp_path, monkeypatch):
    monkeypatch.setenv("MESA_DIR_SEI", str(_docs(tmp_path)))
    monkeypatch.setenv("MESA_MEDIR_SEI", "1")
    llamadas = []
    c = corrida.ejecutar(raiz_temporal, AHORA, "manual", descarga_ejemplo, _llamador_que_anota(llamadas))
    m = c["medicion_sei"]
    assert m["sin_documentos"]["tokens_salida"] and m["con_documentos"]["tokens_salida"]
    tipos = [x["tipo"] for x in leer_json(raiz_temporal / "data" / "costos.json")["corridas"]]
    assert "medicion_sin_documentos" in tipos and "redaccion" in tipos
    assert MARCA not in json.dumps(c)


def test_costo_con_cache_de_prompt():
    ajustes = {"precios_usd_por_millon": {"claude-haiku-4-5": {"entrada": 1.0, "salida": 5.0}}}
    assert modelo.costo_estimado("claude-haiku-4-5", 1_000_000, 0, ajustes) == 1.0
    assert modelo.costo_estimado("claude-haiku-4-5", 0, 0, ajustes, cache_escritura=1_000_000) == 1.25
    assert modelo.costo_estimado("claude-haiku-4-5", 0, 0, ajustes, cache_lectura=1_000_000) == 0.1


# ---------------------------------------------------------------- controles de estilo

def test_controles_marcan_para_revision():
    t = [{"titulo": "Fulano gana", "resumen": "Dijo «gracias».", "cuerpo": "Ganó $50,000."},
         {"titulo": "WSOP anuncia un socio", "resumen": "Stake se suma.", "cuerpo": "La fuente no precisa el monto de la inversión."},
         {"titulo": "Según la información disponible, sigue la pelea", "resumen": "", "cuerpo": ""},
         {"titulo": "Negreanu habló", "resumen": "", "cuerpo": 'Dijo: "I think this is the best thing that happened to me."'},
         {"titulo": "Todo en orden", "resumen": "Según PokerNews, el torneo reunió 370 entradas.",
          "cuerpo": 'Dijo: "Es el mayor premio de mi carrera y estoy muy feliz".'}]
    controles = revision.marcar(t)
    assert "comillas" in t[0]["revisar"][0]
    assert "habla de la fuente" in t[1]["revisar"][0] and "habla de la fuente" in t[2]["revisar"][0]
    assert "cita en inglés" in t[3]["revisar"][0]
    assert "revisar" not in t[4]  # nombrar la fuente («según PokerNews») está bien
    assert len(controles) == 4
