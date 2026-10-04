"""Salud de fuentes: pausa tras varios días seguidos de fallos y reintento semanal."""

from __future__ import annotations

from datetime import date


def _fecha(valor: str | None) -> date | None:
    if not valor:
        return None
    try:
        return date.fromisoformat(valor[:10])
    except ValueError:
        return None


def debe_intentar(fuente: dict, hoy: date, dias_reintento: int = 7) -> bool:
    """Las activas se consultan siempre; las pausadas, una vez por semana."""
    if fuente.get("estado", "activa") != "en_pausa":
        return True
    ultimo = _fecha(fuente.get("ultimo_intento"))
    return ultimo is None or (hoy - ultimo).days >= dias_reintento


def registrar_exito(fuente: dict, hoy: date, cantidad: int) -> None:
    fuente["estado"] = "activa"
    fuente["fallos_consecutivos"] = 0
    fuente["ultimo_ok"] = hoy.isoformat()
    fuente["ultimo_intento"] = hoy.isoformat()
    fuente["ultimos_titulares"] = cantidad
    for clave in ("motivo_pausa", "pausada_desde", "ultimo_motivo"):
        fuente.pop(clave, None)


def registrar_fallo(fuente: dict, hoy: date, motivo: str, fallos_para_pausa: int = 3) -> None:
    """Cuenta como mucho un fallo por día (varias corridas el mismo día no suman)."""
    hoy_txt = hoy.isoformat()
    if fuente.get("ultimo_fallo") != hoy_txt:
        fuente["fallos_consecutivos"] = int(fuente.get("fallos_consecutivos") or 0) + 1
    fuente["ultimo_fallo"] = hoy_txt
    fuente["ultimo_intento"] = hoy_txt
    fuente["ultimo_motivo"] = motivo
    fuente["ultimos_titulares"] = 0
    if fuente.get("estado") == "en_pausa":
        fuente["motivo_pausa"] = motivo
    elif fuente["fallos_consecutivos"] >= fallos_para_pausa:
        fuente["estado"] = "en_pausa"
        fuente["pausada_desde"] = hoy_txt
        fuente["motivo_pausa"] = motivo
