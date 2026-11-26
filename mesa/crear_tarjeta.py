"""«Crear tarjeta» desde «Consultar registros» del administrador (pedido de Hoozh, 26 nov 2026).

Toma un hecho del registro (data/registro/hechos.jsonl) o un resultado del archivo privado
(resultados/archivo.jsonl) y escribe una tarjeta con sus datos y sus fuentes, en uno de tres tamaños:
- small: titular y bajada;
- medium: titular, bajada y un párrafo;
- large: titular, bajada y dos párrafos (como los textos largos del barrido).

Una sola llamada al modelo por tarjeta, con los documentos de SEI (caché de prompt) y solo con lo que dicen
el registro y las notas que cita (se vuelven a abrir para leer su texto). Pasa los mismos controles que la
redacción del barrido y los de estilo. La tarjeta queda **oculta** (data/ediciones.json) para que Hoozh la
revise y la publique desde el administrador. El costo va a data/costos.json (tipo «tarjeta_registro»).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from . import calidad, control, modelo, recoleccion, registro, revision, sei
from .dias import agregar_corrida, cargar_dias
from .util import RAIZ, escribir_json, leer_json, limpiar_texto

TAMANOS = {"small": 0, "medium": 1, "large": 2}

SISTEMA_TARJETA = """Usted redacta una tarjeta de noticias de poker para Mesa Caliente, en español neutro, a partir de un hecho ya registrado y del texto de las notas que lo cuentan.

Devuelva solo JSON: {{"titulo": "...", "resumen": "...", "cuerpo": "..."}}

- "titulo": en español, con la jerga y los nombres propios en inglés; el hecho principal.
- "resumen": la bajada, de 2 a 3 líneas.
- "cuerpo": {cuerpo}
- Solo datos que estén en el registro o en el texto de las notas que recibe: nombres, cifras, fechas, lugares, cargos, nacionalidades. Nunca invente ni complete. Si un dato no está, no lo mencione y no diga que falta.
- Las fuentes se nombran en el texto cuando hace falta ("según PokerNews"); no hable de "la fuente" ni de "la información disponible".
- Números con el formato de GipsyTeam: dólares con "$" pegado y coma de miles ("$110,938"); cantidades con punto de miles ("2.310 inscripciones"). Comillas rectas dobles para las citas, traducidas al español.
"""

CUERPOS = {0: 'vacío ("").',
           1: "un párrafo con lo esencial (quién, qué, cuándo, dónde, cuánto) que no repita la bajada.",
           2: 'dos párrafos separados por una línea en blanco ("\\n\\n"): el primero con los datos duros (cuándo, '
              "qué, dónde, cuánto); el segundo con el contexto o lo que dijeron los involucrados, según el texto."}


def clave_archivo(e: dict) -> str:
    """Identificador estable de una línea del archivo de resultados (el administrador usa el mismo)."""
    return hashlib.sha1(f"{e.get('fecha', '')}|{e.get('titulo', '')}".encode("utf-8")).hexdigest()[:12]


def _leer_jsonl(ruta: Path) -> list[dict]:
    if not ruta.exists():
        return []
    salida = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        try:
            salida.append(json.loads(linea))
        except ValueError:
            pass
    return salida


def buscar_registro(raiz: Path, privado: Path | None, origen: str, clave: str) -> dict:
    """Devuelve {'fecha', 'titulo', 'resumen', 'cuerpo', 'fuentes', 'datos'} del registro elegido."""
    if origen == "hechos":
        h = next((x for x in _leer_jsonl(Path(raiz) / "data" / "registro" / "hechos.jsonl") if x.get("id") == clave), None)
        if not h:
            raise SystemExit(f"No está el hecho {clave} en el registro.")
        publicada = {}
        for d in cargar_dias(raiz):  # el texto que se publicó de ese hecho (mismo id)
            for c in d.get("corridas", []):
                for t in c.get("tarjetas", []) + (c.get("escena_latam") or {}).get("tarjetas", []):
                    if registro.id_hecho(d["fecha"], c.get("id", ""), t.get("titulo", "")) == clave:
                        publicada = t
        datos = {k: h[k] for k in ("tipo", "salas", "jugadores", "torneo", "pais_evento") if h.get(k)}
        return {"fecha": h["fecha"], "titulo": h["titulo"], "resumen": publicada.get("resumen", ""),
                "cuerpo": publicada.get("cuerpo", ""), "fuentes": h.get("fuentes") or publicada.get("fuentes") or [], "datos": datos}
    if origen == "archivo":
        if not privado:
            raise SystemExit("Falta el repositorio privado (archivo de resultados).")
        e = next((x for x in _leer_jsonl(Path(privado) / "resultados" / "archivo.jsonl") if clave_archivo(x) == clave), None)
        if not e:
            raise SystemExit(f"No está el resultado {clave} en el archivo.")
        return {"fecha": e.get("fecha", ""), "titulo": e.get("titulo", ""), "resumen": e.get("resumen", ""), "cuerpo": "",
                "fuentes": e.get("fuentes") or [], "datos": {}}
    raise SystemExit("Origen desconocido: use «hechos» o «archivo».")


def items_de_fuentes(fuentes: list[dict], ajustes: dict, descargar) -> list[dict]:
    """Una nota por fuente, con el texto del artículo si se puede abrir (como en la redacción del barrido)."""
    items = []
    for f in fuentes[:4]:
        it = {"fuente": f.get("nombre", ""), "url": f.get("url", ""), "titulo": f.get("titular", ""),
              "primera_linea": f.get("primera_linea", ""), "fecha": f.get("fecha"), "clase": f.get("clase", "medio")}
        if it["url"] and descargar:
            try:
                r = descargar(it["url"])
                if r.estado == 200:
                    extracto, _, imagen = recoleccion.extraer_articulo(r.texto, r.url or it["url"],
                                                                       ajustes.get("max_caracteres_extracto_tarjeta", 3000),
                                                                       con_imagen=True)
                    if extracto:
                        it["extracto"] = extracto
                    if imagen:
                        it["imagen"] = imagen
            except Exception:  # una fuente que no abre no impide la tarjeta
                pass
        items.append(it)
    return items


def crear(raiz: Path, privado: Path | None, origen: str, clave: str, tamano: str, llamar=None,
          descargar=recoleccion.descargar_http, ahora: datetime | None = None) -> dict:
    raiz = Path(raiz)
    ahora = ahora or datetime.now(timezone.utc)
    if tamano not in TAMANOS:
        raise SystemExit("Tamaño desconocido: small, medium o large.")
    ajustes = leer_json(raiz / "config" / "ajustes.json", {})
    reg = buscar_registro(raiz, privado, origen, clave)
    items = items_de_fuentes(reg["fuentes"], ajustes, descargar)
    # Lo que dice el registro también cuenta como texto enviado (para los controles).
    items.insert(0, {"fuente": "registro", "url": "", "titulo": reg["titulo"], "primera_linea": reg["resumen"],
                     "extracto": "\n".join(x for x in (reg["cuerpo"], json.dumps(reg["datos"], ensure_ascii=False)
                                                       if reg["datos"] else "") if x), "clase": "registro"})
    mensaje = "Hecho registrado:\n" + json.dumps({k: reg[k] for k in ("fecha", "titulo", "resumen", "cuerpo", "datos")},
                                                 ensure_ascii=False) + "\n\nNotas que lo cuentan:\n" + "\n\n".join(
        f"[{it['fuente']}] {it.get('titulo') or ''}\n{it.get('primera_linea') or ''}\n{it.get('extracto') or ''}".strip()
        for it in items[1:])
    sistema = SISTEMA_TARJETA.format(cuerpo=CUERPOS[TAMANOS[tamano]])
    docs = sei.cargar()
    if docs["texto"]:
        sistema = [{"type": "text", "text": docs["texto"], "cache_control": {"type": "ephemeral"}},
                   {"type": "text", "text": sistema}]
    nombre = modelo.modelo_configurado(ajustes)
    texto, uso = (llamar or modelo.llamar_anthropic)(sistema, mensaje, nombre, ajustes.get("max_tokens_tarjeta", 1500))
    del docs, sistema
    uso = modelo._con_costo(uso, nombre, ajustes)
    datos = modelo._extraer_json(texto)

    # Controles: los mismos del barrido (nada que no esté en el texto enviado) y los de estilo.
    texto_enviado = calidad.texto_items(items)
    tarjeta = {"clasificacion": "confirmado", "fecha": reg["fecha"], "titulo": limpiar_texto(datos.get("titulo")) or reg["titulo"],
               "resumen": limpiar_texto(datos.get("resumen")),
               "fuentes": [{"nombre": f.get("nombre", ""), "fecha": f.get("fecha"), "url": f.get("url", "")} for f in reg["fuentes"]]}
    avisos = []
    ok, motivos = control.verificar_nombres(tarjeta, texto_enviado)
    if ok:
        ok, m2 = calidad.aplicar_regla_maxima(tarjeta, texto_enviado)
        motivos += m2
    if not ok or not tarjeta.get("resumen"):
        avisos.append("El título o la bajada tenían datos que no están en el registro ni en las notas: se usó el título del registro.")
        tarjeta.update(titulo=reg["titulo"], resumen=reg["resumen"] or "")
    avisos += motivos
    if TAMANOS[tamano]:
        cuerpo, cambios = modelo.verificar_cuerpo(str(datos.get("cuerpo") or ""), tarjeta["titulo"], items)
        parrafos = [p for p in cuerpo.split("\n\n") if p][:TAMANOS[tamano]]
        if parrafos:
            tarjeta["cuerpo"] = "\n\n".join(parrafos)
        avisos += cambios
    imagenes = modelo.imagenes_de_fuentes(items[1:])
    if imagenes:
        tarjeta["imagen"], tarjeta["imagenes"] = imagenes[0]["url"], imagenes
    revision.marcar([tarjeta])
    tarjeta["revisar"] = list(dict.fromkeys((tarjeta.get("revisar") or []) + ["tarjeta creada desde el registro"]))

    hoy = ahora.date()
    corrida = {"id": f"registro-{ahora.strftime('%Y%m%dT%H%M%S')}", "origen": "registro", "titulo_mostrar": "Tarjeta creada desde el registro",
               "estado": "ok", "motivo": None, "tarjetas": [tarjeta], "escena_latam": {"tarjetas": [], "texto": None},
               "notas": [f"Creada desde {origen} ({clave}), tamaño {tamano}."], "uso": uso, "controles": [{"motivo": a} for a in avisos]}
    agregar_corrida(raiz, hoy, corrida)
    nid = registro.id_hecho(hoy.isoformat(), corrida["id"], tarjeta["titulo"])
    ruta_ed = raiz / "data" / "ediciones.json"
    ediciones = leer_json(ruta_ed, None) or {"tarjetas": {}}
    ediciones.setdefault("tarjetas", {})[nid] = {
        "oculta": True, "original": {"titulo": tarjeta["titulo"], "resumen": tarjeta["resumen"]},
        "historial": [{"fecha": ahora.strftime("%Y-%m-%d %H:%M UTC"), "motivo": f"Creada desde {origen} ({tamano}); oculta hasta revisarla.", "campos": ["oculta"]}]}
    escribir_json(ruta_ed, ediciones)
    ruta_costos = raiz / "data" / "costos.json"
    costos = leer_json(ruta_costos, {"corridas": []})
    costos["corridas"].append({"fecha": hoy.isoformat(), "id": corrida["id"], "tipo": "tarjeta_registro", "modelo": nombre,
                               "tokens_entrada": uso.get("tokens_entrada", 0), "tokens_salida": uso.get("tokens_salida", 0),
                               **{k: uso[k] for k in ("tokens_cache_escritura", "tokens_cache_lectura") if uso.get(k)},
                               "costo_usd": uso.get("costo_usd", 0.0), "estado": "ok", "tamano": tamano})
    escribir_json(ruta_costos, costos)
    return {"nid": nid, "titulo": tarjeta["titulo"], "tamano": tamano, "costo_usd": uso.get("costo_usd", 0.0), "avisos": avisos}


def main() -> None:
    ap = argparse.ArgumentParser(description="Crea una tarjeta oculta desde el registro.")
    ap.add_argument("--origen", required=True, choices=("hechos", "archivo"))
    ap.add_argument("--clave", required=True)
    ap.add_argument("--tamano", required=True, choices=tuple(TAMANOS))
    a = ap.parse_args()
    privado = os.environ.get("MESA_DIR_PRIVADO", "").strip()
    r = crear(RAIZ, Path(privado) if privado else None, a.origen, a.clave, a.tamano)
    # Solo datos de la tarjeta creada (nunca el contenido de los documentos de SEI).
    print(json.dumps({k: r[k] for k in ("nid", "tamano", "costo_usd")}, ensure_ascii=False))
    print(f"Avisos: {len(r['avisos'])}")


if __name__ == "__main__":
    main()
