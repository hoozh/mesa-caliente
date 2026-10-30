"""Carga, una sola vez, el historial de data/dias/ (desde el 21 sep 2026) en data/registro/.

Las tarjetas viejas no traen tipo, jugadores, torneo ni país: hace falta el modelo. Sin --confirmar,
el script solo informa cuántas tarjetas faltan, cuántas llamadas haría y el costo estimado.

Uso:
  python -m mesa.historial_registro              # solo informa el costo estimado (no llama al modelo)
  python -m mesa.historial_registro --confirmar  # carga el historial (necesita ANTHROPIC_API_KEY)

El modelo recibe, por tarjeta, su título y su resumen publicados y, desde el 7 oct, el titular y la
primera frase de sus notas (data/entradas/). Todo campo se verifica contra ese texto; un «dato» de
cambio en sala debe estar copiado tal cual del titular o la primera frase de la fuente.
No pisa nada: las tarjetas que ya están en el registro (por su id) se saltean.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import modelo
from . import registro as registro_mod
from .dias import cargar_dias
from .pagina import unir_temas
from .util import RAIZ, ahora_utc, escribir_json, leer_json, limpiar_texto

DESDE = "2026-09-21"
POR_LLAMADA = 20
TOKENS_SALIDA_POR_TARJETA = 90  # estimación de la ficha que devuelve el modelo

SISTEMA_HISTORIAL = """Usted completa fichas de archivo para tarjetas ya publicadas en "Mesa Caliente" (noticias de poker).
Escriba en español neutro, sin voseo.

Recibirá tarjetas numeradas. Cada una trae su título, su resumen y, a veces, el titular y la primera frase de sus notas originales.
Para cada tarjeta devuelva "n" (su número) y "registro", solo con datos escritos en el texto de esa tarjeta. Omita lo que no figure:
- "tipo": resultado, anuncio_evento, industria_polemica, software, obituario u otro.
- "salas": salas de poker online nombradas.
- "jugadores": lista de [nombre, país]. El país solo si el texto lo dice de esa persona; si no, "". Nunca lo deduzca del nombre, del alias ni de la sala.
- "torneo": torneo o serie, con el nombre que da el texto.
- "pais_evento": país donde se juega, solo si el texto lo nombra.
Además, "cambios_salas": si una nota original trae un cambio de una sala vigilada ({salas}) sobre uno de estos temas ({temas_salas}), agregue {{"sala", "tema" (la clave), "dato"}}; "dato" es un fragmento copiado letra por letra del titular o la primera frase de la nota, en su idioma. Si no hay, deje la lista vacía.

REGLA MÁXIMA: nunca invente datos ni los complete con su conocimiento.

Responda únicamente con un objeto JSON:
{{"fichas": [{{"n": 1, "registro": {{"tipo": "resultado", "jugadores": [["...", ""]], "torneo": "..."}}, "cambios_salas": []}}]}}"""


def _entradas(raiz: Path) -> dict[str, dict]:
    """URL -> lo que se envió al modelo por esa nota (titular y primera frase)."""
    notas: dict[str, dict] = {}
    for ruta in sorted((Path(raiz) / "data" / "entradas").glob("*.json")):
        for c in (leer_json(ruta) or {}).get("corridas", []):
            for n in c.get("notas", []):
                if n.get("url"):
                    notas.setdefault(n["url"], n)
    return notas


def pendientes(raiz: Path) -> list[dict]:
    """Tarjetas y temas publicados desde DESDE que todavía no están en el registro."""
    raiz = Path(raiz)
    ya = {f.get("id") for f in registro_mod.leer_jsonl(raiz / registro_mod.HECHOS)}
    clases = {f["nombre"]: f.get("clase") or "" for f in leer_json(raiz / "config" / "fuentes.json",
                                                                   {"fuentes": []})["fuentes"]}
    entradas = _entradas(raiz)
    resultado = []
    for d in sorted(cargar_dias(raiz), key=lambda d: d["fecha"]):
        if d["fecha"] < DESDE:
            continue
        for c in d.get("corridas", []):
            publicados = [("general", t) for t in c.get("tarjetas") or []]
            publicados += [("latam", t) for t in (c.get("escena_latam") or {}).get("tarjetas") or []]
            publicados += [("tema", t) for t in unir_temas([c])]
            for seccion, t in publicados:
                titulo = t["tema"] if seccion == "tema" else t["titulo"]
                ident = registro_mod.id_hecho(d["fecha"], c.get("id") or "", titulo)
                if ident in ya:
                    continue
                fuentes = t.get("novedades") if seccion == "tema" else t.get("fuentes")
                notas = []
                for f in fuentes or []:
                    nombre = f.get("nombre") or f.get("fuente") or ""
                    e = entradas.get(f.get("url") or "")
                    notas.append({"fuente": nombre, "url": f.get("url") or "", "clase": clases.get(nombre, ""),
                                  "titulo": (e or {}).get("titular") or "", "primera_linea": (e or {}).get("primera_frase") or ""})
                resumen = (t.get("linea") or " ".join(n.get("aporta") or "" for n in t.get("novedades") or [])
                           if seccion == "tema" else f"{t.get('resumen') or ''} {t.get('novedad') or ''}")
                resultado.append({"fecha": d["fecha"], "corrida": c.get("id") or "", "seccion": seccion,
                                  "publicado": t, "titulo": titulo, "resumen": limpiar_texto(resumen), "notas": notas})
    return resultado


def mensaje(lote: list[dict]) -> str:
    lineas = []
    for n, p in enumerate(lote, 1):
        lineas.append(f"[{n}] Título: {p['titulo']}\n    Resumen: {p['resumen']}")
        for nota in p["notas"]:
            if nota["titulo"]:
                lineas.append(f"    Nota ({nota['fuente']}): {limpiar_texto(nota['titulo'])} | "
                              f"{limpiar_texto(nota['primera_linea'])}")
    return "\n".join(lineas)


def estimar(raiz: Path, ajustes: dict, salas: dict) -> dict:
    lista = pendientes(raiz)
    lotes = [lista[i:i + POR_LLAMADA] for i in range(0, len(lista), POR_LLAMADA)]
    sistema = SISTEMA_HISTORIAL.format(**modelo.textos_salas(salas))
    entrada = sum((len(sistema) + len(mensaje(l))) // 3 for l in lotes)  # ~3 caracteres por token (conservador)
    salida = len(lista) * TOKENS_SALIDA_POR_TARJETA
    nombre = modelo.modelo_configurado(ajustes)
    return {"tarjetas": len(lista), "llamadas": len(lotes), "tokens_entrada": entrada, "tokens_salida": salida,
            "modelo": nombre, "costo_usd": modelo.costo_estimado(nombre, entrada, salida, ajustes)}


def _verificar(p: dict, crudo: dict, salas: dict, temas: list[dict]) -> tuple[dict, list[dict]]:
    """Ficha y cambios de una tarjeta, verificados contra el texto que recibió el modelo para ella."""
    tarjeta_texto = {"fuente": "", "url": "", "titulo": p["titulo"], "primera_linea": p["resumen"]}
    items = [tarjeta_texto] + p["notas"]
    t = p["publicado"]
    if p["seccion"] == "tema":
        hecho = registro_mod.hecho_de_tema(p["fecha"], p["corrida"], t, crudo.get("registro"), items, salas)
    else:
        hecho = registro_mod.hecho_de_tarjeta(p["fecha"], p["corrida"], t, p["seccion"], crudo.get("registro"),
                                              items, salas, temas)
    clases = {n["url"]: n["clase"] for n in p["notas"]}
    for f in hecho["fuentes"]:
        f["clase"] = f["clase"] or clases.get(f["url"], "")
    cambios = []
    notas = [n for n in p["notas"] if n["titulo"]]  # el dato debe salir de la nota original, no del resumen
    for c in crudo.get("cambios_salas") or []:
        if isinstance(c, dict) and notas:
            cambio, _ = registro_mod.verificar_cambio({**c, "ids": list(range(1, len(notas) + 1))}, notas, salas,
                                                      p["fecha"])
            if cambio:
                cambios.append(cambio)
    return hecho, cambios


def cargar(raiz: Path, llamar=modelo.llamar_anthropic) -> dict:
    raiz = Path(raiz)
    ajustes = leer_json(raiz / "config" / "ajustes.json", {})
    salas = registro_mod.cargar_salas(raiz)
    # Para el historial cuentan todos los temas conocidos, también los que ya se apagaron.
    from . import temas as temas_mod
    temas = [t for t in temas_mod.cargar_manual(raiz).get("temas", []) if t.get("palabras_clave")]
    temas += [t for t in (leer_json(temas_mod.ruta_auto(raiz), {"temas": []}) or {}).get("temas", [])
              if t.get("palabras_clave")]
    lista = pendientes(raiz)
    nombre = modelo.modelo_configurado(ajustes)
    sistema = SISTEMA_HISTORIAL.format(**modelo.textos_salas(salas))
    hechos, cambios, uso_total, fallidas = [], [], {"tokens_entrada": 0, "tokens_salida": 0, "costo_usd": 0.0}, 0
    for i in range(0, len(lista), POR_LLAMADA):
        lote = lista[i:i + POR_LLAMADA]
        try:
            texto, uso = llamar(sistema, mensaje(lote), nombre, TOKENS_SALIDA_POR_TARJETA * len(lote) + 400)
            uso = modelo._con_costo(uso, nombre, ajustes)
            datos = modelo._extraer_json(texto)
        except modelo.ErrorModelo as e:
            fallidas += 1
            print(f"Lote {i // POR_LLAMADA + 1}: {e}", file=sys.stderr)
            continue
        for k in ("tokens_entrada", "tokens_salida", "costo_usd"):
            uso_total[k] += uso.get(k, 0)
        por_n = {}
        for f in datos.get("fichas") or []:
            if isinstance(f, dict):
                try:
                    por_n[int(f.get("n"))] = f
                except (TypeError, ValueError):
                    continue
        for n, p in enumerate(lote, 1):
            hecho, cambios_p = _verificar(p, por_n.get(n, {}), salas, temas)
            hechos.append(hecho)
            cambios += cambios_p
    agregados = registro_mod.registrar(raiz, hechos, cambios)
    uso_total["costo_usd"] = round(uso_total["costo_usd"], 6)
    if uso_total["tokens_entrada"]:
        ahora = ahora_utc()
        costos = leer_json(raiz / "data" / "costos.json", {"corridas": []})
        costos["corridas"].append({"fecha": ahora.date().isoformat(), "id": ahora.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                   "tipo": "historial", "modelo": nombre, **uso_total,
                                   "estado": "ok" if not fallidas else "parcial"})
        escribir_json(raiz / "data" / "costos.json", costos)
    return {"hechos": agregados[0], "cambios_salas": agregados[1], "paises_confirmados": agregados[2],
            "lotes_fallidos": fallidas, "uso": uso_total}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Carga el historial de data/dias/ en data/registro/ (una sola vez)")
    p.add_argument("--confirmar", action="store_true", help="Llama al modelo y carga el historial.")
    args = p.parse_args(argv)
    ajustes = leer_json(RAIZ / "config" / "ajustes.json", {})
    e = estimar(RAIZ, ajustes, registro_mod.cargar_salas(RAIZ))
    print(f"Tarjetas por cargar: {e['tarjetas']} en {e['llamadas']} llamadas a {e['modelo']}.")
    print(f"Estimación: {e['tokens_entrada']} tokens de entrada y {e['tokens_salida']} de salida, "
          f"unos US$ {e['costo_usd']:.3f}.")
    if not args.confirmar:
        print("No se llamó al modelo. Para cargar el historial, repita con --confirmar.")
        return 0
    if not e["tarjetas"]:
        print("El historial ya está cargado: no hay nada que hacer.")
        return 0
    r = cargar(RAIZ)
    print(f"Agregados: {r['hechos']} hechos y {r['cambios_salas']} cambios en salas. "
          f"Costo US$ {r['uso']['costo_usd']:.4f}. Lotes fallidos: {r['lotes_fallidos']}.")
    print(json.dumps(r["uso"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
