"""Utilidades comunes: dominios, URLs, fechas en español y archivos JSON."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

RAIZ = Path(__file__).resolve().parent.parent

MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]
MESES_CORTOS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago",
                "sep", "oct", "nov", "dic"]

# Sitios que nunca se usan ni se citan (incluye cualquier subdominio).
ETIQUETAS_BLOQUEADAS = {"gipsyteam"}

# Sufijos de dos niveles frecuentes, para calcular el dominio registrable.
SUFIJOS_DOBLES = {
    "com.ar", "com.br", "com.mx", "com.co", "com.pe", "com.uy", "com.py",
    "com.ve", "com.ec", "com.bo", "com.au", "net.au", "org.au", "co.uk",
    "org.uk", "co.nz", "co.za", "com.es", "com.cl", "gob.ar", "gov.br",
    "co.in", "com.tr", "com.cn", "co.jp", "com.pa", "com.do", "com.gt",
}

REDES_SOCIALES = {
    "facebook.com", "fb.com", "fb.me", "twitter.com", "x.com", "t.co",
    "instagram.com", "youtube.com", "youtu.be", "tiktok.com", "linkedin.com",
    "lnkd.in", "reddit.com", "redd.it", "twitch.tv", "t.me", "telegram.org",
    "telegram.me", "whatsapp.com", "wa.me", "discord.com", "discord.gg",
    "threads.net", "pinterest.com", "bsky.app", "snapchat.com", "kick.com",
    "vk.com", "tumblr.com", "medium.com", "substack.com", "spotify.com",
    "patreon.com", "linktr.ee", "flickr.com", "vimeo.com", "rumble.com",
}

# Dominios técnicos que no son medios (no cuentan como candidatas).
DOMINIOS_TECNICOS = {
    "google.com", "googleapis.com", "gstatic.com", "googletagmanager.com",
    "doubleclick.net", "gravatar.com", "wp.com", "wordpress.com",
    "wordpress.org", "cloudflare.com", "apple.com", "microsoft.com",
    "amazon.com", "amzn.to", "bit.ly", "goo.gl", "tinyurl.com", "ow.ly",
    "feedburner.com", "w3.org", "schema.org", "gmpg.org", "creativecommons.org",
    "archive.org", "wikipedia.org", "github.com", "mailchimp.com",
    "list-manage.com", "eepurl.com", "addtoany.com", "sharethis.com",
    "disqus.com", "onetrust.com", "cookielaw.org", "jsdelivr.net",
    "cdnjs.com", "fonts.com", "typekit.net", "imgur.com", "giphy.com",
    "begambleaware.org", "gamstop.co.uk", "ncpgambling.org",
    "juegoresponsable.com.ar", "jugarbien.es", "gamcare.org.uk",
}


def host_de(url: str) -> str:
    try:
        host = urlsplit(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def dominio_base(url_o_host: str) -> str:
    """Devuelve el dominio registrable (ej. 'es.pokernews.com' -> 'pokernews.com')."""
    host = host_de(url_o_host) if "/" in url_o_host else url_o_host.lower()
    host = host.split(":")[0].strip(".")
    partes = [p for p in host.split(".") if p]
    if len(partes) <= 2:
        return ".".join(partes)
    if ".".join(partes[-2:]) in SUFIJOS_DOBLES:
        return ".".join(partes[-3:])
    return ".".join(partes[-2:])


def esta_bloqueado(url_o_host: str) -> bool:
    host = host_de(url_o_host) if "/" in url_o_host else url_o_host.lower()
    return any(etiqueta in ETIQUETAS_BLOQUEADAS for etiqueta in host.split("."))


def es_red_social(dominio: str) -> bool:
    return dominio_base(dominio) in REDES_SOCIALES


def es_dominio_tecnico(dominio: str) -> bool:
    return dominio_base(dominio) in DOMINIOS_TECNICOS


def normalizar_url(url: str) -> str:
    """Forma canónica para comparar URLs ya vistas."""
    url = (url or "").strip()
    if not url:
        return ""
    try:
        partes = urlsplit(url)
    except ValueError:
        return url
    host = (partes.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    consulta = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                if not k.lower().startswith(_PARAMS_RASTREO)]
    ruta = partes.path or "/"
    if len(ruta) > 1:
        ruta = ruta.rstrip("/")
    return urlunsplit(("https", host, ruta, urlencode(consulta), partes.fragment if partes.fragment.startswith("t-") else ""))


_PARAMS_RASTREO = ("utm_", "fbclid", "gclid", "mc_", "ref_src")


def quitar_rastreo(url: str) -> str:
    """Quita parámetros de seguimiento (utm_*, etc.) sin tocar el resto de la URL."""
    try:
        partes = urlsplit(url)
    except ValueError:
        return url
    consulta = [(k, v) for k, v in parse_qsl(partes.query, keep_blank_values=True)
                if not k.lower().startswith(_PARAMS_RASTREO)]
    return urlunsplit((partes.scheme, partes.netloc, partes.path, urlencode(consulta), partes.fragment))


def es_afiliado(url: str) -> bool:
    """Enlaces de afiliados o de seguimiento comercial (no son medios)."""
    host = host_de(url)
    consulta = urlsplit(url).query.lower() if "?" in url else ""
    return ("affiliat" in host or "afiliad" in host or host.startswith(("go.", "track.", "click.", "record."))
            or any(p in consulta for p in ("btag=", "bta=", "affid=", "aff_id=", "affiliate")))


def limpiar_texto(texto: str | None) -> str:
    return re.sub(r"\s+", " ", texto or "").strip()


def recortar(texto: str, maximo: int) -> str:
    texto = limpiar_texto(texto)
    if len(texto) <= maximo:
        return texto
    corte = texto[:maximo].rsplit(" ", 1)[0]
    return corte + "…"


def primera_oracion(texto: str, maximo: int = 280) -> str:
    texto = limpiar_texto(texto)
    m = re.match(r"(.{40,}?[.!?])(\s|$)", texto)
    return recortar(m.group(1) if m else texto, maximo)


def ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def fecha_larga(d: date) -> str:
    return f"{d.day} de {MESES[d.month - 1]} de {d.year}"


def fecha_corta(d: date) -> str:
    return f"{d.day} {MESES_CORTOS[d.month - 1]} {d.year}"


def rango_fechas(fechas: list[date]) -> str | None:
    """Texto de fecha para una tarjeta a partir de las fechas reales de sus fuentes."""
    fechas = sorted(set(fechas))
    if not fechas:
        return None
    a, b = fechas[0], fechas[-1]
    if a == b:
        return fecha_corta(a)
    if a.year == b.year and a.month == b.month:
        return f"{a.day}-{b.day} {MESES_CORTOS[b.month - 1]} {b.year}"
    if a.year == b.year:
        return f"{a.day} {MESES_CORTOS[a.month - 1]}-{b.day} {MESES_CORTOS[b.month - 1]} {b.year}"
    return f"{fecha_corta(a)}-{fecha_corta(b)}"


def leer_json(ruta: Path, por_defecto=None):
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return por_defecto


def escribir_json(ruta: Path, datos) -> None:
    """Escritura atómica: nunca deja un archivo a medio escribir."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(datos, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, ruta)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def escribir_texto(ruta: Path, texto: str) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ruta.parent, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
        os.replace(tmp, ruta)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
