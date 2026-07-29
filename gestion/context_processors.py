SECCION_INICIO = "inicio"
SECCION_CLIENTES = "clientes"
SECCION_MEMBRESIAS = "membresias"
SECCION_ASISTENCIAS = "asistencias"
SECCION_CHECKIN = "checkin"

_SECCION_POR_URL_NAME = {
    "inicio": SECCION_INICIO,
    "cliente_lista": SECCION_CLIENTES,
    "cliente_crear": SECCION_CLIENTES,
    "cliente_detalle": SECCION_CLIENTES,
    "cliente_editar": SECCION_CLIENTES,
    "cliente_toggle_activo": SECCION_CLIENTES,
    "pago_registrar": SECCION_CLIENTES,
    "pago_historial": SECCION_CLIENTES,
    "pago_editar": SECCION_CLIENTES,
    "pago_eliminar": SECCION_CLIENTES,
    "membresia_lista": SECCION_MEMBRESIAS,
    "asistencia_lista": SECCION_ASISTENCIAS,
    "checkin": SECCION_CHECKIN,
    "checkin_confirmar": SECCION_CHECKIN,
    "checkin_olvidar": SECCION_CHECKIN,
}


def navegacion(request):
    """Expone seccion_activa a todos los templates según la URL resuelta.

    No consulta la base de datos: solo traduce request.resolver_match
    (ya calculado por Django para esta petición) mediante un mapeo fijo.
    """
    resolver_match = getattr(request, "resolver_match", None)
    seccion_activa = None
    if resolver_match is not None and resolver_match.namespace == "gestion":
        seccion_activa = _SECCION_POR_URL_NAME.get(resolver_match.url_name)
    return {"seccion_activa": seccion_activa}
