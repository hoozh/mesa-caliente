"""Una sola llamada al modelo por corrida: selecciona, agrupa, clasifica y resume."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Callable

from . import control
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
5. Aparte, complete "escena_latam" con hasta {max_latam} hechos sobre jugadores argentinos o latinoamericanos, o torneos como CAP, WCOOP, WSOP Online, SCOOP, CLSOP o BSOP con protagonistas latinos. Dé prioridad a las fuentes de región latam y argentina. Un hecho no debe repetirse en "tarjetas" y "escena_latam".

Reglas estrictas:
- Use solo la información de los titulares, primeras líneas y extractos recibidos. Nunca invente hechos, fechas, cifras, nombres ni enlaces.
- No escriba URLs ni fechas: el programa las agrega a partir de los números que usted indique.
- Si una cifra o dato no aparece en el material, no lo mencione.
- Términos exactos: conserve el premio o título que nombra la fuente, traducido de forma literal. "Bracelet" es "brazalete" (WSOP); "ring" es "anillo" (WSOP Circuit y otros circuitos); "trophy" es "trofeo". Nunca cambie uno por otro: si la fuente dice "ring", no escriba "brazalete". Lo mismo vale para nombres de torneos, eventos y circuitos: use el nombre que da la fuente.
- Nacionalidad: si el titular, la primera línea o el extracto indican la nacionalidad de un jugador (por ejemplo "el argentino...", "Brazilian..."), inclúyala en el resumen. En "escena_latam", si la fuente no indica la nacionalidad de un jugador, escriba "[nacionalidad a confirmar]" junto a su nombre; nunca la deduzca del nombre, del alias ni de la sala.
- Nombres propios: escriba los nombres de personas exactamente como aparecen en el material recibido. Nunca complete, agregue ni invente nombres de pila, apellidos o alias: si la fuente solo da un alias, use solo el alias; si solo da el apellido, use solo el apellido. El programa elimina las partes de un nombre que no figuren en el material y descarta la tarjeta si el nombre completo no figura.
- Ventana de la corrida: el mensaje indica desde qué fecha y hora se tomaron los titulares. Cada tarjeta debe tratar un hecho ocurrido o informado dentro de esa ventana.
- Hechos ya publicados: al final del mensaje recibirá los títulos de las tarjetas publicadas en los últimos días, numerados P1, P2, etc. No cree una tarjeta para un hecho ya cubierto ni para un hecho anterior a la ventana. Solo si hay un desarrollo nuevo (un resultado final, una cifra nueva, una confirmación, una respuesta oficial), cree la tarjeta e indique "actualiza" con el número del título previo (por ejemplo "P3") y "novedad" con una frase que diga exactamente qué cambió; el resumen debe contar ese cambio.

Responda únicamente con un objeto JSON, sin texto adicional, con esta forma:
{{"tarjetas": [{{"ids": [1, 4], "clasificacion": "confirmado", "titulo": "...", "resumen": "..."}},
              {{"ids": [9], "clasificacion": "confirmado", "titulo": "...", "resumen": "...", "actualiza": "P3", "novedad": "..."}}],
 "escena_latam": [{{"ids": [7], "clasificacion": "confirmado", "titulo": "...", "resumen": "..."}}]}}"""


def construir_mensaje(items: list[dict], previos: list[str] | None = None, corte: datetime | None = None) -> str:
    lineas = []
    for n, it in enumerate(items, 1):
        fecha = (it.get("fecha") or "sin fecha")[:10]
        linea = f"[{n}] {it['fuente']} ({it.get('region') or 'internacional'}) | {fecha} | {limpiar_texto(it['titulo'])}"
        if it.get("primera_linea"):
            linea += f" | {limpiar_texto(it['primera_linea'])}"
        if it.get("extracto"):
            linea += f"\n    Extracto: {limpiar_texto(it['extracto'])}"
        lineas.append(linea)
    encabezado = (f"Titulares publicados desde el {corte:%Y-%m-%d %H:%M} UTC (ventana de esta corrida):"
                  if corte else "Titulares de hoy:")
    mensaje = encabezado + "\n\n" + "\n".join(lineas)
    if previos:
        mensaje += ("\n\nTítulos ya publicados en los últimos días (no repetir salvo desarrollo nuevo):\n"
                    + "\n".join(f"[P{n}] {limpiar_texto(t)}" for n, t in enumerate(previos, 1)))
    return mensaje


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


def texto_enviado(items: list[dict]) -> str:
    """Todo el texto de los titulares que recibe el modelo (para verificar nombres)."""
    return " ".join(f"{it.get('titulo') or ''} {it.get('primera_linea') or ''} {it.get('extracto') or ''}"
                    for it in items)


def _previo_indicado(valor, previos: list[str]) -> str | None:
    m = re.fullmatch(r"\s*P?\s*(\d+)\s*", str(valor or ""), re.I)
    if m and 1 <= int(m.group(1)) <= len(previos):
        return previos[int(m.group(1)) - 1]
    return None


def _construir_tarjetas(crudas, items: list[dict], maximo: int, usados: set[int], hoy=None,
                        previos: list[str] | None = None, es_latam: bool = False,
                        registro: list[dict] | None = None, enviado: str | None = None) -> list[dict]:
    previos = previos or []
    enviado = texto_enviado(items) if enviado is None else enviado
    registro = registro if registro is not None else []
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

        # Hechos ya publicados: solo pasan si el modelo declara qué cambió.
        actualiza = _previo_indicado(c.get("actualiza"), previos)
        novedad = limpiar_texto(str(c.get("novedad") or ""))
        if actualiza and not novedad:
            registro.append({"titulo": titulo, "motivo": "repetida sin novedad", "previo": actualiza})
            continue
        if not actualiza:
            previo = control.parece_repetida(titulo, previos)
            if previo:
                registro.append({"titulo": titulo, "motivo": "repetida", "previo": previo})
                continue

        fuentes_items = [items[i - 1] for i in ids]
        fuentes = []
        for it in fuentes_items:
            f = _fecha_item(it)
            fuentes.append({"nombre": it["fuente"], "fecha": f.isoformat() if f else None, "url": it["url"]})
        fechas = [f for f in (_fecha_item(it) for it in fuentes_items) if f]
        tarjeta = {
            "clasificacion": clasif,
            "fecha": rango_fechas(fechas) or (f"sin fecha en la fuente · vista el {fecha_corta(hoy)}" if hoy else "sin fecha en la fuente"),
            "titulo": titulo,
            "resumen": resumen,
            "fuentes": fuentes,
        }
        if actualiza:
            tarjeta["actualiza"] = actualiza
            tarjeta["novedad"] = novedad
        conservar, cambios = control.verificar_nombres(tarjeta, enviado)
        for cambio in cambios:
            registro.append({"titulo": titulo, "motivo": cambio})
        if not conservar:
            continue
        usados.update(ids)
        for cambio in control.corregir_terminos(tarjeta, fuentes_items):
            registro.append({"titulo": tarjeta["titulo"], "motivo": f"término corregido ({cambio})"})
        cambio = control.asegurar_nacionalidad(tarjeta, fuentes_items, es_latam)
        if cambio:
            registro.append({"titulo": tarjeta["titulo"], "motivo": cambio})
        tarjetas.append(tarjeta)
    return tarjetas


def resumir(items: list[dict], ajustes: dict, llamar: Llamador = llamar_anthropic, hoy=None,
            previos: list[str] | None = None, corte: datetime | None = None) -> dict:
    """Devuelve {'tarjetas', 'escena_latam', 'controles', 'uso'} o lanza ErrorModelo."""
    previos = list(previos or [])[: ajustes.get("max_titulos_previos", 80)]
    modelo = modelo_configurado(ajustes)
    sistema = SISTEMA.format(max_tarjetas=ajustes.get("max_tarjetas", 12),
                             max_latam=ajustes.get("max_tarjetas_latam", 6))
    mensaje = construir_mensaje(items, previos, corte)
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
    registro: list[dict] = []
    tarjetas = _construir_tarjetas(datos.get("tarjetas"), items, ajustes.get("max_tarjetas", 12), usados, hoy,
                                   previos, False, registro)
    latam = _construir_tarjetas(datos.get("escena_latam"), items, ajustes.get("max_tarjetas_latam", 6), usados, hoy,
                                previos, True, registro)
    return {"tarjetas": tarjetas, "escena_latam": latam, "controles": registro, "uso": uso}
