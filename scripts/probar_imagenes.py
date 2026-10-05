"""Experimento: imágenes de vista previa para las tarjetas (sin modelo, sin guardar imágenes).

Para las tarjetas de los últimos 2 días de data/dias/ busca, por cada fuente de la tarjeta:
  1. la imagen del feed (media:content, media:thumbnail o enclosure), si la fuente es RSS;
  2. og:image o twitter:image de la página del artículo.
Luego verifica cada URL candidata (responde con content-type image/* y tamaño).

No usa el modelo (cero tokens) y no guarda imágenes: solo escribe las URLs y las
mediciones en docs/propuestas/imagenes-prueba.json.

Uso: python scripts/probar_imagenes.py
"""

from __future__ import annotations

import json
import sys
import time
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

import feedparser  # noqa: E402
import requests  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from mesa.dias import cargar_dias  # noqa: E402
from mesa.recoleccion import AGENTES, ESTADOS_REINTENTO  # noqa: E402
from mesa.util import esta_bloqueado, leer_json, normalizar_url  # noqa: E402

SALIDA = RAIZ / "docs" / "propuestas" / "imagenes-prueba.json"
MAX_BYTES_LECTURA = 8 * 1024 * 1024  # para medir el tamaño cuando no viene Content-Length


# ---------------------------------------------------------------- extracción (sin red: se prueba aparte)

def imagen_de_pagina(html: str, url_base: str) -> tuple[str | None, str | None]:
    """Devuelve (url, metodo) con og:image, og:image:secure_url o twitter:image."""
    sopa = BeautifulSoup(html, "html.parser")
    for atributo, nombre in (("property", "og:image"), ("property", "og:image:secure_url"),
                             ("name", "og:image"), ("name", "twitter:image"),
                             ("property", "twitter:image"), ("name", "twitter:image:src")):
        meta = sopa.find("meta", attrs={atributo: nombre})
        if meta and (meta.get("content") or "").strip():
            url = urljoin(url_base, meta["content"].strip())
            if url.startswith("http") and not esta_bloqueado(url):
                return url, nombre
    return None, None


def imagenes_de_feed(texto: str) -> dict[str, str]:
    """URL normalizada del artículo -> imagen indicada en el feed."""
    feed = feedparser.parse(texto)
    resultado = {}
    for e in feed.entries:
        enlace = e.get("link")
        if not enlace:
            continue
        candidatas = []
        for m in e.get("media_content", []) or []:
            if m.get("url") and (m.get("medium") in (None, "image") or str(m.get("type", "")).startswith("image")):
                candidatas.append(m["url"])
        for m in e.get("media_thumbnail", []) or []:
            if m.get("url"):
                candidatas.append(m["url"])
        for enc in e.get("enclosures", []) or []:
            if str(enc.get("type", "")).startswith("image") and enc.get("href"):
                candidatas.append(enc["href"])
        candidatas = [c for c in candidatas if c.startswith("http") and not esta_bloqueado(c)]
        if candidatas:
            resultado[normalizar_url(enlace)] = candidatas[0]
    return resultado


# ---------------------------------------------------------------- red

class Contador:
    def __init__(self):
        self.peticiones = defaultdict(int)

    def get(self, url: str, tipo: str, **kwargs) -> requests.Response:
        respuesta = None
        for n, agente in enumerate(AGENTES):
            self.peticiones[tipo] += 1
            respuesta = requests.get(url, timeout=20, headers={"User-Agent": agente}, **kwargs)
            if respuesta.status_code not in ESTADOS_REINTENTO or n == len(AGENTES) - 1:
                break
            respuesta.close()
        return respuesta


def verificar_imagen(url: str, contador: Contador) -> dict:
    """Pide la imagen sin guardarla: estado, content-type y tamaño en bytes."""
    try:
        r = contador.get(url, "imagen", stream=True)
    except requests.RequestException as e:
        return {"ok": False, "motivo": f"error de conexión ({type(e).__name__})"}
    tipo = r.headers.get("content-type", "").split(";")[0].strip().lower()
    tamano = r.headers.get("content-length")
    if tamano is None and r.status_code == 200 and tipo.startswith("image/"):
        leidos = 0
        for trozo in r.iter_content(64 * 1024):
            leidos += len(trozo)
            if leidos > MAX_BYTES_LECTURA:
                break
        tamano = leidos
    r.close()
    ok = r.status_code == 200 and tipo.startswith("image/")
    return {"ok": ok, "estado": r.status_code, "content_type": tipo or None,
            "bytes": int(tamano) if tamano is not None else None,
            "motivo": None if ok else f"HTTP {r.status_code}, tipo {tipo or 'desconocido'}"}


def main() -> int:
    inicio = time.monotonic()
    dias = cargar_dias(RAIZ)[:2]
    fuentes_conf = {f["nombre"]: f for f in leer_json(RAIZ / "config" / "fuentes.json")["fuentes"]}
    contador = Contador()

    # Feeds de las fuentes RSS que aparecen en las tarjetas (en la corrida real ya se descargan).
    feeds: dict[str, dict[str, str]] = {}
    tarjetas = []
    for d in dias:
        for c in d["corridas"]:
            for seccion, lista in (("principal", c.get("tarjetas", [])),
                                   ("latam", (c.get("escena_latam") or {}).get("tarjetas", []))):
                for t in lista:
                    tarjetas.append((d["fecha"], seccion, t))
    for _, _, t in tarjetas:
        for f in t["fuentes"]:
            conf = fuentes_conf.get(f["nombre"])
            if conf and conf.get("tipo") == "rss" and f["nombre"] not in feeds:
                try:
                    r = contador.get(conf["url"], "feed")
                    feeds[f["nombre"]] = imagenes_de_feed(r.text) if r.status_code == 200 else {}
                except requests.RequestException:
                    feeds[f["nombre"]] = {}

    resultados = []
    por_fuente = defaultdict(lambda: {"enlaces": 0, "con_candidata": 0, "imagen_valida": 0,
                                      "desde_feed": 0, "desde_pagina": 0})
    for fecha, seccion, t in tarjetas:
        enlaces = []
        for f in t["fuentes"]:
            url = f.get("url") or ""
            fila = {"fuente": f["nombre"], "url": url, "imagen": None, "metodo": None, "verificacion": None}
            est = por_fuente[f["nombre"]]
            est["enlaces"] += 1
            if not url or esta_bloqueado(url):
                fila["motivo"] = "sin URL o dominio prohibido"
                enlaces.append(fila)
                continue
            imagen = feeds.get(f["nombre"], {}).get(normalizar_url(url))
            metodo = "feed" if imagen else None
            if not imagen and "#t-" not in url:  # los titulares sin página propia no tienen imagen
                try:
                    r = contador.get(url, "pagina")
                    if r.status_code == 200:
                        imagen, metodo = imagen_de_pagina(r.text, r.url)
                    else:
                        fila["motivo"] = f"página HTTP {r.status_code}"
                except requests.RequestException as e:
                    fila["motivo"] = f"página: error de conexión ({type(e).__name__})"
            if imagen and not esta_bloqueado(imagen):
                est["con_candidata"] += 1
                fila["imagen"], fila["metodo"] = imagen, metodo
                fila["verificacion"] = verificar_imagen(imagen, contador)
                if fila["verificacion"]["ok"]:
                    est["imagen_valida"] += 1
                    est["desde_feed" if metodo == "feed" else "desde_pagina"] += 1
            enlaces.append(fila)
        valida = next((e for e in enlaces if e["verificacion"] and e["verificacion"]["ok"]), None)
        resultados.append({"fecha": fecha, "seccion": seccion, "titulo": t["titulo"],
                           "clasificacion": t["clasificacion"], "imagen": valida["imagen"] if valida else None,
                           "fuente_imagen": valida["fuente"] if valida else None, "enlaces": enlaces})

    duracion = time.monotonic() - inicio
    total = len(resultados)
    con_imagen = sum(1 for r in resultados if r["imagen"])
    con_candidata = sum(1 for r in resultados if any(e["imagen"] for e in r["enlaces"]))
    tamanos = [e["verificacion"]["bytes"] for r in resultados for e in r["enlaces"]
               if e["verificacion"] and e["verificacion"]["ok"] and e["verificacion"]["bytes"]]
    informe = {
        "dias": [d["fecha"] for d in dias],
        "tarjetas": total,
        "tarjetas_con_candidata": con_candidata,
        "tarjetas_con_imagen_valida": con_imagen,
        "porcentaje_tarjetas_con_imagen": round(100 * con_imagen / total, 1) if total else 0,
        "peticiones": dict(contador.peticiones),
        "segundos": round(duracion, 1),
        "tokens_modelo": 0,
        "tamano_bytes": {"minimo": min(tamanos, default=0), "maximo": max(tamanos, default=0),
                         "promedio": round(sum(tamanos) / len(tamanos)) if tamanos else 0},
        "por_fuente": {k: {**v, "porcentaje": round(100 * v["imagen_valida"] / v["enlaces"], 1) if v["enlaces"] else 0}
                       for k, v in sorted(por_fuente.items())},
        "resultados": resultados,
    }
    SALIDA.parent.mkdir(parents=True, exist_ok=True)
    SALIDA.write_text(json.dumps(informe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"Días: {', '.join(informe['dias'])}")
    print(f"Tarjetas: {total}; con imagen candidata: {con_candidata}; con imagen válida: {con_imagen} "
          f"({informe['porcentaje_tarjetas_con_imagen']} %)")
    print(f"Peticiones: {informe['peticiones']}; tiempo: {informe['segundos']} s; tokens de modelo: 0")
    print(f"Tamaño de imagen (bytes): {informe['tamano_bytes']}")
    for nombre, v in informe["por_fuente"].items():
        print(f"  {nombre:30} {v['imagen_valida']}/{v['enlaces']} ({v['porcentaje']} %) "
              f"feed={v['desde_feed']} página={v['desde_pagina']}")
    print(f"Informe: {SALIDA.relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
