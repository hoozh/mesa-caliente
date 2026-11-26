"""Videos de Instagram: guion de Claude controlado contra la noticia, versiones y render (simulado)."""

import json

from mesa import video
from mesa.util import leer_json

NOTICIA = {"id": "2026-11-25-x", "dia": "2026-11-25", "titulo": "Fulano Inventado gana el Ficticio Open",
           "resumen": "Fulano Inventado ganó $50,000 ante 370 entradas.", "fuentes": [{"nombre": "Medio Inventado"}]}


def test_guion_sin_cifras_inventadas():
    guion, avisos = video.validar_guion({"formato": "9:16", "escenas": [
        {"tipo": "portada", "duracion": 4, "titulo": "Fulano Inventado gana el Ficticio Open"},
        {"tipo": "dato", "duracion": 3, "dato": "$50,000", "texto": "Ante 370 entradas."},
        {"tipo": "dato", "duracion": 3, "dato": "$900,000", "texto": "Inventado."},
        {"tipo": "cierre", "duracion": 20, "titulo": "Más noticias de poker"}]}, NOTICIA)
    assert [e["tipo"] for e in guion["escenas"]] == ["portada", "dato", "texto", "cierre"]
    assert "dato" not in guion["escenas"][2] and avisos and guion["escenas"][3]["duracion"] == 8.0


def test_version_nueva_con_pedido_de_cambios(tmp_path, monkeypatch):
    raiz, privado = tmp_path / "pub", tmp_path / "priv"
    (raiz / "docs" / "datos").mkdir(parents=True)
    (raiz / "config").mkdir()
    (raiz / "config" / "ajustes.json").write_text("{}", encoding="utf-8")
    (raiz / "docs" / "datos" / "tarjetas.json").write_text(json.dumps({"tarjetas": [NOTICIA]}), encoding="utf-8")
    (privado / "videos").mkdir(parents=True)
    (privado / "videos" / "videos.json").write_text(json.dumps({"videos": [{"id": "v1", "noticia": NOTICIA["id"], "titulo": "x", "versiones": []}]}), encoding="utf-8")
    vistos = {}

    def llamar(sistema, mensaje, modelo, max_tokens):
        vistos["mensaje"] = mensaje
        return json.dumps({"escenas": [{"tipo": "portada", "duracion": 4, "titulo": "Fulano Inventado gana"}]}), {"tokens_entrada": 10, "tokens_salida": 10}
    monkeypatch.setattr(video, "renderizar", lambda g, m, i, salida: salida)
    v = video.ejecutar(raiz, privado, "v1", "", False, llamar)
    assert v["n"] == 1 and v["archivo"] == "videos/v1/v1.mp4"
    v = video.ejecutar(raiz, privado, "v1", "menos texto", False, llamar)
    assert v["n"] == 2 and "Guion anterior" in vistos["mensaje"] and "menos texto" in vistos["mensaje"]
    datos = leer_json(privado / "videos" / "videos.json")
    assert len(datos["videos"][0]["versiones"]) == 2 and datos["videos"][0]["estado"] == "listo"
    v = video.ejecutar(raiz, privado, "v1", "", True, None)  # sin modelo: renderiza el guion guardado
    assert v["n"] == 3 and v["costo_usd"] == 0.0
