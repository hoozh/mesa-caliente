"""Importa mesa_caliente_historial.json (versión anterior) a data/.

Uso: python -m mesa.importar [ruta_historial]
No sobrescribe días que ya existan en data/dias/.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path

from .dias import ruta_dia
from .util import (MESES, RAIZ, dominio_base, escribir_json, esta_bloqueado,
                   fecha_larga, leer_json, normalizar_url)

_PATRON_TITULO = re.compile(r"^\s*(\d{1,2}) de ([a-záéíóú]+) de (\d{4})\s*(?:[—–-]\s*(.+))?$", re.I)


def fecha_de_titulo(titulo: str) -> tuple[date, str | None]:
    m = _PATRON_TITULO.match(titulo)
    if not m:
        raise ValueError(f"Título de día no reconocido: {titulo!r}")
    dia, mes, anio, sub = m.groups()
    return date(int(anio), MESES.index(mes.lower()) + 1, int(dia)), (sub.strip() if sub else None)


def agrupar_por_dia(historial: dict) -> dict[date, list[dict]]:
    """Agrupa los bloques del historial por fecha, en orden cronológico dentro del día."""
    grupos: dict[date, list[dict]] = {}
    for bloque in historial.get("dias", []):
        fecha, sub = fecha_de_titulo(bloque["titulo"])
        grupos.setdefault(fecha, []).append((sub, bloque))
    resultado = {}
    for fecha, bloques in grupos.items():
        corridas = []
        # El historial está ordenado del más nuevo al más viejo: se invierte.
        for n, (sub, bloque) in enumerate(reversed(bloques), 1):
            tarjetas = bloque.get("tarjetas", [])
            corridas.append({
                "id": f"importada-{fecha.isoformat()}-{n}",
                "titulo": sub[0].upper() + sub[1:] if sub else "Corrida principal",
                "titulo_original": bloque["titulo"],
                "origen": "importada",
                "estado": "ok" if tarjetas else "sin_noticias",
                "motivo": None,
                "tarjetas": tarjetas,
                "notas": list(bloque.get("notas", [])),
                **{k: v for k, v in bloque.items() if k not in ("titulo", "tarjetas", "notas")},
            })
        resultado[fecha] = corridas
    return resultado


def importar(raiz: Path, historial: dict) -> dict:
    raiz = Path(raiz)
    resumen = {"dias_creados": 0, "dias_omitidos": 0, "corridas": 0, "tarjetas": 0}
    for fecha, corridas in sorted(agrupar_por_dia(historial).items()):
        ruta = ruta_dia(raiz, fecha)
        if ruta.exists():
            resumen["dias_omitidos"] += 1
            continue
        escribir_json(ruta, {"fecha": fecha.isoformat(), "titulo": fecha_larga(fecha), "corridas": corridas})
        resumen["dias_creados"] += 1
        resumen["corridas"] += len(corridas)
        resumen["tarjetas"] += sum(len(c["tarjetas"]) for c in corridas)

    # URLs ya publicadas: cuentan como vistas para no repetirlas.
    ruta_vistos = raiz / "data" / "vistos.json"
    vistos = leer_json(ruta_vistos, {"urls": {}})
    for fecha, corridas in agrupar_por_dia(historial).items():
        for c in corridas:
            for t in c["tarjetas"]:
                for f in t.get("fuentes", []):
                    if f.get("url"):
                        vistos["urls"].setdefault(normalizar_url(f["url"]), fecha.isoformat())
    escribir_json(ruta_vistos, vistos)

    # Candidatas de la versión anterior.
    ruta_cand = raiz / "data" / "candidatas.json"
    estado = leer_json(ruta_cand, {"candidatas": [], "menciones": []})
    existentes = {c.get("dominio") for c in estado["candidatas"]}
    for c in historial.get("candidatas", []):
        dominio = dominio_base(c.get("ejemplo") or "")
        if not dominio or dominio in existentes or esta_bloqueado(dominio):
            continue
        m = re.search(r"(\d{1,2} de [a-záéíóú]+ de \d{4})", c.get("detectada", ""))
        detectada = fecha_de_titulo(m.group(1))[0].isoformat() if m else None
        estado["candidatas"].append({
            "nombre": c["nombre"],
            "dominio": dominio,
            "detectada": detectada,
            "detectada_texto": c.get("detectada"),
            "motivo": c.get("motivo"),
            "ejemplo": c.get("ejemplo"),
            "origen": "importada",
        })
    escribir_json(ruta_cand, estado)

    escribir_json(raiz / "data" / "importacion.json", {
        "fuente_importacion": historial.get("fuente_importacion"),
        "fuentes_habituales": historial.get("fuentes_habituales", []),
        **resumen,
    })
    return resumen


def main(argv: list[str]) -> int:
    ruta = Path(argv[1]) if len(argv) > 1 else RAIZ / "mesa_caliente_historial.json"
    resumen = importar(RAIZ, leer_json(ruta))
    print(f"Importación terminada: {resumen}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
