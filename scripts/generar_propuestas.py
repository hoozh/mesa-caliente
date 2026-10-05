"""Genera las propuestas de diseño en docs/propuestas/ con los datos reales de todo el historial.

No toca docs/index.html ni la recolección: solo lee data/ y escribe en docs/propuestas/.
Las imágenes salen de docs/propuestas/imagenes-prueba.json (creado por probar_imagenes.py);
las tarjetas sin imagen muestran un degradado según su clasificación.

Uso: python scripts/generar_propuestas.py
"""

from __future__ import annotations

import json
import shutil
import sys
import unicodedata
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: E402

from mesa.dias import cargar_dias  # noqa: E402
from mesa.pagina import ETIQUETAS, fecha_fuente  # noqa: E402
from mesa.util import esta_bloqueado, fecha_larga, leer_json  # noqa: E402

PLANTILLAS = RAIZ / "templates" / "propuestas"
SALIDA = RAIZ / "docs" / "propuestas"
PROPUESTAS = {"propuesta-a": "Editorial", "propuesta-b": "Compacta"}
ESTATICOS = ["propuesta-a.css", "propuesta-b.css", "filtros.js"]


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn").lower()


def cargar_imagenes(ruta: Path) -> dict[tuple[str, str], str]:
    """(fecha del día, título) -> URL de imagen verificada. Nunca imágenes de GipsyTeam."""
    informe = leer_json(ruta, {}) or {}
    imagenes = {}
    for r in informe.get("resultados", []):
        if r.get("imagen") and not esta_bloqueado(r["imagen"]):
            imagenes[(r["fecha"], r["titulo"])] = r["imagen"]
    return imagenes


def _enlaces(fuentes: list[dict]) -> list[dict]:
    """Enlaces de una tarjeta; si una fuente aparece varias veces, se numeran sus notas."""
    validas = [f for f in fuentes if f.get("url") and not esta_bloqueado(f["url"])]
    repetidas = {f["nombre"] for f in validas if sum(1 for g in validas if g["nombre"] == f["nombre"]) > 1}
    contador: dict[str, int] = {}
    enlaces = []
    for f in validas:
        texto = f["nombre"]
        if f["nombre"] in repetidas:
            contador[f["nombre"]] = contador.get(f["nombre"], 0) + 1
            texto = f"{f['nombre']} · nota {contador[f['nombre']]}"
        enlaces.append(dict(f, texto=texto, fecha_texto=fecha_fuente(f.get("fecha"))))
    return enlaces


def preparar(raiz: Path, imagenes: dict) -> dict:
    """Arma los datos de las propuestas: días, tarjetas y opciones de los filtros."""
    dias, fuentes, total = [], set(), 0
    for d in cargar_dias(raiz):
        tarjetas, notas = [], []
        for c in reversed(d.get("corridas", [])):  # la corrida más reciente primero
            notas += c.get("notas") or []
            if c.get("estado") == "fallida" and c.get("motivo"):
                notas.append(f"Corrida fallida: {c['motivo']}")
            secciones = [(False, c.get("tarjetas") or []),
                         (True, (c.get("escena_latam") or {}).get("tarjetas") or [])]
            for latam, lista in secciones:
                for t in lista:
                    total += 1
                    nombres = [f["nombre"] for f in t.get("fuentes", [])]
                    fuentes.update(nombres)
                    texto = " ".join([t.get("titulo", ""), t.get("resumen", ""), t.get("novedad") or "", *nombres])
                    imagen = imagenes.get((d["fecha"], t["titulo"]))
                    tarjetas.append({
                        **t,
                        "id": f"t{total}",
                        "latam": latam,
                        "etiqueta": ETIQUETAS.get(t["clasificacion"], t["clasificacion"]),
                        "imagen": imagen if imagen and not esta_bloqueado(imagen) else None,
                        "fuentes": _enlaces(t.get("fuentes", [])),
                        "buscar": _sin_tildes(texto),
                        "datos_fuentes": "|".join(nombres),
                    })
        dias.append({"fecha": d["fecha"], "titulo": d.get("titulo") or fecha_larga(date.fromisoformat(d["fecha"])),
                     "tarjetas": tarjetas, "notas": notas})
    # Escena argentina y latinoamericana destacada: la del día más reciente que la tenga.
    destacado = next((d["fecha"] for d in dias if any(t["latam"] for t in d["tarjetas"])), None)
    return {"dias": dias, "total": total, "fuentes": sorted(fuentes, key=_sin_tildes),
            "destacado": destacado, "etiquetas": ETIQUETAS}


def generar(raiz: Path = RAIZ, salida: Path = SALIDA, imagenes: dict | None = None) -> list[Path]:
    salida = Path(salida)
    salida.mkdir(parents=True, exist_ok=True)
    if imagenes is None:
        imagenes = cargar_imagenes(salida / "imagenes-prueba.json")
    datos = preparar(raiz, imagenes)
    entorno = Environment(loader=FileSystemLoader(str(PLANTILLAS)), autoescape=select_autoescape(["html", "j2"]),
                          trim_blocks=True, lstrip_blocks=True)
    escritos = []
    for nombre, estilo in PROPUESTAS.items():
        html = entorno.get_template(f"{nombre}.html.j2").render(**datos, estilo=estilo)
        destino = salida / f"{nombre}.html"
        destino.write_text(html, encoding="utf-8")
        escritos.append(destino)
    for archivo in ESTATICOS:
        shutil.copyfile(PLANTILLAS / archivo, salida / archivo)
        escritos.append(salida / archivo)
    return escritos


if __name__ == "__main__":
    for ruta in generar():
        print(ruta.relative_to(RAIZ))
