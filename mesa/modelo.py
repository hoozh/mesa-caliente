"""Dos llamadas cortas al modelo por corrida.

1. Selección: solo titulares y primera línea; elige y agrupa los hechos que valen una tarjeta.
2. Redacción: solo las notas elegidas, con el extracto de su artículo; clasifica y resume.

Así el modelo escribe con el texto real de la nota (los controles pueden rastrear cada dato)
y la cantidad de tarjetas no depende de una segunda llamada de recuperación."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from typing import Callable

from . import calidad, control
from .util import esta_bloqueado, fecha_corta, limpiar_texto, rango_fechas

CLASIFICACIONES = {"confirmado", "discusion", "rumor"}

SISTEMA = """Usted es el editor de "Mesa Caliente", un resumen diario de noticias de poker.
Escriba siempre en español neutro y formal, sin voseo (use "usted" o formas impersonales).

Recibirá una lista numerada de titulares recientes. Cada línea tiene: número, fuente (con su región y su clase: oficial, medio o comunidad), fecha, titular y primera línea; algunos traen además un extracto del artículo.

Tareas:
1. Elija hasta {max_tarjetas} hechos relevantes para la comunidad de poker (polémicas, personalidades, cambios de reglas, regulación, salas online, ofertas, entrevistas, opinión, industria del poker). Resultados de torneos solo en "escena_latam" (vea la regla de resultados más abajo).
2. Agrupe por hecho: si varias fuentes cuentan el mismo hecho, van en una sola tarjeta con todos sus números.
3. Clasifique cada tarjeta:
   - "confirmado": solo si lo informa una fuente oficial o un medio especializado con fecha, y se trata de un resultado, un anuncio oficial o un hecho verificado.
   - "discusion": tema abierto, polémica, versiones encontradas, o cualquier hecho respaldado solo por fuentes de clase "comunidad" (Reddit, foros) o por fuentes sin fecha.
   - "rumor": sin confirmación.
4. Escriba un título en español, un resumen de 2 a 3 líneas (la bajada de la tarjeta) y un "cuerpo": la noticia contada en 3 o 4 párrafos separados por una línea en blanco ("\n\n"), para la página propia de la noticia, con el formato de las notas de GipsyTeam:
   - Primer párrafo con los datos duros: cuándo (día de la semana y fecha, si la fuente los da), qué, dónde (sala, o sede y ciudad) y cuánto (entradas, buy-in, pozo, premio).
   - Nacionalidad antes del nombre ("el argentino Alejandro “Papo MC” Lococo"), solo si la fuente la dice; el apodo entre comillas dentro del nombre. Puestos con "°" ("6°").
   - Resultados: después, field, pozo y jugadores conocidos que cobraron con su puesto entre paréntesis. Industria o polémica: el hecho, quién lo anunció o lo informó, qué implica para los jugadores y, si está en discusión, qué queda por confirmar.
   - Las fuentes se nombran en el texto ("según PokerNews", "informó la sala"); sin línea "Fuente:". Narración impersonal, sin opiniones propias, sin emojis.
   - Solo datos del titular, la primera línea y el extracto de las notas que cita. Si el texto no alcanza para tres párrafos con datos, escriba menos (uno o dos); nunca complete con suposiciones ni con relleno. La jerga del poker y los nombres propios quedan en inglés dentro del título en español (por ejemplo: "César García, runner-up en el GGMillion$ PLO"; "Ivelin Kolev gana el WPT Prime Cyprus Championship").
5. Aparte, complete "escena_latam" con hasta {max_latam} hechos sobre jugadores argentinos o latinoamericanos, o torneos latinoamericanos (CAP, BSOP, CLSOP, LAPT y similares). Dé prioridad a las fuentes de región latam y argentina. Un hecho no debe repetirse en "tarjetas" y "escena_latam".
6. En cada tarjeta y en cada tema agregue "registro", una ficha breve para el archivo interno, solo con datos escritos en las notas que cita. Omita los campos que no figuren:
   - "tipo": resultado, anuncio_evento, industria_polemica, software, obituario, oferta u otro.
   - "salas": salas de poker online nombradas.
   - "jugadores": lista de [nombre, país]. El país solo si la nota lo dice de esa persona; si no, "". Nunca lo deduzca del nombre, del alias ni de la sala.
   - "torneo": torneo o serie, con el nombre que da la fuente.
   - "pais_evento": país donde se juega, solo si la nota lo nombra.
7. "cambios_salas": por cada nota que trae un cambio de una sala vigilada ({salas}) sobre uno de estos temas ({temas_salas}), agregue un elemento con "ids" (la nota), "sala", "tema" (la clave) y "dato": un fragmento copiado letra por letra de esa nota, en su idioma, sin traducir ni resumir. Las notas marcadas "solo cambios_salas" van únicamente aquí: nunca en "tarjetas" ni en "escena_latam". Si no hay cambios, deje la lista vacía.

Temas calientes:
- Algunas notas llegan marcadas con [tema: NOMBRE]. Esas notas van SOLO en la sección "temas", nunca en "tarjetas" ni en "escena_latam".
- Por cada tema escriba una "linea_principal" (una oración que resuma el estado del tema con datos del material) y una lista de "novedades": una por nota, con "id" (el número de UNA nota), "clasificacion" y "aporta" (qué dato nuevo trae esa nota: respuesta de una sala, medida, cifra, cita, acción legal, reacción de un jugador u otro hecho). Si dos fuentes difieren, explique la diferencia solo con lo que dice cada una ("según X…; según Y…").
- Si una nota del tema no aporta ningún dato nuevo respecto de lo ya publicado, no la incluya.

Qué no publicar:
- Notas sin relación con el poker (otros deportes, apuestas deportivas o casino sin poker, celebridades ajenas al poker).
- Satélites y calendarios comerciales. Las ofertas de salas de poker (bonos, promociones, rakeback) sí se publican: en su ficha, "tipo": "oferta".
- Resultados de torneos fuera de la regla de resultados (vea "Regla de resultados").
- Notas sin dato concreto: un resultado debe nombrar al ganador; toda tarjeta necesita al menos un nombre, un monto o un evento con nombre. Si el titular no los trae ("campeón francés en Marrakesh", "una mano decide al campeón"), no cree la tarjeta salvo que el extracto los dé.
- Hechos ya publicados (ver más abajo), salvo con un desarrollo nuevo y concreto.

Regla de resultados: un resultado de torneo solo se publica si es (a) la definición de un circuito o torneo nacional de América Latina (campeón o mesa final), o (b) una mesa final o mejor de un jugador latinoamericano en una serie de marca mundial (WSOP en todas sus variantes, WPT, LAPT, EPT, Triton); esos van en "escena_latam". También (c) un resultado de una figura histórica del poker o uno que bate un récord o mueve una lista histórica (all-time money list, cantidad de brazaletes o títulos); esos van en "tarjetas". Ningún otro resultado se publica, tampoco avances de días iniciales.

Reglas estrictas:
- REGLA MÁXIMA: nunca invente datos. Toda cifra, nombre, cargo, edad, nacionalidad, fecha, lugar, cita o hecho que escriba debe estar en el texto de las notas que cita esa tarjeta (o esa novedad). Si una fuente no precisa algo, escriba "la fuente no precisa …"; nunca lo complete con su conocimiento. El programa borra lo que no pueda rastrear y descarta la tarjeta si pierde su dato principal.
- Use solo la información de los titulares, primeras líneas y extractos recibidos. Nunca invente hechos, fechas, cifras, nombres ni enlaces.
- Las citas textuales solo pueden copiarse exactas, en el idioma de la fuente; si no, cuente lo que dijo sin comillas.
- No escriba URLs ni fechas: el programa las agrega a partir de los números que usted indique.
- Si una cifra o dato no aparece en el material, no lo mencione.
- Datos de personas: cargos, posiciones de juego o deportivas, edades y nacionalidades solo si aparecen literalmente en el material recibido. No los deduzca ni los complete con su conocimiento.
- Jerga del poker: conserve los términos en inglés tal como los usa la comunidad (score, runner-up, chip leader, bounty, heads-up, bubble, sevens, etc.); no los traduzca. Tampoco traduzca nombres de premios, rankings ni eventos ("Purple Jacket", "Player of the Year", "Main Event"). Use tildes en las palabras en español ("prohíbe", "Día").
- Tono: no copie superlativos ni elogios de la fuente ("el más prestigioso del mundo", "espectacular", "histórico", "increíble"). Escriba de forma neutra.
- Números (formato de GipsyTeam): dólares con "$" pegado y coma de miles ("$110,938", "$1,050", "$343,242.10"); abreviados solo si la fuente los abrevia, con coma decimal ("$1,4M"). Cantidades que no son dinero, con punto de miles ("2.310 inscripciones"). Otras monedas con su símbolo y sin convertir ("€550", "R$ 61K").
- Montos: la moneda es la que indica la fuente (dólares, €, £, R$, MXN, etc.). Si una fuente latinoamericana solo pone "$", escriba "$ (moneda a confirmar)" después del número y no lo convierta en dólares.
- Términos exactos: conserve el premio o título que nombra la fuente, traducido de forma literal. "Bracelet" es "brazalete" (WSOP); "ring" es "anillo" (WSOP Circuit y otros circuitos); "trophy" es "trofeo". Nunca cambie uno por otro: si la fuente dice "ring", no escriba "brazalete". Lo mismo vale para nombres de torneos, eventos y circuitos: use el nombre que da la fuente.
- Nacionalidad: si el titular, la primera línea o el extracto indican la nacionalidad de un jugador (por ejemplo "el argentino...", "Brazilian..."), inclúyala en el resumen. En "escena_latam", solo cuando la tarjeta nombra a una persona y la fuente no indica su nacionalidad, escriba "[nacionalidad a confirmar]" junto a su nombre; nunca la deduzca del nombre, del alias ni de la sala.
- Nombres propios: escriba los nombres de personas exactamente como aparecen en el material recibido. Nunca complete, agregue ni invente nombres de pila, apellidos o alias: si la fuente solo da un alias, use solo el alias; si solo da el apellido, use solo el apellido. El programa elimina las partes de un nombre que no figuren en el material y descarta la tarjeta si el nombre completo no figura.
- Ventana de la corrida: el mensaje indica desde qué fecha y hora se tomaron los titulares. Cada tarjeta debe tratar un hecho ocurrido o informado dentro de esa ventana.
- Hechos ya publicados: al final del mensaje recibirá las tarjetas ya publicadas, numeradas P1, P2, etc. Las publicadas hoy por corridas anteriores llegan con su resumen; las de días anteriores, solo con el título. No cree una tarjeta para un hecho ya cubierto ni para un hecho anterior a la ventana. Solo si hay un desarrollo nuevo y concreto (un resultado final, una cifra nueva, una confirmación oficial, una respuesta de los involucrados) que no figure en la tarjeta publicada, cree la tarjeta e indique "actualiza" con el número de la tarjeta previa (por ejemplo "P3") y "novedad" con una frase que diga exactamente qué cambió; el resumen debe contar ese cambio. Repetir con otras palabras lo ya publicado no es una novedad.

Responda únicamente con un objeto JSON, sin texto adicional, con esta forma (en este orden: temas, tarjetas, escena_latam, cambios_salas):
{{"temas": [{{"tema": "NOMBRE", "linea_principal": "...", "novedades": [{{"id": 2, "clasificacion": "confirmado", "aporta": "..."}}], "registro": {{"tipo": "industria_polemica"}}}}],
 "tarjetas": [{{"ids": [1, 4], "clasificacion": "confirmado", "titulo": "...", "resumen": "...", "cuerpo": "...\n\n...", "registro": {{"tipo": "resultado", "jugadores": [["...", "..."]], "torneo": "..."}}}},
              {{"ids": [9], "clasificacion": "confirmado", "titulo": "...", "resumen": "...", "actualiza": "P3", "novedad": "...", "registro": {{"tipo": "otro"}}}}],
 "escena_latam": [{{"ids": [7], "clasificacion": "confirmado", "titulo": "...", "resumen": "...", "registro": {{"tipo": "resultado"}}}}],
 "cambios_salas": [{{"ids": [5], "sala": "...", "tema": "rake_rakeback", "dato": "..."}}]}}"""


SELECCION = """Usted es el editor de "Mesa Caliente", un resumen diario de noticias de poker para un público hispanohablante. En este paso solo elige: no escribe tarjetas.

Recibirá una lista numerada de titulares. Cada línea tiene: número, fuente (con su región y su clase: oficial, medio o comunidad), fecha, titular y primera línea. Los marcados "(pendiente)" son de las últimas 48 horas y todavía no se publicaron.

Qué buscamos (escena mundial): debates y polémicas, chismes y novedades de grandes personalidades del poker, cambios de reglas o de políticas importantes o discutidos, temas del momento, regulación y mercados, novedades de salas y operadores, ofertas de salas de poker, entrevistas, artículos de opinión, artículos interesantes, escándalos e integridad, historia y figuras del poker.
Personalidades muy reconocidas (sus noticias tienen prioridad, salvo que sean solo un resultado fuera de la regla): {personalidades}.

Regla de resultados de torneos: un resultado solo se publica si es
  (a) la definición de un circuito o torneo nacional de América Latina (campeón o mesa final), o
  (b) una mesa final o mejor de un jugador latinoamericano en una serie de marca mundial: WSOP (todas sus variantes), WPT, LAPT, EPT o Triton, o
  (c) un resultado de una figura histórica del poker ({historicas}) o un resultado que bate un récord o mueve una lista histórica (all-time money list, cantidad de brazaletes o títulos y similares). Estos van en "general".
Todos los demás resultados (de cualquier país, también avances de días iniciales y líderes de fichas) van a la sección "archivo": no se publican, pero quedan guardados con sus fuentes.

Tareas:
1. Agrupe por hecho: todos los números que cuentan el mismo hecho van juntos en un solo elemento.
2. Elija TODOS los hechos relevantes; no se detenga en una cantidad. Secciones:
   - "general" (hasta {max_general}): escena mundial.
   - "latam" (hasta {max_latam}): protagonistas argentinos o latinoamericanos, o torneos latinoamericanos. Que la fuente sea latinoamericana no basta.
   - "europa" (hasta {max_europa}): hechos de España o de la escena europea (jugadores, circuitos, regulación). Los resultados siguen la regla de resultados.
   - "salas" (hasta {max_salas}; no se publica, alimenta un registro interno): notas que cambian un dato de una sala vigilada ({salas}) sobre estos temas: {temas_salas}. Si el hecho merece tarjeta, va en otra sección, no aquí.
   - "archivo" (hasta {max_archivo}; no se publica): resultados de torneos que no cumplen la regla de resultados.
   Cada hecho va en una sola sección.
3. Los pendientes valen igual que los de la ventana: si son relevantes y no se publicaron, elíjalos.

No elija:
- Notas sin relación con el poker (otros deportes, apuestas deportivas o casino sin poker).
- Satélites y calendarios comerciales.
- Titulares sin nombre de persona, cifra ni evento con nombre.
- Hechos ya publicados (lista P al final), salvo un desarrollo nuevo y concreto (resultado final, cifra nueva, confirmación oficial, respuesta de los involucrados): en ese caso agregue "actualiza": "P3".
- Notas de comunidad (Reddit, foros) solas, salvo que traten un hecho de alto impacto para el poker (escándalo, trampas, seguridad, regulación) o una polémica muy comentada.

Responda únicamente con un objeto JSON, sin texto adicional:
{{"hechos": [{{"ids": [3, 17], "seccion": "general"}}, {{"ids": [8], "seccion": "latam"}}, {{"ids": [4], "seccion": "europa"}}, {{"ids": [11], "seccion": "salas"}}, {{"ids": [21, 22], "seccion": "archivo"}}]}}"""


def textos_salas(salas: dict | None) -> dict:
    """Salas vigiladas y temas, como texto para los mensajes al sistema."""
    salas = salas or {}
    nombres = ", ".join(s["nombre"] for s in salas.get("salas", [])) or "ninguna"
    temas = ", ".join(f"{k}: {v.lower()}" for k, v in (salas.get("temas") or {}).items()) or "ninguno"
    return {"salas": nombres, "temas_salas": temas}


def normalizar_previos(previos) -> list[dict]:
    """Acepta títulos sueltos o tarjetas {titulo, resumen, hoy} y devuelve tarjetas."""
    resultado = []
    for p in previos or []:
        if isinstance(p, str):
            resultado.append({"titulo": p, "resumen": "", "hoy": False})
        elif isinstance(p, dict) and p.get("titulo"):
            resultado.append({"titulo": p["titulo"], "resumen": p.get("resumen") or "", "hoy": bool(p.get("hoy")),
                              # Texto completo de la tarjeta publicada (solo para los controles, no va al modelo).
                              "texto": p.get("texto") or f"{p['titulo']} {p.get('resumen') or ''}"})
    return resultado


def construir_mensaje(items: list[dict], previos=None, corte: datetime | None = None,
                      grupos: list[dict] | None = None, con_extractos: bool = True) -> str:
    lineas = []
    for n, it in enumerate(items, 1):
        fecha = (it.get("fecha") or "sin fecha")[:10]
        origen = ", ".join(x for x in (it.get("region") or "internacional", it.get("clase")) if x)
        etiqueta = f"[tema: {it['tema']}] " if it.get("tema") else ""
        if it.get("pendiente"):
            etiqueta += "(pendiente) "
        linea = f"[{n}] {etiqueta}{it['fuente']} ({origen}) | {fecha} | {limpiar_texto(it['titulo'])}"
        if it.get("primera_linea"):
            linea += f" | {limpiar_texto(it['primera_linea'])}"
        if it.get("extracto") and con_extractos:
            linea += f"\n    Extracto: {limpiar_texto(it['extracto'])}"
        lineas.append(linea)
    encabezado = (f"Titulares publicados desde el {corte:%Y-%m-%d %H:%M} UTC (ventana de esta corrida):"
                  if corte else "Titulares de hoy:")
    mensaje = encabezado + "\n\n" + "\n".join(lineas)
    if grupos:
        filas = []
        for g in grupos:
            destino = {"latam": "escena_latam", "salas": "solo cambios_salas (sin tarjeta)",
                       "europa": "tarjetas (etiqueta Europa)"}.get(g["seccion"], "tarjetas")
            fila = f"- {g['ids']} → {destino}"
            if g.get("actualiza"):
                fila += f" (actualiza {g['actualiza']})"
            filas.append(fila)
        mensaje += ("\n\nHechos ya elegidos: escriba una tarjeta por cada uno, con esos números y en esa sección. "
                    "Si al leer el extracto un hecho no tiene dato concreto o resulta promocional, omítalo; "
                    "no agregue hechos nuevos:\n" + "\n".join(filas))
    previos = normalizar_previos(previos)
    if previos:
        filas = []
        for n, p in enumerate(previos, 1):
            if p["hoy"] and p["resumen"]:
                filas.append(f"[P{n}] (publicada hoy) {limpiar_texto(p['titulo'])} — {limpiar_texto(p['resumen'])}")
            else:
                filas.append(f"[P{n}] {limpiar_texto(p['titulo'])}")
        mensaje += ("\n\nTítulos ya publicados (las de hoy, con su resumen; no repetir salvo desarrollo nuevo):\n"
                    + "\n".join(filas))
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


def _fuente_de(it: dict, hoy) -> dict:
    """Enlace a la fuente. Sin fecha en la fuente, cuenta la fecha de la barrida; la hora, solo si la fuente la da."""
    f = _fecha_item(it) or hoy
    fuente = {"nombre": it["fuente"], "fecha": f.isoformat() if f else None, "url": it["url"]}
    if it.get("hora") and _fecha_item(it):
        fuente["hora"] = it["hora"]
    return fuente


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


def _previo_indicado(valor, previos: list[dict]) -> dict | None:
    m = re.fullmatch(r"\s*P?\s*(\d+)\s*", str(valor or ""), re.I)
    if m and 1 <= int(m.group(1)) <= len(previos):
        return previos[int(m.group(1)) - 1]
    return None


def _construir_tarjetas(crudas, items: list[dict], maximo: int, usados: set[int], hoy=None,
                        previos=None, es_latam: bool = False,
                        registro: list[dict] | None = None, enviado: str | None = None,
                        niveles: dict[int, int] | None = None, excluir: set[int] | None = None,
                        fichas: list[dict] | None = None) -> list[dict]:
    previos = normalizar_previos(previos)
    titulos_previos = [p["titulo"] for p in previos]
    enviado = texto_enviado(items) if enviado is None else enviado
    registro = registro if registro is not None else []
    tarjetas = []
    if not isinstance(crudas, list):
        return tarjetas

    nombres_fuentes: list[str] = []

    def descartar(titulo, motivo, **extra):
        registro.append({"titulo": titulo, "motivo": motivo, "fuentes": list(nombres_fuentes), **extra})

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
        # Las notas de temas calientes van solo en su tarjeta de tema.
        ids = [i for i in ids if not items[i - 1].get("tema")]
        # Las notas elegidas solo para «cambios en salas» nunca son tarjetas.
        ids = [i for i in ids if i not in (excluir or set())]
        nombres_fuentes[:] = [items[i - 1]["fuente"] for i in ids]
        titulo = limpiar_texto(str(c.get("titulo") or ""))
        resumen = limpiar_texto(str(c.get("resumen") or ""))
        clasif = str(c.get("clasificacion") or "").lower().replace("ó", "o").replace(" ", "_")
        if clasif in ("en_discusion", "discusión"):
            clasif = "discusion"
        if not ids or not titulo or not resumen or clasif not in CLASIFICACIONES:
            continue
        if set(ids) <= usados:  # el mismo hecho ya está en otra tarjeta
            continue

        # 1. Hechos ya publicados: solo pasan si el modelo declara qué cambió y es algo nuevo de verdad.
        previo = _previo_indicado(c.get("actualiza"), previos)
        novedad = limpiar_texto(str(c.get("novedad") or ""))
        if previo and not novedad:
            descartar(titulo, "repetida sin novedad", previo=previo["titulo"])
            continue
        # Lo publicado hoy cuenta entero: la novedad debe ser nueva respecto de todas las tarjetas del día.
        publicado_hoy = " ".join(f"{p['titulo']} {p['resumen']}" for p in previos if p["hoy"])
        if previo and not calidad.es_novedad_real(novedad, f"{previo['titulo']} {previo['resumen']} {publicado_hoy}"):
            descartar(titulo, "repetida: la novedad no agrega nada a lo publicado", previo=previo["titulo"])
            continue
        if not previo:
            parecido = control.parece_repetida(titulo, titulos_previos, [p["texto"] for p in previos])
            if not parecido:
                parecido = next((p["titulo"] for p in previos if p["hoy"] and
                                 calidad.misma_noticia(f"{titulo} {resumen}", f"{p['titulo']} {p['resumen']}")), None)
            if parecido:
                descartar(titulo, "repetida", previo=parecido)
                continue

        fuentes_items = [items[i - 1] for i in ids]
        fuentes = [_fuente_de(it, hoy) for it in fuentes_items]
        fechas = [date.fromisoformat(f["fecha"]) for f in fuentes if f["fecha"]]
        tarjeta = {
            "clasificacion": clasif,
            "fecha": rango_fechas(fechas) or "",
            "titulo": titulo,
            "resumen": resumen,
            "fuentes": fuentes,
        }
        if previo:
            tarjeta["actualiza"] = previo["titulo"]
            tarjeta["novedad"] = novedad

        # Segundo nivel: lo decide la selección (solo para completar la meta), nunca la redacción.
        if niveles and any(niveles.get(i) == 2 for i in ids):
            if any(it.get("clase", "medio") not in ("oficial", "medio") for it in fuentes_items):
                descartar(titulo, "segundo nivel: solo se admiten fuentes oficiales o medios")
                continue
            tarjeta["nivel"] = 2
        # Regla máxima: todo se verifica contra el texto enviado para ESTA tarjeta (sus fuentes).
        texto_tarjeta = calidad.texto_items(fuentes_items)
        conservar, cambios = control.verificar_nombres(tarjeta, texto_tarjeta)
        for cambio in cambios:
            descartar(titulo, cambio)
        if not conservar:
            continue
        # 3. Relación con el poker y datos de personas (cargos, edades, nacionalidades).
        if not calidad.es_de_poker(tarjeta, fuentes_items):
            descartar(titulo, "sin relación con el poker")
            continue
        conservar, cambios = calidad.aplicar_regla_maxima(tarjeta, texto_tarjeta)
        for cambio in cambios:
            descartar(titulo, cambio)
        if not conservar:
            continue
        # 5. Promocionales y superlativos. Las ofertas de salas se publican (decisión del 26 nov); los satélites no.
        tipo_crudo = str((c.get("registro") or {}).get("tipo") or "") if isinstance(c.get("registro"), dict) else ""
        if tipo_crudo == "oferta" and not calidad.es_satelite(tarjeta):
            pass
        elif calidad.es_promocional(tarjeta):
            descartar(titulo, "nota promocional")
            continue
        for cambio in calidad.quitar_superlativos(tarjeta):
            descartar(titulo, cambio)
        # 4. Dato concreto.
        concreta, motivo = calidad.tiene_dato_concreto(tarjeta, calidad.texto_items(fuentes_items))
        if not concreta:
            descartar(titulo, motivo)
            continue

        usados.update(ids)
        for cambio in control.corregir_terminos(tarjeta, fuentes_items):
            descartar(tarjeta["titulo"], f"término corregido ({cambio})")
        # 7. Monedas.
        for cambio in calidad.corregir_monedas(tarjeta, fuentes_items):
            descartar(tarjeta["titulo"], cambio)
        # 6. Nacionalidad (marca solo si hay una persona nombrada).
        cambio = control.asegurar_nacionalidad(tarjeta, fuentes_items, es_latam)
        if cambio:
            descartar(tarjeta["titulo"], cambio)
        # 2. Clasificación según el tipo de fuente.
        cambio = calidad.ajustar_clasificacion(tarjeta, fuentes_items)
        if cambio:
            descartar(tarjeta["titulo"], cambio)
        cuerpo, cambios = verificar_cuerpo(str(c.get("cuerpo") or ""), tarjeta["titulo"], fuentes_items, es_latam)
        for cambio in cambios:
            descartar(tarjeta["titulo"], cambio)
        if cuerpo and cuerpo != tarjeta["resumen"]:
            tarjeta["cuerpo"] = cuerpo
        tarjetas.append(tarjeta)
        if fichas is not None:  # ficha para el registro permanente (no se guarda en la tarjeta)
            fichas.append({"tarjeta": tarjeta, "crudo": c.get("registro"), "items": fuentes_items,
                           "seccion": "latam" if es_latam else "general"})
    return tarjetas


def construir_temas(crudos, items: list[dict], hoy, publicado: dict[str, str] | None = None,
                    registro: list[dict] | None = None) -> list[dict]:
    """Una tarjeta por tema caliente. Cada novedad sale de UNA nota y se verifica contra ella.
    Repetido = no aporta ningún dato nuevo respecto de lo ya publicado del tema (no se mira a las personas)."""
    publicado = publicado or {}
    registro = registro if registro is not None else []
    temas: dict[str, dict] = {}
    for crudo in crudos if isinstance(crudos, list) else []:
        if not isinstance(crudo, dict):
            continue
        for nov in crudo.get("novedades") or []:
            try:
                i = int(nov.get("id"))
            except (TypeError, ValueError, AttributeError):
                continue
            if not 1 <= i <= len(items) or not items[i - 1].get("tema"):
                continue
            it = items[i - 1]
            nombre = it["tema"]
            tema = temas.setdefault(nombre, {"tema": nombre, "linea": "", "novedades": [], "_textos": []})
            aporta = limpiar_texto(str(nov.get("aporta") or ""))
            fuente_texto = calidad.texto_items([it])
            aporta, quitadas = calidad.depurar_texto(aporta, fuente_texto)
            for q in quitadas:
                registro.append({"tema": nombre, "url": it["url"], "motivo": q})
            if not aporta:
                registro.append({"tema": nombre, "url": it["url"], "motivo": "novedad sin datos rastreables en su fuente"})
                continue
            ya = f"{publicado.get(nombre, '')} {' '.join(tema['_textos'])}"
            if ya.strip() and not calidad.es_novedad_real(aporta, ya):
                registro.append({"tema": nombre, "url": it["url"], "motivo": "repetida: no aporta ningún dato nuevo al tema"})
                continue
            clasif = str(nov.get("clasificacion") or "discusion").lower().replace("ó", "o").replace(" ", "_")
            clasif = "discusion" if clasif in ("en_discusion", "discusión") else clasif
            if clasif not in CLASIFICACIONES:
                clasif = "discusion"
            novedad = {"aporta": aporta, "clasificacion": clasif, **_fuente_de(it, hoy)}
            novedad["fuente"] = novedad.pop("nombre")
            cambio = calidad.ajustar_clasificacion(novedad, [it])
            if cambio:
                registro.append({"tema": nombre, "url": it["url"], "motivo": cambio})
            tema["novedades"].append(novedad)
            tema["_textos"].append(aporta)
        nombre_crudo = str(crudo.get("tema") or "").strip().lower()
        linea = limpiar_texto(str(crudo.get("linea_principal") or ""))
        for nombre, tema in temas.items():
            if nombre.lower() == nombre_crudo and linea and not tema["linea"]:
                notas_tema = [it for it in items if it.get("tema") == nombre]
                tema["linea"], quitadas = calidad.depurar_texto(linea, calidad.texto_items(notas_tema))
                for q in quitadas:
                    registro.append({"tema": nombre, "motivo": f"línea principal: {q}"})
    resultado = []
    for tema in temas.values():
        tema.pop("_textos")
        if tema["novedades"]:
            resultado.append(tema)
    return resultado


def _limitar_segundo_nivel(tarjetas: list[dict], latam: list[dict], faltan: int, registro: list[dict]):
    """El segundo nivel solo completa lo que falta para la meta: nunca la supera."""
    primer_nivel = sum(1 for t in tarjetas + latam if t.get("nivel") != 2)
    cupo = max(0, faltan - primer_nivel)
    resultado = []
    for lista in (tarjetas, latam):
        quedan = []
        for t in lista:
            if t.get("nivel") == 2:
                if cupo <= 0:
                    registro.append({"titulo": t["titulo"], "motivo": "segundo nivel: la meta ya se alcanzó",
                                     "fuentes": [f["nombre"] for f in t["fuentes"]]})
                    continue
                cupo -= 1
            quedan.append(t)
        resultado.append(quedan)
    return resultado[0], resultado[1]


CATEGORIAS_DESCARTE = {
    "promociones": ("nota promocional",),
    "archivo": ("resultado fuera de la regla",),
    "dia1": ("avance de día 1",),
    "sin_dato": ("sin dato concreto", "resultado sin nombre del ganador"),
    "sin_respaldo": ("título con datos", "«qué cambió» con datos", "resumen vacío", "nombre no presente",
                     "novedad sin datos rastreables", "en el título"),
    "repetidas": ("repetida",),
    "sin_poker": ("sin relación con el poker",),
    "segundo_nivel": ("segundo nivel",),
}
NOMBRES_CATEGORIAS = {"promociones": "promociones", "dia1": "avances de día 1", "sin_dato": "sin dato concreto",
                      "sin_respaldo": "sin respaldo en la fuente", "repetidas": "repetidas sin dato nuevo",
                      "sin_poker": "sin relación con el poker", "segundo_nivel": "segundo nivel no admitido",
                      "archivo": "resultados al archivo"}


def categoria_descarte(motivo: str) -> str | None:
    """Regla fija que causó el descarte de una tarjeta o nota (None si fue una corrección, no un descarte)."""
    for categoria, prefijos in CATEGORIAS_DESCARTE.items():
        if any(motivo.startswith(p) or (p == "en el título" and p in motivo) for p in prefijos):
            return categoria
    return None


_RECORD = re.compile(r"\b(r[eé]cords?|all[- ]time|hist[oó]ric[oa]s?|lista hist[oó]rica|money list|m[aá]s brazaletes|"
                     r"m[aá]s t[ií]tulos|iguala|supera a|leaderboard de todos los tiempos)\b", re.I)


def imagenes_de_fuentes(items: list[dict], maximo: int = 4) -> list[dict]:
    """Una imagen por fuente (sin repetir), con el nombre del medio y el enlace a la nota."""
    vistas, salida = set(), []
    for it in items:
        url = it.get("imagen")
        if url and url not in vistas:
            vistas.add(url)
            salida.append({"url": url, "fuente": it.get("fuente") or "", "enlace": it.get("url") or ""})
    return salida[:maximo]


def verificar_cuerpo(cuerpo: str, titulo: str, items: list[dict], es_latam: bool = False) -> tuple[str, list[str]]:
    """El cuerpo pasa los mismos controles que la bajada, párrafo por párrafo. Un párrafo con datos que no
    están en el texto enviado se quita; si no queda ninguno, la página usa la bajada."""
    texto = calidad.texto_items(items)
    parrafos, cambios = [], []
    for crudo in re.split(r"\n\s*\n|\n", cuerpo or ""):
        parrafo = limpiar_texto(crudo)
        if not parrafo:
            continue
        prueba = {"titulo": titulo, "resumen": parrafo, "clasificacion": "confirmado"}
        ok, _ = control.verificar_nombres(prueba, texto)
        if ok:
            ok, _ = calidad.aplicar_regla_maxima(prueba, texto)
        if not ok or not prueba.get("resumen"):
            cambios.append("párrafo del cuerpo sin respaldo en la fuente (quitado)")
            continue
        calidad.quitar_superlativos(prueba)
        control.corregir_terminos(prueba, items)
        calidad.corregir_monedas(prueba, items)
        parrafos.append(prueba["resumen"])
    return "\n\n".join(parrafos), cambios


def resultado_excepcional(tarjeta: dict, historicas: list[str] | None) -> bool:
    """Resultado que se publica aunque no sea latino: figura histórica o récord / lista histórica."""
    texto = f"{tarjeta.get('titulo', '')} {tarjeta.get('resumen', '')}"
    if _RECORD.search(texto):
        return True
    nombres = [re.sub(r"\s*\(.*\)$", "", h) for h in historicas or []]
    return any(n and n.lower() in texto.lower() for n in nombres)


def _rescatar_json(texto: str) -> dict:
    """Si la salida se cortó, rescata los elementos completos de cada lista (temas, tarjetas, escena_latam)."""
    decodificador = json.JSONDecoder()
    datos: dict = {}
    for clave in ("temas", "tarjetas", "escena_latam", "cambios_salas", "hechos"):
        m = re.search(rf'"{clave}"\s*:\s*\[', texto)
        if not m:
            continue
        pos, elementos = m.end(), []
        while True:
            while pos < len(texto) and texto[pos] in " \n\r\t,":
                pos += 1
            if pos >= len(texto) or texto[pos] != "{":
                break
            try:
                obj, fin = decodificador.raw_decode(texto, pos)
            except json.JSONDecodeError:
                break  # elemento cortado: se descarta entero
            elementos.append(obj)
            pos = fin
        datos[clave] = elementos
    if not datos:
        raise ErrorModelo("La respuesta del modelo no es un JSON válido.")
    return datos


def tarjetas_perdidas(datos: dict, grupos: list[dict] | None) -> int | None:
    """Hechos elegidos (que iban a ser tarjeta) sin ninguna tarjeta completa en la salida cortada.
    Sin hechos elegidos (selección fallida) no se puede saber."""
    if not grupos:
        return None
    escritos = set()
    for clave in ("tarjetas", "escena_latam"):
        for c in datos.get(clave) or []:
            for i in (c.get("ids") or []) if isinstance(c, dict) else []:
                try:
                    escritos.add(int(i))
                except (TypeError, ValueError):
                    continue
    return sum(1 for g in grupos if g.get("seccion") != "salas" and not set(g["ids"]) & escritos)


def _leer_respuesta(texto: str, uso: dict) -> tuple[dict, bool]:
    """Devuelve (datos, rescatado). Si la salida se cortó, conserva lo que llegó completo."""
    try:
        return _extraer_json(texto), False
    except ErrorModelo as e:
        try:
            return _rescatar_json(texto), True
        except ErrorModelo:
            motivo = str(e) + (" La salida se cortó por el tope de tokens." if uso.get("truncado") else "")
            raise ErrorModelo(motivo, uso) from e


def _con_costo(uso: dict, modelo: str, ajustes: dict) -> dict:
    uso = dict(uso)
    uso["modelo"] = modelo
    uso["costo_usd"] = costo_estimado(modelo, uso.get("tokens_entrada", 0), uso.get("tokens_salida", 0), ajustes)
    return uso


def seleccionar_hechos(items: list[dict], ajustes: dict, llamar: Llamador = llamar_anthropic, previos=None,
                       corte: datetime | None = None, publicadas: int = 0, salas: dict | None = None,
                       personalidades: list[str] | None = None, historicas: list[str] | None = None) -> dict:
    """Primera llamada: elige y agrupa hechos a partir de titulares y primeras líneas (sin extractos).
    Devuelve {'hechos': [{'ids', 'seccion', 'nivel', 'actualiza'}], 'uso', 'descartados'}."""
    previos = normalizar_previos(previos)[: ajustes.get("max_titulos_previos", 80)]
    modelo = modelo_configurado(ajustes)
    sistema = SELECCION.format(max_general=ajustes.get("max_hechos_general", 14),
                               max_latam=ajustes.get("max_hechos_latam", 6),
                               max_salas=ajustes.get("max_hechos_salas", 6) if salas else 0,
                               max_europa=ajustes.get("max_hechos_europa", 6),
                               max_archivo=ajustes.get("max_hechos_archivo", 40),
                               personalidades=", ".join(personalidades or []) or "(lista vacía)",
                               historicas=", ".join(historicas or []) or "Daniel Negreanu, Phil Hellmuth",
                               **textos_salas(salas))
    mensaje = construir_mensaje(items, previos, corte, con_extractos=False)
    texto, uso = llamar(sistema, mensaje, modelo, ajustes.get("max_tokens_seleccion", 1200))
    uso = _con_costo(uso, modelo, ajustes)
    datos, rescatado = _leer_respuesta(texto, uso)
    if "hechos" not in datos or not isinstance(datos["hechos"], list):
        raise ErrorModelo("La selección no devolvió la lista de hechos.", uso)
    maximos = {"general": ajustes.get("max_hechos_general", 14), "latam": ajustes.get("max_hechos_latam", 6),
               "salas": ajustes.get("max_hechos_salas", 6) if salas else 0,
               "europa": ajustes.get("max_hechos_europa", 6), "archivo": ajustes.get("max_hechos_archivo", 40)}
    cuenta = {k: 0 for k in maximos}
    hechos, usados, descartados = [], set(), []
    for h in datos["hechos"]:
        if not isinstance(h, dict):
            continue
        ids = []
        for i in h.get("ids") or []:
            try:
                i = int(i)
            except (TypeError, ValueError):
                continue
            if 1 <= i <= len(items) and i not in usados and i not in ids and not esta_bloqueado(items[i - 1]["url"]):
                ids.append(i)
        if not ids:
            continue
        seccion = str(h.get("seccion") or "").lower()
        seccion = ("latam" if seccion.startswith("lat") else "salas" if seccion.startswith("sala") else
                   "europa" if seccion.startswith("europ") else "archivo" if seccion.startswith("archiv") else "general")
        if cuenta[seccion] >= maximos[seccion]:
            continue
        usados.update(ids)
        cuenta[seccion] += 1
        hecho = {"ids": ids, "seccion": seccion}
        if h.get("actualiza"):
            hecho["actualiza"] = str(h["actualiza"])
        hechos.append(hecho)
    return {"hechos": hechos, "uso": uso, "descartados": descartados, "rescatado": rescatado}


def resumir(items: list[dict], ajustes: dict, llamar: Llamador = llamar_anthropic, hoy=None,
            previos=None, corte: datetime | None = None, publicado_temas: dict[str, str] | None = None,
            grupos: list[dict] | None = None, faltan: int | None = None, max_tokens: int | None = None,
            salas: dict | None = None, historicas: list[str] | None = None) -> dict:
    """Redacción. Devuelve {'tarjetas', 'escena_latam', 'temas', 'controles', 'uso'} o lanza ErrorModelo.
    Además devuelve, para el registro permanente, 'fichas' (una por tarjeta o tema publicado, con lo que propuso
    el modelo y las notas que cita) y 'cambios_salas' (crudos): los verifica mesa/registro.py.

    `grupos` son los hechos elegidos en la selección (números referidos a `items`); `faltan` es cuántas
    tarjetas faltaban para la meta antes de esta corrida: el segundo nivel nunca la supera."""
    previos = normalizar_previos(previos)[: ajustes.get("max_titulos_previos", 80)]
    modelo = modelo_configurado(ajustes)
    sistema = SISTEMA.format(max_tarjetas=ajustes.get("max_tarjetas", 14),
                             max_latam=ajustes.get("max_tarjetas_latam", 6), **textos_salas(salas))
    mensaje = construir_mensaje(items, previos, corte, grupos)
    texto, uso = llamar(sistema, mensaje, modelo, max_tokens or ajustes.get("max_tokens_salida", 4500))
    uso = _con_costo(uso, modelo, ajustes)
    datos, rescatado = _leer_respuesta(texto, uso)
    niveles = {i: g.get("nivel", 1) for g in grupos or [] for i in g["ids"]}
    solo_salas = {i for g in grupos or [] if g.get("seccion") == "salas" for i in g["ids"]}
    fichas: list[dict] = []
    usados: set[int] = set()
    registro: list[dict] = []
    cortada = None
    if rescatado:
        cortada = {"perdidas": tarjetas_perdidas(datos, grupos)}
        perdidas = ("no se puede saber cuántas tarjetas se perdieron (la redacción no tenía hechos elegidos)"
                    if cortada["perdidas"] is None else
                    f"{cortada['perdidas']} tarjeta{'' if cortada['perdidas'] == 1 else 's'} perdida{'' if cortada['perdidas'] == 1 else 's'}")
        registro.append({"motivo": f"salida cortada por el tope de tokens: se conservaron las tarjetas completas; {perdidas}",
                         "perdidas": cortada["perdidas"]})
    tarjetas = _construir_tarjetas(datos.get("tarjetas"), items, ajustes.get("max_tarjetas", 14), usados, hoy,
                                   previos, False, registro, niveles=niveles, excluir=solo_salas, fichas=fichas)
    # La sección latina no repite un hecho que ya quedó en la general (aunque venga de otra fuente).
    previos_latam = [{"titulo": t["titulo"], "resumen": t["resumen"], "hoy": True} for t in tarjetas] + previos
    latam = _construir_tarjetas(datos.get("escena_latam"), items, ajustes.get("max_tarjetas_latam", 6), usados, hoy,
                                previos_latam, True, registro, niveles=niveles, excluir=solo_salas, fichas=fichas)
    if faltan is not None:
        tarjetas, latam = _limitar_segundo_nivel(tarjetas, latam, faltan, registro)
    # Europa: etiqueta de las tarjetas generales que la selección puso en esa sección.
    ids_europa = {i for g in grupos or [] if g.get("seccion") == "europa" for i in g["ids"]}
    for f in fichas:
        if f["seccion"] == "general" and {items.index(it) + 1 for it in f["items"]} & ids_europa:
            f["seccion"] = "europa"
            f["tarjeta"]["region"] = "europa"
    # Regla de resultados: un resultado solo se publica en la escena latina; el resto va al archivo.
    archivo = []
    for f in list(fichas):
        crudo = f.get("crudo") if isinstance(f.get("crudo"), dict) else {}
        if f["seccion"] in ("general", "europa") and str(crudo.get("tipo") or "") == "resultado":
            t = f["tarjeta"]
            if resultado_excepcional(t, historicas):
                continue  # figura histórica o récord: se publica (decisión del 26 nov)
            tarjetas = [x for x in tarjetas if x is not t]
            fichas.remove(f)
            archivo.append({"titulo": t["titulo"], "resumen": t["resumen"], "fuentes": t["fuentes"], "motivo": "redaccion"})
            registro.append({"titulo": t["titulo"], "motivo": "resultado fuera de la regla de resultados (al archivo)"})
    # Fotos: la imagen que cada medio declara para compartir su nota, con su crédito y enlace.
    for f in fichas:
        imagenes = imagenes_de_fuentes(f["items"])
        if imagenes:
            f["tarjeta"]["imagen"] = imagenes[0]["url"]
            f["tarjeta"]["imagenes"] = imagenes
    temas = construir_temas(datos.get("temas"), items, hoy, publicado_temas, registro)
    publicadas = {id(t) for t in tarjetas + latam}
    fichas = [f for f in fichas if id(f["tarjeta"]) in publicadas]
    crudos_temas = {str(t.get("tema") or "").strip().lower(): t.get("registro")
                    for t in datos.get("temas") or [] if isinstance(t, dict)}
    for tema in temas:
        urls = {n.get("url") for n in tema["novedades"]}
        fichas.append({"tema": tema, "crudo": crudos_temas.get(tema["tema"].lower()),
                       "items": [it for it in items if it.get("tema") == tema["tema"] and it["url"] in urls],
                       "seccion": "tema"})
    cambios = datos.get("cambios_salas") if isinstance(datos.get("cambios_salas"), list) else []
    return {"tarjetas": tarjetas, "escena_latam": latam, "temas": temas, "controles": registro, "uso": uso,
            "fichas": fichas, "cambios_salas": cambios, "salida_cortada": cortada, "archivo": archivo}
