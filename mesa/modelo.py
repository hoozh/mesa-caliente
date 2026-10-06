"""Una sola llamada al modelo por corrida: selecciona, agrupa, clasifica y resume."""

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
1. Elija hasta {max_tarjetas} hechos relevantes para la comunidad de poker (torneos importantes, resultados destacados, regulación del poker, salas online, escándalos, industria del poker).
2. Agrupe por hecho: si varias fuentes cuentan el mismo hecho, van en una sola tarjeta con todos sus números.
3. Clasifique cada tarjeta:
   - "confirmado": solo si lo informa una fuente oficial o un medio especializado con fecha, y se trata de un resultado, un anuncio oficial o un hecho verificado.
   - "discusion": tema abierto, polémica, versiones encontradas, o cualquier hecho respaldado solo por fuentes de clase "comunidad" (Reddit, foros) o por fuentes sin fecha.
   - "rumor": sin confirmación.
4. Escriba un título en español y un resumen de 2 a 3 líneas.
5. Aparte, complete "escena_latam" con hasta {max_latam} hechos sobre jugadores argentinos o latinoamericanos, o torneos como CAP, WCOOP, WSOP Online, SCOOP, CLSOP o BSOP con protagonistas latinos. Dé prioridad a las fuentes de región latam y argentina. Un hecho no debe repetirse en "tarjetas" y "escena_latam".

Temas calientes:
- Algunas notas llegan marcadas con [tema: NOMBRE]. Esas notas van SOLO en la sección "temas", nunca en "tarjetas" ni en "escena_latam".
- Por cada tema escriba una "linea_principal" (una oración que resuma el estado del tema con datos del material) y una lista de "novedades": una por nota, con "id" (el número de UNA nota), "clasificacion" y "aporta" (qué dato nuevo trae esa nota: respuesta de una sala, medida, cifra, cita, acción legal, reacción de un jugador u otro hecho). Si dos fuentes difieren, explique la diferencia solo con lo que dice cada una ("según X…; según Y…").
- Si una nota del tema no aporta ningún dato nuevo respecto de lo ya publicado, no la incluya.

Qué no publicar:
- Notas sin relación con el poker (otros deportes, apuestas deportivas o casino sin poker, celebridades ajenas al poker).
- Notas puramente promocionales: satélites, promociones, bonos, ofertas, rakeback, freerolls, anuncios de garantizados o calendarios comerciales.
- Notas sin dato concreto: un resultado debe nombrar al ganador; toda tarjeta necesita al menos un nombre, un monto o un evento con nombre. Si el titular no los trae ("campeón francés en Marrakesh", "una mano decide al campeón"), no cree la tarjeta salvo que el extracto los dé.
- Hechos ya publicados (ver más abajo), salvo con un desarrollo nuevo y concreto.

Reglas estrictas:
- REGLA MÁXIMA: nunca invente datos. Toda cifra, nombre, cargo, edad, nacionalidad, fecha, lugar, cita o hecho que escriba debe estar en el texto de las notas que cita esa tarjeta (o esa novedad). Si una fuente no precisa algo, escriba "la fuente no precisa …"; nunca lo complete con su conocimiento. El programa borra lo que no pueda rastrear y descarta la tarjeta si pierde su dato principal.
- Use solo la información de los titulares, primeras líneas y extractos recibidos. Nunca invente hechos, fechas, cifras, nombres ni enlaces.
- Las citas textuales solo pueden copiarse exactas, en el idioma de la fuente; si no, cuente lo que dijo sin comillas.
- No escriba URLs ni fechas: el programa las agrega a partir de los números que usted indique.
- Si una cifra o dato no aparece en el material, no lo mencione.
- Datos de personas: cargos, posiciones de juego o deportivas, edades y nacionalidades solo si aparecen literalmente en el material recibido. No los deduzca ni los complete con su conocimiento.
- Tono: no copie superlativos ni elogios de la fuente ("el más prestigioso del mundo", "espectacular", "histórico", "increíble"). Escriba de forma neutra.
- Montos: escriba la moneda exactamente como la indica la fuente (US$, €, £, R$, MXN, etc.). Si una fuente latinoamericana solo pone "$", escriba "$ (moneda a confirmar)" después del número y no lo convierta en dólares.
- Términos exactos: conserve el premio o título que nombra la fuente, traducido de forma literal. "Bracelet" es "brazalete" (WSOP); "ring" es "anillo" (WSOP Circuit y otros circuitos); "trophy" es "trofeo". Nunca cambie uno por otro: si la fuente dice "ring", no escriba "brazalete". Lo mismo vale para nombres de torneos, eventos y circuitos: use el nombre que da la fuente.
- Nacionalidad: si el titular, la primera línea o el extracto indican la nacionalidad de un jugador (por ejemplo "el argentino...", "Brazilian..."), inclúyala en el resumen. En "escena_latam", solo cuando la tarjeta nombra a una persona y la fuente no indica su nacionalidad, escriba "[nacionalidad a confirmar]" junto a su nombre; nunca la deduzca del nombre, del alias ni de la sala.
- Nombres propios: escriba los nombres de personas exactamente como aparecen en el material recibido. Nunca complete, agregue ni invente nombres de pila, apellidos o alias: si la fuente solo da un alias, use solo el alias; si solo da el apellido, use solo el apellido. El programa elimina las partes de un nombre que no figuren en el material y descarta la tarjeta si el nombre completo no figura.
- Ventana de la corrida: el mensaje indica desde qué fecha y hora se tomaron los titulares. Cada tarjeta debe tratar un hecho ocurrido o informado dentro de esa ventana.
- Hechos ya publicados: al final del mensaje recibirá las tarjetas ya publicadas, numeradas P1, P2, etc. Las publicadas hoy por corridas anteriores llegan con su resumen; las de días anteriores, solo con el título. No cree una tarjeta para un hecho ya cubierto ni para un hecho anterior a la ventana. Solo si hay un desarrollo nuevo y concreto (un resultado final, una cifra nueva, una confirmación oficial, una respuesta de los involucrados) que no figure en la tarjeta publicada, cree la tarjeta e indique "actualiza" con el número de la tarjeta previa (por ejemplo "P3") y "novedad" con una frase que diga exactamente qué cambió; el resumen debe contar ese cambio. Repetir con otras palabras lo ya publicado no es una novedad.

Responda únicamente con un objeto JSON, sin texto adicional, con esta forma:
{{"tarjetas": [{{"ids": [1, 4], "clasificacion": "confirmado", "titulo": "...", "resumen": "..."}},
              {{"ids": [9], "clasificacion": "confirmado", "titulo": "...", "resumen": "...", "actualiza": "P3", "novedad": "..."}}],
 "escena_latam": [{{"ids": [7], "clasificacion": "confirmado", "titulo": "...", "resumen": "..."}}],
 "temas": [{{"tema": "NOMBRE", "linea_principal": "...", "novedades": [{{"id": 2, "clasificacion": "confirmado", "aporta": "..."}}]}}]}}"""


def normalizar_previos(previos) -> list[dict]:
    """Acepta títulos sueltos o tarjetas {titulo, resumen, hoy} y devuelve tarjetas."""
    resultado = []
    for p in previos or []:
        if isinstance(p, str):
            resultado.append({"titulo": p, "resumen": "", "hoy": False})
        elif isinstance(p, dict) and p.get("titulo"):
            resultado.append({"titulo": p["titulo"], "resumen": p.get("resumen") or "", "hoy": bool(p.get("hoy"))})
    return resultado


def construir_mensaje(items: list[dict], previos=None, corte: datetime | None = None) -> str:
    lineas = []
    for n, it in enumerate(items, 1):
        fecha = (it.get("fecha") or "sin fecha")[:10]
        origen = ", ".join(x for x in (it.get("region") or "internacional", it.get("clase")) if x)
        etiqueta = f"[tema: {it['tema']}] " if it.get("tema") else ""
        linea = f"[{n}] {etiqueta}{it['fuente']} ({origen}) | {fecha} | {limpiar_texto(it['titulo'])}"
        if it.get("primera_linea"):
            linea += f" | {limpiar_texto(it['primera_linea'])}"
        if it.get("extracto"):
            linea += f"\n    Extracto: {limpiar_texto(it['extracto'])}"
        lineas.append(linea)
    encabezado = (f"Titulares publicados desde el {corte:%Y-%m-%d %H:%M} UTC (ventana de esta corrida):"
                  if corte else "Titulares de hoy:")
    mensaje = encabezado + "\n\n" + "\n".join(lineas)
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
                        recuperacion: dict | None = None) -> list[dict]:
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
            parecido = control.parece_repetida(titulo, titulos_previos)
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

        # Segundo nivel (solo en la recuperación de la meta diaria): únicamente fuentes oficiales o medios.
        if c.get("nivel") in (2, "2"):
            if not recuperacion:
                descartar(titulo, "segundo nivel fuera de la recuperación")
                continue
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
        # 5. Promocionales y superlativos.
        if calidad.es_promocional(tarjeta):
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
        tarjetas.append(tarjeta)
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
                      "sin_poker": "sin relación con el poker", "segundo_nivel": "segundo nivel no admitido"}


def categoria_descarte(motivo: str) -> str | None:
    """Regla fija que causó el descarte de una tarjeta o nota (None si fue una corrección, no un descarte)."""
    for categoria, prefijos in CATEGORIAS_DESCARTE.items():
        if any(motivo.startswith(p) or (p == "en el título" and p in motivo) for p in prefijos):
            return categoria
    return None


def resumir(items: list[dict], ajustes: dict, llamar: Llamador = llamar_anthropic, hoy=None,
            previos=None, corte: datetime | None = None, publicado_temas: dict[str, str] | None = None,
            recuperacion: dict | None = None, max_tokens: int | None = None) -> dict:
    """Devuelve {'tarjetas', 'escena_latam', 'controles', 'uso'} o lanza ErrorModelo."""
    previos = normalizar_previos(previos)[: ajustes.get("max_titulos_previos", 80)]
    modelo = modelo_configurado(ajustes)
    sistema = SISTEMA.format(max_tarjetas=ajustes.get("max_tarjetas", 12),
                             max_latam=ajustes.get("max_tarjetas_latam", 6))
    mensaje = construir_mensaje(items, previos, corte)
    if recuperacion:
        mensaje = (f"CORRIDA DE RECUPERACIÓN. Hoy hay {recuperacion['publicadas']} tarjetas publicadas; la meta es "
                   f"{recuperacion['meta']}. Faltan {recuperacion['faltan']}. Estas notas son de las últimas 48 horas o no se "
                   "eligieron antes. Elija primero hechos relevantes. Solo si no alcanzan, puede agregar hechos de segundo "
                   "nivel (resultados de torneos menores, novedades de salas o circuitos con dato concreto) marcando "
                   '"nivel": 2, y solo de notas de clase oficial o medio. Todas las reglas siguen igual: nunca invente ni '
                   "complete datos para llegar a la meta; si no hay suficientes hechos, devuelva menos tarjetas.\n\n" + mensaje)
    texto, uso = llamar(sistema, mensaje, modelo, max_tokens or ajustes.get("max_tokens_salida", 3000))
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
                                   previos, False, registro, recuperacion=recuperacion)
    latam = _construir_tarjetas(datos.get("escena_latam"), items, ajustes.get("max_tarjetas_latam", 6), usados, hoy,
                                previos, True, registro, recuperacion=recuperacion)
    if recuperacion:
        tarjetas, latam = _limitar_segundo_nivel(tarjetas, latam, recuperacion["faltan"], registro)
    temas = construir_temas(datos.get("temas"), items, hoy, publicado_temas, registro)
    return {"tarjetas": tarjetas, "escena_latam": latam, "temas": temas, "controles": registro, "uso": uso}
