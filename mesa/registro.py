"""Registro permanente para el resto de SEI.

- data/registro/hechos.jsonl: una línea por tarjeta publicada (también las de temas calientes).
- data/registro/cambios_salas.jsonl: cambios de las salas vigiladas (config/salas_vigiladas.json).

Los campos los propone la llamada de redacción (sin llamada extra) y el programa los verifica contra
el texto enviado al modelo: lo que no figura ahí queda vacío. Nada se completa con conocimiento propio.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date, timedelta
from pathlib import Path

from . import calidad
from . import temas as temas_mod
from .util import esta_bloqueado, leer_json, limpiar_texto

TIPOS = {
    "resultado": "Resultado",
    "anuncio_evento": "Anuncio de evento",
    "industria_polemica": "Industria o polémica",
    "software": "Software",
    "obituario": "Obituario",
    "otro": "Otro",
}

CARPETA = Path("data") / "registro"
HECHOS = CARPETA / "hechos.jsonl"
CAMBIOS = CARPETA / "cambios_salas.jsonl"


# ---------------------------------------------------------------- archivos

def leer_jsonl(ruta: Path) -> list[dict]:
    ruta = Path(ruta)
    if not ruta.exists():
        return []
    filas = []
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea:
            continue
        try:
            filas.append(json.loads(linea))
        except json.JSONDecodeError:
            continue
    return filas


def escribir_jsonl(ruta: Path, filas: list[dict]) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    texto = "".join(json.dumps(f, ensure_ascii=False, sort_keys=False) + "\n" for f in filas)
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(ruta)


def agregar(ruta: Path, nuevas: list[dict], clave) -> int:
    """Agrega las filas que todavía no están (según `clave`), ordenadas por fecha. Devuelve cuántas agregó."""
    filas = leer_jsonl(ruta)
    vistas = {clave(f) for f in filas}
    agregadas = 0
    for f in nuevas:
        k = clave(f)
        if k in vistas:
            continue
        vistas.add(k)
        filas.append(f)
        agregadas += 1
    if agregadas:
        filas.sort(key=lambda f: f.get("fecha") or "")  # estable: dentro del día, en el orden de llegada
        escribir_jsonl(ruta, filas)
    return agregadas


def clave_hecho(f: dict) -> str:
    return f.get("id") or ""


def clave_cambio(f: dict) -> tuple:
    return (f.get("sala"), f.get("tema"), _norm(f.get("dato") or ""), (f.get("fuente") or {}).get("url"))


def id_hecho(fecha: str, corrida_id: str, titulo: str) -> str:
    huella = hashlib.sha1(f"{corrida_id}|{titulo}".encode("utf-8")).hexdigest()[:8]
    return f"{fecha}-{huella}"


# ---------------------------------------------------------------- salas vigiladas

def cargar_salas(raiz: Path) -> dict:
    datos = leer_json(Path(raiz) / "config" / "salas_vigiladas.json", {}) or {}
    return {"salas": [s for s in datos.get("salas", []) if s.get("nombre")], "temas": datos.get("temas", {})}


def _norm(texto: str) -> str:
    return " " + re.sub(r"[^\w$]+", " ", calidad.sin_tildes(texto or "")).strip() + " "


def detectar_salas(texto: str, salas: dict) -> list[str]:
    """Salas vigiladas que se nombran en el texto (por nombre o alias, como palabra completa)."""
    plano = _norm(texto)
    return [s["nombre"] for s in salas.get("salas", [])
            if any(_norm(n) in plano for n in [s["nombre"], *s.get("alias", [])] if n.strip())]


def sala_vigilada(valor: str, salas: dict) -> dict | None:
    """La sala de la lista que corresponde a lo que escribió el modelo (nombre o alias)."""
    v = _norm(valor)
    for s in salas.get("salas", []):
        if any(_norm(n) == v for n in [s["nombre"], *s.get("alias", [])] if n.strip()):
            return s
    return None


def tema_sala(valor: str, salas: dict) -> str | None:
    """Clave del tema (acepta la clave o el texto de la etiqueta)."""
    v = _norm((valor or "").replace("_", " ")).strip()
    for clave, etiqueta in salas.get("temas", {}).items():
        if v in (_norm(clave.replace("_", " ")).strip(), _norm(etiqueta).strip()):
            return clave
    return None


# ---------------------------------------------------------------- verificación de campos

def figura(valor: str, texto: str) -> bool:
    """¿El valor está escrito en el texto? (sin distinguir mayúsculas, tildes ni puntuación)."""
    v = _norm(valor)
    return bool(v.strip()) and v in _norm(texto)


# Palabras que pueden ir entre el gentilicio y el nombre: «Argentine pro X», «el jugador argentino X» no.
_DESCRIPTORES = r"(?:pro|player|jugador|jugadora|jogador|jogadora|profesional|professional|grinder|star|estrella|regular|reg|streamer|poker)"


def _formas_pais(pais: str) -> tuple[str, str, str]:
    """(nombre normalizado del país, gentilicios, nombres del país) como alternativas de regex."""
    clave = next((k for k in calidad.NACIONALIDADES if calidad.sin_tildes(k) == calidad.sin_tildes(pais).strip()), None)
    if not clave:
        nombre = re.escape(calidad.sin_tildes(pais).strip())
        return pais.strip(), "(?!)", nombre
    formas = calidad.NACIONALIDADES[clave][1]
    # Las formas «from X» (algunas con grupos: «from the (us|usa|united states)», «from (south )?korea») dan los
    # nombres del país; el resto son gentilicios.
    desde = re.findall(r"from (?:the )?(?:\([^)]*\)[a-z ?]*|[a-z ]+)", formas)
    resto = formas
    for d in desde:
        resto = resto.replace(d, "")
    gentilicios = "|".join(f for f in resto.split("|") if f and "'" not in f) or "(?!)"
    nombres = {re.escape(calidad.sin_tildes(clave))}
    for d in desde:
        d = re.sub(r"^from (?:the )?", "", d).replace("(", "").replace(")", "").replace("?", "")
        nombres.update(re.escape(x.strip()) for x in d.split("|") if len(x.strip()) > 2)
    return clave, gentilicios, "|".join(sorted(nombres))


def pais_explicito(pais: str, nombre: str, texto: str) -> bool:
    """¿La fuente vincula el país con esa persona de forma explícita?
    «el argentino X», «Argentine pro X», «X (Argentina)», «X, de Brasil», «X, from Brazil», «X, argentino»."""
    _, gent, nombres = _formas_pais(pais)
    n = r"\s+".join(re.escape(p) for p in calidad.sin_tildes(nombre).split())
    plano = re.sub(r"\s+", " ", calidad.sin_tildes(texto))
    patrones = [
        rf"\b(?:{gent})\s+(?:{_DESCRIPTORES}\s+)?{n}\b",
        rf"\b{n}\s*\(\s*(?:{nombres}|{gent})\s*\)",
        rf"\b{n},\s*(?:(?:de|from|do|da|of)\s+(?:the\s+)?(?:{nombres})|(?:(?:el|la|o|a|an|the)\s+)?(?:{gent}))\b",
    ]
    return any(re.search(p, plano) for p in patrones)


def _menciona_pais(pais: str, texto: str) -> bool:
    _, gent, nombres = _formas_pais(pais)
    return bool(re.search(rf"\b(?:{gent}|{nombres})\b", calidad.sin_tildes(texto)))


def pais_de_jugador(pais: str, nombre: str, notas: list[tuple[str, dict | None]]) -> dict:
    """{"pais"} si una nota de una fuente (con URL) lo dice de forma explícita; si el país solo aparece
    sin vínculo claro, queda vacío con {"pais_pendiente": {"valor", "fuente"}}; si no aparece, vacío."""
    if not pais:
        return {"pais": ""}
    clave = _formas_pais(pais)[0]
    for texto, fuente in notas:
        if fuente and pais_explicito(pais, nombre, texto):
            return {"pais": clave}
    con_url = [(t, f) for t, f in notas if f]
    menciona = next((f for t, f in con_url if _menciona_pais(pais, t)), None)
    if menciona or any(_menciona_pais(pais, t) for t, _ in notas):
        fuente = menciona or (con_url[0][1] if con_url else {})
        return {"pais": "", "pais_pendiente": {"valor": clave, "fuente": fuente}}
    return {"pais": ""}


def _pais_evento(pais: str, texto: str) -> str | None:
    """El país del evento solo si el texto nombra al país (en español o en inglés)."""
    clave = next((k for k in calidad.NACIONALIDADES if calidad.sin_tildes(k) == calidad.sin_tildes(pais).strip()), None)
    plano = calidad.sin_tildes(texto)
    nombres = {calidad.sin_tildes(pais).strip()}
    if clave:
        nombres.add(calidad.sin_tildes(clave))
        # Nombre en inglés, tomado de las formas «from X» de la tabla de nacionalidades.
        for alt in re.findall(r"from (?:the )?\(?([a-z |]+)", calidad.NACIONALIDADES[clave][1]):
            nombres.update(x.strip() for x in alt.split("|") if len(x.strip()) > 2)
    for n in nombres:
        if n and re.search(rf"\b{re.escape(n)}\b", plano):
            return clave or pais.strip()
    return None


def _lista(valor) -> list:
    return valor if isinstance(valor, list) else []


def _texto(valor) -> str:
    return limpiar_texto(str(valor)) if isinstance(valor, (str, int, float)) else ""


def verificar_ficha(crudo, texto: str, texto_publicado: str, salas: dict, items: list[dict] | None = None) -> dict:
    """Campos del registro propuestos por el modelo, verificados contra el texto enviado.
    `texto_publicado` es el título y el resumen ya verificados de la tarjeta; `items`, las notas enviadas
    (el país de un jugador solo se asigna si una nota con URL lo dice de forma explícita)."""
    notas = [(calidad.texto_items([it]), {"nombre": it.get("fuente") or "", "url": it["url"]} if it.get("url") else None)
             for it in items or []]
    crudo = crudo if isinstance(crudo, dict) else {}
    tipo = _texto(crudo.get("tipo")).lower().replace(" ", "_")
    tipo = tipo if tipo in TIPOS else next((k for k, v in TIPOS.items() if calidad.sin_tildes(v) == calidad.sin_tildes(tipo.replace("_", " "))), "otro")

    nombres_salas = []
    for s in _lista(crudo.get("salas")):
        s = _texto(s)
        vigilada = sala_vigilada(s, salas)
        if s and figura(s, texto):
            s = vigilada["nombre"] if vigilada else s
            if s not in nombres_salas:
                nombres_salas.append(s)
    # Las salas vigiladas nombradas en la tarjeta publicada (ya verificada) también cuentan.
    for s in detectar_salas(texto_publicado, salas):
        if s not in nombres_salas:
            nombres_salas.append(s)

    jugadores = []
    for j in _lista(crudo.get("jugadores")):
        if isinstance(j, dict):
            nombre, pais = _texto(j.get("nombre")), _texto(j.get("pais"))
        elif isinstance(j, list) and j:
            nombre, pais = _texto(j[0]), _texto(j[1]) if len(j) > 1 else ""
        else:
            nombre, pais = _texto(j), ""
        if not nombre or not figura(nombre, texto) or any(x["nombre"] == nombre for x in jugadores):
            continue
        jugadores.append({"nombre": nombre, **pais_de_jugador(pais, nombre, notas)})

    torneo = _texto(crudo.get("torneo"))
    pais_evento = _texto(crudo.get("pais_evento"))
    return {
        "tipo": tipo,
        "salas": nombres_salas,
        "jugadores": jugadores,
        "torneo": torneo if torneo and figura(torneo, texto) else "",
        "pais_evento": (_pais_evento(pais_evento, texto) or "") if pais_evento else "",
    }


def temas_de(texto: str, temas: list[dict]) -> list[str]:
    return [t["nombre"] for t in temas if temas_mod.coincide(texto, t.get("palabras_clave") or [])]


def _fuentes(fuentes: list[dict], items: list[dict]) -> list[dict]:
    clases = {it.get("url"): it.get("clase") for it in items}
    return [{"nombre": f.get("nombre") or f.get("fuente"), "url": f.get("url"), "clase": clases.get(f.get("url")) or ""}
            for f in fuentes if f.get("url") and not esta_bloqueado(f["url"])]


def hecho_de_tarjeta(fecha: str, corrida_id: str, tarjeta: dict, seccion: str, crudo, items: list[dict],
                     salas: dict, temas: list[dict]) -> dict:
    texto = calidad.texto_items(items)
    publicado = f"{tarjeta['titulo']} {tarjeta.get('resumen') or ''} {tarjeta.get('novedad') or ''}"
    ficha = verificar_ficha(crudo, texto, publicado, salas, items)
    return {
        "fecha": fecha,
        "id": id_hecho(fecha, corrida_id, tarjeta["titulo"]),
        "titulo": tarjeta["titulo"],
        "tipo": ficha["tipo"],
        "clasificacion": tarjeta["clasificacion"],
        "seccion": seccion,
        "salas": ficha["salas"],
        "jugadores": ficha["jugadores"],
        "torneo": ficha["torneo"],
        "pais_evento": ficha["pais_evento"],
        "temas_calientes": temas_de(f"{publicado} {texto}", temas),
        "fuentes": _fuentes(tarjeta.get("fuentes") or [], items),
    }


def hecho_de_tema(fecha: str, corrida_id: str, tema: dict, crudo, items: list[dict], salas: dict) -> dict:
    publicado = " ".join([tema.get("linea") or ""] + [n.get("aporta") or "" for n in tema.get("novedades", [])])
    ficha = verificar_ficha(crudo, calidad.texto_items(items), publicado, salas, items)
    clasif = [n.get("clasificacion") for n in tema.get("novedades", [])]
    return {
        "fecha": fecha,
        "id": id_hecho(fecha, corrida_id, tema["tema"]),
        "titulo": tema["tema"],
        "tipo": ficha["tipo"],
        # La más firme de sus novedades (la tarjeta de tema no tiene una clasificación propia).
        "clasificacion": next((c for c in ("confirmado", "discusion", "rumor") if c in clasif), "discusion"),
        "seccion": "tema",
        "salas": ficha["salas"],
        "jugadores": ficha["jugadores"],
        "torneo": ficha["torneo"],
        "pais_evento": ficha["pais_evento"],
        "temas_calientes": [tema["tema"]],
        "fuentes": _fuentes(tema.get("novedades") or [], items),
    }


def verificar_cambio(crudo, items: list[dict], salas: dict, fecha: str) -> tuple[dict | None, str | None]:
    """Un cambio de sala propuesto por el modelo. Devuelve (cambio, motivo de descarte)."""
    if not isinstance(crudo, dict):
        return None, "formato inválido"
    ids = []
    for i in _lista(crudo.get("ids")) or [crudo.get("id")]:
        try:
            i = int(i)
        except (TypeError, ValueError):
            continue
        if 1 <= i <= len(items) and not esta_bloqueado(items[i - 1]["url"]):
            ids.append(i)
    if not ids:
        return None, "sin nota de origen"
    sala = sala_vigilada(_texto(crudo.get("sala")), salas)
    if not sala:
        return None, f"sala no vigilada «{_texto(crudo.get('sala'))[:40]}»"
    tema = tema_sala(_texto(crudo.get("tema")), salas)
    if not tema:
        return None, f"tema fuera de la lista «{_texto(crudo.get('tema'))[:40]}»"
    dato = _texto(crudo.get("dato"))
    if len(dato) < 8:
        return None, "dato vacío"
    for i in ids:
        it = items[i - 1]
        texto = calidad.texto_items([it])
        # La sala debe estar nombrada en la nota y el dato, copiado tal como lo dice la fuente.
        if sala["nombre"] in detectar_salas(texto, salas) and _norm(dato) in _norm(texto):
            return {"fecha": fecha, "sala": sala["nombre"], "tema": tema, "dato": dato,
                    "fuente": {"nombre": it["fuente"], "url": it["url"], "clase": it.get("clase") or ""}}, None
    return None, "el dato o la sala no figuran tal cual en la nota"


def confirmar_paises(filas: list[dict]) -> int:
    """Confirma los países pendientes cuando una línea posterior del registro trae a ese jugador
    con ese país dicho de forma explícita por su fuente. Devuelve cuántos confirmó."""
    confirmados = 0
    for i, fila in enumerate(filas):
        for j in fila.get("jugadores") or []:
            pendiente = j.get("pais_pendiente")
            if not pendiente:
                continue
            for posterior in filas[i + 1:]:
                if any(_norm(x.get("nombre")) == _norm(j["nombre"]) and x.get("pais") == pendiente["valor"]
                       for x in posterior.get("jugadores") or []):
                    j["pais"] = pendiente["valor"]
                    j.pop("pais_pendiente")
                    j["pais_confirmado_por"] = {"fecha": posterior.get("fecha"), "hecho": posterior.get("id")}
                    confirmados += 1
                    break
    return confirmados


def registrar(raiz: Path, hechos: list[dict], cambios: list[dict]) -> tuple[int, int, int]:
    """Agrega hechos y cambios nuevos y confirma países pendientes. Devuelve (hechos, cambios, países confirmados)."""
    raiz = Path(raiz)
    nuevos_hechos = agregar(raiz / HECHOS, hechos, clave_hecho) if hechos else 0
    confirmados = 0
    if nuevos_hechos:
        filas = leer_jsonl(raiz / HECHOS)
        confirmados = confirmar_paises(filas)
        if confirmados:
            escribir_jsonl(raiz / HECHOS, filas)
    return nuevos_hechos, agregar(raiz / CAMBIOS, cambios, clave_cambio) if cambios else 0, confirmados


# ---------------------------------------------------------------- página

def cambios_recientes(raiz: Path, hoy: date, dias: int = 30) -> dict:
    """Datos de la sección «Cambios en salas» (los últimos `dias`), de lo más reciente a lo más antiguo."""
    salas = cargar_salas(raiz)
    desde = (hoy - timedelta(days=dias)).isoformat()
    filas = [dict(c, tema_texto=salas["temas"].get(c.get("tema"), c.get("tema")))
             for c in leer_jsonl(Path(raiz) / CAMBIOS) if desde <= (c.get("fecha") or "") <= hoy.isoformat()]
    filas.sort(key=lambda c: c.get("fecha") or "", reverse=True)
    return {"dias": dias, "filas": filas}
