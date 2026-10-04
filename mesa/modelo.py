"""Una sola llamada al modelo por corrida: selecciona, agrupa, clasifica y resume."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Callable

from .util import esta_bloqueado, fecha_corta, limpiar_texto, rango_fechas

CLASIFICACIONES = {"confirmado", "discusion", "rumor"}

SISTEMA = """Usted es el editor de "Mesa Caliente", un resumen diario de noticias de poker.
Escriba siempre en español neutro y formal, sin voseo (use "usted" o formas impersonales).

Recibirá una lista numerada de titulares recientes. Cada línea tiene: número, fuente, región de la fuente, fecha, titular y primera línea; algunos traen además un extracto del artículo.

Tareas:
1. Elija hasta {max_tarjetas} hechos relevantes para la comunidad de poker (torneos importantes, resultados destacados, regulación, salas online, escándalos, industria). Ignore promociones comerciales, artículos de estrategia sin noticia y contenido que no sea de poker.
2. Agrupe por hecho: si varias fuentes cuentan el mismo hecho, van en una sola tarjeta con todos sus números.
3. Clasifique cada tarjeta: "confirmado" (anuncio oficial o resultado verificado), "discusion" (tema abierto, polémica o versiones encontradas), "rumor" (sin confirmación).
4. Escriba un título en español y un resumen de 2 a 3 líneas.
5. Aparte, complete "escena_latam" con hasta {max_latam} hechos sobre jugadores argentinos o latinoamericanos, o torneos como CAP, WCOOP, WSOP Online, SCOOP, CLSOP o BSOP con protagonistas latinos. Dé prioridad a las fuentes de región latam y argentina. Si la fuente no indica la nacionalidad de un jugador, escriba "[nacionalidad a confirmar]" junto a su nombre. Un hecho no debe repetirse en "tarjetas" y "escena_latam".

Reglas estrictas:
- Use solo la información de los titulares, primeras líneas y extractos recibidos. Nunca invente hechos, fechas, cifras, nombres ni enlaces.
- No escriba URLs ni fechas: el programa las agrega a partir de los números que usted indique.
- Si una cifra o dato no aparece en el material, no lo mencione.

Responda únicamente con un objeto JSON, sin texto adicional, con esta forma:
{{"tarjetas": [{{"ids": [1, 4], "clasificacion": "confirmado", "titulo": "...", "resumen": "..."}}],
 "escena_latam": [{{"ids": [7], "clasificacion": "confirmado", "titulo": "...", "resumen": "..."}}]}}"""


def construir_mensaje(items: list[dict]) -> str:
    lineas = []
    for n, it in enumerate(items, 1):
        fecha = (it.get("fecha") or "sin fecha")[:10]
        linea = f"[{n}] {it['fuente']} ({it.get('region') or 'internacional'}) | {fecha} | {limpiar_texto(it['titulo'])}"
        if it.get("primera_linea"):
            linea += f" | {limpiar_texto(it['primera_linea'])}"
        if it.get("extracto"):
            linea += f"\n    Extracto: {limpiar_texto(it['extracto'])}"
        lineas.append(linea)
    return "Titulares de hoy:\n\n" + "\n".join(lineas)


def modelo_configurado(ajustes: dict) -> str:
    return (os.environ.get("MESA_MODELO") or "").strip() or ajustes.get("modelo_por_defecto", "claude-haiku-4-5")


def costo_estimado(modelo: str, entrada: int, salida: int, ajustes: dict) -> float:
    precios = ajustes.get("precios_usd_por_millon", {})
    p = precios.get(modelo)
    if p is None:
        p = next((v for k, v in precios.items() if modelo.startswith(k)), None)
    if p is None:  # modelo desconocido: se estima con el precio más alto conocido
        p = max(precios.values(), key=lambda v: v["salida"], default={"entrada": 0, "salida": 0})
    try:
        entrada_p = float(os.environ.get("MESA_PRECIO_ENTRADA") or p["entrada"])
        salida_p = float(os.environ.get("MESA_PRECIO_SALIDA") or p["salida"])
    except ValueError:
        entrada_p, salida_p = p["entrada"], p["salida"]
    return round(entrada * entrada_p / 1e6 + salida * salida_p / 1e6, 6)


class ErrorModelo(Exception):
    def __init__(self, motivo: str, uso: dict | None = None):
        super().__init__(motivo)
        self.uso = uso or {}


Llamador = Callable[[str, str, str, int], tuple[str, dict]]


def llamar_anthropic(sistema: str, mensaje: str, modelo: str, max_tokens: int) -> tuple[str, dict]:
    """Llamada real a la API. Devuelve (texto, uso)."""
    import anthropic

    clave = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not clave:
        raise ErrorModelo("Falta la clave ANTHROPIC_API_KEY (secreto no configurado).")
    cliente = anthropic.Anthropic(api_key=clave, timeout=180.0, max_retries=2)
    try:
        r = cliente.messages.create(
            model=modelo,
            max_tokens=max_tokens,
            system=sistema,
            messages=[{"role": "user", "content": mensaje}],
        )
    except anthropic.AuthenticationError as e:
        raise ErrorModelo("La clave ANTHROPIC_API_KEY fue rechazada (401).") from e
    except anthropic.NotFoundError as e:
        raise ErrorModelo(f"El modelo '{modelo}' no existe o no está disponible (404).") from e
    except anthropic.RateLimitError as e:
        raise ErrorModelo("Límite de uso o saldo agotado en la API (429).") from e
    except anthropic.APIStatusError as e:
        raise ErrorModelo(f"La API respondió con error {e.status_code}.") from e
    except anthropic.APIConnectionError as e:
        raise ErrorModelo("No se pudo conectar con la API de Anthropic.") from e
    uso = {"tokens_entrada": r.usage.input_tokens, "tokens_salida": r.usage.output_tokens}
    texto = "".join(b.text for b in r.content if getattr(b, "type", "") == "text")
    if r.stop_reason == "refusal":
        raise ErrorModelo("El modelo rechazó la solicitud.", uso)
    if r.stop_reason == "max_tokens":
        uso["truncado"] = True
    return texto, uso


def _extraer_json(texto: str) -> dict:
    texto = texto.strip()
    texto = re.sub(r"^```(?:json)?\s*|\s*```$", "", texto)
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio >= 0 and fin > inicio:
        try:
            return json.loads(texto[inicio:fin + 1])
        except json.JSONDecodeError:
            pass
    raise ErrorModelo("La respuesta del modelo no es un JSON válido.")


def _fecha_item(it: dict):
    if not it.get("fecha"):
        return None
    try:
        return datetime.fromisoformat(it["fecha"]).date()
    except ValueError:
        return None


def _construir_tarjetas(crudas, items: list[dict], maximo: int, usados: set[int], hoy=None) -> list[dict]:
    tarjetas = []
    if not isinstance(crudas, list):
        return tarjetas
    for c in crudas:
        if len(tarjetas) >= maximo or not isinstance(c, dict):
            continue
        ids = []
        for i in c.get("ids") or []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(items) and i not in ids and not esta_bloqueado(items[i - 1]["url"]):
                ids.append(i)
        titulo = limpiar_texto(str(c.get("titulo") or ""))
        resumen = limpiar_texto(str(c.get("resumen") or ""))
        clasif = str(c.get("clasificacion") or "").lower().replace("ó", "o").replace(" ", "_")
        if clasif in ("en_discusion", "discusión"):
            clasif = "discusion"
        if not ids or not titulo or not resumen or clasif not in CLASIFICACIONES:
            continue
        if set(ids) <= usados:  # el mismo hecho ya está en otra tarjeta
            continue
        usados.update(ids)
        fuentes = []
        for i in ids:
            it = items[i - 1]
            f = _fecha_item(it)
            fuentes.append({"nombre": it["fuente"], "fecha": f.isoformat() if f else None, "url": it["url"]})
        fechas = [f for f in (_fecha_item(items[i - 1]) for i in ids) if f]
        tarjetas.append({
            "clasificacion": clasif,
            "fecha": rango_fechas(fechas) or (f"sin fecha en la fuente · vista el {fecha_corta(hoy)}" if hoy else "sin fecha en la fuente"),
            "titulo": titulo,
            "resumen": resumen,
            "fuentes": fuentes,
        })
    return tarjetas


def resumir(items: list[dict], ajustes: dict, llamar: Llamador = llamar_anthropic, hoy=None) -> dict:
    """Devuelve {'tarjetas', 'escena_latam', 'uso'} o lanza ErrorModelo."""
    modelo = modelo_configurado(ajustes)
    sistema = SISTEMA.format(max_tarjetas=ajustes.get("max_tarjetas", 12),
                             max_latam=ajustes.get("max_tarjetas_latam", 6))
    mensaje = construir_mensaje(items)
    texto, uso = llamar(sistema, mensaje, modelo, ajustes.get("max_tokens_salida", 3000))
    uso = dict(uso)
    uso["modelo"] = modelo
    uso["costo_usd"] = costo_estimado(modelo, uso.get("tokens_entrada", 0), uso.get("tokens_salida", 0), ajustes)
    try:
        datos = _extraer_json(texto)
    except ErrorModelo as e:
        motivo = str(e) + (" La salida se cortó por el tope de tokens." if uso.get("truncado") else "")
        raise ErrorModelo(motivo, uso) from e
    usados: set[int] = set()
    tarjetas = _construir_tarjetas(datos.get("tarjetas"), items, ajustes.get("max_tarjetas", 12), usados, hoy)
    latam = _construir_tarjetas(datos.get("escena_latam"), items, ajustes.get("max_tarjetas_latam", 6), usados, hoy)
    return {"tarjetas": tarjetas, "escena_latam": latam, "uso": uso}
