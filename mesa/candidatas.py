"""Fuentes candidatas: dominios enlazados desde varios artículos abiertos.

Nunca se agregan solas a config/fuentes.json y nunca se borran solas.
"""

from __future__ import annotations

from datetime import date, timedelta

from .util import (dominio_base, es_afiliado, es_dominio_tecnico, es_red_social,
                   esta_bloqueado, fecha_larga)


def dominios_de_fuentes(fuentes: list[dict]) -> set[str]:
    dominios = set()
    for f in fuentes:
        for clave in ("url", "portada"):
            if f.get(clave):
                dominios.add(dominio_base(f[clave]))
    return dominios


def registrar_menciones(estado: dict, articulo_url: str, enlaces: list[str], hoy: date) -> None:
    """Guarda qué dominios externos enlaza un artículo abierto (una vez por par)."""
    menciones = estado.setdefault("menciones", [])
    existentes = {(m["dominio"], m["articulo"]) for m in menciones}
    propio = dominio_base(articulo_url)
    for enlace in enlaces:
        dominio = dominio_base(enlace)
        if (not dominio or dominio == propio or esta_bloqueado(enlace) or es_afiliado(enlace)
                or es_red_social(dominio) or es_dominio_tecnico(dominio)):
            continue
        if (dominio, articulo_url) in existentes:
            continue
        existentes.add((dominio, articulo_url))
        menciones.append({"dominio": dominio, "articulo": articulo_url,
                          "enlace": enlace, "fecha": hoy.isoformat()})


def actualizar(estado: dict, fuentes: list[dict], hoy: date,
               ventana_dias: int = 14, minimo: int = 3) -> list[dict]:
    """Recorta menciones viejas y suma candidatas nuevas. Devuelve las agregadas."""
    limite = hoy - timedelta(days=ventana_dias)
    estado["menciones"] = [m for m in estado.get("menciones", [])
                           if date.fromisoformat(m["fecha"]) > limite]
    candidatas = estado.setdefault("candidatas", [])
    conocidas = dominios_de_fuentes(fuentes)
    ya_candidatas = {c.get("dominio") for c in candidatas}

    articulos: dict[str, set[str]] = {}
    ejemplo: dict[str, str] = {}
    for m in estado["menciones"]:
        articulos.setdefault(m["dominio"], set()).add(m["articulo"])
        ejemplo.setdefault(m["dominio"], m["enlace"])

    nuevas = []
    for dominio, arts in sorted(articulos.items()):
        if len(arts) < minimo:
            continue
        if (dominio in conocidas or dominio in ya_candidatas or es_red_social(dominio)
                or es_dominio_tecnico(dominio) or esta_bloqueado(dominio)):
            continue
        cand = {
            "nombre": dominio,
            "dominio": dominio,
            "detectada": hoy.isoformat(),
            "detectada_texto": f"detectada el {fecha_larga(hoy)}",
            "motivo": f"Enlazada desde {len(arts)} artículos distintos en los últimos {ventana_dias} días.",
            "ejemplo": ejemplo[dominio],
            "origen": "automatica",
        }
        candidatas.append(cand)
        nuevas.append(cand)
    # Por si alguien agregó a mano un dominio prohibido: no se muestra.
    estado["candidatas"] = [c for c in candidatas if not esta_bloqueado(c.get("dominio", ""))
                            and not esta_bloqueado(c.get("ejemplo", "") or "")]
    return nuevas
