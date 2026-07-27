import calendar
from datetime import date, timedelta
from decimal import Decimal

TARIFA_GENERAL = Decimal("330")
TARIFA_ESTUDIANTE = Decimal("280")
TARIFAS_POR_TIPO = {
    "general": TARIFA_GENERAL,
    "estudiante": TARIFA_ESTUDIANTE,
}

INSCRIPCION_MONTO = Decimal("50")
REAJUSTES_VALIDOS = (Decimal("-50"), Decimal("0"), Decimal("50"))

DIAS_GRACIA = 3  # regla del reglamento
DIAS_POR_VENCER = 3  # decisión del sistema; no es una regla del reglamento original
MORA_DIARIA = Decimal("10")

ESTADO_VIGENTE = "vigente"
ESTADO_POR_VENCER = "por_vencer"
ESTADO_EN_GRACIA = "en_gracia"
ESTADO_VENCIDA_CON_MORA = "vencida_con_mora"
ESTADO_SIN_PAGOS = "sin_pagos"


def tarifa_base(tipo_tarifa):
    return TARIFAS_POR_TIPO[tipo_tarifa]


def _ultimo_dia_mes(anio, mes):
    return calendar.monthrange(anio, mes)[1]


def calcular_periodo_natural(dia_pago, fecha_referencia):
    """Periodo (mes o quincena) al que pertenece fecha_referencia según dia_pago.

    Bloque de construcción de bajo nivel: NO usar directamente para calcular
    renovaciones ni para inferir el siguiente periodo de pago de un cliente
    con historial (eso lo hace siguiente_periodo, a partir del último
    periodo cubierto, sin importar la fecha real de pago). Úsala solo a
    través de primer_periodo, para clientes sin pagos previos.
    """
    anio, mes = fecha_referencia.year, fecha_referencia.month
    if dia_pago == 1:
        inicio = date(anio, mes, 1)
        fin = date(anio, mes, _ultimo_dia_mes(anio, mes))
    elif dia_pago == 15:
        inicio = date(anio, mes, 15)
        anio_siguiente, mes_siguiente = (anio, mes + 1) if mes < 12 else (anio + 1, 1)
        fin = date(anio_siguiente, mes_siguiente, 14)
    else:
        raise ValueError("dia_pago debe ser 1 o 15.")
    return inicio, fin


def primer_periodo(dia_pago, fecha_referencia):
    """Periodo inicial para un cliente sin pagos previos.

    Se invoca únicamente cuando el encargado ya decidió, desde la interfaz,
    la fecha de arranque acordada con el cliente. El motor de reglas nunca
    la invoca por su cuenta.
    """
    return calcular_periodo_natural(dia_pago, fecha_referencia)


def siguiente_periodo(periodo_fin, dia_pago):
    """Periodo pendiente inmediatamente después del último periodo cubierto.

    Depende únicamente de periodo_fin y dia_pago: nunca recibe una fecha de
    pago ni de referencia, para que un pago atrasado siempre cubra el
    siguiente periodo pendiente real, sin saltarse periodos ni adelantarse
    según la fecha en que efectivamente se pague.
    """
    return calcular_periodo_natural(dia_pago, periodo_fin + timedelta(days=1))


def periodos_candidatos(dia_pago, fecha_referencia):
    """Periodo anterior, actual y siguiente relativos a fecha_referencia.

    Uso exclusivo para ofrecer opciones visuales al encargado al registrar
    el primer pago de un cliente sin historial: el sistema nunca asume por
    su cuenta cuál es el periodo correcto, solo ofrece opciones cercanas.
    """
    actual = calcular_periodo_natural(dia_pago, fecha_referencia)
    # Retroceder dia_pago días desde el inicio de "actual" siempre aterriza
    # en el último día del mes anterior (ej. 15 - 15 días = día 0 = último
    # día del mes previo), sea cual sea el día fijo (1 o 15).
    anterior = calcular_periodo_natural(dia_pago, actual[0] - timedelta(days=dia_pago))
    siguiente = siguiente_periodo(actual[1], dia_pago)
    return [anterior, actual, siguiente]


def fecha_vencimiento(periodo_inicio):
    return periodo_inicio


def calcular_mora(fecha_vencimiento_actual, fecha_referencia):
    dias_retraso = (fecha_referencia - fecha_vencimiento_actual).days
    dias_con_mora = max(dias_retraso - DIAS_GRACIA, 0)
    return MORA_DIARIA * dias_con_mora


def calcular_estado(fecha_vencimiento_actual, fecha_referencia):
    dias_retraso = (fecha_referencia - fecha_vencimiento_actual).days
    if dias_retraso > DIAS_GRACIA:
        return ESTADO_VENCIDA_CON_MORA
    if dias_retraso > 0:
        return ESTADO_EN_GRACIA
    if dias_retraso >= -DIAS_POR_VENCER:
        return ESTADO_POR_VENCER
    return ESTADO_VIGENTE


def calcular_total_sugerido(tipo_tarifa, incluir_inscripcion, fecha_vencimiento_actual, fecha_referencia):
    total = tarifa_base(tipo_tarifa)
    if incluir_inscripcion:
        total += INSCRIPCION_MONTO
    total += calcular_mora(fecha_vencimiento_actual, fecha_referencia)
    return total
