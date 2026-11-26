"""Videos para Instagram (stories y reels) a partir de una noticia de Mesa Caliente.

Flujo (GitHub Actions, «Generar video»):
1. Claude escribe o corrige el guion (escenas con texto, cifra, duración e imagen) a partir de la noticia y,
   si lo hay, del pedido de cambios escrito en el administrador. Solo usa datos de la noticia.
2. El programa controla las cifras contra el texto de la noticia.
3. docs/video/reproductor.html dibuja cada cuadro (30 por segundo) y ffmpeg arma el MP4.
4. Guion y MP4 se guardan en el repositorio privado (videos/videos.json y videos/<id>/vN.mp4).

Con --solo-render no se llama al modelo: se renderiza el último guion (editado a mano en el administrador).

Marcas (instagram/marcas.json), formatos de video (instagram/formatos.json), voz (Piper, voces libres en
español) y música (archivos propios en instagram/audio/) se eligen por video desde el administrador.
Las escenas pueden tener un video propio de fondo (instagram/videos/): el reproductor deja esa escena
transparente y ffmpeg pone el video debajo.
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
VOCES = {"es_MX-claude-high": "Voz masculina (México)", "es_MX-ald-medium": "Voz masculina 2 (México)",
         "es_AR-daniela-high": "Voz femenina (Argentina)"}
VOZ_POR_DEFECTO = "es_MX-claude-high"

FORMATOS_BASE = [
    {"id": "noticia-rapida", "nombre": "Noticia rápida", "duracion": "15 a 20 segundos",
     "instrucciones": "Portada con el hecho, dos escenas de texto con lo esencial (quién, qué, dónde, cuánto) y cierre."},
    {"id": "dato-destacado", "nombre": "Dato destacado", "duracion": "12 a 18 segundos",
     "instrucciones": "Portada, una escena «dato» con la cifra principal y una línea que la explique, una escena de contexto y cierre."},
    {"id": "resultado", "nombre": "Resultado con premio", "duracion": "18 a 25 segundos",
     "instrucciones": "Portada con el ganador y el torneo; escena «dato» con el premio; escena con field y pozo; escena con otros jugadores destacados si los hay; cierre."},
    {"id": "polemica", "nombre": "Polémica: qué pasó y qué falta", "duracion": "20 a 30 segundos",
     "instrucciones": "Portada con el conflicto; qué pasó; quién lo dijo o lo informó; qué implica para los jugadores; qué queda por confirmar; cierre."},
]


def cargar_marcas(privado: Path) -> dict:
    """instagram/marcas.json; si solo existe la marca única anterior (marca.json), se usa esa."""
    datos = leer_json(privado / "instagram" / "marcas.json", None)
    if datos and datos.get("marcas"):
        return datos
    vieja = leer_json(privado / "instagram" / "marca.json", None) or {}
    marca = {"id": "prueba", **{k: v for k, v in vieja.items() if k not in ("nota", "cuentas")}} if vieja else \
        {"id": "prueba", "nombre": "Marca de prueba", "usuario": "", "fondo": "#16171a", "acento": "#d9262c", "texto": "#f1f2f4"}
    return {"activa": marca["id"], "marcas": [marca]}


def cargar_formatos(privado: Path) -> list[dict]:
    return (leer_json(privado / "instagram" / "formatos.json", None) or {}).get("formatos") or FORMATOS_BASE

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
- "imagen" (foto de fondo) y "video" (video de fondo): déjelos como están en el guion anterior, o use uno de los disponibles si el pedido lo indica. Fotos disponibles: {imagenes}. Videos disponibles: {videos}.
- {voz}
- Formato elegido: {formato}
- Si recibe un guion anterior y un pedido de cambios, devuelva el guion completo con los cambios pedidos y el resto igual.
"""

REGLA_VOZ = ('"voz": lo que dice la voz en off en esa escena, en español neutro, frases cortas y naturales, solo con datos '
             'de la noticia. Las cifras, escritas como se dicen ("ciento diez mil novecientos treinta y ocho dólares"; '
             'si es muy larga, "más de ciento diez mil dólares"). Nombres propios y jerga del poker en inglés. '
             'Cerca de 2,5 palabras por segundo de la escena; en el cierre, una invitación breve a seguir la cuenta.')


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
        e = {k: str(e.get(k) or "").strip() for k in ("tipo", "seccion", "titulo", "texto", "dato", "imagen", "video", "voz",
                                                       "fuente", "fondo")} | {"duracion": e.get("duracion")}
        e["tipo"] = e["tipo"] if e["tipo"] in TIPOS_ESCENA else "texto"
        try:
            e["duracion"] = max(2.0, min(12.0, float(e["duracion"])))
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


def escribir_guion(noticia: dict, anterior: dict | None, pedido: str, imagenes: list[str], llamar, ajustes: dict,
                   videos: list[str] | None = None, formato: dict | None = None, con_voz: bool = False) -> tuple[dict, dict]:
    f = formato or FORMATOS_BASE[0]
    sistema = SISTEMA_VIDEO.format(imagenes=", ".join(imagenes) or "ninguna", videos=", ".join(videos or []) or "ninguno",
                                   voz=REGLA_VOZ if con_voz else '"voz": vacío (este video no lleva voz en off).',
                                   formato=f"{f.get('nombre', '')} ({f.get('duracion', '')}): {f.get('instrucciones', '')}")
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


def voz_en_off(guion: dict, voz: str, carpeta: Path, voces_dir: Path) -> list[Path | None]:
    """Un WAV por escena con Piper (voces libres). Alarga la escena si la voz dura más que ella."""
    import wave
    salida = []
    modelo_voz = voces_dir / f"{voz}.onnx"
    if not modelo_voz.exists():
        subprocess.run([sys.executable, "-m", "piper.download_voices", "--download-dir", str(voces_dir), voz], check=True)
    for n, e in enumerate(guion["escenas"]):
        texto = (e.get("voz") or "").strip()
        if not texto:
            salida.append(None)
            continue
        wav = carpeta / f"voz{n:02d}.wav"
        subprocess.run([sys.executable, "-m", "piper", "-m", str(modelo_voz), "-f", str(wav), "--sentence-silence", "0.25"],
                       input=texto.encode("utf-8"), check=True)
        with wave.open(str(wav)) as w:
            dur = w.getnframes() / float(w.getframerate())
        e["duracion"] = max(float(e.get("duracion") or 0), round(dur + 0.6, 2))
        salida.append(wav)
    return salida


def renderizar(guion: dict, marca: dict, imagenes: dict[str, Path], salida: Path, fps: int = 30,
               videos: dict[str, Path] | None = None, voz: str = "", musica: Path | None = None,
               volumen_musica: float = 0.5, voces_dir: Path | None = None) -> Path:
    """Dibuja cada cuadro con el reproductor (Chromium) y arma el MP4 con ffmpeg: videos de fondo debajo,
    cuadros encima, voz en off y música."""
    from playwright.sync_api import sync_playwright

    videos = videos or {}
    formatos = {"9:16": (1080, 1920), "4:5": (1080, 1350), "1:1": (1080, 1080)}
    ancho, alto = formatos.get(guion.get("formato"), (1080, 1920))
    reproductor = (RAIZ / "docs" / "video" / "reproductor.html").resolve().as_uri() + "?render=1"
    con_fondo_video = any(e.get("video") in videos for e in guion["escenas"])
    ext = "png" if con_fondo_video else "jpg"
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        voces = voz_en_off(guion, voz, tmp, voces_dir or (RAIZ / ".voces")) if voz else [None] * len(guion["escenas"])
        marca_html = dict(marca)
        if marca.get("logo") and (Path(marca["logo"]).exists()):
            marca_html["logo_url"] = Path(marca["logo"]).resolve().as_uri()
        with sync_playwright() as p:
            nav = p.chromium.launch()
            pagina = nav.new_page(viewport={"width": ancho, "height": alto})
            pagina.goto(reproductor)
            pagina.wait_for_function("window.mcListo === true")
            total = pagina.evaluate("d => window.mcCargar(d)", {"guion": guion, "marca": marca_html,
                                                              "imagenes": {k: v.resolve().as_uri() for k, v in imagenes.items()}})
            pagina.evaluate("document.fonts.ready.then(() => true)")
            pagina.wait_for_timeout(400)
            duraciones = pagina.evaluate("window.mcDuraciones()")
            cuadros = int(total * fps) + fps // 2
            for n in range(cuadros):
                pagina.evaluate("t => window.mcIr(t)", n / fps)
                if ext == "png":
                    pagina.screenshot(path=str(tmp / f"c{n:05d}.png"), omit_background=True)
                else:
                    pagina.screenshot(path=str(tmp / f"c{n:05d}.jpg"), type="jpeg", quality=88)
            nav.close()
        total = cuadros / fps
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-framerate", str(fps), "-i", str(tmp / f"c%05d.{ext}")]
        filtros, k = [], 1
        if con_fondo_video:  # fondo: video propio en sus escenas, negro en las demás
            partes = []
            for n, (e, d) in enumerate(zip(guion["escenas"], duraciones)):
                if e.get("video") in videos:
                    cmd += ["-stream_loop", "-1", "-i", str(videos[e["video"]])]
                    filtros.append(f"[{k}:v]scale={ancho}:{alto}:force_original_aspect_ratio=increase,crop={ancho}:{alto},"
                                   f"fps={fps},trim=duration={d},setpts=PTS-STARTPTS,setsar=1,format=yuv420p[f{n}]")
                    k += 1
                else:
                    filtros.append(f"color=c=black:s={ancho}x{alto}:r={fps}:d={d},setsar=1,format=yuv420p[f{n}]")
                partes.append(f"[f{n}]")
            filtros.append(f"{''.join(partes)}concat=n={len(partes)}:v=1:a=0,tpad=stop_mode=clone:stop_duration=1[fondo]")
            filtros.append("[fondo][0:v]overlay=format=auto,format=yuv420p[v]")
        else:
            filtros.append("[0:v]format=yuv420p[v]")
        audios, inicio = [], 0.0
        for n, (w, d) in enumerate(zip(voces, duraciones)):
            if w:
                cmd += ["-i", str(w)]
                filtros.append(f"[{k}:a]adelay={int((inicio + 0.3) * 1000)}:all=1[a{n}]")
                audios.append(f"[a{n}]")
                k += 1
            inicio += d
        if musica:
            cmd += ["-stream_loop", "-1", "-i", str(musica)]
            vol = volumen_musica * (0.35 if audios else 1.0)
            filtros.append(f"[{k}:a]atrim=duration={total},volume={vol:.2f},afade=t=out:st={max(0, total - 1.5)}:d=1.5[musica]")
            audios.append("[musica]")
            k += 1
        mapa = ["-map", "[v]"]
        if audios:
            filtros.append(f"{''.join(audios)}amix=inputs={len(audios)}:normalize=0:duration=longest,atrim=duration={total}[a]")
            mapa += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
        salida.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(cmd + ["-filter_complex", ";".join(filtros)] + mapa +
                       ["-t", f"{total:.3f}", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "21",
                        "-movflags", "+faststart", str(salida)], check=True)
    return salida


def _archivos(carpeta: Path, extensiones: tuple[str, ...]) -> dict[str, Path]:
    return {p.name: p for p in carpeta.glob("*") if p.suffix.lower() in extensiones} if carpeta.exists() else {}


def ejecutar(raiz: Path, privado: Path, vid: str, pedido: str, solo_render: bool, llamar=None) -> dict:
    ruta = privado / "videos" / "videos.json"
    datos = leer_json(ruta, None) or {"nota": "Videos de Instagram (stories y reels). Se piden desde el administrador.", "videos": []}
    video = next((v for v in datos["videos"] if v.get("id") == vid), None)
    if not video:
        raise SystemExit(f"No existe el video {vid} en videos/videos.json.")
    ajustes = leer_json(Path(raiz) / "config" / "ajustes.json", {})
    marcas = cargar_marcas(privado)
    marca = next((m for m in marcas["marcas"] if m.get("id") == (video.get("marca") or marcas.get("activa"))), marcas["marcas"][0])
    marca = dict(marca)
    if marca.get("logo"):
        marca["logo"] = str(privado / "instagram" / "imagenes" / marca["logo"])
    formato = next((f for f in cargar_formatos(privado) if f.get("id") == video.get("formato")), None)
    imagenes = _archivos(privado / "instagram" / "imagenes", (".jpg", ".jpeg", ".png", ".webp"))
    videos_fondo = _archivos(privado / "instagram" / "videos", (".mp4", ".mov", ".webm", ".m4v"))
    audios = _archivos(privado / "instagram" / "audio", (".mp3", ".wav", ".m4a", ".ogg", ".aac"))
    con_voz = bool(video.get("voz"))
    noticia = cargar_noticia(raiz, video["noticia"]) if video.get("noticia") else {"titulo": video.get("titulo", ""), "fuentes": []}
    versiones = video.setdefault("versiones", [])
    anterior = video.get("guion") or (versiones[-1]["guion"] if versiones else None)
    uso, avisos = {}, []
    if solo_render and anterior:
        guion = anterior
    else:
        crudo, uso = escribir_guion(noticia, anterior, pedido, sorted(imagenes), llamar or modelo.llamar_anthropic, ajustes,
                                    sorted(videos_fondo), formato, con_voz)
        guion, avisos = validar_guion(crudo, noticia)
    n = len(versiones) + 1
    archivo = f"videos/{vid}/v{n}.mp4"
    musica = audios.get(video.get("musica") or "")
    if video.get("musica") and not musica:
        avisos.append(f"No se encontró la música «{video['musica']}».")
    renderizar(guion, marca, {k: v for k, v in imagenes.items() if any(e.get("imagen") == k for e in guion["escenas"])},
               privado / archivo, videos={k: v for k, v in videos_fondo.items() if any(e.get("video") == k for e in guion["escenas"])},
               voz=(video.get("voz_nombre") or marca.get("voz") or VOZ_POR_DEFECTO) if con_voz else "",
               musica=musica, volumen_musica=float(video.get("volumen_musica") or 0.5))
    versiones.append({"n": n, "fecha": _ahora(), "pedido": pedido, "solo_render": solo_render, "guion": guion,
                      "archivo": archivo, "avisos": avisos, "costo_usd": uso.get("costo_usd", 0.0),
                      "marca": marca.get("id"), "formato": (formato or {}).get("id"), "voz": con_voz, "musica": video.get("musica") or ""})
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
