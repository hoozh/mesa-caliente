"""Abre las propuestas en Chromium (Playwright) y verifica imágenes, filtros y celular.

- Recorre la página para que carguen las imágenes diferidas (loading="lazy") y cuenta
  cuántas se ven de verdad (naturalWidth > 0) y cuántas cayeron al degradado (onerror).
- Prueba el buscador, los filtros, el contador y el botón "Limpiar filtros".
- Revisa que en ancho de celular (390 px) no haya desplazamiento horizontal.
- Guarda capturas en la carpeta indicada (por defecto, una carpeta temporal).

Uso: python scripts/verificar_propuestas.py [carpeta_de_capturas]
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parent.parent
PROPUESTAS = RAIZ / "docs" / "propuestas"
CHROMIUM = os.environ.get("CHROMIUM", "/opt/pw-browsers/chromium")


def argumentos_chromium() -> list[str]:
    """Si el entorno usa un proxy con su propia autoridad de certificados (por ejemplo, el de
    una sesión en la nube), se le indica a Chromium que confíe solo en esa autoridad."""
    ca = os.environ.get("MESA_PROXY_CA", "/root/.ccr/agent-proxy-ca.crt")
    if not os.path.exists(ca):
        return []
    import base64
    import hashlib

    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    cert = x509.load_pem_x509_certificate(Path(ca).read_bytes())
    spki = cert.public_key().public_bytes(serialization.Encoding.DER,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)
    return [f"--ignore-certificate-errors-spki-list={base64.b64encode(hashlib.sha256(spki).digest()).decode()}"]


def recorrer(pagina) -> None:
    alto = pagina.evaluate("document.body.scrollHeight")
    y = 0
    while y < alto:
        pagina.evaluate(f"window.scrollTo(0, {y})")
        pagina.wait_for_timeout(120)
        y += 700
        alto = pagina.evaluate("document.body.scrollHeight")
    pagina.wait_for_timeout(2500)
    pagina.evaluate("window.scrollTo(0, 0)")


def estado_imagenes(pagina) -> dict:
    return pagina.evaluate("""() => {
      const conImagen = document.querySelectorAll('[data-tarjeta] img, [data-tarjeta] .media--sin-imagen, [data-tarjeta] .miniatura--sin-imagen');
      const imgs = [...document.querySelectorAll('[data-tarjeta] img')];
      const vistas = imgs.filter(i => i.complete && i.naturalWidth > 0).length;
      const pendientes = imgs.filter(i => !i.complete).length;
      return {img_en_pagina: imgs.length, vistas, pendientes};
    }""")


def probar_filtros(pagina) -> dict:
    total = pagina.locator("[data-tarjeta]").count()
    visibles = lambda: pagina.locator("[data-tarjeta]:not([hidden])").count()  # noqa: E731
    contador = lambda: pagina.locator("#f-contador").inner_text()  # noqa: E731
    r = {"total": total, "contador_inicial": contador()}
    if pagina.locator("#f-mostrar").is_visible():
        pagina.click("#f-mostrar")
    pagina.fill("#f-buscar", "aido")
    pagina.wait_for_timeout(300)
    r["buscar_aido"] = visibles()
    r["contador_aido"] = contador()
    pagina.fill("#f-buscar", "")
    pagina.select_option("#f-clasif", "rumor")
    r["solo_rumor"] = visibles()
    pagina.select_option("#f-clasif", "")
    pagina.check("#f-latam")
    r["solo_latam"] = visibles()
    pagina.select_option("#f-fuente", "Poker Noticias")
    r["latam_y_poker_noticias"] = visibles()
    pagina.uncheck("#f-latam")
    pagina.select_option("#f-fuente", "")
    pagina.select_option("#f-dia", "2026-09-27")
    r["dia_27_sep"] = visibles()
    r["dia_27_sep_nota_visible"] = pagina.locator("text=no encontró hechos nuevos").first.is_visible()
    pagina.select_option("#f-dia", "")
    pagina.fill("#f-buscar", "xyzxyz sin resultados")
    pagina.wait_for_timeout(300)
    r["sin_resultados_aviso"] = pagina.locator("#f-vacio").is_visible()
    pagina.click("#f-limpiar")
    pagina.wait_for_timeout(200)
    r["tras_limpiar"] = visibles()
    r["contador_final"] = contador()
    return r


def main() -> int:
    capturas = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="propuestas-"))
    capturas.mkdir(parents=True, exist_ok=True)
    proxy = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
    resultado = {}
    with sync_playwright() as p:
        navegador = p.chromium.launch(executable_path=CHROMIUM, proxy={"server": proxy} if proxy else None,
                                      args=argumentos_chromium())
        for nombre in ("propuesta-a", "propuesta-b"):
            url = (PROPUESTAS / f"{nombre}.html").as_uri()
            resultado[nombre] = {}
            for ancho, alto, etiqueta in ((390, 844, "celular"), (1280, 900, "escritorio")):
                pagina = navegador.new_page(viewport={"width": ancho, "height": alto})
                pagina.goto(url, wait_until="domcontentloaded")
                recorrer(pagina)
                datos = {"imagenes": estado_imagenes(pagina),
                         "desborde_horizontal": pagina.evaluate(
                             "document.documentElement.scrollWidth > document.documentElement.clientWidth")}
                pagina.wait_for_timeout(1500)
                pagina.screenshot(path=str(capturas / f"{nombre}-{etiqueta}.png"))
                if etiqueta == "celular":
                    datos["filtros"] = probar_filtros(pagina)
                    pagina.fill("#f-buscar", "")
                resultado[nombre][etiqueta] = datos
                pagina.close()
        navegador.close()
    print(json.dumps(resultado, ensure_ascii=False, indent=2))
    print(f"Capturas en: {capturas}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
