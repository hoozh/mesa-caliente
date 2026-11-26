"""Documentos vigentes de SEI para la redacción de Mesa Caliente.

El flujo del barrido los descarga en cada corrida desde el repositorio privado hoozh/SEO-Articles
(docs/estandares/) con el secreto SEI_LECTURA_TOKEN, a una carpeta temporal fuera del repositorio
(MESA_DIR_SEI). Son la fuente de vocabulario, trato, formato y errores frecuentes: no se copian ni se resumen.

Privacidad: mesa-caliente es público y sus registros de Actions también. El contenido de estos documentos
nunca se imprime, nunca se guarda en el repositorio y nunca va a docs/: solo viaja dentro de la llamada de
redacción. Lo único que se registra es qué archivos se usaron, cuáles faltaron y su tamaño.
"""

from __future__ import annotations

import os
from pathlib import Path

DOCUMENTOS = ("guia-traduccion-poker.md", "reglas-comunes-sei.md", "estandar-editorial-notas.md",
              "redaccion-natural-anti-ia.md")

INTRODUCCION = (
    "Documentos vigentes de SEI (Sistema Editorial de GipsyTeam en español). Úselos como fuente de vocabulario, "
    "trato al lector, formato de números y citas, y errores frecuentes al escribir títulos, bajadas y cuerpos. "
    "Si algo de estos documentos choca con las instrucciones de Mesa Caliente que siguen (solo datos del texto "
    "recibido, nunca inventar, formato de la respuesta en JSON), mandan las instrucciones de Mesa Caliente. "
    "No copie frases de estos documentos en las tarjetas ni los mencione."
)


def cargar(carpeta: str | Path | None = None) -> dict:
    """Lee los documentos descargados. Devuelve {'texto', 'usados', 'faltan', 'estado', 'caracteres'}.

    estado: 'completos', 'incompletos' (falta alguno), 'sin_secreto' (no hay carpeta: falta SEI_LECTURA_TOKEN)."""
    carpeta = carpeta if carpeta is not None else os.environ.get("MESA_DIR_SEI", "").strip()
    if not carpeta or not Path(carpeta).is_dir():
        return {"texto": "", "usados": [], "faltan": list(DOCUMENTOS), "estado": "sin_secreto", "caracteres": 0}
    partes, usados, faltan = [], [], []
    for nombre in DOCUMENTOS:
        ruta = Path(carpeta) / nombre
        try:
            contenido = ruta.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            contenido = ""
        if contenido:
            partes.append(f"<documento nombre=\"{nombre}\">\n{contenido}\n</documento>")
            usados.append(nombre)
        else:
            faltan.append(nombre)
    if not usados:
        return {"texto": "", "usados": [], "faltan": faltan, "estado": "incompletos", "caracteres": 0}
    texto = INTRODUCCION + "\n\n" + "\n\n".join(partes)
    return {"texto": texto, "usados": usados, "faltan": faltan,
            "estado": "completos" if not faltan else "incompletos", "caracteres": len(texto)}


def resumen_estado(docs: dict) -> dict:
    """Lo que se puede registrar (sin contenido)."""
    return {"estado": docs["estado"], "usados": docs["usados"], "faltan": docs["faltan"], "caracteres": docs["caracteres"]}


def aviso(docs: dict) -> str:
    """Texto del aviso para el pie de la página y el registro de costos ('' si están completos)."""
    if docs["estado"] == "completos":
        return ""
    if docs["estado"] == "sin_secreto":
        return "La redacción no usó los documentos de SEI (falta el secreto SEI_LECTURA_TOKEN o no se pudieron descargar)."
    return "Faltan documentos de SEI para la redacción: " + ", ".join(docs["faltan"]) + "."
