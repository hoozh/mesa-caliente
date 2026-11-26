"""Videos para Instagram (stories y reels) a partir de una noticia de Mesa Caliente.

Flujo (GitHub Actions, «Generar video»):
1. Claude escribe o corrige el guion (escenas con texto, cifra, duración e imagen) a partir de la noticia y,
   si lo hay, del pedido de cambios escrito en el administrador. Solo usa datos de la noticia.
2. El programa controla las cifras contra el texto de la noticia.
3. docs/video/reproductor.html dibuja cada cuadro (30 por segundo) y ffmpeg arma el MP4.
4. Guion y MP4 se guardan en el repositorio privado (videos/videos.json y videos/<id>/vN.mp4).

Con --solo-render no se llama al modelo: se renderiza el último guion (editado a mano en el administrador).
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import modelo
from .util import RAIZ, escribir_json, leer_json

TIPOS_ESCENA = ("portada", "texto", "dato", "cierre")

SISTEMA_VIDEO = """Usted escribe guiones de videos cortos verticales (stories y reels de Instagram) para una marca de noticias de poker en español neutro.

Devuelva solo JSON con esta forma:
{{"formato": "9:16", "escenas": [{{"tipo": "portada", "duracion": 4, "seccion": "...", "titulo": "...", "texto": "...", "dato": "", "imagen": "", "fuente": "..."}}, ...]}}

Reglas:
- Entre 4 y 7 escenas; el video dura entre 15 y 30 segundos. "duracion" en segundos (entre 2 y 8). Más texto, más duración (unas 12 palabras cada 4 segundos).
- Tipos: "portada" (primera escena: sección y título con el hecho), "texto" (un dato o una idea por escena), "dato" (una cifra grande en "dato" y una línea que la explique en "texto"), "cierre" (última escena: "titulo" corto, por ejemplo "Más noticias de poker", y "texto" con una invitación a seguir la cuenta).
- "titulo" de hasta 70 caracteres; "texto" de hasta 110; "dato" de hasta 14 (por ejemplo "$110,938" o "2.310").
- Solo datos que estén en la noticia que recibe: nombres, cifras, fechas, lugares. Nunca invente ni complete. La jerga del poker y los nombres propios quedan en inglés.
- Números con el formato de GipsyTeam: dólares con "$" y coma de miles ("$110,938"); cantidades con punto de miles ("2.310 inscripciones").
- "fuente": el medio de la noticia en la portada y en las escenas con datos; vacío en el cierre.
- "imagen": déjela como está en el guion anterior; si no hay guion anterior, vacía. Las imágenes disponibles son: {imagenes}.
- Si recibe un guion anterior y un pedido de cambios, devuelva el guion completo con los cambios pedidos y el resto igual.
"""


def _ahora() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def cargar_noticia(raiz: Path, nid: str) -> dict:
    """La noticia con las ediciones manuales aplicadas (título, bajada y texto corregidos)."""
    tarjetas = (leer_json(Path(raiz) / "docs" / "datos" / "tarjetas.json", {}) or {}).get("tarjetas", [])
    t = next((x for x in tarjetas if x.get("id") == nid), None)
    if not t:
        raise SystemExit(f"No se encontró la noticia {nid} en docs/datos/tarjetas.json.")
    ed = ((leer_json(Path(raiz) / "data" / "ediciones.json", {}) or {}).get("tarjetas") or {}).get(nid) or {}
    return {**t, **{k: ed[k] for k in ("titulo", "resumen", "cuerpo") if ed.get(k)}}


def texto_noticia(n: dict) -> str:
    return "\n".join(x for x in (n.get("titulo"), n.get("resumen"), n.get("cuerpo")) if x)


def _numeros(texto: str) -> set[str]:
    return {re.sub(r"\D", "", m) for m in re.findall(r"\d[\d.,]*", texto or "") if len(re.sub(r"\D", "", m)) >= 2}


def validar_guion(guion: dict, noticia: dict) -> tuple[dict, list[str]]:
    """Normaliza el guion y quita las cifras que no están en la noticia (regla máxima: nada inventado)."""
    permitidos = _numeros(texto_noticia(noticia))
    avisos, escenas = [], []
    for e in (guion or {}).get("escenas") or []:
        if not isinstance(e, dict):
            continue
        e = {k: str(e.get(k) or "").strip() for k in ("tipo", "seccion", "titulo", "texto", "dato", "imagen", "fuente", "fondo")} | \
            {"duracion": e.get("duracion")}
        e["tipo"] = e["tipo"] if e["tipo"] in TIPOS_ESCENA else "texto"
        try:
            e["duracion"] = max(2.0, min(8.0, float(e["duracion"])))
        except (TypeError, ValueError):
            e["duracion"] = 4.0
        for campo in ("titulo", "texto", "dato"):
            sobrantes = _numeros(e[campo]) - permitidos
            if sobrantes and e["tipo"] != "cierre":
                avisos.append(f"Se quitó «{e[campo]}»: la cifra {', '.join(sorted(sobrantes))} no está en la noticia.")
                e[campo] = ""
        if e["tipo"] == "dato" and not e["dato"]:
            e["tipo"] = "texto"
        if e["titulo"] or e["texto"] or e["dato"]:
            escenas.append({k: v for k, v in e.items() if v not in ("", None)})
    if not escenas:
        raise ValueError("El guion quedó vacío.")
    return {"formato": guion.get("formato") if guion.get("formato") in ("9:16", "4:5", "1:1") else "9:16",
            "escenas": escenas[:8]}, avisos


def escribir_guion(noticia: dict, anterior: dict | None, pedido: str, imagenes: list[str], llamar, ajustes: dict) -> tuple[dict, dict]:
    sistema = SISTEMA_VIDEO.format(imagenes=", ".join(imagenes) or "ninguna")
    fuentes = ", ".join(f.get("nombre", "") for f in noticia.get("fuentes", []) if f.get("nombre"))
    mensaje = f"Noticia ({noticia.get('dia', '')}; fuentes: {fuentes}):\n{texto_noticia(noticia)}"
    if anterior:
        mensaje += "\n\nGuion anterior:\n" + json.dumps(anterior, ensure_ascii=False)
    if pedido:
        mensaje += "\n\nPedido de cambios de Hoozh:\n" + pedido
    nombre = modelo.modelo_configurado(ajustes)
    texto, uso = llamar(sistema, mensaje, nombre, 1500)
    uso["modelo"] = nombre
    uso["costo_usd"] = modelo.costo_estimado(nombre, uso.get("tokens_entrada", 0), uso.get("tokens_salida", 0), ajustes)
    m = re.search(r"\{.*\}", texto, re.S)
    if not m:
        raise ValueError("La respuesta del modelo no trae un guion en JSON.")
    return json.loads(m.group(0)), uso


def renderizar(guion: dict, marca: dict, imagenes: dict[str, Path], salida: Path, fps: int = 30) -> Path:
    """Dibuja cada cuadro con el reproductor (Chromium) y arma el MP4 con ffmpeg."""
    from playwright.sync_api import sync_playwright

    formatos = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
    ancho, alto = formatos.get(guion.get("formato"), (1080, 1920))
    reproductor = (RAIZ / "docs" / "video" / "reproductor.html").resolve().as_uri() + "?render=1"
    with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
        nav = p.chromium.launch()
        pagina = nav.new_page(viewport={"width": ancho, "height": alto})
        pagina.goto(reproductor)
        pagina.wait_for_function("window.mcListo === true")
        pagina.evaluate("document.fonts.ready")
        total = pagina.evaluate("d => window.mcCargar(d)", {"guion": guion, "marca": marca,
                                                          "imagenes": {k: v.resolve().as_uri() for k, v in imagenes.items()}})
        pagina.wait_for_timeout(300)
        cuadros = int(total * fps) + fps // 2
        for n in range(cuadros):
            pagina.evaluate("t => window.mcIr(t)", n / fps)
            pagina.screenshot(path=f"{tmp}/c{n:05d}.jpg", type="jpeg", quality=88)
        nav.close()
        salida.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", f"{tmp}/c%05d.jpg",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "21",
                        "-movflags", "+faststart", str(salida)], check=True)
    return salida


def ejecutar(raiz: Path, privado: Path, vid: str, pedido: str, solo_render: bool, llamar=None) -> dict:
    ruta = privado / "videos" / "videos.json"
    datos = leer_json(ruta, None) or {"nota": "Videos de Instagram (stories y reels). Se piden desde el administrador.", "videos": []}
    video = next((v for v in datos["videos"] if v.get("id") == vid), None)
    if not video:
        raise SystemExit(f"No existe el video {vid} en videos/videos.json.")
    ajustes = leer_json(Path(raiz) / "config" / "ajustes.json", {})
    marca = leer_json(privado / "instagram" / "marca.json", {}) or {}
    carpeta_img = privado / "instagram" / "imagenes"
    imagenes = {p.name: p for p in carpeta_img.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")} if carpeta_img.exists() else {}
    noticia = cargar_noticia(raiz, video["noticia"]) if video.get("noticia") else {"titulo": video.get("titulo", ""), "fuentes": []}
    versiones = video.setdefault("versiones", [])
    anterior = video.get("guion") or (versiones[-1]["guion"] if versiones else None)
    uso, avisos = {}, []
    if solo_render and anterior:
        guion = anterior
    else:
        crudo, uso = escribir_guion(noticia, anterior, pedido, sorted(imagenes), llamar or modelo.llamar_anthropic, ajustes)
        guion, avisos = validar_guion(crudo, noticia)
    n = len(versiones) + 1
    archivo = f"videos/{vid}/v{n}.mp4"
    renderizar(guion, marca, {k: v for k, v in imagenes.items() if any(e.get("imagen") == k for e in guion["escenas"])},
               privado / archivo)
    versiones.append({"n": n, "fecha": _ahora(), "pedido": pedido, "solo_render": solo_render, "guion": guion,
                      "archivo": archivo, "avisos": avisos, "costo_usd": uso.get("costo_usd", 0.0)})
    video.update(guion=guion, estado="listo", actualizado=_ahora())
    escribir_json(ruta, datos)
    return versiones[-1]


def main() -> None:
    ap = argparse.ArgumentParser(description="Genera o corrige un video de Instagram.")
    ap.add_argument("--id", required=True)
    ap.add_argument("--pedido", default="")
    ap.add_argument("--solo-render", action="store_true")
    ap.add_argument("--privado", default="privado")
    a = ap.parse_args()
    if not shutil.which("ffmpeg"):
        sys.exit("Falta ffmpeg.")
    v = ejecutar(RAIZ, Path(a.privado), a.id, a.pedido.strip(), a.solo_render)
    print(json.dumps({k: v[k] for k in ("n", "archivo", "avisos", "costo_usd")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
