"""Genera docs/index.html a partir de los datos. No sabe nada de la recolección.

Para cambiar el diseño basta con editar templates/index.html.j2 y templates/estilo.css.
"""

from __future__ import annotations

import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .dias import cargar_dias
from .util import (MESES, RAIZ, escribir_texto, fecha_corta, fecha_larga,
                   leer_json)

ETIQUETAS = {"confirmado": "Confirmado", "discusion": "En discusión", "rumor": "Rumor"}
ORIGENES = {"automatica": "Corrida automática", "manual": "Corrida manual"}


def fecha_fuente(valor: str | None) -> str:
    """Fechas ISO de las corridas nuevas se muestran cortas; las importadas, tal cual."""
    if not valor:
        return ""
    try:
        return fecha_corta(date.fromisoformat(valor))
    except ValueError:
        return valor


def _titulo_corrida(c: dict) -> str:
    if c.get("origen") == "importada":
        return c.get("titulo") or "Corrida"
    base = ORIGENES.get(c.get("origen"), "Corrida")
    hora = (c.get("id") or "")[11:16]
    return f"{base} · {hora} UTC" if hora else base


def _salud(fuentes: list[dict], dias_reintento: int) -> dict:
    pausadas, con_fallos = [], []
    for f in fuentes:
        if f.get("estado") == "en_pausa":
            proximo = None
            if f.get("ultimo_intento"):
                proximo = fecha_corta(date.fromisoformat(f["ultimo_intento"]) + timedelta(days=dias_reintento))
            pausadas.append({
                "nombre": f["nombre"],
                "motivo": f.get("motivo_pausa") or "sin detalle",
                "desde": fecha_corta(date.fromisoformat(f["pausada_desde"])) if f.get("pausada_desde") else None,
                "proximo": proximo,
                "portada": f.get("portada") or f.get("url"),
            })
        elif int(f.get("fallos_consecutivos") or 0) > 0:
            con_fallos.append({"nombre": f["nombre"], "fallos": f["fallos_consecutivos"],
                               "motivo": f.get("ultimo_motivo") or "sin detalle"})
    activas = sum(1 for f in fuentes if f.get("estado") != "en_pausa")
    return {"pausadas": pausadas, "con_fallos": con_fallos, "activas": activas, "total": len(fuentes)}


def _costos(costos: dict, hoy: date) -> dict:
    corridas = costos.get("corridas", [])
    mes = hoy.strftime("%Y-%m")
    del_mes = [c for c in corridas if (c.get("fecha") or "").startswith(mes)]
    return {
        "ultima": corridas[-1] if corridas else None,
        "mes_nombre": f"{MESES[hoy.month - 1]} de {hoy.year}",
        "mes_costo": round(sum(c.get("costo_usd", 0) for c in del_mes), 4),
        "mes_entrada": sum(c.get("tokens_entrada", 0) for c in del_mes),
        "mes_salida": sum(c.get("tokens_salida", 0) for c in del_mes),
        "mes_corridas": len(del_mes),
    }


def _ultimo_registro(dias: list[dict]) -> dict | None:
    """Recolección de la corrida más reciente que registró descartes."""
    for d in dias:
        for c in d["corridas"]:  # ya vienen de la más reciente a la más antigua
            r = c.get("recoleccion") or {}
            if r.get("descartes"):
                corte = r.get("corte")
                return {"id": c.get("id"), "corte": corte.replace("T", " ").replace("Z", " UTC") if corte else None,
                        "enviados": r.get("titulares_enviados", 0), **r["descartes"]}
    return None


def contexto(raiz: Path, hoy: date, generado: datetime) -> dict:
    raiz = Path(raiz)
    ajustes = leer_json(raiz / "config" / "ajustes.json", {})
    fuentes = leer_json(raiz / "config" / "fuentes.json", {"fuentes": []})["fuentes"]
    candidatas = leer_json(raiz / "data" / "candidatas.json", {"candidatas": []}).get("candidatas", [])
    costos = leer_json(raiz / "data" / "costos.json", {"corridas": []})
    abiertos = ajustes.get("dias_abiertos_en_pagina", 3)

    dias = []
    total_tarjetas = 0
    for n, d in enumerate(cargar_dias(raiz)):
        corridas = []
        for c in reversed(d.get("corridas", [])):  # la más reciente primero
            corridas.append({**c, "titulo_mostrar": _titulo_corrida(c)})
        cantidad = sum(len(c.get("tarjetas", [])) + len((c.get("escena_latam") or {}).get("tarjetas", []))
                       for c in corridas)
        total_tarjetas += cantidad
        dias.append({**d, "corridas": corridas, "abierto": n < abiertos, "cantidad": cantidad})

    return {
        "dias": dias,
        "total_tarjetas": total_tarjetas,
        "fuentes_activas": [f["nombre"] for f in fuentes if f.get("estado") != "en_pausa"],
        "salud": _salud(fuentes, ajustes.get("dias_pausa_reintento", 7)),
        "candidatas": sorted(candidatas, key=lambda c: c.get("detectada") or "", reverse=True),
        "costos": _costos(costos, hoy),
        "descartes": _ultimo_registro(dias),
        "etiquetas": ETIQUETAS,
        "hoy_texto": fecha_larga(hoy),
        "generado": generado.strftime("%Y-%m-%d %H:%M UTC"),
        "fecha_fuente": fecha_fuente,
    }


def generar(raiz: Path, hoy: date, generado: datetime, plantillas: Path | None = None,
            salida: Path | None = None) -> Path:
    raiz = Path(raiz)
    plantillas = Path(plantillas or raiz / "templates")
    salida = Path(salida or raiz / "docs")
    entorno = Environment(loader=FileSystemLoader(str(plantillas)),
                          autoescape=select_autoescape(["html", "j2"]),
                          trim_blocks=True, lstrip_blocks=True)
    html = entorno.get_template("index.html.j2").render(**contexto(raiz, hoy, generado))
    escribir_texto(salida / "index.html", html)
    shutil.copyfile(plantillas / "estilo.css", salida / "estilo.css")
    (salida / ".nojekyll").touch()
    return salida / "index.html"


if __name__ == "__main__":
    from .util import ahora_utc
    ahora = ahora_utc()
    print(generar(RAIZ, ahora.date(), ahora))
    sys.exit(0)
