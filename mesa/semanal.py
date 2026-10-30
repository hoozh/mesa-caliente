"""Brief semanal: lee el registro de la semana (data/registro/) y hace UNA llamada al modelo.

Uso:
  python -m mesa.semanal                  # semana anterior completa (lunes a domingo)
  python -m mesa.semanal --semana 2026-W43

Guarda data/semanal/AAAA-Wnn.json y .md, publica docs/semanal/ y registra el costo en data/costos.json.
Nada que no esté en el registro: el programa elige las ideas de artículos y las fuentes nuevas, y quita
las oraciones del modelo con datos que no figuran en las líneas del registro que cita.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import calidad, control, modelo
from . import registro as registro_mod
from .util import RAIZ, ahora_utc, escribir_json, escribir_texto, fecha_corta, leer_json, limpiar_texto

SISTEMA_BRIEF = """Usted prepara el brief semanal interno de "Mesa Caliente" para el equipo editorial. Escriba en español neutro y formal, sin voseo (use "usted" o formas impersonales).

Recibirá el registro de la semana:
- Hechos H1, H2…: fecha | tipo | clasificación | sección | título | salas | jugadores (país) | torneo | país del evento | temas calientes | fuentes. Los marcados (latino) tienen protagonistas o torneos latinos.
- Cambios en salas C1, C2…: fecha | sala | tema | dato tal como lo dice la fuente | fuente.
- Ideas de artículos I1, I2…: ya elegidas por el programa, con su motivo.

Escriba:
1. "temas": hasta {max_temas} temas de la semana, cada uno con "texto" (una o dos oraciones) e "ids" (los hechos H que lo respaldan).
2. "cambios": los cambios en salas, agrupados por sala, con "texto" e "ids" (los C). Puede citar el dato entre comillas, copiado tal cual.
3. "latinos": jugadores y torneos latinos destacados, solo con hechos marcados (latino), con "texto" e "ids".
4. "ideas": para cada idea I, "id" y "texto": una oración con el ángulo posible del artículo.

Reglas:
- REGLA MÁXIMA: use solo lo que está en el registro recibido. Todo nombre, cifra, país, torneo o sala debe figurar en las líneas que cita ese elemento. No agregue contexto, fechas ni datos de su conocimiento. El programa quita las oraciones con datos que no estén en esas líneas.
- No escriba URLs: el programa agrega las fuentes.
- No use superlativos ni elogios. Conserve la jerga del poker y los nombres de eventos en inglés.

Responda únicamente con un objeto JSON, sin texto adicional:
{{"temas": [{{"texto": "...", "ids": ["H1", "H4"]}}], "cambios": [{{"texto": "...", "ids": ["C1"]}}],
 "latinos": [{{"texto": "...", "ids": ["H3"]}}], "ideas": [{{"id": "I1", "texto": "..."}}]}}"""

MAX_TEMAS = 8
CLASIFICACIONES = {"confirmado": "confirmado", "discusion": "en discusión", "rumor": "rumor"}
SECCIONES = {"general": "general", "latam": "escena latina", "tema": "tema caliente"}


# ---------------------------------------------------------------- semana

def semana_de(texto: str | None, hoy: date) -> tuple[str, date, date]:
    """(«AAAA-Wnn», lunes, domingo). Sin texto: la semana anterior completa."""
    if texto:
        m = re.fullmatch(r"\s*(\d{4})-?W(\d{1,2})\s*", texto, re.I)
        if not m:
            raise ValueError(f"Semana inválida: «{texto}» (use AAAA-Wnn, por ejemplo 2026-W43).")
        lunes = date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    else:
        lunes = hoy - timedelta(days=hoy.weekday() + 7)
    anio, num, _ = lunes.isocalendar()
    return f"{anio}-W{num:02d}", lunes, lunes + timedelta(days=6)


def _en(f: dict, desde: date, hasta: date) -> bool:
    return desde.isoformat() <= (f.get("fecha") or "") <= hasta.isoformat()


def _semana_iso(fecha: str) -> tuple[int, int]:
    a, s, _ = date.fromisoformat(fecha).isocalendar()
    return a, s


# ---------------------------------------------------------------- lo que decide el programa

def es_latino(h: dict) -> bool:
    paises = {j.get("pais") for j in h.get("jugadores") or []} | {h.get("pais_evento")}
    return h.get("seccion") == "latam" or bool(paises & set(control.PAISES))


def fuentes_nuevas(todos: list[dict], desde: date, hasta: date) -> list[dict]:
    """Fuentes que aparecen por primera vez en el registro esta semana y aportaron hechos."""
    primera: dict[str, str] = {}
    for h in sorted(todos, key=lambda h: h.get("fecha") or ""):
        for f in h.get("fuentes") or []:
            primera.setdefault(f.get("nombre") or "", h.get("fecha") or "")
    nuevas: dict[str, dict] = {}
    for h in todos:
        if not _en(h, desde, hasta):
            continue
        for f in h.get("fuentes") or []:
            nombre = f.get("nombre") or ""
            if nombre and desde.isoformat() <= primera.get(nombre, "") <= hasta.isoformat():
                fila = nuevas.setdefault(nombre, {"nombre": nombre, "clase": f.get("clase") or "", "hechos": []})
                if h["titulo"] not in [x["titulo"] for x in fila["hechos"]]:
                    fila["hechos"].append({"titulo": h["titulo"], "url": f.get("url")})
    return sorted(nuevas.values(), key=lambda f: -len(f["hechos"]))


def ideas_candidatas(todos: list[dict], desde: date, hasta: date) -> list[dict]:
    """Temas que siguieron 2 semanas o más (seguidas, hasta esta) o con 3 o más fuentes distintas en la semana."""
    semana = [h for h in todos if _en(h, desde, hasta)]
    ideas = []
    nombres_temas = []
    for h in semana:
        for t in h.get("temas_calientes") or []:
            if t not in nombres_temas:
                nombres_temas.append(t)
    for tema in nombres_temas:
        semanas = {_semana_iso(h["fecha"]) for h in todos
                   if tema in (h.get("temas_calientes") or []) and h.get("fecha", "") <= hasta.isoformat()}
        seguidas, lunes = 0, desde
        while _semana_iso(lunes.isoformat()) in semanas:
            seguidas += 1
            lunes -= timedelta(days=7)
        de_semana = [h for h in semana if tema in (h.get("temas_calientes") or [])]
        fuentes = {f.get("nombre") for h in de_semana for f in h.get("fuentes") or []}
        motivos = []
        if seguidas >= 2:
            motivos.append(f"siguió {seguidas} semanas seguidas")
        if len(fuentes) >= 3:
            motivos.append(f"{len(fuentes)} fuentes distintas esta semana")
        if motivos:
            ideas.append({"tema": tema, "motivo": "; ".join(motivos), "hechos": de_semana})
    for h in semana:
        if h.get("temas_calientes"):
            continue  # ya cuenta dentro de su tema
        fuentes = {f.get("nombre") for f in h.get("fuentes") or []}
        if len(fuentes) >= 3:
            ideas.append({"tema": h["titulo"], "motivo": f"{len(fuentes)} fuentes distintas", "hechos": [h]})
    for n, idea in enumerate(ideas, 1):
        idea["id"] = f"I{n}"
    return ideas


# ---------------------------------------------------------------- mensaje

def linea_hecho(h: dict) -> str:
    jugadores = ", ".join(j["nombre"] + (f" ({j['pais']})" if j.get("pais") else "") for j in h.get("jugadores") or [])
    partes = [h.get("fecha") or "", registro_mod.TIPOS.get(h.get("tipo"), "Otro").lower(),
              CLASIFICACIONES.get(h.get("clasificacion"), h.get("clasificacion") or ""),
              SECCIONES.get(h.get("seccion"), h.get("seccion") or ""), h.get("titulo") or "",
              "salas: " + (", ".join(h.get("salas") or []) or "-"), "jugadores: " + (jugadores or "-"),
              "torneo: " + (h.get("torneo") or "-"), "país del evento: " + (h.get("pais_evento") or "-"),
              "temas: " + (", ".join(h.get("temas_calientes") or []) or "-"),
              "fuentes: " + ", ".join(f.get("nombre") or "" for f in h.get("fuentes") or [])]
    return " | ".join(limpiar_texto(p) for p in partes)


def linea_cambio(c: dict, temas: dict) -> str:
    return " | ".join(limpiar_texto(p) for p in [
        c.get("fecha") or "", c.get("sala") or "", temas.get(c.get("tema"), c.get("tema") or ""),
        c.get("dato") or "", (c.get("fuente") or {}).get("nombre") or ""])


def construir_mensaje(hechos: list[dict], cambios: list[dict], ideas: list[dict], temas_salas: dict,
                      etiqueta: str) -> tuple[str, dict[str, str]]:
    """Mensaje para el modelo y el texto de cada referencia (H, C, I) para verificar lo que escriba."""
    refs: dict[str, str] = {}
    lineas = [f"Registro de la semana {etiqueta}.", "", "Hechos:"]
    for n, h in enumerate(hechos, 1):
        refs[f"H{n}"] = linea_hecho(h)
        lineas.append(f"[H{n}]{' (latino)' if es_latino(h) else ''} {refs[f'H{n}']}")
    lineas += ["", "Cambios en salas:"]
    for n, c in enumerate(cambios, 1):
        refs[f"C{n}"] = linea_cambio(c, temas_salas)
        lineas.append(f"[C{n}] {refs[f'C{n}']}")
    lineas += ["", "Ideas de artículos (elegidas por el programa):"]
    for idea in ideas:
        ids = [f"H{hechos.index(h) + 1}" for h in idea["hechos"] if h in hechos]
        refs[idea["id"]] = " ".join([idea["tema"]] + [refs[i] for i in ids])
        lineas.append(f"[{idea['id']}] {idea['tema']} — motivo: {idea['motivo']} — hechos: {', '.join(ids)}")
    return "\n".join(lineas), refs


# ---------------------------------------------------------------- verificación

def _ids(valor, prefijo: str, refs: dict) -> list[str]:
    ids = []
    for i in valor if isinstance(valor, list) else []:
        i = str(i).strip().upper()
        if re.fullmatch(rf"{prefijo}\d+", i) and i in refs and i not in ids:
            ids.append(i)
    return ids


def verificar(datos: dict, refs: dict, hechos: list[dict], cambios: list[dict],
              ideas: list[dict]) -> tuple[dict, list[str]]:
    """Cada texto debe citar líneas del registro y solo puede decir lo que está en ellas."""
    controles: list[str] = []
    salida = {"temas": [], "cambios": [], "latinos": [], "ideas": {}}

    def depurar(texto, ids, seccion):
        texto = limpiar_texto(str(texto or ""))
        if not texto or not ids:
            controles.append(f"{seccion}: elemento sin texto o sin referencias válidas")
            return ""
        limpio, quitadas = calidad.depurar_texto(texto, " ".join(refs[i] for i in ids))
        controles.extend(f"{seccion}: {q}" for q in quitadas)
        return limpio

    for seccion, prefijo in (("temas", "H"), ("cambios", "C"), ("latinos", "H")):
        elementos = datos.get(seccion) if isinstance(datos.get(seccion), list) else []
        for e in elementos[:MAX_TEMAS if seccion == "temas" else None]:
            if not isinstance(e, dict):
                continue
            ids = _ids(e.get("ids"), prefijo, refs)
            if seccion == "latinos":
                ids = [i for i in ids if es_latino(hechos[int(i[1:]) - 1])]
            texto = depurar(e.get("texto"), ids, seccion)
            if not texto:
                continue
            if prefijo == "H":
                fuentes = [f for i in ids for f in hechos[int(i[1:]) - 1].get("fuentes") or []]
            else:
                fuentes = [cambios[int(i[1:]) - 1].get("fuente") or {} for i in ids]
            vistas, unicas = set(), []
            for f in fuentes:
                if f.get("url") and f["url"] not in vistas:
                    vistas.add(f["url"])
                    unicas.append({"nombre": f.get("nombre"), "url": f["url"]})
            salida[seccion].append({"texto": texto, "fuentes": unicas})
    for e in datos.get("ideas") if isinstance(datos.get("ideas"), list) else []:
        if isinstance(e, dict) and str(e.get("id") or "").upper() in {i["id"] for i in ideas}:
            clave = str(e["id"]).upper()
            texto = depurar(e.get("texto"), [clave], "ideas")
            if texto:
                salida["ideas"][clave] = texto
    return salida, controles


# ---------------------------------------------------------------- brief

def generar_brief(raiz: Path, hoy: date, ahora: datetime, llamar=modelo.llamar_anthropic,
                  semana: str | None = None) -> dict:
    raiz = Path(raiz)
    ajustes = leer_json(raiz / "config" / "ajustes.json", {})
    salas = registro_mod.cargar_salas(raiz)
    etiqueta, desde, hasta = semana_de(semana, hoy)
    todos = registro_mod.leer_jsonl(raiz / registro_mod.HECHOS)
    hechos = [h for h in todos if _en(h, desde, hasta)]
    cambios = [c for c in registro_mod.leer_jsonl(raiz / registro_mod.CAMBIOS) if _en(c, desde, hasta)]
    ideas = ideas_candidatas(todos, desde, hasta)
    brief = {
        "semana": etiqueta, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "generado": ahora.strftime("%Y-%m-%d %H:%M UTC"), "hechos": len(hechos), "cambios_registrados": len(cambios),
        "temas": [], "cambios": [], "latinos": [],
        "fuentes_nuevas": fuentes_nuevas(todos, desde, hasta),
        "ideas": [{"tema": i["tema"], "motivo": i["motivo"], "texto": "",
                   "fuentes": [{"nombre": f.get("nombre"), "url": f.get("url")}
                               for h in i["hechos"] for f in (h.get("fuentes") or [])[:1]]} for i in ideas],
        "nota": None, "controles": [], "uso": None,
    }
    if not hechos and not cambios:
        brief["nota"] = "El registro no tiene hechos ni cambios en salas para esta semana: no se consultó al modelo."
        return brief

    mensaje, refs = construir_mensaje(hechos, cambios, ideas, salas["temas"], etiqueta)
    nombre_modelo = modelo.modelo_configurado(ajustes)
    try:
        texto, uso = llamar(SISTEMA_BRIEF.format(max_temas=MAX_TEMAS), mensaje, nombre_modelo,
                            ajustes.get("max_tokens_brief", 2000))
        brief["uso"] = modelo._con_costo(uso, nombre_modelo, ajustes)
        datos = modelo._extraer_json(texto)
    except modelo.ErrorModelo as e:
        if e.uso:
            brief["uso"] = modelo._con_costo(e.uso, nombre_modelo, ajustes)
        brief["nota"] = f"La llamada al modelo falló ({e}): el brief solo trae lo que arma el programa."
        brief["estado"] = "fallida"
        return brief
    salida, brief["controles"] = verificar(datos, refs, hechos, cambios, ideas)
    brief.update(temas=salida["temas"], cambios=salida["cambios"], latinos=salida["latinos"])
    for idea, base in zip(brief["ideas"], ideas):
        idea["texto"] = salida["ideas"].get(base["id"], "")
    brief["estado"] = "ok"
    return brief


def registrar_costo(raiz: Path, brief: dict, ahora: datetime) -> None:
    if not brief.get("uso"):
        return
    ruta = Path(raiz) / "data" / "costos.json"
    costos = leer_json(ruta, {"corridas": []})
    u = brief["uso"]
    costos["corridas"].append({
        "fecha": ahora.date().isoformat(), "id": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"), "tipo": "brief",
        "semana": brief["semana"], "modelo": u.get("modelo"), "tokens_entrada": u.get("tokens_entrada", 0),
        "tokens_salida": u.get("tokens_salida", 0), "costo_usd": u.get("costo_usd", 0.0),
        "estado": brief.get("estado", "ok"),
    })
    escribir_json(ruta, costos)


def _entorno(raiz: Path) -> Environment:
    return Environment(loader=FileSystemLoader(str(Path(raiz) / "templates" / "secciones")),
                       autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


def escribir(raiz: Path, brief: dict) -> dict[str, Path]:
    """Guarda el JSON y el Markdown en data/semanal/ y publica las páginas en docs/semanal/."""
    raiz = Path(raiz)
    entorno = _entorno(raiz)
    extra = {"fecha_corta": lambda f: fecha_corta(date.fromisoformat(f))}
    datos = raiz / "data" / "semanal"
    escribir_json(datos / f"{brief['semana']}.json", brief)
    md = Environment(loader=FileSystemLoader(str(raiz / "templates" / "secciones")), autoescape=False,
                     trim_blocks=True, lstrip_blocks=True).get_template("semanal.md.j2").render(b=brief, **extra)
    escribir_texto(datos / f"{brief['semana']}.md", md)
    docs = raiz / "docs" / "semanal"
    escribir_texto(docs / f"{brief['semana']}.html", entorno.get_template("semanal.html.j2").render(b=brief, **extra))
    briefs = []
    for ruta in sorted(datos.glob("*.json"), reverse=True):
        b = leer_json(ruta)
        if isinstance(b, dict) and b.get("semana"):
            briefs.append(b)
    escribir_texto(docs / "index.html", entorno.get_template("semanal_indice.html.j2").render(briefs=briefs, **extra))
    shutil.copyfile(raiz / "templates" / "estilo.css", raiz / "docs" / "estilo.css")
    shutil.copyfile(raiz / "templates" / "secciones" / "secciones.css", raiz / "docs" / "secciones.css")
    return {"json": datos / f"{brief['semana']}.json", "md": datos / f"{brief['semana']}.md",
            "html": docs / f"{brief['semana']}.html"}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Brief semanal de Mesa Caliente")
    p.add_argument("--semana", default="", help="AAAA-Wnn (por defecto, la semana anterior completa)")
    args = p.parse_args(argv)
    ahora = ahora_utc()
    try:
        brief = generar_brief(RAIZ, ahora.date(), ahora, semana=args.semana or None)
    except ValueError as e:
        print(e, file=sys.stderr)
        return 1
    registrar_costo(RAIZ, brief, ahora)
    rutas = escribir(RAIZ, brief)
    print(f"Brief {brief['semana']}: {brief['hechos']} hechos, {brief['cambios_registrados']} cambios en salas.")
    if brief.get("uso"):
        print(f"Costo estimado US$ {brief['uso'].get('costo_usd', 0):.4f}")
    if brief.get("nota"):
        print(brief["nota"])
    print(f"SEMANA={brief['semana']}")
    print(f"MD={rutas['md'].relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
