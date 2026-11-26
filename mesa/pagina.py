"""Genera docs/index.html a partir de los datos. No sabe nada de la recolección.

Para cambiar el diseño basta con editar templates/index.html.j2 y templates/estilo.css.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import modelo
from . import registro
from . import temas as temas_mod
from .dias import cargar_dias
from .util import (MESES, RAIZ, escribir_texto, fecha_corta, fecha_larga,
                   leer_json)

ETIQUETAS = {"confirmado": "Confirmado", "discusion": "En discusión", "rumor": "Rumor"}
ORIGENES = {"automatica": "Corrida automática", "manual": "Corrida manual", "externo": "Corrida externa"}


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


def _ultima_corrida_costos(corridas: list[dict]) -> dict | None:
    """Suma las llamadas de la última corrida (selección y redacción) y separa el costo de la selección."""
    # El brief semanal y la carga del historial no son corridas del barrido: suman al mes, no a «la última corrida».
    corridas = [c for c in corridas if c.get("tipo") not in ("brief", "historial")]
    if not corridas:
        return None
    ultimo_id = corridas[-1].get("id")
    llamadas = [c for c in corridas if c.get("id") == ultimo_id] if ultimo_id else corridas[-1:]
    principal = next((c for c in llamadas if c.get("tipo", "principal") in ("principal", "redaccion")), llamadas[-1])
    seleccion = [c for c in llamadas if c.get("tipo") == "seleccion"]
    extra = [c for c in llamadas if c.get("tipo") == "recuperacion"]
    return {**principal,
            "tokens_entrada": sum(c.get("tokens_entrada", 0) for c in llamadas),
            "tokens_salida": sum(c.get("tokens_salida", 0) for c in llamadas),
            "costo_usd": sum(c.get("costo_usd", 0.0) for c in llamadas),
            "costo_seleccion": sum(c.get("costo_usd", 0.0) for c in seleccion) if seleccion else None,
            "costo_recuperacion": sum(c.get("costo_usd", 0.0) for c in extra) if extra else None}


def _costos(costos: dict, hoy: date) -> dict:
    corridas = costos.get("corridas", [])
    mes = hoy.strftime("%Y-%m")
    del_mes = [c for c in corridas if (c.get("fecha") or "").startswith(mes)]
    return {
        "ultima": _ultima_corrida_costos(corridas),
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


def meta_del_dia(dia: dict | None, meta: int) -> dict | None:
    """Cuántas tarjetas tiene el día frente a la meta y, si no se alcanzó, por qué:
    descartes de la recolección, descartes por regla fija y por fuente, y lo que hizo la selección."""
    if dia is None:
        return None
    resultado = {"cantidad": dia["cantidad"], "meta": meta, "alcanzada": dia["cantidad"] >= meta}
    if resultado["alcanzada"]:
        return resultado
    recoleccion = {"por_fecha": 0, "por_orden": 0, "por_tope": 0, "ya_vistos": 0, "por_tematica": 0, "enviados": 0,
                   "pendientes": 0}
    reglas: dict[str, int] = {}
    por_fuente: dict[str, dict[str, int]] = {}
    recuperacion = {"enviadas": 0, "tarjetas": 0, "segundo_nivel": 0, "omitida": None, "costo_usd": 0.0}
    hubo_recuperacion = False
    seleccion = {"hechos": 0, "fallida": False}
    hubo_seleccion = False
    for c in dia.get("corridas", []):
        r = c.get("recoleccion") or {}
        totales = (r.get("descartes") or {}).get("totales") or {}
        for k in ("por_fecha", "por_orden", "por_tope", "ya_vistos", "por_tematica"):
            recoleccion[k] += totales.get(k, 0)
        recoleccion["enviados"] += r.get("titulares_enviados", 0)
        recoleccion["pendientes"] += r.get("pendientes_enviados", 0)
        sel = c.get("seleccion")
        if sel:
            hubo_seleccion = True
            seleccion["hechos"] += sel.get("hechos", 0)
            seleccion["fallida"] = seleccion["fallida"] or sel.get("estado") == "fallida"
        for x in c.get("controles") or []:
            categoria = modelo.categoria_descarte(x.get("motivo") or "")
            if not categoria:
                continue
            reglas[categoria] = reglas.get(categoria, 0) + 1
            for f in x.get("fuentes") or ([x["fuente"]] if x.get("fuente") else []):
                fila = por_fuente.setdefault(f, {})
                fila[categoria] = fila.get(categoria, 0) + 1
        rec = (c.get("meta") or {}).get("recuperacion")
        if rec:
            hubo_recuperacion = True
            for k in ("enviadas", "tarjetas", "segundo_nivel", "costo_usd"):
                recuperacion[k] += rec.get(k, 0) or 0
            recuperacion["omitida"] = recuperacion["omitida"] or rec.get("omitida")
    nombres = modelo.NOMBRES_CATEGORIAS
    resultado.update({
        "recoleccion": recoleccion,
        "reglas": [{"nombre": nombres[k], "cantidad": n} for k, n in sorted(reglas.items(), key=lambda kv: -kv[1])],
        "por_fuente": [{"fuente": f, "detalle": ", ".join(f"{nombres[k]} ({n})" for k, n in sorted(v.items(), key=lambda kv: -kv[1]))}
                       for f, v in sorted(por_fuente.items(), key=lambda kv: -sum(kv[1].values()))],
        "recuperacion": recuperacion if hubo_recuperacion else None,
        "seleccion": seleccion if hubo_seleccion else None,
    })
    return resultado


def salidas_cortadas(dia: dict | None) -> list[dict]:
    """Corridas del día cuya redacción se cortó por el tope de tokens, con las tarjetas perdidas."""
    return [{"corrida": c.get("titulo_mostrar") or c.get("id"), "perdidas": c["salida_cortada"].get("perdidas")}
            for c in (dia or {}).get("corridas", []) if c.get("salida_cortada")]


def unir_temas(corridas: list[dict]) -> list[dict]:
    """Una sola tarjeta por tema caliente y por día: junta las novedades de todas las corridas del día
    (en orden) y usa la línea principal más reciente."""
    temas: dict[str, dict] = {}
    for c in corridas:  # orden cronológico
        for t in c.get("temas") or []:
            actual = temas.setdefault(t["tema"], {"tema": t["tema"], "linea": "", "novedades": []})
            if t.get("linea"):
                actual["linea"] = t["linea"]
            vistas = {(n.get("url"), n.get("aporta")) for n in actual["novedades"]}
            actual["novedades"] += [n for n in t.get("novedades", []) if (n.get("url"), n.get("aporta")) not in vistas]
    return [t for t in temas.values() if t["novedades"]]


# ---------------------------------------------------------------- ediciones manuales y páginas de noticia

CAMPOS_EDITABLES = ("titulo", "resumen", "cuerpo", "imagen", "imagenes", "etiqueta", "secciones", "oculta", "destacada",
                    "instagram")

CATEGORIAS = {"resultado": "Resultados", "anuncio_evento": "Torneos y eventos", "industria_polemica": "Polémica e industria",
              "software": "Software", "obituario": "Obituario", "oferta": "Ofertas", "otro": "Noticias"}


def cargar_sitio(raiz: Path) -> dict:
    """config/sitio.json: menú, secciones y botones de la portada (se editan desde el administrador)."""
    sitio = leer_json(Path(raiz) / "config" / "sitio.json", {}) or {}
    sitio.setdefault("marca", {"nombre": "Mesa Caliente", "lema": ""})
    sitio.setdefault("secciones", [])
    sitio.setdefault("botones", [])
    return sitio


def tipos_del_registro(raiz: Path) -> dict:
    """id de tarjeta -> tipo (resultado, oferta…), desde data/registro/hechos.jsonl."""
    ruta = Path(raiz) / "data" / "registro" / "hechos.jsonl"
    tipos = {}
    if ruta.exists():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            try:
                h = json.loads(linea)
            except ValueError:
                continue
            if h.get("id"):
                tipos[h["id"]] = h.get("tipo") or "otro"
    return tipos


def avisos_sei(dia: dict | None) -> list[str]:
    """Avisos de documentos de SEI faltantes en las corridas del día (solo el estado, nunca el contenido)."""
    return list(dict.fromkeys(c["documentos_sei"]["aviso"] for c in (dia or {}).get("corridas", [])
                              if (c.get("documentos_sei") or {}).get("aviso")))


def secciones_de(t: dict, sitio: dict) -> list[str]:
    """Secciones de una tarjeta: las asignadas a mano y las que cumplen alguna condición de su regla."""
    salida = list(t.get("secciones") or [])
    texto = f"{t.get('titulo', '')} {t.get('resumen', '')}".lower()
    valores = {"seccion": t.get("seccion"), "region": t.get("region") or ("europa" if t.get("seccion") == "europa" else ""),
               "tipo": t.get("tipo"), "clasificacion": t.get("clasificacion")}
    for sec in sitio.get("secciones", []):
        if not sec.get("visible", True) or sec["id"] in salida:
            continue
        regla = sec.get("regla") or {}
        if any(valores.get(k) in (v or []) for k, v in regla.items() if k != "palabras") or \
                any(p.lower() in texto for p in regla.get("palabras") or [] if p.strip()):
            salida.append(sec["id"])
    visibles = {s["id"] for s in sitio.get("secciones", []) if s.get("visible", True)}
    return [x for x in salida if x in visibles]


def datos_portada(ctx: dict, sitio: dict, tipos: dict) -> list[dict]:
    """Noticias de los últimos días para la portada (JSON dentro de la página; la portada filtra por sección)."""
    dias = sitio.get("dias_en_portada", 30)
    limite = (date.fromisoformat(ctx["dias"][0]["fecha"]) - timedelta(days=dias)).isoformat() if ctx["dias"] else ""
    nombres = {s["id"]: s["nombre"] for s in sitio.get("secciones", [])}
    salida = []
    for t in todas_las_tarjetas(ctx):
        if t["dia"] < limite:
            continue
        t["tipo"] = "tema" if t["seccion"] == "tema" else tipos.get(t["nid"], t.get("tipo") or "otro")
        secs = secciones_de(t, sitio)
        imagenes = t.get("imagenes") or ([{"url": t["imagen"], "fuente": (t.get("fuentes") or [{}])[0].get("nombre", "")}]
                                         if t.get("imagen") else [])
        fuentes = [f.get("nombre") for f in t.get("fuentes", []) if f.get("nombre")] or \
                  [n.get("fuente") for n in t.get("novedades", []) if n.get("fuente")]
        manual = [nombres[x] for x in t.get("secciones") or [] if x in nombres]
        salida.append({"id": t["nid"], "dia": t["dia"], "titulo": t["titulo"], "bajada": t.get("resumen") or "",
                       "foto": imagenes[0]["url"] if imagenes else "", "credito": imagenes[0].get("fuente", "") if imagenes else "",
                       "cat": t.get("etiqueta") or (manual[0] if manual else ("Tema caliente" if t["tipo"] == "tema" else
                                                                              CATEGORIAS.get(t["tipo"], "Noticias"))),
                       "sec": t["seccion"], "region": t.get("region") or "", "secs": secs,
                       "destacada": bool(t.get("destacada")), "fuente": fuentes[0] if fuentes else "",
                       "nf": len(dict.fromkeys(fuentes))})
    salida.sort(key=lambda x: x["dia"], reverse=True)
    return salida


def id_tema(fecha: str, tema: str) -> str:
    return f"{fecha}-tema-{hashlib.sha1(tema.encode('utf-8')).hexdigest()[:8]}"


def cargar_ediciones(raiz: Path) -> dict:
    """data/ediciones.json: cambios hechos a mano desde el administrador, por id de tarjeta.
    Cada edición guarda también el texto original, para que SEI aprenda de las correcciones."""
    return (leer_json(Path(raiz) / "data" / "ediciones.json", {}) or {}).get("tarjetas", {})


def preparar_tarjeta(t: dict, nid: str, seccion: str, fecha: str, ediciones: dict) -> dict:
    """Copia de la tarjeta con su id, su sección y las ediciones manuales aplicadas (el dato del día no se toca)."""
    x = {**t, "nid": nid, "seccion": seccion, "dia": fecha, "original_titulo": t.get("titulo") or t.get("tema"),
         "original_resumen": t.get("resumen") or "", "original_cuerpo": t.get("cuerpo") or "",
         "original_imagenes": t.get("imagenes") or ([{"url": t["imagen"], "fuente": (t.get("fuentes") or [{}])[0].get("nombre", ""),
                                                      "enlace": (t.get("fuentes") or [{}])[0].get("url", "")}] if t.get("imagen") else [])}
    ed = ediciones.get(nid) or {}
    for campo in CAMPOS_EDITABLES:
        if campo in ed and ed[campo] not in (None, ""):
            x[campo] = ed[campo]
    if "oculta" in ed:
        x["oculta"] = bool(ed["oculta"])
    x["editada"] = bool(ed)
    return x


def contexto(raiz: Path, hoy: date, generado: datetime) -> dict:
    raiz = Path(raiz)
    ajustes = leer_json(raiz / "config" / "ajustes.json", {})
    fuentes = leer_json(raiz / "config" / "fuentes.json", {"fuentes": []})["fuentes"]
    candidatas = leer_json(raiz / "data" / "candidatas.json", {"candidatas": []}).get("candidatas", [])
    costos = leer_json(raiz / "data" / "costos.json", {"corridas": []})
    abiertos = ajustes.get("dias_abiertos_en_pagina", 3)

    ediciones = cargar_ediciones(raiz)
    ocultas = []  # siguen en el administrador (para poder mostrarlas de nuevo), no en la página
    def visibles(lista):
        lista = list(lista)
        ocultas.extend(x for x in lista if x.get("oculta"))
        return [x for x in lista if not x.get("oculta")]
    dias = []
    total_tarjetas = 0
    for n, d in enumerate(cargar_dias(raiz)):
        corridas = []
        for c in reversed(d.get("corridas", [])):  # la más reciente primero
            c = {**c, "titulo_mostrar": _titulo_corrida(c)}
            prep = lambda lista, sec: visibles(preparar_tarjeta(t, registro.id_hecho(d["fecha"], c.get("id", ""), t["titulo"]),
                                                                sec, d["fecha"], ediciones) for t in lista)
            c["tarjetas"] = prep(c.get("tarjetas", []), "general")
            if c.get("escena_latam"):
                c["escena_latam"] = {**c["escena_latam"], "tarjetas": prep(c["escena_latam"].get("tarjetas", []), "latam")}
            corridas.append(c)
        cantidad = sum(len(c.get("tarjetas", [])) + len((c.get("escena_latam") or {}).get("tarjetas", []))
                       for c in corridas)
        total_tarjetas += cantidad
        temas_dia = visibles(preparar_tarjeta({**t, "titulo": t["tema"], "resumen": t.get("linea", "")},
                                              id_tema(d["fecha"], t["tema"]), "tema", d["fecha"], ediciones)
                             for t in unir_temas(d.get("corridas", [])))
        cantidad += len(temas_dia)
        total_tarjetas += len(temas_dia)
        dias.append({**d, "corridas": corridas, "abierto": n < abiertos, "cantidad": cantidad, "temas": temas_dia})

    return {
        "dias": dias,
        "ocultas": ocultas,
        "total_tarjetas": total_tarjetas,
        "fuentes_activas": [f["nombre"] for f in fuentes if f.get("estado") != "en_pausa"],
        "salud": _salud(fuentes, ajustes.get("dias_pausa_reintento", 7)),
        "candidatas": sorted(candidatas, key=lambda c: c.get("detectada") or "", reverse=True),
        "costos": _costos(costos, hoy),
        "descartes": _ultimo_registro(dias),
        "salida_cortada": salidas_cortadas(next((d for d in dias if d["fecha"] == hoy.isoformat()), None)),
        "aviso_sei": avisos_sei(next((d for d in dias if d["fecha"] == hoy.isoformat()), None)),
        "meta_hoy": meta_del_dia(next((d for d in dias if d["fecha"] == hoy.isoformat()), None),
                                 ajustes.get("meta_diaria", 10)),
        "temas_vigilancia": temas_mod.activos(raiz),
        # Secciones nuevas: salen de data/registro/ y se dibujan con templates/secciones/.
        "cambios_salas": registro.cambios_recientes(raiz, hoy, ajustes.get("dias_cambios_salas_en_pagina", 30)),
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
    ctx = contexto(raiz, hoy, generado)
    # historial.html: la vista completa por día (la página de siempre, con salud de fuentes, costos y registro).
    escribir_texto(salida / "historial.html", entorno.get_template("index.html.j2").render(**ctx))
    # Secciones de cada noticia (regla de config/sitio.json + asignación manual): van en docs/datos/tarjetas.json
    # para filtrar en el administrador y para que BIBLIO y SEI puedan buscar por sección.
    sitio, tipos = cargar_sitio(raiz), tipos_del_registro(raiz)
    secciones_por_id = {}
    for t in todas_las_tarjetas(ctx) + ctx.get("ocultas", []):
        t["tipo"] = "tema" if t["seccion"] == "tema" else tipos.get(t["nid"], t.get("tipo") or "otro")
        secciones_por_id[t["nid"]] = secciones_de(t, sitio)
    escribir_noticias(entorno, ctx, salida, plantillas, secciones_por_id)
    # index.html: la portada estilo portal, con el menú y las secciones de config/sitio.json.
    if (plantillas / "portada.html.j2").exists():
        tarjetas = datos_portada(ctx, sitio, tipos)
        escribir_texto(salida / "index.html", entorno.get_template("portada.html.j2").render(
            sitio=sitio, tarjetas=tarjetas, hoy_texto=ctx["hoy_texto"], salud=ctx["salud"], generado=ctx["generado"],
            aviso_sei=ctx.get("aviso_sei", [])))
        shutil.copyfile(plantillas / "portada.css", salida / "portada.css")
    else:
        escribir_texto(salida / "index.html", entorno.get_template("index.html.j2").render(**ctx))
    shutil.copyfile(plantillas / "estilo.css", salida / "estilo.css")
    if (plantillas / "secciones" / "secciones.css").exists():
        shutil.copyfile(plantillas / "secciones" / "secciones.css", salida / "secciones.css")
    if (plantillas / "secciones" / "semanal_indice.html.j2").exists() and salida == raiz / "docs":
        from .semanal import escribir_indice  # índice de briefs: el enlace «Brief semanal» nunca queda roto
        escribir_indice(raiz)
    (salida / ".nojekyll").touch()
    return salida / "index.html"


def todas_las_tarjetas(ctx: dict) -> list[dict]:
    lista = []
    for d in ctx["dias"]:
        for c in d["corridas"]:
            lista += c.get("tarjetas", []) + (c.get("escena_latam") or {}).get("tarjetas", [])
        lista += d.get("temas", [])
    return lista


def escribir_noticias(entorno, ctx: dict, salida: Path, plantillas: Path, secciones_por_id: dict | None = None) -> int:
    """Una página por noticia (docs/n/<id>.html): resumen dentro de Mesa Caliente que cierra con las fuentes.
    Además, docs/datos/tarjetas.json para el administrador (últimos 60 días, con el texto original)."""
    if not (plantillas / "noticia.html.j2").exists():
        return 0
    plantilla = entorno.get_template("noticia.html.j2")
    tarjetas = todas_las_tarjetas(ctx)
    carpeta = salida / "n"
    for t in ctx.get("ocultas", []):
        (carpeta / f"{t['nid']}.html").unlink(missing_ok=True)
    for t in tarjetas:
        escribir_texto(carpeta / f"{t['nid']}.html", plantilla.render(t=t, etiquetas=ctx["etiquetas"],
                                                                      fecha_larga_iso=_fecha_larga_iso,
                                                                      generado=ctx["generado"]))
    if (plantillas / "noticia.css").exists():
        shutil.copyfile(plantillas / "noticia.css", salida / "noticia.css")
    limite = (date.fromisoformat(ctx["dias"][0]["fecha"]) - timedelta(days=60)).isoformat() if ctx["dias"] else ""
    datos = [{"id": t["nid"], "dia": t["dia"], "seccion": t["seccion"], "titulo": t.get("original_titulo"),
              "resumen": t.get("original_resumen"), "clasificacion": t.get("clasificacion"),
              "imagen": t.get("imagen"), "region": t.get("region"), "oculta": bool(t.get("oculta")),
              "cuerpo": t.get("original_cuerpo") or "", "imagenes": t.get("original_imagenes") or [],
              "tipo": t.get("tipo") or "", "secs": (secciones_por_id or {}).get(t["nid"], []),
              "revisar": t.get("revisar") or [],
              "fuentes": [{"nombre": f.get("nombre"), "url": f.get("url")} for f in t.get("fuentes", [])]
                         or [{"nombre": n.get("fuente"), "url": n.get("url")} for n in t.get("novedades", [])]}
             for t in tarjetas + ctx.get("ocultas", []) if t["dia"] >= limite]
    datos.sort(key=lambda x: x["dia"], reverse=True)
    escribir_texto(salida / "datos" / "tarjetas.json", json.dumps({"tarjetas": datos}, ensure_ascii=False))
    return len(tarjetas)


def _fecha_larga_iso(iso: str) -> str:
    try:
        return fecha_larga(date.fromisoformat(iso))
    except (TypeError, ValueError):
        return iso or ""


if __name__ == "__main__":
    from .util import ahora_utc
    ahora = ahora_utc()
    print(generar(RAIZ, ahora.date(), ahora))
    sys.exit(0)
