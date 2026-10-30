"""Corrida diaria completa: recolecta, elige hechos (1.ª llamada), abre sus artículos,
redacta las tarjetas (2.ª llamada), guarda y genera la página.

Uso:
  python -m mesa.corrida                 # corrida normal
  python -m mesa.corrida --origen manual
  python -m mesa.corrida --solo-recoleccion   # prueba de fuentes, no guarda nada
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import candidatas as cand
from . import temas as temas_mod
from . import torneos
from . import modelo, pagina, recoleccion
from . import registro as registro_mod
from .dias import agregar_corrida, cargar_dias, ruta_dia
from .util import (RAIZ, ahora_utc, escribir_json, esta_bloqueado, leer_json,
                   normalizar_url)
from .pagina import unir_temas

TEXTO_LATAM_VACIO = "Sin novedades relevantes de la escena argentina y latinoamericana en esta corrida."


ESTADOS_EXITOSOS = ("ok", "sin_noticias")


def ultima_corrida_exitosa(raiz: Path, antes_de: datetime) -> datetime | None:
    """Hora de inicio de la última corrida exitosa (las importadas no cuentan)."""
    for d in cargar_dias(raiz):  # del día más reciente al más antiguo
        for c in reversed(d.get("corridas", [])):
            if c.get("origen") == "importada" or c.get("estado") not in ESTADOS_EXITOSOS:
                continue
            try:
                momento = datetime.strptime(c["id"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (KeyError, ValueError):
                continue
            if momento < antes_de:
                return momento
    return None


def calcular_corte(raiz: Path, ahora: datetime, ajustes: dict) -> tuple[datetime, str]:
    """Desde cuándo se toman titulares: la última corrida exitosa menos el margen,
    sin ir más atrás que el máximo de días."""
    margen = timedelta(hours=ajustes.get("margen_horas", 3))
    minimo = ahora - timedelta(days=ajustes.get("dias_maximos_atras", 3))
    ultima = ultima_corrida_exitosa(raiz, ahora)
    if ultima is None:
        return minimo, f"sin corridas exitosas previas: {ajustes.get('dias_maximos_atras', 3)} días hacia atrás"
    if ultima - margen < minimo:
        return minimo, f"última corrida exitosa ({ultima:%Y-%m-%d %H:%M} UTC) muy antigua: tope de {ajustes.get('dias_maximos_atras', 3)} días"
    return ultima - margen, f"última corrida exitosa ({ultima:%Y-%m-%d %H:%M} UTC) menos {ajustes.get('margen_horas', 3)} horas"


def contar_descartes(informe: list[dict], items: list[dict], enviados: list[dict]) -> dict:
    """Suma al tope por fuente lo que quedó fuera por el tope total y arma el resumen."""
    enviados_ids = {id(it) for it in enviados}
    fuera_por_total: dict[str, int] = {}
    for it in items:
        if id(it) not in enviados_ids:
            fuera_por_total[it["fuente"]] = fuera_por_total.get(it["fuente"], 0) + 1
    por_fuente, totales = {}, {"por_fecha": 0, "por_orden": 0, "por_tope": 0, "por_dia1": 0, "ya_vistos": 0,
                               "por_tematica": 0}
    for x in informe:
        if x.get("resultado") != "ok":
            continue
        x["por_tope_total"] = fuera_por_total.get(x["fuente"], 0)
        x["enviados"] = x["nuevos"] - x["por_tope_total"]
        fila = {"modo": x["modo"], "por_fecha": x["por_fecha"], "por_orden": x["por_orden"],
                "por_tope": x["por_tope"] + x["por_tope_total"], "por_dia1": x.get("por_dia1", 0),
                "ya_vistos": x["ya_vistos"], "por_tematica": x.get("por_tematica", 0), "enviados": x["enviados"]}
        por_fuente[x["fuente"]] = fila
        for k in totales:
            totales[k] += fila[k]
    return {"totales": totales, "por_fuente": por_fuente}


def guardar_entradas(raiz: Path, dia: date, corrida_id: str, enviados: list[dict], tipo: str = "principal") -> None:
    """Auditoría: lo que se envió al modelo, por nota (fuente, URL, titular y primera frase, máx. 200 caracteres)."""
    ruta = Path(raiz) / "data" / "entradas" / f"{dia.isoformat()}.json"
    datos = leer_json(ruta) or {"fecha": dia.isoformat(), "corridas": []}
    notas = []
    for n, it in enumerate(enviados, 1):
        nota = {"n": n, "fuente": it["fuente"], "url": it["url"], "titular": (it.get("titulo") or "")[:200],
                "primera_frase": (it.get("primera_linea") or "")[:200]}
        if it.get("extracto"):
            nota["con_extracto"] = True  # además se envió un extracto del artículo abierto
        if it.get("tema"):
            nota["tema"] = it["tema"]
        notas.append(nota)
    registro = {"id": corrida_id, "notas": notas}
    if tipo != "principal":
        registro["tipo"] = tipo
    datos["corridas"].append(registro)
    escribir_json(ruta, datos)


def publicado_por_tema(raiz: Path, hoy: date, temas: list[dict], dias: int = 7) -> dict[str, str]:
    """Texto ya publicado de cada tema caliente: sus tarjetas de tema y las tarjetas que lo mencionan."""
    desde = (hoy - timedelta(days=dias)).isoformat()
    textos: dict[str, list[str]] = {t["nombre"]: [] for t in temas}
    for d in cargar_dias(raiz):
        if not (desde <= d["fecha"] <= hoy.isoformat()):
            continue
        for c in d.get("corridas", []):
            for tema in c.get("temas") or []:
                if tema.get("tema") in textos:
                    textos[tema["tema"]] += [tema.get("linea", "")] + [n.get("aporta", "") for n in tema.get("novedades", [])]
            for t in c.get("tarjetas", []) + ((c.get("escena_latam") or {}).get("tarjetas") or []):
                texto = f"{t['titulo']} {t['resumen']} {t.get('novedad') or ''}"
                for tema in temas:
                    if temas_mod.coincide(texto, tema["palabras_clave"]):
                        textos[tema["nombre"]].append(texto)
    return {k: " ".join(v) for k, v in textos.items()}


def titulos_recientes(raiz: Path, hoy: date, dias: int = 3) -> list[str]:
    """Títulos de las tarjetas publicadas en los últimos días (incluye corridas previas de hoy)."""
    desde = (hoy - timedelta(days=dias)).isoformat()
    titulos = []
    for d in cargar_dias(raiz):  # del más reciente al más antiguo
        if d["fecha"] < desde or d["fecha"] > hoy.isoformat():
            continue
        for c in reversed(d.get("corridas", [])):
            for t in c.get("tarjetas", []) + ((c.get("escena_latam") or {}).get("tarjetas") or []):
                if t.get("titulo") and t["titulo"] not in titulos:
                    titulos.append(t["titulo"])
    return titulos


def previos_recientes(raiz: Path, hoy: date, dias: int = 5) -> list[dict]:
    """Tarjetas ya publicadas: las de hoy con su resumen (primero) y las de días anteriores solo con el título."""
    desde = (hoy - timedelta(days=dias)).isoformat()
    previos, vistos = [], set()
    for d in cargar_dias(raiz):  # del día más reciente al más antiguo
        if d["fecha"] < desde or d["fecha"] > hoy.isoformat():
            continue
        es_hoy = d["fecha"] == hoy.isoformat()
        for c in reversed(d.get("corridas", [])):
            for t in c.get("tarjetas", []) + ((c.get("escena_latam") or {}).get("tarjetas") or []):
                if t.get("titulo") and t["titulo"] not in vistos:
                    vistos.add(t["titulo"])
                    previos.append({"titulo": t["titulo"], "resumen": t.get("resumen", "") if es_hoy else "",
                                    "texto": f"{t['titulo']} {t.get('resumen', '')} {t.get('novedad') or ''}",
                                    "hoy": es_hoy})
    return previos


def hubo_corrida_exitosa(raiz: Path, dia: date) -> bool:
    """¿Ya hubo hoy una corrida exitosa (automática o manual)? Las importadas no cuentan."""
    datos = leer_json(ruta_dia(raiz, dia)) or {}
    return any(c.get("origen") != "importada" and c.get("estado") in ESTADOS_EXITOSOS
               for c in datos.get("corridas", []))


def _abrir_articulos(items: list[dict], ajustes: dict, descargar, estado_cand: dict, hoy: date,
                     tope: int | None = None) -> int:
    """Abre (como máximo el tope) los artículos cuyo titular no alcanza para entenderlos.
    Primero los titulares vagos (sin nombre ni cifra), después los que no traen primera línea."""
    if tope is None:
        tope = ajustes.get("max_articulos_abiertos", 6)
    abiertos = 0
    candidatos = sorted(items, key=lambda it: 0 if recoleccion.titular_vago(it) else 1)
    for it in candidatos:
        if abiertos >= tope:
            break
        if not (recoleccion.titular_vago(it) or recoleccion.necesita_texto(it)) or esta_bloqueado(it["url"]):
            continue
        abiertos += 1
        try:
            r = descargar(it["url"])
        except recoleccion.ErrorDescarga:
            continue
        if r.estado != 200:
            continue
        try:
            extracto, enlaces = recoleccion.extraer_articulo(r.texto, r.url or it["url"],
                                                             ajustes.get("max_caracteres_extracto", 900))
        except Exception:
            continue
        if extracto:
            it["extracto"] = extracto
        cand.registrar_menciones(estado_cand, it["url"], enlaces, hoy)
    return abiertos


def _abrir_para_redaccion(redaccion: list[dict], grupos: list[dict], ajustes: dict, descargar,
                          estado_cand: dict, hoy: date) -> int:
    """Abre el artículo de cada hecho elegido (una nota por hecho, de un medio u oficial si la hay),
    para que la redacción tenga el texto real y los controles puedan rastrear cada dato."""
    tope = ajustes.get("max_articulos_abiertos", 20)
    a_abrir = []
    for g in grupos:
        notas = [redaccion[i - 1] for i in g["ids"]]
        if any(it.get("extracto") for it in notas):
            continue
        candidatas = [it for it in notas if "#t-" not in it["url"] and not esta_bloqueado(it["url"])]
        candidatas.sort(key=lambda it: 0 if it.get("clase", "medio") in ("oficial", "medio") else 1)
        # Primero la nota con menos texto: es la que más necesita el artículo.
        if candidatas:
            a_abrir.append(candidatas[0])
    # Notas de temas calientes con titular insuficiente.
    a_abrir += [it for it in redaccion if it.get("tema") and not it.get("extracto")
                and (recoleccion.titular_vago(it) or recoleccion.necesita_texto(it))]
    abiertos = 0
    for it in a_abrir:
        if abiertos >= tope:
            break
        abiertos += 1
        try:
            r = descargar(it["url"])
        except recoleccion.ErrorDescarga:
            continue
        if r.estado != 200:
            continue
        try:
            extracto, enlaces = recoleccion.extraer_articulo(r.texto, r.url or it["url"],
                                                             ajustes.get("max_caracteres_extracto", 1200))
        except Exception:
            continue
        if extracto:
            it["extracto"] = extracto
        cand.registrar_menciones(estado_cand, it["url"], enlaces, hoy)
    return abiertos


def registrar_enlaces_compartidos(leidos: list[dict], estado_cand: dict, hoy: date) -> int:
    """Fuentes de descubrimiento (Reddit): los sitios que comparten sus usuarios suman como menciones
    para las fuentes candidatas. Nada se agrega solo a la lista de fuentes."""
    total = 0
    for bloque in leidos:
        if not bloque.get("descubrimiento"):
            continue
        for it in bloque["items"]:
            if it.get("enlaces"):
                cand.registrar_menciones(estado_cand, it["url"], it["enlaces"], hoy)
                total += len(it["enlaces"])
    return total


# ---------------------------------------------------------------- meta diaria

def tarjetas_publicadas_hoy(raiz: Path, hoy: date, corrida: dict | None = None) -> int:
    """Tarjetas publicadas en el día, sumando todas sus corridas (y la corrida en curso, si se da).
    Cada tema caliente cuenta una sola vez por día, como se muestra en la página."""
    corridas = list((leer_json(ruta_dia(raiz, hoy)) or {}).get("corridas", []))
    if corrida is not None:
        corridas.append(corrida)
    total = sum(len(c.get("tarjetas") or []) + len((c.get("escena_latam") or {}).get("tarjetas") or [])
                for c in corridas)
    return total + len(unir_temas(corridas))


def urls_publicadas(raiz: Path, hoy: date, dias: int = 3) -> set[str]:
    """URLs ya citadas en tarjetas o temas de los últimos días."""
    desde = (hoy - timedelta(days=dias)).isoformat()
    urls = set()
    for d in cargar_dias(raiz):
        if not (desde <= d["fecha"] <= hoy.isoformat()):
            continue
        for c in d.get("corridas", []):
            for t in (c.get("tarjetas") or []) + ((c.get("escena_latam") or {}).get("tarjetas") or []):
                urls.update(normalizar_url(f["url"]) for f in t.get("fuentes", []) if f.get("url"))
            for tema in c.get("temas") or []:
                urls.update(normalizar_url(n["url"]) for n in tema.get("novedades", []) if n.get("url"))
    return urls


def notas_pendientes(leidos: list[dict], ahora: datetime, vistos: dict, excluidas: set[str],
                     horas: int, tope_fuente: int, prioridad=None, descartar_dia1=None) -> list[dict]:
    """Notas de las últimas `horas` aún no publicadas ni enviadas en esta corrida (pendientes).

    - Fuentes con fecha propia: solo lo publicado dentro de la ventana.
    - Fuentes sin fecha (modo orden): lo no visto antes o visto por primera vez dentro de la ventana.
    Se excluyen dominios prohibidos, notas de temas calientes (ya van en la llamada principal)
    y avances de día 1 de torneos que no están en la lista. Rige el tope de cada fuente."""
    desde = ahora - timedelta(hours=horas)
    desde_dia = desde.date().isoformat()
    resultado, ya = [], set(excluidas)
    for bloque in leidos:
        items = bloque["items"]
        modo = recoleccion.modo_de_lectura(items)
        tomados = []
        for it in items:
            norm = normalizar_url(it["url"])
            if norm in ya or esta_bloqueado(it["url"]):
                continue
            if modo == "fecha":
                f = datetime.fromisoformat(it["fecha"])
                if f < desde or f > ahora + timedelta(days=1):
                    continue
            else:
                visto = vistos.get(norm)
                if visto is not None and visto < desde_dia:
                    continue
                it = dict(it, fecha=None, hora=None) if it.get("fecha") else it
            if prioridad and prioridad(it):
                continue
            if descartar_dia1 and descartar_dia1(it):
                continue
            ya.add(norm)
            tomados.append(dict(it, pendiente=True))
        resultado += tomados[:bloque.get("tope", tope_fuente)]
    return resultado


def sumar_uso(*usos: dict | None) -> dict | None:
    """Uso total de la corrida (selección + redacción)."""
    usos = [u for u in usos if u]
    if not usos:
        return None
    total = {"tokens_entrada": sum(u.get("tokens_entrada", 0) for u in usos),
             "tokens_salida": sum(u.get("tokens_salida", 0) for u in usos),
             "modelo": usos[-1].get("modelo"),
             "costo_usd": round(sum(u.get("costo_usd", 0.0) for u in usos), 6)}
    return total


def _registrar_costo(costos: dict, hoy: date, corrida: dict, uso: dict, tipo: str) -> None:
    costos["corridas"].append({
        "fecha": hoy.isoformat(),
        "id": corrida["id"],
        "tipo": tipo,
        "modelo": uso.get("modelo"),
        "tokens_entrada": uso.get("tokens_entrada", 0),
        "tokens_salida": uso.get("tokens_salida", 0),
        "costo_usd": uso.get("costo_usd", 0.0),
        "estado": corrida["estado"],
    })


def registrar_hechos(raiz: Path, hoy: date, corrida: dict, resultado: dict, redaccion: list[dict],
                     salas: dict, temas_activos: list[dict]) -> dict:
    """Registro permanente: una línea por tarjeta o tema publicado y los cambios de las salas vigiladas.
    Solo agrega archivos en data/registro/: nunca cambia las tarjetas ni la página del día."""
    fecha = hoy.isoformat()
    hechos = []
    for f in resultado.get("fichas") or []:
        if "tema" in f:
            hechos.append(registro_mod.hecho_de_tema(fecha, corrida["id"], f["tema"], f["crudo"], f["items"], salas))
        else:
            hechos.append(registro_mod.hecho_de_tarjeta(fecha, corrida["id"], f["tarjeta"], f["seccion"], f["crudo"],
                                                        f["items"], salas, temas_activos))
    cambios, descartados = [], []
    for crudo in resultado.get("cambios_salas") or []:
        cambio, motivo = registro_mod.verificar_cambio(crudo, redaccion, salas, fecha)
        if cambio:
            cambios.append(cambio)
        else:
            descartados.append({"motivo": f"cambio en sala descartado: {motivo}"})
    nuevos_hechos, nuevos_cambios = registro_mod.registrar(raiz, hechos, cambios)
    return {"hechos": nuevos_hechos, "cambios_salas": nuevos_cambios, "descartados": descartados}


def ejecutar(raiz: Path, ahora: datetime, origen: str = "automatica",
             descargar=recoleccion.descargar_http, llamar=modelo.llamar_anthropic,
             generar_pagina: bool = True) -> dict:
    raiz = Path(raiz)
    hoy = ahora.date()
    ajustes = leer_json(raiz / "config" / "ajustes.json", {})
    ruta_fuentes = raiz / "config" / "fuentes.json"
    conf_fuentes = leer_json(ruta_fuentes, {"fuentes": []})
    fuentes = conf_fuentes["fuentes"]
    ruta_vistos = raiz / "data" / "vistos.json"
    vistos = leer_json(ruta_vistos, {"urls": {}})
    ruta_cand = raiz / "data" / "candidatas.json"
    estado_cand = leer_json(ruta_cand, {"candidatas": [], "menciones": []})
    ruta_costos = raiz / "data" / "costos.json"
    costos = leer_json(ruta_costos, {"corridas": []})

    corrida = {
        "id": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "origen": origen,
        "estado": "ok",
        "motivo": None,
        "tarjetas": [],
        "escena_latam": {"tarjetas": [], "texto": None},
        "notas": [],
        "uso": None,
        "recoleccion": {},
    }

    try:
        corte, motivo_corte = calcular_corte(raiz, ahora, ajustes)
        temas_activos = temas_mod.activos(raiz)
        salas = registro_mod.cargar_salas(raiz)
        prioridad = temas_mod.detector(temas_activos)
        descartar_dia1 = torneos.descartador_dia1(torneos.cargar_relevantes(raiz))
        vistos_antes = dict(vistos["urls"])
        leidos: list[dict] = []
        items, informe, recogidos = recoleccion.recolectar(
            fuentes, hoy, ahora, set(vistos["urls"]), ajustes, descargar, corte,
            prioridad=prioridad, descartar_dia1=descartar_dia1, leidos=leidos)
        ventana = recoleccion.seleccionar(items, ajustes.get("max_titulares_enviados", 120))
        descartes = contar_descartes(informe, items, ventana)
        corrida["controles"] = [{"motivo": "descartes", "fuente": f, **fila}
                                for f, fila in descartes["por_fuente"].items()]
        for x in informe:
            for d1 in x.pop("dia1", []) if isinstance(x, dict) else []:
                corrida["controles"].append({"fuente": x["fuente"], "titulo": d1["titulo"], "url": d1["url"],
                                             "torneo": d1["torneo"], "motivo": d1["motivo"]})
        # Pendientes: notas de las últimas 48 horas que todavía no se publicaron ni entraron en la ventana.
        excluidas = urls_publicadas(raiz, hoy) | {normalizar_url(it["url"]) for it in items}
        pendientes = notas_pendientes(leidos, ahora, vistos_antes, excluidas,
                                      ajustes.get("ventana_pendientes_horas", 48),
                                      ajustes.get("max_titulares_por_fuente", 10), prioridad, descartar_dia1)
        pendientes = recoleccion.seleccionar(pendientes, ajustes.get("max_pendientes_enviados", 40))
        enviados = ventana + pendientes
        corrida["recoleccion"] = {
            "corte": corte.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "motivo_corte": motivo_corte,
            "descartes": descartes,
            "titulares_recogidos": len(recogidos),
            "titulares_nuevos": len(items),
            "titulares_enviados": len(ventana),
            "pendientes_enviados": len(pendientes),
            "articulos_abiertos": 0,
            "enlaces_compartidos": registrar_enlaces_compartidos(leidos, estado_cand, hoy),
            "fuentes_ok": sum(1 for x in informe if x["resultado"] == "ok"),
            "fuentes_con_fallo": sum(1 for x in informe if x["resultado"] == "fallo"),
            "fuentes_en_pausa": sum(1 for x in informe if x["resultado"] == "en_pausa"),
            "detalle": informe,
        }
        meta_n = ajustes.get("meta_diaria", 10)
        publicadas_antes = tarjetas_publicadas_hoy(raiz, hoy)
        corrida["meta"] = {"meta": meta_n, "publicadas_antes": publicadas_antes, "publicadas": publicadas_antes,
                           "recuperacion": None}

        if not enviados:
            corrida["estado"] = "sin_noticias"
            corrida["notas"].append("No aparecieron titulares nuevos en las fuentes desde la corrida anterior.")
            corrida["escena_latam"]["texto"] = TEXTO_LATAM_VACIO
        else:
            if llamar is modelo.llamar_anthropic and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
                raise modelo.ErrorModelo("Falta la clave ANTHROPIC_API_KEY (secreto no configurado).")
            previos = previos_recientes(raiz, hoy, ajustes.get("dias_titulos_previos", 5))
            de_temas = [it for it in enviados if it.get("tema")]
            a_elegir = [it for it in enviados if not it.get("tema")]

            # 1.ª llamada: selección de hechos (solo titulares).
            seleccion = {"estado": "sin_notas", "hechos": 0, "enviadas": len(a_elegir)}
            grupos_sel: list[dict] = []
            if a_elegir:
                guardar_entradas(raiz, hoy, corrida["id"], a_elegir, tipo="seleccion")
                try:
                    sel = modelo.seleccionar_hechos(a_elegir, ajustes, llamar, previos, corte, publicadas_antes,
                                                    salas=salas)
                    corrida["uso_seleccion"] = sel["uso"]
                    grupos_sel = sel["hechos"]
                    # «hechos» cuenta solo lo que puede ser tarjeta; las notas de salas van aparte.
                    n_salas = sum(1 for g in grupos_sel if g["seccion"] == "salas")
                    seleccion.update(estado="ok", hechos=len(grupos_sel) - n_salas, rescatada=sel["rescatado"],
                                     costo_usd=sel["uso"].get("costo_usd", 0.0))
                    if n_salas:
                        seleccion["salas"] = n_salas
                    corrida["controles"] += [dict(d, motivo=d["motivo"], seleccion=True) for d in sel["descartados"]]
                except modelo.ErrorModelo as e:
                    # Respaldo: sin selección, la redacción recibe la ventana como antes (sin pendientes).
                    if e.uso:
                        corrida["uso_seleccion"] = {**e.uso, "modelo": modelo.modelo_configurado(ajustes)}
                    seleccion.update(estado="fallida", motivo=str(e))
            corrida["seleccion"] = seleccion

            # Notas para la redacción: temas calientes y, después, las de cada hecho elegido.
            if seleccion["estado"] == "ok":
                redaccion = list(de_temas)
                grupos = []
                for g in grupos_sel:
                    inicio = len(redaccion)
                    redaccion += [a_elegir[i - 1] for i in g["ids"]]
                    grupos.append({**g, "ids": list(range(inicio + 1, len(redaccion) + 1))})
            else:
                redaccion = list(de_temas) + [it for it in ventana if not it.get("tema")]
                grupos = []
            if not redaccion:
                corrida["estado"] = "sin_noticias"
                corrida["notas"].append("La selección no encontró hechos relevantes entre los titulares nuevos.")
                corrida["escena_latam"]["texto"] = TEXTO_LATAM_VACIO
            else:
                if grupos:
                    # Primero los artículos de los hechos que serán tarjetas; los de salas, con lo que sobre.
                    orden = sorted(grupos, key=lambda g: g["seccion"] == "salas")
                    abiertos = _abrir_para_redaccion(redaccion, orden, ajustes, descargar, estado_cand, hoy)
                else:
                    abiertos = _abrir_articulos(redaccion, ajustes, descargar, estado_cand, hoy)
                corrida["recoleccion"]["articulos_abiertos"] = abiertos
                guardar_entradas(raiz, hoy, corrida["id"], redaccion, tipo="redaccion")
                # 2.ª llamada: redacción, dentro del tope de costo de la corrida.
                max_salida = ajustes.get("max_tokens_salida", 4500)
                costo_sel = (corrida.get("uso_seleccion") or {}).get("costo_usd", 0.0)
                disponible = ajustes.get("max_costo_usd_por_corrida", 0.05) - costo_sel
                nombre_modelo = modelo.modelo_configurado(ajustes)
                entrada_est = (len(modelo.SISTEMA) + sum(len(it.get("titulo") or "") + len(it.get("primera_linea") or "")
                                                         + len(it.get("extracto") or "") + 120 for it in redaccion)
                               + 60 * ajustes.get("max_titulos_previos", 80)) // 4
                while max_salida > 1500 and modelo.costo_estimado(nombre_modelo, entrada_est, max_salida, ajustes) > disponible:
                    max_salida -= 500
                resultado = modelo.resumir(redaccion, ajustes, llamar, hoy, previos, corte,
                                           publicado_por_tema(raiz, hoy, temas_activos), grupos=grupos or None,
                                           faltan=max(0, meta_n - publicadas_antes), max_tokens=max_salida,
                                           salas=salas)
                if resultado.get("temas"):
                    corrida["temas"] = resultado["temas"]
                corrida["controles"] += resultado.get("controles", [])
                corrida["uso_redaccion"] = resultado["uso"]
                corrida["tarjetas"] = resultado["tarjetas"]
                corrida["escena_latam"] = {"tarjetas": resultado["escena_latam"],
                                           "texto": None if resultado["escena_latam"] else TEXTO_LATAM_VACIO}
                # Un error del registro nunca afecta la corrida: queda anotado y el día se publica igual.
                try:
                    reg = registrar_hechos(raiz, hoy, corrida, resultado, redaccion, salas, temas_activos)
                    corrida["registro"] = {"hechos": reg["hechos"], "cambios_salas": reg["cambios_salas"]}
                    corrida["controles"] += reg["descartados"]
                except Exception as e:
                    corrida["registro"] = {"error": f"{type(e).__name__}: {e}"}
                    traceback.print_exc()
            corrida["uso"] = sumar_uso(corrida.get("uso_seleccion"), corrida.get("uso_redaccion"))
        corrida["meta"]["publicadas"] = tarjetas_publicadas_hoy(raiz, hoy, corrida)
        if corrida["tarjetas"] or corrida["escena_latam"]["tarjetas"]:
            corrida["estado"] = "ok"
            if corrida["escena_latam"]["tarjetas"]:
                corrida["escena_latam"]["texto"] = None
            corrida["notas"] = [n for n in corrida["notas"] if not n.startswith("No aparecieron titulares")]
        elif enviados and not corrida.get("temas"):
            corrida["estado"] = "sin_noticias"
            corrida["notas"].append("El modelo no encontró hechos relevantes entre los titulares nuevos.")
        # Corrida exitosa: todo lo recogido (elegido o no) cuenta como visto.
        # Si la corrida falla, no se marca nada y la próxima lo vuelve a intentar.
        for url in recogidos:
            vistos["urls"].setdefault(normalizar_url(url), hoy.isoformat())
    except modelo.ErrorModelo as e:
        corrida["estado"] = "fallida"
        corrida["motivo"] = str(e)
        uso_error = ({**e.uso, "modelo": modelo.modelo_configurado(ajustes)} if e.uso else None)
        if uso_error and "costo_usd" not in uso_error:
            uso_error["costo_usd"] = modelo.costo_estimado(
                uso_error["modelo"], e.uso.get("tokens_entrada", 0), e.uso.get("tokens_salida", 0), ajustes)
        if uso_error:
            corrida["uso_redaccion"] = uso_error
        corrida["uso"] = sumar_uso(corrida.get("uso_seleccion"), uso_error)
        corrida["escena_latam"] = {"tarjetas": [], "texto": "No disponible: la corrida falló."}
    except Exception as e:  # cualquier error inesperado queda registrado, no rompe la página
        corrida["estado"] = "fallida"
        corrida["motivo"] = f"Error interno: {type(e).__name__}: {e}"
        corrida["escena_latam"] = {"tarjetas": [], "texto": "No disponible: la corrida falló."}
        traceback.print_exc()

    # Registro de costos (también si la llamada falló después de consumir tokens).
    if corrida.get("uso_seleccion"):
        _registrar_costo(costos, hoy, corrida, corrida["uso_seleccion"], "seleccion")
    if corrida.get("uso_redaccion"):
        _registrar_costo(costos, hoy, corrida, corrida["uso_redaccion"], "redaccion")

    try:
        cand.actualizar(estado_cand, fuentes, hoy, ajustes.get("candidatas_ventana_dias", 14),
                        ajustes.get("candidatas_min_articulos", 3))
    except Exception:
        traceback.print_exc()

    # Memoria de vistos acotada para que el archivo no crezca sin límite.
    limite = (hoy - timedelta(days=ajustes.get("dias_memoria_vistos", 180))).isoformat()
    vistos["urls"] = {u: f for u, f in vistos["urls"].items() if f >= limite}

    escribir_json(ruta_fuentes, conf_fuentes)
    escribir_json(ruta_vistos, vistos)
    escribir_json(ruta_cand, estado_cand)
    escribir_json(ruta_costos, costos)
    agregar_corrida(raiz, hoy, corrida)
    try:
        temas_mod.detectar_auto(raiz, hoy)
    except Exception:
        traceback.print_exc()

    if generar_pagina:
        pagina.generar(raiz, hoy, ahora)
    return corrida


def probar_recoleccion(raiz: Path, ahora: datetime) -> int:
    """Consulta todas las fuentes (incluidas las pausadas) sin guardar nada."""
    fuentes = leer_json(Path(raiz) / "config" / "fuentes.json")["fuentes"]
    ok = 0
    for f in fuentes:
        try:
            items = recoleccion.leer_fuente(f, recoleccion.descargar_http)
            ok += 1
            print(f"RESPONDE    {f['nombre']}: {len(items)} titulares")
        except recoleccion.ErrorDescarga as e:
            print(f"NO RESPONDE {f['nombre']}: {e}")
    print(f"\n{ok} de {len(fuentes)} fuentes respondieron.")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Corrida diaria de Mesa Caliente")
    p.add_argument("--origen", choices=["automatica", "manual", "externo"], default="manual")
    p.add_argument("--solo-recoleccion", action="store_true")
    p.add_argument("--omitir-si-hubo-exito-hoy", action="store_true",
                   help="Para los horarios programados: no hace nada si hoy ya hubo una corrida exitosa.")
    args = p.parse_args(argv)
    ahora = ahora_utc()
    if args.solo_recoleccion:
        return probar_recoleccion(RAIZ, ahora)
    if args.omitir_si_hubo_exito_hoy and hubo_corrida_exitosa(RAIZ, ahora.date()):
        print(f"Hoy ({ahora.date().isoformat()}) ya hubo una corrida exitosa: esta corrida programada se omite.")
        return 0
    corrida = ejecutar(RAIZ, ahora, args.origen)
    r = corrida.get("recoleccion", {})
    print(f"Estado: {corrida['estado']}" + (f" ({corrida['motivo']})" if corrida["motivo"] else ""))
    print(f"Titulares nuevos: {r.get('titulares_nuevos', 0)}, enviados: {r.get('titulares_enviados', 0)}, "
          f"artículos abiertos: {r.get('articulos_abiertos', 0)}, tarjetas: {len(corrida['tarjetas'])}")
    if corrida["uso"]:
        u = corrida["uso"]
        print(f"Tokens: {u.get('tokens_entrada', 0)} entrada / {u.get('tokens_salida', 0)} salida, "
              f"costo estimado US$ {u.get('costo_usd', 0):.4f}")
    sel = corrida.get("seleccion") or {}
    if sel:
        print(f"Selección: {sel.get('estado')}, {sel.get('hechos', 0)} hechos de {sel.get('enviadas', 0)} notas"
              + (f" ({sel['motivo']})" if sel.get("motivo") else ""))
    m = corrida.get("meta") or {}
    if m:
        print(f"Meta diaria: {m['publicadas']} de {m['meta']} tarjetas hoy.")
    # Una corrida fallida igual termina bien: el día queda registrado y la página publicada.
    return 0


if __name__ == "__main__":
    sys.exit(main())
