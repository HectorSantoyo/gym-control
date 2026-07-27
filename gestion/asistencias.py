from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.utils import timezone

from .models import Asistencia, Cliente, OrigenAsistencia

VENTANA_DUPLICADO_MINUTOS = 5

MOTIVO_CREADA = "creada"
MOTIVO_DUPLICADO = "duplicado"
MOTIVO_CLIENTE_NO_ENCONTRADO = "cliente_no_encontrado"
MOTIVO_CLIENTE_INACTIVO = "cliente_inactivo"


@dataclass
class SnapshotMembresia:
    estado_membresia: str
    fecha_vencimiento: Optional[date]
    mora: Decimal


@dataclass
class ResultadoCheckIn:
    ok: bool
    motivo: str
    asistencia: Optional[Asistencia] = None
    cliente: Optional[Cliente] = None


def buscar_cliente_por_id_acceso(id_acceso) -> Optional[Cliente]:
    return Cliente.objects.filter(id_acceso=id_acceso).first()


def capturar_estado_membresia(cliente: Cliente, ahora: Optional[datetime] = None) -> SnapshotMembresia:
    ahora = ahora or timezone.now()
    fecha_referencia = timezone.localtime(ahora).date()
    return SnapshotMembresia(
        estado_membresia=cliente.estado_actual(fecha_referencia),
        fecha_vencimiento=cliente.fecha_vencimiento_pendiente(),
        mora=cliente.mora_actual(fecha_referencia),
    )


def tiene_asistencia_reciente(cliente: Cliente, ahora: Optional[datetime] = None) -> Optional[Asistencia]:
    ahora = ahora or timezone.now()
    ultima = cliente.ultima_asistencia()
    if ultima is None:
        return None
    if ahora - ultima.fecha_hora < timedelta(minutes=VENTANA_DUPLICADO_MINUTOS):
        return ultima
    return None


def registrar_asistencia(cliente: Cliente, origen: str, ahora: Optional[datetime] = None) -> ResultadoCheckIn:
    ahora = ahora or timezone.now()
    # transaction.atomic() serializa la verificación de duplicado y la creación
    # dentro de la misma transacción de escritura. En SQLite esto es una
    # mitigación aceptada para desarrollo local (el motor bloquea el archivo
    # completo ante escrituras concurrentes), no una garantía real de
    # exclusión mutua como la que daría select_for_update() en Postgres.
    with transaction.atomic():
        duplicado = tiene_asistencia_reciente(cliente, ahora)
        if duplicado is not None:
            return ResultadoCheckIn(ok=False, motivo=MOTIVO_DUPLICADO, asistencia=duplicado, cliente=cliente)

        snapshot = capturar_estado_membresia(cliente, ahora)
        asistencia = Asistencia.objects.create(
            cliente=cliente,
            fecha_hora=ahora,
            estado_membresia=snapshot.estado_membresia,
            fecha_vencimiento=snapshot.fecha_vencimiento,
            mora_al_ingresar=snapshot.mora,
            origen=origen,
        )
    return ResultadoCheckIn(ok=True, motivo=MOTIVO_CREADA, asistencia=asistencia, cliente=cliente)


def procesar_checkin(
    id_acceso, origen: str = OrigenAsistencia.CLIENTE, ahora: Optional[datetime] = None
) -> ResultadoCheckIn:
    cliente = buscar_cliente_por_id_acceso(id_acceso)
    if cliente is None:
        return ResultadoCheckIn(ok=False, motivo=MOTIVO_CLIENTE_NO_ENCONTRADO)
    if not cliente.activo:
        return ResultadoCheckIn(ok=False, motivo=MOTIVO_CLIENTE_INACTIVO, cliente=cliente)
    return registrar_asistencia(cliente, origen, ahora)
