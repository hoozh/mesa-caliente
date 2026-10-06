"""Temas calientes: lista manual (config/temas_calientes.json) y detección automática
(data/temas_auto.json).

Uso desde el botón de GitHub (flujo "Gestionar tema caliente"):
  python -m mesa.temas --accion agregar --nombre "Caso X" --palabras "X, Y"
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import date, timedelta
from pathlib import Path

from . import calidad
from .dias import cargar_dias
from .util import RAIZ, ahora_utc, dominio_base, escribir_json, leer_json

DIAS_DETECCION = 3       # ventana para contar fuentes independientes
MIN_FUENTES = 3          # fuentes independientes para que un tema sea caliente
DIAS_APAGADO = 7         # días sin novedades para apagar un tema automático


def ruta_manual(raiz: Path) -> Path:
    return Path(raiz) / "config" / "temas_calientes.json"


def ruta_auto(raiz: Path) -> Path:
    return Path(raiz) / "data" / "temas_auto.json"


def _normalizar(texto: str) -> str:
    return " " + re.sub(r"[^\w$]+", " ", calidad.sin_tildes(texto)) + " "


def coincide(texto: str, palabras: list[str]) -> bool:
    plano = _normalizar(texto)
    return any(_normalizar(p) in plano for p in palabras if p.strip())


# ---------------------------------------------------------------- lista manual

def cargar_manual(raiz: Path) -> dict:
    return leer_json(ruta_manual(raiz), {"temas": []}) or {"temas": []}


def gestionar(raiz: Path, accion: str, nombre: str, palabras: str, hoy: date) -> str:
    """Agrega, quita o desactiva un tema de la lista manual. Devuelve un mensaje para el registro."""
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("Falta el nombre del tema.")
    lista = [p.strip() for p in (palabras or "").split(",") if p.strip()]
    datos = cargar_manual(raiz)
    temas = datos.setdefault("temas", [])
    existente = next((t for t in temas if t["nombre"].strip().lower() == nombre.lower()), None)
    if accion == "agregar":
        if existente:
            existente["palabras_clave"] = sorted(set(existente.get("palabras_clave", [])) | set(lista), key=str.lower)
            existente["estado"] = "activo"
            mensaje = f"Tema «{nombre}» actualizado y activo."
        else:
            if not lista:
                raise ValueError("Para agregar un tema hace falta al menos una palabra clave.")
            temas.append({"nombre": nombre, "palabras_clave": lista, "alta": hoy.isoformat(), "estado": "activo"})
            mensaje = f"Tema «{nombre}» agregado."
    elif accion == "quitar":
        if not existente:
            raise ValueError(f"No existe un tema llamado «{nombre}».")
        temas.remove(existente)
        mensaje = f"Tema «{nombre}» quitado."
    elif accion == "desactivar":
        if not existente:
            raise ValueError(f"No existe un tema llamado «{nombre}».")
        existente["estado"] = "inactivo"
        mensaje = f"Tema «{nombre}» desactivado."
    else:
        raise ValueError(f"Acción desconocida: {accion}. Use agregar, quitar o desactivar.")
    escribir_json(ruta_manual(raiz), datos)
    return mensaje


# ---------------------------------------------------------------- temas activos

def activos(raiz: Path) -> list[dict]:
    """Temas activos, manuales primero. Cada uno: nombre, palabras_clave y origen."""
    resultado = [dict(t, origen="manual") for t in cargar_manual(raiz).get("temas", [])
                 if t.get("estado", "activo") == "activo" and t.get("palabras_clave")]
    auto = leer_json(ruta_auto(raiz), {"temas": []}) or {"temas": []}
    resultado += [dict(t, origen="automatico") for t in auto.get("temas", []) if t.get("estado") == "activo"]
    return resultado


def detector(temas: list[dict]):
    """Función que devuelve el nombre del tema de una nota (o None)."""
    def tema_de(item: dict) -> str | None:
        texto = f"{item.get('titulo') or ''} {item.get('primera_linea') or ''}"
        for t in temas:
            if coincide(texto, t["palabras_clave"]):
                return t["nombre"]
        return None
    return tema_de


# ---------------------------------------------------------------- detección automática

def _tarjetas_recientes(raiz: Path, hoy: date, dias: int) -> list[dict]:
    desde = (hoy - timedelta(days=dias - 1)).isoformat()
    tarjetas = []
    for d in cargar_dias(raiz):
        if not (desde <= d["fecha"] <= hoy.isoformat()):
            continue
        for c in d.get("corridas", []):
            for t in c.get("tarjetas", []) + ((c.get("escena_latam") or {}).get("tarjetas") or []):
                tarjetas.append({"fecha": d["fecha"], "texto": f"{t['titulo']}. {t['resumen']}",
                                 "titulo": t["titulo"], "actualiza": t.get("actualiza"),
                                 "urls": [f["url"] for f in t.get("fuentes", []) if f.get("url")]})
            for tema in c.get("temas") or []:
                for n in tema.get("novedades", []):
                    tarjetas.append({"fecha": d["fecha"], "texto": f"{tema.get('tema', '')}. {n.get('aporta', '')}",
                                     "titulo": tema.get("tema", ""), "actualiza": None,
                                     "urls": [n["url"]] if n.get("url") else []})
    return tarjetas


def _agrupar(tarjetas: list[dict]) -> list[list[int]]:
    """Une tarjetas del mismo hecho: comparten una persona nombrada o una actualiza a la otra."""
    padre = list(range(len(tarjetas)))

    def raiz_de(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i

    personas = [set(calidad.personas(t["texto"])) for t in tarjetas]
    for i in range(len(tarjetas)):
        for j in range(i + 1, len(tarjetas)):
            if (personas[i] & personas[j] or tarjetas[i]["actualiza"] == tarjetas[j]["titulo"]
                    or tarjetas[j]["actualiza"] == tarjetas[i]["titulo"]):
                padre[raiz_de(i)] = raiz_de(j)
    grupos: dict[int, list[int]] = {}
    for i in range(len(tarjetas)):
        grupos.setdefault(raiz_de(i), []).append(i)
    return list(grupos.values())


def detectar_auto(raiz: Path, hoy: date) -> dict:
    """Actualiza data/temas_auto.json y devuelve su contenido."""
    raiz = Path(raiz)
    datos = leer_json(ruta_auto(raiz), {"temas": []}) or {"temas": []}
    temas = datos.setdefault("temas", [])
    manuales = [t for t in cargar_manual(raiz).get("temas", []) if t.get("estado", "activo") == "activo"]
    tarjetas = _tarjetas_recientes(raiz, hoy, DIAS_DETECCION)

    for grupo in _agrupar(tarjetas):
        miembros = [tarjetas[i] for i in grupo]
        dominios = {dominio_base(u) for m in miembros for u in m["urls"]}
        if len(dominios) < MIN_FUENTES:
            continue
        conteo: dict[str, int] = {}
        for m in miembros:
            for p in set(calidad.personas(m["texto"])):
                conteo[p] = conteo.get(p, 0) + 1
        claves = sorted(p for p, n in conteo.items() if n >= 2) or sorted(conteo, key=conteo.get, reverse=True)[:1]
        if not claves:
            continue  # sin un nombre que lo identifique no se puede vigilar
        if any(coincide(" ".join(claves), t.get("palabras_clave", [])) for t in manuales):
            continue  # ya está en la lista manual
        ultima = max(m["fecha"] for m in miembros)
        existente = next((t for t in temas if set(t["palabras_clave"]) & set(claves)), None)
        if existente:
            existente["palabras_clave"] = sorted(set(existente["palabras_clave"]) | set(claves))
            existente["ultima_novedad"] = max(existente.get("ultima_novedad", ultima), ultima)
            existente["fuentes"] = sorted(set(existente.get("fuentes", [])) | dominios)
            existente["estado"] = "activo"
        else:
            principal = max(conteo, key=conteo.get)
            temas.append({"nombre": f"Tema: {principal}", "palabras_clave": claves, "alta": hoy.isoformat(),
                          "ultima_novedad": ultima, "fuentes": sorted(dominios), "estado": "activo"})

    # Novedades de temas ya existentes y apagado tras 7 días sin novedades.
    for t in temas:
        fechas = [m["fecha"] for m in tarjetas if coincide(m["texto"], t["palabras_clave"])]
        if fechas:
            t["ultima_novedad"] = max([t.get("ultima_novedad", fechas[0])] + fechas)
        ultima = date.fromisoformat(t.get("ultima_novedad", t["alta"]))
        if (hoy - ultima).days > DIAS_APAGADO:
            t["estado"] = "inactivo"
    escribir_json(ruta_auto(raiz), datos)
    return datos


# ---------------------------------------------------------------- línea de comandos (botón de GitHub)

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Gestionar temas calientes")
    p.add_argument("--accion", default=os.environ.get("TEMA_ACCION", ""))
    p.add_argument("--nombre", default=os.environ.get("TEMA_NOMBRE", ""))
    p.add_argument("--palabras", default=os.environ.get("TEMA_PALABRAS", ""))
    args = p.parse_args(argv)
    try:
        print(gestionar(RAIZ, args.accion.strip().lower(), args.nombre, args.palabras, ahora_utc().date()))
    except ValueError as e:
        print(f"No se pudo: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
