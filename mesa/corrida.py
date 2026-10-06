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


def guardar_entradas(raiz: Path, dia: date, corrida_id: str, enviados: list[dict]) -> None:
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
    datos["corridas"].append({"id": corrida_id, "notas": notas})
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


def _abrir_articulos(items: list[dict], ajustes: dict, descargar, estado_cand: dict, hoy: date) -> int:
    """Abre (como máximo el tope) los artículos cuyo titular no alcanza para entenderlos.
    Primero los titulares vagos (sin nombre ni cifra), después los que no traen primera línea."""
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
        items, informe, recogidos = recoleccion.recolectar(
            fuentes, hoy, ahora, set(vistos["urls"]), ajustes, descargar, corte,
            prioridad=temas_mod.detector(temas_activos),
            descartar_dia1=torneos.descartador_dia1(torneos.cargar_relevantes(raiz)))
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
            if not resultado["tarjetas"] and not resultado["escena_latam"] and not resultado.get("temas"):
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
        costos["corridas"].append({
            "fecha": hoy.isoformat(),
            "id": corrida["id"],
            "modelo": corrida["uso"].get("modelo"),
            "tokens_entrada": corrida["uso"].get("tokens_entrada", 0),
            "tokens_salida": corrida["uso"].get("tokens_salida", 0),
            "costo_usd": corrida["uso"].get("costo_usd", 0.0),
            "estado": corrida["estado"],
        })

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
    p.add_argument("--origen", choices=["automatica", "manual"], default="manual")
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
    # Una corrida fallida igual termina bien: el día queda registrado y la página publicada.
    return 0


if __name__ == "__main__":
    sys.exit(main())
