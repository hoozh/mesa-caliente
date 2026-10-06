"""Corrida diaria completa: recolecta, llama una vez al modelo, guarda y genera la página.

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
    por_fuente, totales = {}, {"por_fecha": 0, "por_orden": 0, "por_tope": 0, "por_dia1": 0, "ya_vistos": 0}
    for x in informe:
        if x.get("resultado") != "ok":
            continue
        x["por_tope_total"] = fuera_por_total.get(x["fuente"], 0)
        x["enviados"] = x["nuevos"] - x["por_tope_total"]
        fila = {"modo": x["modo"], "por_fecha": x["por_fecha"], "por_orden": x["por_orden"],
                "por_tope": x["por_tope"] + x["por_tope_total"], "por_dia1": x.get("por_dia1", 0),
                "ya_vistos": x["ya_vistos"], "enviados": x["enviados"]}
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


def candidatas_recuperacion(leidos: list[dict], ahora: datetime, vistos: dict, excluidas: set[str],
                            horas: int, tope_fuente: int, prioridad=None, descartar_dia1=None) -> list[dict]:
    """Notas de las últimas `horas` aún no publicadas ni enviadas en esta corrida.

    - Fuentes con fecha propia: solo lo publicado dentro de la ventana.
    - Fuentes sin fecha (modo orden): lo no visto antes o visto por primera vez dentro de la ventana.
    Se excluyen dominios prohibidos, notas de temas calientes (ya van en la llamada principal)
    y avances de día 1 de torneos que no están en la lista. Rige el tope por fuente."""
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
            tomados.append(dict(it))
        resultado += tomados[:tope_fuente]
    return resultado


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


def recuperar_meta(raiz: Path, hoy: date, ahora: datetime, corrida: dict, ajustes: dict, llamar,
                   leidos: list[dict], vistos: dict, enviados: list[dict], prioridad, descartar_dia1,
                   descargar, estado_cand: dict) -> dict:
    """Si el día queda por debajo de la meta, una segunda llamada (acotada) con las notas de las
    últimas 48 horas aún no publicadas. Las reglas fijas no cambian; nada se completa ni se inventa."""
    meta_n = ajustes.get("meta_diaria", 10)
    antes = tarjetas_publicadas_hoy(raiz, hoy, corrida)
    meta = {"meta": meta_n, "publicadas_antes": antes, "publicadas": antes, "recuperacion": None}
    if antes >= meta_n:
        return meta
    faltan = meta_n - antes
    info = {"faltan": faltan, "candidatas": 0, "enviadas": 0, "tarjetas": 0, "segundo_nivel": 0,
            "costo_usd": 0.0, "omitida": None}
    meta["recuperacion"] = info

    uso_principal = corrida.get("uso") or {}
    tope_salida = ajustes.get("max_tokens_salida_por_corrida", 3000)
    salida_restante = min(tope_salida - uso_principal.get("tokens_salida", 0), ajustes.get("max_tokens_salida", 3000))
    titulares_restantes = ajustes.get("max_titulares_enviados", 120) - len(enviados)
    costo_restante = ajustes.get("max_costo_usd_por_corrida", 0.05) - uso_principal.get("costo_usd", 0.0)
    if salida_restante < ajustes.get("min_tokens_salida_recuperacion", 600):
        info["omitida"] = "sin presupuesto de tokens de salida en esta corrida"
        return meta
    if titulares_restantes <= 0:
        info["omitida"] = "sin cupo de titulares en esta corrida"
        return meta

    excluidas = urls_publicadas(raiz, hoy) | {normalizar_url(it["url"]) for it in enviados}
    candidatas = candidatas_recuperacion(leidos, ahora, vistos, excluidas,
                                         ajustes.get("ventana_recuperacion_horas", 48),
                                         ajustes.get("max_titulares_por_fuente", 10), prioridad, descartar_dia1)
    info["candidatas"] = len(candidatas)
    elegidas = recoleccion.seleccionar(candidatas, titulares_restantes)
    if not elegidas:
        info["omitida"] = "no hay notas de las últimas 48 horas sin publicar"
        return meta
    # Tope de costo: la peor salida posible más una estimación de la entrada (unos 4 caracteres por token).
    modelo_nombre = modelo.modelo_configurado(ajustes)
    entrada_estimada = (len(modelo.SISTEMA) + sum(len(it.get("titulo") or "") + len(it.get("primera_linea") or "")
                                                   + len(it.get("extracto") or "") + 120 for it in elegidas)) // 4
    if modelo.costo_estimado(modelo_nombre, entrada_estimada, salida_restante, ajustes) > costo_restante:
        info["omitida"] = "superaría el tope de costo por corrida"
        return meta
    if llamar is modelo.llamar_anthropic and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        info["omitida"] = "falta la clave ANTHROPIC_API_KEY"
        return meta

    abiertos = corrida.get("recoleccion", {}).get("articulos_abiertos", 0)
    _abrir_articulos(elegidas, ajustes, descargar, estado_cand, hoy,
                     tope=max(0, ajustes.get("max_articulos_abiertos", 6) - abiertos))
    previos = previos_recientes(raiz, hoy, ajustes.get("dias_titulos_previos", 5))
    previos = [{"titulo": t["titulo"], "resumen": t.get("resumen", ""), "hoy": True}
               for t in corrida["tarjetas"] + corrida["escena_latam"]["tarjetas"]] + previos
    guardar_entradas(raiz, hoy, corrida["id"], elegidas, tipo="recuperacion")
    info["enviadas"] = len(elegidas)
    try:
        resultado = modelo.resumir(elegidas, ajustes, llamar, hoy, previos, None, {},
                                   recuperacion={"publicadas": antes, "meta": meta_n, "faltan": faltan},
                                   max_tokens=salida_restante)
    except modelo.ErrorModelo as e:
        info["omitida"] = f"falló la llamada de recuperación: {e}"
        if e.uso:
            uso = {**e.uso, "modelo": modelo_nombre}
            uso.setdefault("costo_usd", modelo.costo_estimado(modelo_nombre, uso.get("tokens_entrada", 0),
                                                              uso.get("tokens_salida", 0), ajustes))
            corrida["uso_recuperacion"] = uso
            info["costo_usd"] = uso["costo_usd"]
        return meta
    corrida["uso_recuperacion"] = resultado["uso"]
    info["costo_usd"] = resultado["uso"].get("costo_usd", 0.0)
    for t in resultado["tarjetas"] + resultado["escena_latam"]:
        t["recuperada"] = True
    corrida["tarjetas"] += resultado["tarjetas"]
    corrida["escena_latam"]["tarjetas"] += resultado["escena_latam"]
    corrida["controles"] += [dict(x, recuperacion=True) for x in resultado.get("controles", [])]
    nuevas = resultado["tarjetas"] + resultado["escena_latam"]
    info["tarjetas"] = len(nuevas)
    info["segundo_nivel"] = sum(1 for t in nuevas if t.get("nivel") == 2)
    meta["publicadas"] = antes + len(nuevas)
    return meta


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
        prioridad = temas_mod.detector(temas_activos)
        descartar_dia1 = torneos.descartador_dia1(torneos.cargar_relevantes(raiz))
        vistos_antes = dict(vistos["urls"])
        leidos: list[dict] = []
        items, informe, recogidos = recoleccion.recolectar(
            fuentes, hoy, ahora, set(vistos["urls"]), ajustes, descargar, corte,
            prioridad=prioridad, descartar_dia1=descartar_dia1, leidos=leidos)
        enviados = recoleccion.seleccionar(items, ajustes.get("max_titulares_enviados", 120))
        descartes = contar_descartes(informe, items, enviados)
        corrida["controles"] = [{"motivo": "descartes", "fuente": f, **fila}
                                for f, fila in descartes["por_fuente"].items()]
        for x in informe:
            for d1 in x.pop("dia1", []) if isinstance(x, dict) else []:
                corrida["controles"].append({"fuente": x["fuente"], "titulo": d1["titulo"], "url": d1["url"],
                                             "torneo": d1["torneo"], "motivo": d1["motivo"]})
        corrida["recoleccion"] = {
            "corte": corte.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "motivo_corte": motivo_corte,
            "descartes": descartes,
            "titulares_recogidos": len(recogidos),
            "titulares_nuevos": len(items),
            "titulares_enviados": len(enviados),
            "articulos_abiertos": 0,
            "fuentes_ok": sum(1 for x in informe if x["resultado"] == "ok"),
            "fuentes_con_fallo": sum(1 for x in informe if x["resultado"] == "fallo"),
            "fuentes_en_pausa": sum(1 for x in informe if x["resultado"] == "en_pausa"),
            "detalle": informe,
        }

        if not enviados:
            corrida["estado"] = "sin_noticias"
            corrida["notas"].append("No aparecieron titulares nuevos en las fuentes desde la corrida anterior.")
            corrida["escena_latam"]["texto"] = TEXTO_LATAM_VACIO
        else:
            if llamar is modelo.llamar_anthropic and not os.environ.get("ANTHROPIC_API_KEY", "").strip():
                raise modelo.ErrorModelo("Falta la clave ANTHROPIC_API_KEY (secreto no configurado).")
            corrida["recoleccion"]["articulos_abiertos"] = _abrir_articulos(enviados, ajustes, descargar, estado_cand, hoy)
            previos = previos_recientes(raiz, hoy, ajustes.get("dias_titulos_previos", 5))
            guardar_entradas(raiz, hoy, corrida["id"], enviados)
            resultado = modelo.resumir(enviados, ajustes, llamar, hoy, previos, corte,
                                       publicado_por_tema(raiz, hoy, temas_activos))
            if resultado.get("temas"):
                corrida["temas"] = resultado["temas"]
            corrida["controles"] += resultado.get("controles", [])
            corrida["uso"] = resultado["uso"]
            corrida["tarjetas"] = resultado["tarjetas"]
            corrida["escena_latam"] = {"tarjetas": resultado["escena_latam"],
                                       "texto": None if resultado["escena_latam"] else TEXTO_LATAM_VACIO}
        try:
            corrida["meta"] = recuperar_meta(raiz, hoy, ahora, corrida, ajustes, llamar, leidos, vistos_antes,
                                             enviados, prioridad, descartar_dia1, descargar, estado_cand)
        except Exception as e:  # la recuperación nunca tumba lo ya obtenido
            traceback.print_exc()
            corrida["meta"] = {"meta": ajustes.get("meta_diaria", 10),
                               "publicadas": tarjetas_publicadas_hoy(raiz, hoy, corrida),
                               "recuperacion": {"omitida": f"error interno: {type(e).__name__}"}}
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
        corrida["uso"] = ({**e.uso, "modelo": modelo.modelo_configurado(ajustes)} if e.uso else None)
        if e.uso and "costo_usd" not in corrida["uso"]:
            corrida["uso"]["costo_usd"] = modelo.costo_estimado(
                corrida["uso"]["modelo"], e.uso.get("tokens_entrada", 0), e.uso.get("tokens_salida", 0), ajustes)
        corrida["escena_latam"] = {"tarjetas": [], "texto": "No disponible: la corrida falló."}
    except Exception as e:  # cualquier error inesperado queda registrado, no rompe la página
        corrida["estado"] = "fallida"
        corrida["motivo"] = f"Error interno: {type(e).__name__}: {e}"
        corrida["escena_latam"] = {"tarjetas": [], "texto": "No disponible: la corrida falló."}
        traceback.print_exc()

    # Registro de costos (también si la llamada falló después de consumir tokens).
    if corrida["uso"]:
        _registrar_costo(costos, hoy, corrida, corrida["uso"], "principal")
    if corrida.get("uso_recuperacion"):
        _registrar_costo(costos, hoy, corrida, corrida["uso_recuperacion"], "recuperacion")

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
    m = corrida.get("meta") or {}
    if m:
        print(f"Meta diaria: {m['publicadas']} de {m['meta']} tarjetas hoy.")
        rec = m.get("recuperacion")
        if rec:
            print(f"Recuperación: {rec.get('enviadas', 0)} notas enviadas, {rec.get('tarjetas', 0)} tarjetas "
                  f"({rec.get('segundo_nivel', 0)} de segundo nivel), costo extra US$ {rec.get('costo_usd', 0):.4f}"
                  + (f"; omitida: {rec['omitida']}" if rec.get("omitida") else ""))
    # Una corrida fallida igual termina bien: el día queda registrado y la página publicada.
    return 0


if __name__ == "__main__":
    sys.exit(main())
