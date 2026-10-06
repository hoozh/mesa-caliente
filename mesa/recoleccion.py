"""Recolección sin modelo: lee RSS o portadas HTML y devuelve titulares nuevos."""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urljoin, urlsplit

import feedparser
import requests
from bs4 import BeautifulSoup

from . import salud
from .util import (dominio_base, esta_bloqueado, host_de, limpiar_texto, normalizar_url,
                   primera_oracion, quitar_rastreo, recortar)

# Algunos sitios rechazan ciertos agentes: si responde con bloqueo se reintenta con el siguiente.
AGENTES = [
    "Mozilla/5.0 (compatible; MesaCaliente/2.0; +https://github.com/hoozh/mesa-caliente)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
]
ESTADOS_REINTENTO = {202, 403, 429}


@dataclass
class Respuesta:
    estado: int
    texto: str
    url: str


class ErrorDescarga(Exception):
    pass


Descargador = Callable[[str], Respuesta]


def descargar_http(url: str, timeout: int = 25) -> Respuesta:
    for n, agente in enumerate(AGENTES):
        try:
            r = requests.get(url, timeout=timeout, headers={
                "User-Agent": agente,
                "Accept": "text/html,application/xhtml+xml,application/rss+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "es,en;q=0.8,pt;q=0.6",
            })
        except requests.RequestException as e:
            raise ErrorDescarga(f"error de conexión ({type(e).__name__})") from e
        if r.status_code not in ESTADOS_REINTENTO or n == len(AGENTES) - 1:
            break
    if r.encoding is None or r.encoding.lower() == "iso-8859-1" and "charset" not in r.headers.get("content-type", "").lower():
        r.encoding = r.apparent_encoding
    return Respuesta(r.status_code, r.text, r.url)


def motivo_http(estado: int) -> str:
    if estado == 202:
        return "verificación anti-bots (HTTP 202 sin contenido)"
    if estado in (401, 403, 429):
        return f"bloqueo del sitio (HTTP {estado})"
    if estado == 404:
        return "la dirección ya no existe (HTTP 404)"
    if estado >= 500:
        return f"error del servidor (HTTP {estado})"
    return f"respuesta inesperada (HTTP {estado})"


# ---------------------------------------------------------------- RSS

def _fecha_entrada(entrada) -> datetime | None:
    for clave in ("published_parsed", "updated_parsed", "created_parsed"):
        valor = entrada.get(clave)
        if valor:
            try:
                return datetime.fromtimestamp(calendar.timegm(valor), tz=timezone.utc)
            except (OverflowError, ValueError, TypeError):
                continue
    return None


def _texto_html(fragmento: str) -> str:
    if not fragmento:
        return ""
    return limpiar_texto(BeautifulSoup(fragmento, "html.parser").get_text(" "))


def parsear_rss(texto: str, fuente: dict) -> list[dict]:
    feed = feedparser.parse(texto)
    items = []
    for e in feed.entries:
        url = e.get("link") or ""
        titulo = limpiar_texto(_texto_html(e.get("title", "")))
        if not url or not titulo:
            continue
        resumen = _texto_html(e.get("summary", "") or e.get("description", ""))
        if resumen.lower().startswith(titulo.lower()):
            resumen = resumen[len(titulo):]
        if not re.search(r"[^\W\d_]{3}", resumen):  # solo números o símbolos
            resumen = ""
        items.append(_item(fuente, url, titulo, _fecha_entrada(e), primera_oracion(resumen)))
    return items


# ---------------------------------------------------------------- HTML

_RUTAS_NAVEGACION = re.compile(
    r"/(tag|tags|category|categoria|categorias|author|autor|page|pagina|login|"
    r"registro|register|contact|contacto|about|privacy|privacidad|terminos|terms|"
    r"search|buscar|feed|wp-login|cart|account)(/|$)", re.I)


def _titulo_de_enlace(a) -> tuple[str, str]:
    """Separa título y primera línea cuando el enlace envuelve toda la tarjeta."""
    cabecera = a.find(["h1", "h2", "h3", "h4", "h5"])
    texto = limpiar_texto(a.get_text(" "))
    if cabecera:
        titulo = limpiar_texto(cabecera.get_text(" "))
        resto = limpiar_texto(texto.replace(titulo, "", 1))
        return titulo, resto
    titulo = limpiar_texto(a.get("title") or "") or texto
    if len(titulo) > 160:
        return recortar(titulo, 160), titulo
    return titulo, ""


def _fecha_cercana(nodo) -> datetime | None:
    contenedor = nodo
    for _ in range(4):
        if contenedor is None:
            break
        t = contenedor.find("time") if hasattr(contenedor, "find") else None
        if t and t.get("datetime"):
            try:
                d = datetime.fromisoformat(t["datetime"].replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
            except ValueError:
                return None
        contenedor = contenedor.parent
    return None


def _linea_cercana(nodo, titulo: str) -> str:
    padre = nodo.parent
    for _ in range(3):
        if padre is None:
            break
        p = padre.find("p")
        if p:
            texto = limpiar_texto(p.get_text(" "))
            if texto and texto != titulo:
                return texto
        padre = padre.parent
    return ""


def parsear_html(texto: str, url_base: str, fuente: dict) -> list[dict]:
    sopa = BeautifulSoup(texto, "html.parser")
    for basura in sopa(["script", "style", "noscript", "nav", "footer", "form"]):
        basura.decompose()
    incluir = re.compile(fuente["incluir"], re.I) if fuente.get("incluir") else None
    excluir = re.compile(fuente["excluir"], re.I) if fuente.get("excluir") else None
    base = dominio_base(url_base)
    portada = normalizar_url(url_base)
    vistos, items = set(), []

    for a in sopa.find_all("a", href=True):
        url = urljoin(url_base, a["href"]).split("#")[0]
        if not url.startswith("http") or dominio_base(url) != base:
            continue
        norm = normalizar_url(url)
        if norm in vistos or norm == portada:
            continue
        ruta = urlsplit(url).path
        if not ruta.strip("/") or _RUTAS_NAVEGACION.search(ruta):
            continue
        if incluir and not incluir.search(url):
            continue
        if excluir and excluir.search(url):
            continue
        titulo, linea = _titulo_de_enlace(a)
        if len(titulo) < 25 or len(titulo.split()) < 4:
            continue
        vistos.add(norm)
        linea = linea or _linea_cercana(a, titulo)
        items.append(_item(fuente, url, titulo, _fecha_cercana(a), primera_oracion(linea)))

    if items:
        return items

    # Portadas sin enlaces por nota: se toman los titulares (h2/h3) con su párrafo.
    for h in sopa.find_all(["h2", "h3"]):
        titulo = limpiar_texto(h.get_text(" "))
        if len(titulo) < 20 or len(titulo.split()) < 4:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", titulo.lower()).strip("-")[:80]
        url = f"{url_base.split('#')[0]}#t-{slug}"
        if url in vistos:
            continue
        vistos.add(url)
        p = h.find_next_sibling("p")
        linea = limpiar_texto(p.get_text(" ")) if p else ""
        items.append(_item(fuente, url, titulo, _fecha_cercana(h), primera_oracion(linea)))
    return items


# ---------------------------------------------------------------- común

def _item(fuente: dict, url: str, titulo: str, fecha: datetime | None, linea: str) -> dict:
    return {
        "fuente": fuente["nombre"],
        "url": quitar_rastreo(url),
        "titulo": titulo,
        "fecha": fecha.isoformat() if fecha else None,
        # Hora solo si la fuente la da (una medianoche exacta suele ser una fecha sin hora).
        "hora": fecha.astimezone(timezone.utc).strftime("%H:%M") if fecha and (fecha.hour, fecha.minute, fecha.second) != (0, 0, 0) else None,
        "primera_linea": linea,
        "idioma": fuente.get("idioma"),
        "region": fuente.get("region"),
        "clase": fuente.get("clase", "medio"),
        "tematica": fuente.get("tematica", "poker"),
    }


def leer_fuente(fuente: dict, descargar: Descargador) -> list[dict]:
    """Devuelve los titulares de una fuente o lanza ErrorDescarga con el motivo."""
    if esta_bloqueado(fuente["url"]):
        raise ErrorDescarga("dominio prohibido (GipsyTeam)")
    r = descargar(fuente["url"])
    if r.estado != 200:
        raise ErrorDescarga(motivo_http(r.estado))
    texto = r.texto or ""
    if len(texto.strip()) < 300:
        raise ErrorDescarga("sin contenido (respuesta vacía o de verificación anti-bots)")
    if fuente.get("tipo") == "rss":
        items = parsear_rss(texto, fuente)
    else:
        items = parsear_html(texto, r.url or fuente["url"], fuente)
    items = [i for i in items if not esta_bloqueado(i["url"])]
    if not items:
        raise ErrorDescarga("sin contenido (no se encontraron titulares)")
    return items


def modo_de_lectura(items: list[dict]) -> str:
    """'fecha' si cada nota trae su propia fecha; 'orden' si falta alguna fecha
    o si todas comparten la misma, con unos segundos de diferencia (como Poker.org)."""
    fechas = [it.get("fecha") for it in items]
    if not fechas or any(f is None for f in fechas):
        return "orden"
    momentos = [datetime.fromisoformat(f) for f in fechas]
    # Feeds que ponen a todas las notas la hora en que se generan (Poker.org: con
    # diferencias de un segundo): esas fechas no dicen cuándo salió cada nota.
    if len(momentos) >= 2 and max(momentos) - min(momentos) <= timedelta(minutes=10):
        return "orden"
    return "fecha"


def filtrar_fuente(items: list[dict], vistos: set[str], corte: datetime, ahora: datetime,
                   tope: int, primera_vez: bool, prioridad=None, descartar_dia1=None) -> tuple[list[dict], dict]:
    """Aplica el filtro por día a los titulares de una fuente.

    - Modo 'fecha': solo lo publicado desde el corte (y no ya visto).
    - Modo 'orden': desde arriba hasta la primera URL ya vista; ahí se corta.
      Si la fuente se lee por primera vez, no hay corte posible y rige el tope.
    Devuelve (titulares tomados, conteo de descartes)."""
    modo = modo_de_lectura(items)
    conteo = {"modo": modo, "leidos": len(items), "por_fecha": 0, "por_orden": 0,
              "ya_vistos": 0, "por_tope": 0, "por_dia1": 0, "primera_vez": primera_vez}
    tomados, ya = [], set()
    if modo == "fecha":
        for it in sorted(items, key=lambda i: i["fecha"], reverse=True):
            f = datetime.fromisoformat(it["fecha"])
            if f < corte or f > ahora + timedelta(days=1):
                conteo["por_fecha"] += 1
                continue
            norm = normalizar_url(it["url"])
            if norm in vistos or norm in ya:
                conteo["ya_vistos"] += 1
                continue
            ya.add(norm)
            tomados.append(it)
    else:
        for n, it in enumerate(items):
            norm = normalizar_url(it["url"])
            if norm in vistos:
                conteo["por_orden"] = len(items) - n  # de aquí hacia abajo ya se leyó antes
                break
            if norm in ya:
                continue
            ya.add(norm)
            # Una fecha repetida en todas las notas no dice nada: no se muestra.
            tomados.append(dict(it, fecha=None, hora=None) if it.get("fecha") else it)
    # Temas calientes primero y fuera del tope; el filtro de "día 1" no se les aplica.
    prioritarios, resto = [], []
    for it in tomados:
        tema = prioridad(it) if prioridad else None
        if tema:
            prioritarios.append(dict(it, tema=tema))
        elif descartar_dia1:
            motivo = descartar_dia1(it)
            if motivo:
                conteo["por_dia1"] += 1
                conteo.setdefault("dia1", []).append({"titulo": it["titulo"], "url": it["url"], **motivo})
                continue
            resto.append(it)
        else:
            resto.append(it)
    conteo["por_tope"] = max(0, len(resto) - tope)
    conteo["prioritarios"] = len(prioritarios)
    return prioritarios + resto[:tope], conteo


def recolectar(fuentes: list[dict], hoy: date, ahora: datetime, vistos: set[str],
               ajustes: dict, descargar: Descargador = descargar_http,
               corte: datetime | None = None, prioridad=None,
               descartar_dia1=None, leidos: list | None = None) -> tuple[list[dict], list[dict], list[str]]:
    """Consulta las fuentes, actualiza su salud y devuelve
    (titulares nuevos, informe por fuente, URLs de todo lo recogido)."""
    if corte is None:
        corte = ahora - timedelta(days=ajustes.get("dias_maximos_atras", 3))
    tope_fuente = ajustes.get("max_titulares_por_fuente", 10)
    hosts_vistos = {host_de(u).removeprefix("www.") for u in vistos}
    nuevos, informe, recogidos, ya = [], [], [], set()
    for fuente in fuentes:
        if esta_bloqueado(fuente.get("url", "")):
            informe.append({"fuente": fuente["nombre"], "resultado": "omitida", "motivo": "dominio prohibido"})
            continue
        if not salud.debe_intentar(fuente, hoy, ajustes.get("dias_pausa_reintento", 7)):
            informe.append({"fuente": fuente["nombre"], "resultado": "en_pausa", "motivo": fuente.get("motivo_pausa")})
            continue
        try:
            items = leer_fuente(fuente, descargar)
        except ErrorDescarga as e:
            salud.registrar_fallo(fuente, hoy, str(e), ajustes.get("fallos_para_pausa", 3))
            informe.append({"fuente": fuente["nombre"], "resultado": "fallo", "motivo": str(e)})
            continue
        except Exception as e:  # un parser roto no debe tumbar la corrida
            motivo = f"error al leer ({type(e).__name__})"
            salud.registrar_fallo(fuente, hoy, motivo, ajustes.get("fallos_para_pausa", 3))
            informe.append({"fuente": fuente["nombre"], "resultado": "fallo", "motivo": motivo})
            continue
        salud.registrar_exito(fuente, hoy, len(items))
        recogidos.extend(it["url"] for it in items)
        if leidos is not None:  # todo lo leído, para la recuperación de la meta diaria
            leidos.append({"fuente": fuente["nombre"], "items": items})
        hosts = {host_de(it["url"]).removeprefix("www.") for it in items}
        primera_vez = not (hosts & hosts_vistos)
        tomados, conteo = filtrar_fuente(items, vistos | ya, corte, ahora, tope_fuente, primera_vez,
                                         prioridad, descartar_dia1)
        ya.update(normalizar_url(it["url"]) for it in tomados)
        nuevos.extend(tomados)
        informe.append({"fuente": fuente["nombre"], "resultado": "ok", "titulares": len(items),
                        "nuevos": len(tomados), **conteo})
    return nuevos, informe, recogidos


def seleccionar(items: list[dict], tope: int) -> list[dict]:
    """Las notas de temas calientes entran todas y van primero, fuera del tope.
    El resto se reparte por turnos entre fuentes, respetando el orden de cada una."""
    prioritarios = [it for it in items if it.get("tema")]
    por_fuente: dict[str, list[dict]] = {}
    for it in items:
        if not it.get("tema"):
            por_fuente.setdefault(it["fuente"], []).append(it)
    elegidos = []
    while len(elegidos) < tope and any(por_fuente.values()):
        for nombre in list(por_fuente):
            if por_fuente[nombre] and len(elegidos) < tope:
                elegidos.append(por_fuente[nombre].pop(0))
    return prioritarios + elegidos


# ---------------------------------------------------------------- artículos

def extraer_articulo(texto: str, url_base: str, max_caracteres: int) -> tuple[str, list[str]]:
    """Devuelve (extracto del cuerpo, enlaces externos citados en el cuerpo)."""
    sopa = BeautifulSoup(texto, "html.parser")
    for basura in sopa(["script", "style", "noscript", "nav", "footer", "header", "aside", "form", "iframe"]):
        basura.decompose()
    cuerpo = sopa.find("article") or sopa.find("main") or sopa.body or sopa
    parrafos = [limpiar_texto(p.get_text(" ")) for p in cuerpo.find_all("p")]
    extracto = recortar(" ".join(p for p in parrafos if len(p) > 40), max_caracteres)
    propio = dominio_base(url_base)
    enlaces = []
    for a in cuerpo.find_all("a", href=True):
        url = urljoin(url_base, a["href"])
        if url.startswith("http") and dominio_base(url) != propio:
            enlaces.append(url)
    return extracto, enlaces


def titular_vago(item: dict) -> bool:
    """Titular sin nombre propio ni cifra (p. ej. "French grinder wins in Marrakech"): conviene abrir la nota."""
    if "#t-" in item["url"]:
        return False
    texto = f"{item.get('titulo') or ''} {item.get('primera_linea') or ''}"
    tiene_cifra = bool(re.search(r"\d", texto))
    tiene_nombre = bool(re.search(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b", texto))
    return not (tiene_cifra or tiene_nombre)


def necesita_texto(item: dict) -> bool:
    return len(item.get("primera_linea") or "") < 60 and "#t-" not in item["url"]
