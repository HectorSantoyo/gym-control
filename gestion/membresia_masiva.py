from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Optional

from django.utils import timezone

from .models import Cliente


@dataclass(frozen=True)
class EstadoMembresiaCliente:
    cliente: Cliente
    estado: str
    fecha_vencimiento: Optional[date]
    mora: Decimal


def calcular_estados_membresia(queryset=None, fecha_referencia=None):
    """Calcula estado, vencimiento y mora de varios clientes con una sola consulta.

    queryset debe ser un QuerySet de Cliente (por defecto, clientes
    activos). Se anota con ClienteQuerySet.con_ultimo_periodo_fin() para
    evitar una consulta por cliente; estado/vencimiento/mora se calculan
    reutilizando los métodos de instancia de Cliente (misma fuente de
    verdad que gestion/membresia.py y el resto del sistema), pasándoles
    el dato ya anotado.
    """
    fecha_referencia = fecha_referencia or timezone.localdate()
    if queryset is None:
        queryset = Cliente.objects.filter(activo=True)

    clientes_anotados = queryset.con_ultimo_periodo_fin()

    resultados = []
    for cliente in clientes_anotados:
        ultimo_periodo_fin = cliente.ultimo_periodo_fin
        resultados.append(
            EstadoMembresiaCliente(
                cliente=cliente,
                estado=cliente.estado_actual(fecha_referencia, ultimo_periodo_fin=ultimo_periodo_fin),
                fecha_vencimiento=cliente.fecha_vencimiento_pendiente(ultimo_periodo_fin=ultimo_periodo_fin),
                mora=cliente.mora_actual(fecha_referencia, ultimo_periodo_fin=ultimo_periodo_fin),
            )
        )
    return resultados


def contar_por_estado(resultados):
    return Counter(resultado.estado for resultado in resultados)
