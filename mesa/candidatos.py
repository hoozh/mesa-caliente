"""Candidatos diarios de Personalidades y de palabras clave (pedido de Hoozh, 27 nov 2026).

Sin modelo y sin costo: se arma solo con el registro de noticias publicadas (data/registro/hechos.jsonl). Cada día se
guarda data/candidatos.json con los nombres que aparecen seguido y todavía no están en las listas, para que Hoozh
los agregue o los descarte desde el administrador y los filtros se afinen a medida que pasan los barridos.

- Personalidades: jugadores que el registro nombra y que no están en config/personalidades.json.
- Palabras clave: torneos o series y nombres propios que se repiten en los titulares y que no son una personalidad,
  una sala vigilada, un término general ni una palabra clave ya vigilada.
- Lo que Hoozh descarta queda en config/candidatos_descartados.json y no vuelve a proponerse.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

from . import temas as temas_mod
from .util import escribir_json, leer_json

VENTANA_DIAS = 30
MAX_CANDIDATOS = 25
MIN_APARICIONES = 2
MIN_DIAS_FRASE = 2
EJEMPLOS = 3

MESES = {"enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre",
         "diciembre", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
         "november", "december"}
# Palabras que abren frases sin ser nombres propios («El», «Con», «Nuevo»…).
SUELTAS = {"el", "la", "los", "las", "un", "una", "con", "sin", "por", "para", "de", "del", "al", "en", "y", "o", "su",
           "sus", "nuevo", "nueva", "nuevos", "nuevas", "primer", "primera", "gran", "mejor", "este", "esta", "tras",
           "como", "cuando", "según", "sobre", "entre", "desde", "hasta", "the", "a", "an", "of", "and", "in", "on",
           "for", "to", "with", "is", "new", "poker", "póker", "series", "serie", "torneo", "torneos", "final",
           "mesa", "día", "dia", "latam", "latinoamérica"}
# Palabras de formato: una frase hecha solo con ellas («Main Event», «Super High Roller») no es un candidato útil.
FORMATO = {"main", "event", "events", "high", "roller", "rollers", "super", "mini", "mega", "final", "table", "day", "satellite",
           "bounty", "mystery", "turbo", "deepstack", "championship", "open", "freeroll", "knockout", "ko", "pko", "heads", "up"}
TOKEN = re.compile(r"[A-ZÁÉÍÓÚÑÜ][\wÁÉÍÓÚÑÜáéíóúñü'’.$\-]*|\d{4}")


def _norm(texto: str) -> str:
    return temas_mod._normalizar(texto).strip()


def _vigentes(raiz: Path) -> dict[str, set[str]]:
    """Nombres que ya están en alguna lista (normalizados): no se proponen."""
    raiz = Path(raiz)
    personas, palabras = set(), set()
    for p in (leer_json(raiz / "config" / "personalidades.json", {}) or {}).get("personalidades", []):
        personas.update(_norm(n) for n in [p.get("nombre", ""), *p.get("alias", [])] if n)
    for t in (leer_json(raiz / "config" / "temas_calientes.json", {}) or {}).get("temas", []):
        palabras.update(_norm(n) for n in [t.get("nombre", ""), *t.get("palabras_clave", [])] if n)
    for t in (leer_json(raiz / "data" / "temas_auto.json", {}) or {}).get("temas", []):
        palabras.update(_norm(n) for n in [temas_mod.nombre_sin_prefijo(t.get("nombre", "")), *t.get("palabras_clave", [])] if n)
    for b in (leer_json(raiz / "config" / "busquedas.json", {}) or {}).get("busquedas", []):
        palabras.add(_norm(b.get("consulta", "")))
    for lista in temas_mod.cargar_generales(raiz).values():
        palabras.update(_norm(n) for n in lista)
    desc = leer_json(raiz / "config" / "candidatos_descartados.json", {}) or {}
    return {"personas": personas, "palabras": palabras,
            "desc_personas": {_norm(n) for n in desc.get("personalidades", [])},
            "desc_palabras": {_norm(n) for n in desc.get("palabras_clave", [])}}


def _fuentes(h: dict) -> set[str]:
    return {f.get("nombre", "") for f in h.get("fuentes", []) if f.get("nombre")}


def _acumular(d: dict, clave: str, texto: str, h: dict, extra: str = "") -> None:
    e = d.setdefault(clave, {"texto": texto, "hechos": {}, "fuentes": set(), "extra": set()})
    if len(texto) > len(e["texto"]) and _norm(texto) == _norm(e["texto"]):
        e["texto"] = texto
    e["hechos"][h["id"]] = h
    e["fuentes"] |= _fuentes(h)
    if extra:
        e["extra"].add(extra)


def _resumen(e: dict) -> dict:
    hechos = sorted(e["hechos"].values(), key=lambda h: h["fecha"], reverse=True)
    return {"texto": e["texto"], "clave": _norm(e["texto"]), "apariciones": len(hechos), "dias": len({h["fecha"] for h in hechos}),
            "fuentes": len(e["fuentes"]), "primera": min(h["fecha"] for h in hechos), "ultima": hechos[0]["fecha"],
            "paises": sorted(e["extra"]), "ejemplos": [{"id": h["id"], "titulo": h["titulo"], "fecha": h["fecha"]} for h in hechos[:EJEMPLOS]]}


def _frases(titulo: str) -> list[str]:
    """Secuencias de 2 a 4 palabras con mayúscula inicial («Poker Masters 2026», «Triton North Cyprus»)."""
    salida, actual = [], []
    for m in re.finditer(r"\S+", titulo):
        pal = m.group(0).strip(",;:()«»\"¿?¡!")
        if TOKEN.fullmatch(pal) and _norm(pal) not in MESES:
            actual.append(pal)
        else:
            salida.append(actual); actual = []
    salida.append(actual)
    frases = []
    salida = [g for g in salida]
    for grupo in salida:
        while grupo and _norm(grupo[0]) in SUELTAS:
            grupo = grupo[1:]
        while grupo and _norm(grupo[-1]) in SUELTAS:
            grupo = grupo[:-1]
        if 2 <= len(grupo) <= 4 and not all(_norm(t) in FORMATO or t.isdigit() for t in grupo):
            frases.append(" ".join(grupo).rstrip(".,"))
    return frases


def calcular(raiz: Path, hoy: date) -> dict:
    raiz = Path(raiz)
    desde = (hoy - timedelta(days=VENTANA_DIAS)).isoformat()
    hechos = [h for h in (_leer_hechos(raiz)) if h.get("fecha", "") >= desde and h.get("seccion") != "tema" and h.get("id")]
    v = _vigentes(raiz)
    salas = {_norm(n) for n in temas_mod.cargar_generales(raiz)["salas"]}

    personas: dict[str, dict] = {}
    for h in hechos:
        for j in h.get("jugadores", []):
            nombre = (j.get("nombre") or "").strip()
            if len(nombre.split()) >= 2 and _norm(nombre) not in v["personas"] | v["desc_personas"]:
                _acumular(personas, _norm(nombre), nombre, h, j.get("pais") or "")
    nombres_jugadores = set(personas) | v["personas"]

    palabras: dict[str, dict] = {}
    for h in hechos:
        torneo = (h.get("torneo") or "").strip()
        if torneo and not all(_norm(t) in FORMATO or t.isdigit() for t in torneo.split()):
            _acumular(palabras, _norm(torneo), torneo, h, "torneo")
        for frase in set(_frases(h["titulo"])):
            _acumular(palabras, _norm(frase), frase, h, "frase")
    candidatas = []
    for clave, e in palabras.items():
        if clave in v["palabras"] | v["desc_palabras"] | nombres_jugadores or clave in salas:
            continue
        if any(clave == s or clave in s.split() for s in salas):
            continue
        e["extra"] = {"torneo"} if "torneo" in e["extra"] else {"frase"}
        r = _resumen(e); r["origen"] = next(iter(e["extra"])); r.pop("paises")
        if r["apariciones"] >= MIN_APARICIONES and (r["dias"] >= MIN_DIAS_FRASE or r["origen"] == "torneo"):
            candidatas.append((clave, r))
    # Si una frase corta está incluida en otra más larga con las mismas apariciones, queda la más larga.
    claves = [(k, r) for k, r in candidatas]
    finales = [r for k, r in claves if not any(k != k2 and k in k2 and r["apariciones"] <= r2["apariciones"] for k2, r2 in claves)]

    def puntaje(r: dict) -> tuple:
        return (r["dias"] * 2 + r["fuentes"], r["ultima"])
    pers = [_resumen(e) for e in personas.values()]
    pers = [r for r in pers if r["apariciones"] >= MIN_APARICIONES or r["fuentes"] >= 2]
    for lista in (pers, finales):
        lista.sort(key=puntaje, reverse=True)
        for r in lista:
            r["nuevo"] = r["primera"] >= (hoy - timedelta(days=1)).isoformat()
    return {"fecha": hoy.isoformat(), "ventana_dias": VENTANA_DIAS, "hechos_revisados": len(hechos),
            "personalidades": pers[:MAX_CANDIDATOS], "palabras_clave": finales[:MAX_CANDIDATOS]}


def _leer_hechos(raiz: Path) -> list[dict]:
    import json
    ruta = Path(raiz) / "data" / "registro" / "hechos.jsonl"
    if not ruta.exists():
        return []
    salida = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            salida.append(json.loads(linea))
        except ValueError:
            continue
    return salida


def _leer_jsonl(ruta: Path) -> list[dict]:
    import json
    if not ruta.exists():
        return []
    salida = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            salida.append(json.loads(linea))
        except ValueError:
            continue
    return salida


def calcular_privados(raiz: Path, hoy: date, publicos: dict, archivo: list[dict]) -> dict:
    """Lo que sale del archivo de resultados (privado): cuántas veces aparece cada candidato público y qué nombres
    y torneos se repiten allí sin haberse publicado, para decidir si ameritan una tarjeta. Nunca va al repositorio público."""
    from .crear_tarjeta import clave_archivo
    desde = (hoy - timedelta(days=VENTANA_DIAS)).isoformat()
    lineas = [a for a in archivo if (a.get("fecha") or "") >= desde and a.get("titulo")]
    v = _vigentes(raiz)
    salas = {_norm(n) for n in temas_mod.cargar_generales(raiz)["salas"]}
    normalizadas = [(_norm(a["titulo"]), a) for a in lineas]

    def ejemplos(coinciden: list[dict]) -> list[dict]:
        orden = sorted(coinciden, key=lambda a: a["fecha"], reverse=True)
        return [{"fecha": a["fecha"], "titulo": a["titulo"], "clave": clave_archivo(a),
                 "fuentes": [f.get("nombre") for f in a.get("fuentes", []) if f.get("nombre")][:3]} for a in orden[:EJEMPLOS]]

    menciones = {}
    for r in publicos["personalidades"] + publicos["palabras_clave"]:
        c = [a for n, a in normalizadas if r["clave"] and f" {r['clave']} " in f" {n} "]
        if c:
            menciones[r["clave"]] = {"n": len(c), "dias": len({a["fecha"] for a in c}), "ejemplos": ejemplos(c)}

    # Nombres propios que se repiten en el archivo y no están en ninguna lista ni entre los candidatos públicos.
    conocidos = {r["clave"] for r in publicos["personalidades"] + publicos["palabras_clave"]} | v["personas"] | v["palabras"] | v["desc_personas"] | v["desc_palabras"]
    frases: dict[str, dict] = {}
    for a in lineas:
        for frase in set(_frases(a["titulo"])):
            e = frases.setdefault(_norm(frase), {"texto": frase, "lineas": [], "fuentes": set()})
            e["lineas"].append(a)
            e["fuentes"] |= {f.get("nombre") for f in a.get("fuentes", []) if f.get("nombre")}
    nuevos = []
    for clave, e in frases.items():
        if clave in conocidos or clave in salas or any(clave in s.split() for s in salas):
            continue
        dias = {a["fecha"] for a in e["lineas"]}
        if len(e["lineas"]) >= MIN_APARICIONES and len(dias) >= MIN_DIAS_FRASE:
            nuevos.append({"texto": e["texto"], "clave": clave, "menciones": len(e["lineas"]), "dias": len(dias), "fuentes": len(e["fuentes"]),
                           "primera": min(dias), "ultima": max(dias), "ejemplos": ejemplos(e["lineas"])})
    nuevos = [r for r in nuevos if not any(r["clave"] != o["clave"] and r["clave"] in o["clave"] and r["menciones"] <= o["menciones"] for o in nuevos)]
    nuevos.sort(key=lambda r: (r["dias"] * 2 + r["fuentes"], r["ultima"]), reverse=True)
    return {"fecha": hoy.isoformat(), "ventana_dias": VENTANA_DIAS, "lineas_revisadas": len(lineas),
            "menciones": menciones, "del_archivo": nuevos[:MAX_CANDIDATOS]}


def generar(raiz: Path, hoy: date, archivo_privado: Path | None = None) -> dict:
    """Escribe data/candidatos.json (público, solo con el registro de noticias publicadas) y, si hay archivo de
    resultados, candidatos_privados.json junto a él (en el repositorio privado cuando existe)."""
    datos = calcular(raiz, hoy)
    escribir_json(Path(raiz) / "data" / "candidatos.json", datos)
    if archivo_privado is not None and Path(archivo_privado).exists():
        priv = calcular_privados(raiz, hoy, datos, _leer_jsonl(Path(archivo_privado)))
        escribir_json(Path(archivo_privado).parent / "candidatos_privados.json", priv)
    return datos
