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
from . import temas as temas_mod
from .util import RAIZ, ahora_utc, escribir_json, escribir_texto, fecha_corta, leer_json, limpiar_texto

SISTEMA_BRIEF = """Usted prepara el brief semanal interno de "Mesa Caliente" para el equipo editorial. Escriba en español neutro y formal, sin voseo (use "usted" o formas impersonales).

Recibirá el registro de la semana:
- Hechos H1, H2…: fecha | título | clasificación (si no es confirmado) | salas | jugadores (país) | torneo | país del evento | temas calientes. Solo figuran los campos con dato. Los marcados (latino) tienen protagonistas o torneos latinos.
- Ideas de artículos I1, I2…: ya elegidas por el programa, con su motivo.

Escriba:
1. "temas": hasta {max_temas} temas de la semana, cada uno con "texto" (una o dos oraciones) e "ids" (los hechos H que lo respaldan).
2. "latinos": jugadores y torneos latinos destacados, solo con hechos marcados (latino), con "texto" e "ids".
3. "ideas": para cada idea I, "id" y "texto": una oración con el ángulo posible del artículo.

Reglas:
- REGLA MÁXIMA: use solo lo que está en el registro recibido. Todo nombre, cifra, país, torneo o sala debe figurar en las líneas que cita ese elemento. No agregue contexto, fechas ni datos de su conocimiento. El programa quita las oraciones con datos que no estén en esas líneas.
- No escriba URLs: el programa agrega las fuentes.
- No use superlativos ni elogios. Conserve la jerga del poker y los nombres de eventos en inglés.

Responda únicamente con un objeto JSON, sin texto adicional:
{{"temas": [{{"texto": "...", "ids": ["H1", "H4"]}}],
 "latinos": [{{"texto": "...", "ids": ["H3"]}}], "ideas": [{{"id": "I1", "texto": "..."}}]}}"""

MAX_TEMAS = 8
CLASIFICACIONES = {"confirmado": "confirmado", "discusion": "en discusión", "rumor": "rumor"}


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


def clases_de_temas(raiz: Path, todos: list[dict]) -> dict[str, str]:
    """Clase de cada tema caliente del registro, según el archivo de donde viene:
    - «manual»: está en config/temas_calientes.json (conserva su nombre);
    - «general»: automático (data/temas_auto.json) con nombre de juego, formato, base, herramienta o sala;
    - «jugador»: automático con el nombre de una persona del campo «jugadores» del registro;
    - «automatico»: los demás automáticos;
    - «sin_origen»: no figura en ninguno de los dos archivos (por ejemplo, un tema manual ya quitado).
    Los temas «general» y «jugador» no agrupan hechos ni dan nombre a una idea de artículo."""
    manuales = {t.get("nombre") for t in temas_mod.cargar_manual(raiz).get("temas", [])}
    automaticos = {t.get("nombre") for t in (leer_json(temas_mod.ruta_auto(raiz), {}) or {}).get("temas", [])}
    generales = temas_mod.cargar_generales(raiz)
    jugadores = {calidad.sin_tildes(j["nombre"]).strip() for h in todos for j in h.get("jugadores") or []
                 if j.get("nombre")}
    clases = {}
    for nombre in {t for h in todos for t in h.get("temas_calientes") or []}:
        base = temas_mod.nombre_sin_prefijo(nombre)
        if nombre in manuales:
            clases[nombre] = "manual"
        elif nombre not in automaticos:
            clases[nombre] = "sin_origen"
        elif temas_mod.categoria_general(base, generales):
            clases[nombre] = "general"
        elif calidad.sin_tildes(base).strip() in jugadores:
            clases[nombre] = "jugador"
        else:
            clases[nombre] = "automatico"
    return clases


NO_AGRUPAN = {"general", "jugador"}


def _torneo(h: dict) -> str:
    return " ".join(calidad.sin_tildes(h.get("torneo") or "").split())


def _agrupar(semana: list[dict], clases: dict[str, str]) -> list[list[dict]]:
    """Grupos del mismo hecho o tema: comparten un tema caliente que agrupa (no «general» ni «jugador»),
    el mismo torneo o serie (el campo «torneo» igual, sin distinguir mayúsculas ni tildes), o tienen un título
    casi igual y el mismo campo «torneo» (los dos vacíos cuentan como igual): dos hechos de torneos distintos
    nunca se juntan por el título. Las tarjetas de tema caliente (sección «tema») tienen por título el nombre
    del tema, así que no se agrupan por título."""
    padre = list(range(len(semana)))

    def raiz(i):
        while padre[i] != i:
            padre[i] = padre[padre[i]]
            i = padre[i]
        return i
    temas = [{t for t in h.get("temas_calientes") or [] if clases.get(t) not in NO_AGRUPAN} for h in semana]
    for i, a in enumerate(semana):
        for j in range(i + 1, len(semana)):
            b = semana[j]
            mismo = (temas[i] & temas[j]
                     or (_torneo(a) == _torneo(b)
                         and (_torneo(a) or ("tema" not in (a.get("seccion"), b.get("seccion"))
                                             and calidad.misma_noticia(a["titulo"], b["titulo"])))))
            if mismo:
                padre[raiz(j)] = raiz(i)
    grupos: dict[int, list[dict]] = {}
    for i, h in enumerate(semana):
        grupos.setdefault(raiz(i), []).append(h)
    return list(grupos.values())


def _semanas_seguidas(tema: str, todos: list[dict], desde: date, hasta: date) -> int:
    semanas = {_semana_iso(h["fecha"]) for h in todos
               if tema in (h.get("temas_calientes") or []) and h.get("fecha", "") <= hasta.isoformat()}
    seguidas, lunes = 0, desde
    while _semana_iso(lunes.isoformat()) in semanas:
        seguidas += 1
        lunes -= timedelta(days=7)
    return seguidas


def _mas_repetido(valores: list[str]) -> str:
    return max(valores, key=lambda v: (valores.count(v), -valores.index(v)))


def _nombre_idea(grupo: list[dict], temas: list[str], clases: dict[str, str]) -> str | None:
    """Nombre de la idea (None: no es idea). `temas` son solo los que agrupan.
    - Un tema manual conserva su nombre.
    - Con un tema automático: el torneo escrito en el registro (el más repetido) o, si es un solo hecho, su
      título; sin torneo y con varios hechos, el nombre del tema.
    - Sin tema: el torneo si son varios hechos, si no el título.
    Si el nombre resulta ser el de un tema «general» o «jugador» (el título de su tarjeta), se usa el torneo
    del registro y, si no hay, no es idea."""
    torneos = [h["torneo"] for h in grupo if h.get("torneo")]
    manual = next((t for t in temas if clases.get(t) == "manual"), None)
    if manual:
        return manual
    if temas:
        nombre = _mas_repetido(torneos) if torneos else grupo[0]["titulo"] if len(grupo) == 1 else temas[0]
    else:
        nombre = torneos[0] if len(grupo) > 1 and torneos else grupo[0]["titulo"]
    if clases.get(nombre) in NO_AGRUPAN:
        return _mas_repetido(torneos) if torneos else None
    return nombre


def ideas_candidatas(todos: list[dict], desde: date, hasta: date, clases: dict[str, str] | None = None) -> list[dict]:
    """Ideas de artículos, con su motivo. Un hecho o tema entra si en la semana:
    - lo contaron 3 o más fuentes distintas, o
    - apareció en 2 o más días (el mismo hecho, tema caliente o torneo), o
    - es un tema caliente que siguió 2 semanas seguidas o más (hasta esta).
    `clases`: la de cada tema (ver clases_de_temas); sin ella, ningún tema es manual."""
    clases = clases or {}
    semana = sorted((h for h in todos if _en(h, desde, hasta)), key=lambda h: h.get("fecha") or "")
    ideas = []
    for grupo in _agrupar(semana, clases):
        temas = list(dict.fromkeys(t for h in grupo for t in h.get("temas_calientes") or []
                                   if clases.get(t) not in NO_AGRUPAN))
        nombre = _nombre_idea(grupo, temas, clases)
        if not nombre:
            continue
        dias = sorted({h["fecha"] for h in grupo})
        fuentes = {f.get("nombre") for h in grupo for f in h.get("fuentes") or []}
        seguidas = max((_semanas_seguidas(t, todos, desde, hasta) for t in temas), default=0)
        motivos = []
        if len(fuentes) >= 3:
            motivos.append(f"{len(fuentes)} fuentes distintas")
        if len(dias) >= 2:
            motivos.append(f"apareció {len(dias)} días ({', '.join(fecha_corta(date.fromisoformat(d)) for d in dias)})")
        if seguidas >= 2:
            motivos.append(f"tema caliente que siguió {seguidas} semanas seguidas")
        if motivos:
            ideas.append({"tema": nombre, "motivo": "; ".join(motivos), "hechos": grupo})
    for n, idea in enumerate(ideas, 1):
        idea["id"] = f"I{n}"
    return ideas


# ---------------------------------------------------------------- mensaje

def linea_hecho(h: dict) -> str:
    """Solo lo que el modelo usa para escribir: sin tipo, sección ni fuentes (las fuentes las pone el programa)
    y sin campos vacíos. La clasificación va solo si no es «confirmado»."""
    jugadores = ", ".join(j["nombre"] + (f" ({j['pais']})" if j.get("pais") else "") for j in h.get("jugadores") or [])
    clasificacion = h.get("clasificacion")
    partes = [h.get("fecha") or "", h.get("titulo") or "",
              CLASIFICACIONES.get(clasificacion, clasificacion or "") if clasificacion != "confirmado" else "",
              "salas: " + ", ".join(h.get("salas") or []) if h.get("salas") else "",
              "jugadores: " + jugadores if jugadores else "",
              "torneo: " + h["torneo"] if h.get("torneo") else "",
              "país del evento: " + h["pais_evento"] if h.get("pais_evento") else "",
              "temas: " + ", ".join(h["temas_calientes"]) if h.get("temas_calientes") else ""]
    return " | ".join(limpiar_texto(p) for p in partes if p)


def cambios_del_registro(cambios: list[dict], temas_salas: dict) -> list[dict]:
    """La sección «Cambios en salas» del brief: las mismas líneas del registro que cuenta el encabezado
    (misma fuente y mismo rango de fechas), con el dato tal como lo dice la fuente. No pasa por el modelo."""
    salida = []
    for c in sorted(cambios, key=lambda c: (c.get("fecha") or "", c.get("sala") or "")):
        fuente = c.get("fuente") or {}
        tema = temas_salas.get(c.get("tema"), c.get("tema") or "")
        texto = f"{c.get('sala') or ''} · {tema} · {fecha_corta(date.fromisoformat(c['fecha']))}: «{limpiar_texto(c.get('dato') or '')}»"
        salida.append({"texto": texto, "fuentes": [{"nombre": fuente.get("nombre"), "url": fuente["url"]}]
                       if fuente.get("url") else []})
    return salida


def construir_mensaje(hechos: list[dict], ideas: list[dict], etiqueta: str) -> tuple[str, dict[str, str]]:
    """Mensaje para el modelo y el texto de cada referencia (H, I) para verificar lo que escriba."""
    refs: dict[str, str] = {}
    lineas = [f"Registro de la semana {etiqueta}.", "", "Hechos:"]
    for n, h in enumerate(hechos, 1):
        refs[f"H{n}"] = linea_hecho(h)
        lineas.append(f"[H{n}]{' (latino)' if es_latino(h) else ''} {refs[f'H{n}']}")
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


def verificar(datos: dict, refs: dict, hechos: list[dict], ideas: list[dict]) -> tuple[dict, list[str]]:
    """Cada texto debe citar líneas del registro y solo puede decir lo que está en ellas."""
    controles: list[str] = []
    salida = {"temas": [], "latinos": [], "ideas": {}}

    def depurar(texto, ids, seccion):
        texto = limpiar_texto(str(texto or ""))
        if not texto or not ids:
            controles.append(f"{seccion}: elemento sin texto o sin referencias válidas")
            return ""
        limpio, quitadas = calidad.depurar_texto(texto, " ".join(refs[i] for i in ids))
        controles.extend(f"{seccion}: {q}" for q in quitadas)
        return limpio

    for seccion, prefijo in (("temas", "H"), ("latinos", "H")):
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
            fuentes = [f for i in ids for f in hechos[int(i[1:]) - 1].get("fuentes") or []]
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
    ideas = ideas_candidatas(todos, desde, hasta, clases_de_temas(raiz, todos))
    brief = {
        "semana": etiqueta, "desde": desde.isoformat(), "hasta": hasta.isoformat(),
        "generado": ahora.strftime("%Y-%m-%d %H:%M UTC"), "hechos": len(hechos), "cambios_registrados": len(cambios),
        "temas": [], "cambios": cambios_del_registro(cambios, salas["temas"]), "latinos": [],
        "fuentes_nuevas": fuentes_nuevas(todos, desde, hasta),
        "ideas": [{"tema": i["tema"], "motivo": i["motivo"], "texto": "",
                   "fuentes": [{"nombre": f.get("nombre"), "url": f.get("url")}
                               for h in i["hechos"] for f in (h.get("fuentes") or [])[:1]]} for i in ideas],
        "nota": None, "controles": [], "uso": None,
    }
    if not hechos and not cambios:
        brief["nota"] = "El registro no tiene hechos ni cambios en salas para esta semana: no se consultó al modelo."
        return brief

    if not hechos:
        brief["nota"] = "El registro no tiene hechos para esta semana: no se consultó al modelo."
        return brief
    mensaje, refs = construir_mensaje(hechos, ideas, etiqueta)
    datos, brief["uso"], brief["intentos"] = _consultar(llamar, mensaje, modelo.modelo_configurado(ajustes), ajustes)
    if datos is None:
        motivo = brief["intentos"][-1]["error"]
        brief["nota"] = (f"La llamada al modelo falló {len(brief['intentos'])} veces (última: {motivo}): "
                         "el brief solo trae lo que arma el programa.")
        brief["estado"] = "fallida"
        return brief
    salida, brief["controles"] = verificar(datos, refs, hechos, ideas)
    brief.update(temas=salida["temas"], latinos=salida["latinos"])
    for idea, base in zip(brief["ideas"], ideas):
        idea["texto"] = salida["ideas"].get(base["id"], "")
    brief["estado"] = "ok"
    return brief


def _consultar(llamar, mensaje: str, nombre_modelo: str, ajustes: dict) -> tuple[dict | None, dict | None, list]:
    """Una llamada y, si la respuesta no sirve, un solo reintento. Si la primera se cortó por el tope de tokens,
    el reintento tiene el doble de tope, sin pasar el tope de costo por corrida (max_costo_usd_por_corrida)
    sumando las dos llamadas. Devuelve (datos o None, uso sumado, detalle de cada intento)."""
    tope = ajustes.get("max_tokens_brief", 2000)
    sistema = SISTEMA_BRIEF.format(max_temas=MAX_TEMAS)
    intentos, total = [], None
    for n in (1, 2):
        intento = {"n": n, "max_tokens": tope, "tokens_salida": 0, "truncado": False, "error": None}
        intentos.append(intento)
        datos = None
        try:
            texto, uso = llamar(sistema, mensaje, nombre_modelo, tope)
        except modelo.ErrorModelo as e:
            uso, intento["error"] = e.uso, str(e)
        else:
            try:
                datos = modelo._extraer_json(texto)
            except modelo.ErrorModelo as e:
                intento["error"] = str(e) + (f" La salida se cortó por el tope de tokens ({tope})."
                                             if uso.get("truncado") else "")
        if uso:
            uso = modelo._con_costo(uso, nombre_modelo, ajustes)
            intento.update(tokens_salida=uso.get("tokens_salida", 0), truncado=bool(uso.get("truncado")))
            total = _sumar_uso(total, uso)
        if datos is not None:
            return datos, total, intentos
        if n == 1:
            tope = _tope_reintento(tope, intento["truncado"], total, nombre_modelo, ajustes)
            if not tope:
                intento["error"] += " Sin reintento: no alcanza el tope de costo por corrida."
                return None, total, intentos
    return None, total, intentos


def _tope_reintento(tope: int, truncado: bool, uso: dict | None, nombre_modelo: str, ajustes: dict) -> int:
    """Tope de tokens del reintento: el doble si la primera salida se cortó, sin que las dos llamadas juntas
    pasen max_costo_usd_por_corrida. 0 si ni el tope original entra en lo que queda."""
    nuevo = tope * 2 if truncado else tope
    limite = ajustes.get("max_costo_usd_por_corrida")
    precio_salida = ajustes.get("precios_usd_por_millon", {}).get(nombre_modelo, {}).get("salida")
    if not (limite and uso and precio_salida):
        return nuevo
    # La entrada del reintento es la misma de la primera llamada.
    disponible = limite - uso.get("costo_usd", 0) - modelo.costo_estimado(nombre_modelo, uso.get("tokens_entrada", 0), 0, ajustes)
    nuevo = min(nuevo, int(disponible * 1e6 / precio_salida))
    return nuevo if nuevo >= tope else 0


def _sumar_uso(total: dict | None, uso: dict) -> dict:
    if total is None:
        return dict(uso)
    total = dict(total)
    for clave in ("tokens_entrada", "tokens_salida"):
        total[clave] = total.get(clave, 0) + uso.get(clave, 0)
    total["costo_usd"] = round(total.get("costo_usd", 0) + uso.get("costo_usd", 0), 6)
    total["truncado"] = bool(uso.get("truncado"))  # el del último intento
    return total


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
        "estado": brief.get("estado", "ok"), "intentos": len(brief.get("intentos") or []) or 1,
    })
    escribir_json(ruta, costos)


def _entorno(raiz: Path) -> Environment:
    return Environment(loader=FileSystemLoader(str(Path(raiz) / "templates" / "secciones")),
                       autoescape=select_autoescape(["html"]), trim_blocks=True, lstrip_blocks=True)


def escribir_indice(raiz: Path) -> Path:
    """docs/semanal/index.html con todos los briefs (también vacío, para que el enlace de la página funcione)."""
    raiz = Path(raiz)
    briefs = []
    for ruta in sorted((raiz / "data" / "semanal").glob("*.json"), reverse=True):
        b = leer_json(ruta)
        if isinstance(b, dict) and b.get("semana"):
            briefs.append(b)
    html = _entorno(raiz).get_template("semanal_indice.html.j2").render(
        briefs=briefs, fecha_corta=lambda f: fecha_corta(date.fromisoformat(f)))
    escribir_texto(raiz / "docs" / "semanal" / "index.html", html)
    return raiz / "docs" / "semanal" / "index.html"


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
    escribir_indice(raiz)
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
    for i in brief.get("intentos") or []:
        print(f"Intento {i['n']}: tope {i['max_tokens']} tokens, salida {i['tokens_salida']}"
              f"{' (cortada por el tope)' if i['truncado'] else ''}: {i['error'] or 'JSON válido'}")
    if brief.get("uso"):
        print(f"Costo estimado US$ {brief['uso'].get('costo_usd', 0):.4f}")
    if brief.get("nota"):
        print(brief["nota"])
    print(f"SEMANA={brief['semana']}")
    print(f"MD={rutas['md'].relative_to(RAIZ)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
