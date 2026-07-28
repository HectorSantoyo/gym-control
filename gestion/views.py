from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from . import asistencias, membresia
from .forms import CheckinForm, ClienteForm, PagoForm
from .membresia_masiva import calcular_estados_membresia, contar_por_estado
from .models import Asistencia, Cliente, ESTADO_MEMBRESIA_CHOICES, OrigenAsistencia, Pago

COOKIE_CLIENTE_RECORDADO = "gym_cliente_recordado"
COOKIE_CLIENTE_RECORDADO_SALT = "gestion.checkin.cliente_recordado"
COOKIE_CLIENTE_RECORDADO_MAX_AGE = 60 * 60 * 24 * 180  # 180 días

ASISTENCIAS_POR_PAGINA = 20
MEMBRESIAS_POR_PAGINA = 20

_PRIORIDAD_ESTADO = {
    membresia.ESTADO_VENCIDA_CON_MORA: 0,
    membresia.ESTADO_EN_GRACIA: 1,
    membresia.ESTADO_POR_VENCER: 2,
    membresia.ESTADO_SIN_PAGOS: 3,
    membresia.ESTADO_VIGENTE: 4,
}
_ESTADOS_VALIDOS = set(_PRIORIDAD_ESTADO)


@login_required
def inicio(request):
    return render(request, "gestion/inicio.html")


@login_required
def lista_clientes(request):
    query = request.GET.get("q", "").strip()
    clientes = Cliente.objects.all().order_by("nombre_completo")
    if query:
        clientes = clientes.filter(
            Q(nombre_completo__icontains=query) | Q(id_acceso__icontains=query)
        )
    return render(request, "gestion/clientes/lista.html", {"clientes": clientes, "query": query})


@login_required
def cliente_crear(request):
    if request.method == "POST":
        form = ClienteForm(request.POST, request.FILES)
        if form.is_valid():
            cliente = form.save()
            messages.success(request, f"Cliente {cliente.nombre_completo} registrado con ID {cliente.id_acceso}.")
            return redirect("gestion:cliente_detalle", id_acceso=cliente.id_acceso)
    else:
        form = ClienteForm()
    return render(request, "gestion/clientes/formulario.html", {"form": form, "titulo": "Nuevo cliente"})


@login_required
def cliente_detalle(request, id_acceso):
    cliente = get_object_or_404(Cliente, id_acceso=id_acceso)
    hoy = timezone.localdate()
    contexto = {
        "cliente": cliente,
        "estado_actual": cliente.estado_actual(hoy),
        "fecha_vencimiento_pendiente": cliente.fecha_vencimiento_pendiente(),
        "mora_actual": cliente.mora_actual(hoy),
        "total_sugerido": cliente.total_sugerido(hoy),
        "ultimos_pagos": cliente.pagos.all()[:5],
    }
    return render(request, "gestion/clientes/detalle.html", contexto)


@login_required
def cliente_editar(request, id_acceso):
    cliente = get_object_or_404(Cliente, id_acceso=id_acceso)
    if request.method == "POST":
        form = ClienteForm(request.POST, request.FILES, instance=cliente)
        if form.is_valid():
            form.save()
            messages.success(request, "Cliente actualizado correctamente.")
            return redirect("gestion:cliente_detalle", id_acceso=cliente.id_acceso)
    else:
        form = ClienteForm(instance=cliente)
    return render(
        request,
        "gestion/clientes/formulario.html",
        {"form": form, "titulo": "Editar cliente", "cliente": cliente},
    )


@login_required
@require_POST
def cliente_toggle_activo(request, id_acceso):
    cliente = get_object_or_404(Cliente, id_acceso=id_acceso)
    cliente.activo = not cliente.activo
    cliente.save()
    estado = "activado" if cliente.activo else "desactivado"
    messages.success(request, f"Cliente {estado}.")
    return redirect("gestion:cliente_detalle", id_acceso=cliente.id_acceso)


@login_required
def pago_registrar(request, id_acceso):
    cliente = get_object_or_404(Cliente, id_acceso=id_acceso)
    es_primer_pago = cliente.ultimo_pago() is None
    periodo_pendiente = None if es_primer_pago else cliente.siguiente_periodo_pendiente()
    hoy = timezone.localdate()
    candidatos_periodo = membresia.periodos_candidatos(cliente.dia_pago, hoy) if es_primer_pago else None
    mostrar_inscripcion = not cliente.inscripcion_aplicada

    if request.method == "POST":
        form = PagoForm(
            request.POST,
            mostrar_periodo=es_primer_pago,
            periodos_candidatos=candidatos_periodo,
            mostrar_reajuste_inicial=es_primer_pago,
            mostrar_inscripcion=mostrar_inscripcion,
        )
        if form.is_valid():
            if es_primer_pago:
                periodo_inicio = date.fromisoformat(form.cleaned_data["periodo_seleccionado"])
                periodo_inicio, periodo_fin = membresia.calcular_periodo_natural(cliente.dia_pago, periodo_inicio)
            else:
                periodo_inicio, periodo_fin = periodo_pendiente

            pago = form.save(commit=False)
            pago.cliente = cliente
            pago.periodo_inicio = periodo_inicio
            pago.periodo_fin = periodo_fin
            pago.mensualidad_base = membresia.tarifa_base(cliente.tipo_tarifa)
            pago.inscripcion = (
                membresia.INSCRIPCION_MONTO
                if mostrar_inscripcion and form.cleaned_data.get("cobrar_inscripcion")
                else Decimal("0")
            )
            pago.save()
            cliente.recalcular_banderas_pago()
            messages.success(request, "Pago registrado correctamente.")
            return redirect("gestion:cliente_detalle", id_acceso=cliente.id_acceso)
    else:
        form = PagoForm(
            mostrar_periodo=es_primer_pago,
            periodos_candidatos=candidatos_periodo,
            mostrar_reajuste_inicial=es_primer_pago,
            mostrar_inscripcion=mostrar_inscripcion,
            initial={"fecha_pago": hoy},
        )

    mora_estimada = None
    if periodo_pendiente:
        mora_estimada = membresia.calcular_mora(periodo_pendiente[0], hoy)

    return render(
        request,
        "gestion/pagos/formulario.html",
        {
            "form": form,
            "cliente": cliente,
            "es_primer_pago": es_primer_pago,
            "periodo_pendiente": periodo_pendiente,
            "mora_estimada": mora_estimada,
            "mensualidad_base": membresia.tarifa_base(cliente.tipo_tarifa),
            "inscripcion_monto": membresia.INSCRIPCION_MONTO,
            "hoy": hoy,
            "es_edicion": False,
            "titulo": "Registrar pago",
        },
    )


@login_required
def pago_historial(request, id_acceso):
    cliente = get_object_or_404(Cliente, id_acceso=id_acceso)
    return render(
        request,
        "gestion/pagos/historial.html",
        {"cliente": cliente, "pagos": cliente.pagos.all(), "ultimo": cliente.ultimo_pago()},
    )


@login_required
def pago_editar(request, pago_id):
    pago = get_object_or_404(Pago, pk=pago_id)
    cliente = pago.cliente
    if pago.tiene_pagos_posteriores():
        messages.error(
            request,
            "Solo se puede editar el pago más reciente de un cliente. Elimina primero los pagos posteriores.",
        )
        return redirect("gestion:pago_historial", id_acceso=cliente.id_acceso)

    es_primer_pago = cliente.pagos.count() == 1
    inscripcion_aplicada_en_otro_pago = cliente.pagos.exclude(pk=pago.pk).filter(inscripcion__gt=0).exists()
    mostrar_inscripcion = not inscripcion_aplicada_en_otro_pago
    inicial_inscripcion = {"cobrar_inscripcion": pago.inscripcion > 0} if mostrar_inscripcion else None

    if request.method == "POST":
        form = PagoForm(
            request.POST,
            instance=pago,
            mostrar_reajuste_inicial=es_primer_pago,
            mostrar_inscripcion=mostrar_inscripcion,
        )
        if form.is_valid():
            pago = form.save(commit=False)
            if mostrar_inscripcion:
                pago.inscripcion = (
                    membresia.INSCRIPCION_MONTO if form.cleaned_data.get("cobrar_inscripcion") else Decimal("0")
                )
            pago.save()
            cliente.recalcular_banderas_pago()
            messages.success(request, "Pago actualizado correctamente.")
            return redirect("gestion:pago_historial", id_acceso=cliente.id_acceso)
    else:
        form = PagoForm(
            instance=pago,
            mostrar_reajuste_inicial=es_primer_pago,
            mostrar_inscripcion=mostrar_inscripcion,
            initial=inicial_inscripcion,
        )

    return render(
        request,
        "gestion/pagos/formulario.html",
        {
            "form": form,
            "cliente": cliente,
            "es_primer_pago": False,
            "periodo_pendiente": (pago.periodo_inicio, pago.periodo_fin),
            "mora_estimada": None,
            "mensualidad_base": pago.mensualidad_base,
            "inscripcion_monto": membresia.INSCRIPCION_MONTO,
            "hoy": timezone.localdate(),
            "es_edicion": True,
            "titulo": "Editar pago",
        },
    )


@login_required
def pago_eliminar(request, pago_id):
    pago = get_object_or_404(Pago, pk=pago_id)
    cliente = pago.cliente
    if pago.tiene_pagos_posteriores():
        messages.error(request, "Solo se puede eliminar el pago más reciente de un cliente.")
        return redirect("gestion:pago_historial", id_acceso=cliente.id_acceso)

    if request.method == "POST":
        pago.delete()
        cliente.recalcular_banderas_pago()
        messages.success(request, "Pago eliminado.")
        return redirect("gestion:pago_historial", id_acceso=cliente.id_acceso)

    return render(request, "gestion/pagos/eliminar.html", {"pago": pago, "cliente": cliente})


def _resolver_cliente_recordado(request):
    """Lee gym_cliente_recordado y devuelve (cliente, cookie_invalida).

    cookie_invalida es True cuando había una cookie en la petición pero no
    resolvió en un cliente activo (firma inválida/expirada, pk inexistente
    o cliente inactivo) — señal para que la vista la elimine en el response.
    """
    if COOKIE_CLIENTE_RECORDADO not in request.COOKIES:
        return None, False

    pk = request.get_signed_cookie(
        COOKIE_CLIENTE_RECORDADO, salt=COOKIE_CLIENTE_RECORDADO_SALT, default=None
    )
    if pk is None:
        return None, True

    cliente = Cliente.objects.filter(pk=pk, activo=True).first()
    return cliente, cliente is None


def _recordar_cliente(response, cliente):
    response.set_signed_cookie(
        COOKIE_CLIENTE_RECORDADO,
        cliente.pk,
        salt=COOKIE_CLIENTE_RECORDADO_SALT,
        max_age=COOKIE_CLIENTE_RECORDADO_MAX_AGE,
        httponly=True,
        samesite="Lax",
        # TODO: cambiar a secure=True en producción, cuando el sitio se sirva por HTTPS.
        secure=False,
    )


def _contexto_resultado(resultado):
    return {
        "resultado": resultado,
        "MOTIVO_CREADA": asistencias.MOTIVO_CREADA,
        "MOTIVO_DUPLICADO": asistencias.MOTIVO_DUPLICADO,
        "MOTIVO_CLIENTE_NO_ENCONTRADO": asistencias.MOTIVO_CLIENTE_NO_ENCONTRADO,
        "MOTIVO_CLIENTE_INACTIVO": asistencias.MOTIVO_CLIENTE_INACTIVO,
    }


def checkin(request):
    cliente_recordado, cookie_invalida = _resolver_cliente_recordado(request)

    if request.method == "POST":
        form = CheckinForm(request.POST)
        if form.is_valid():
            resultado = asistencias.procesar_checkin(
                form.cleaned_data["id_acceso"], origen=OrigenAsistencia.CLIENTE
            )
            response = render(request, "gestion/checkin/resultado.html", _contexto_resultado(resultado))
            if form.cleaned_data["recordarme"] and resultado.motivo in (
                asistencias.MOTIVO_CREADA,
                asistencias.MOTIVO_DUPLICADO,
            ):
                _recordar_cliente(response, resultado.cliente)
            return response
    else:
        form = CheckinForm()

    response = render(
        request,
        "gestion/checkin/formulario.html",
        {"form": form, "cliente_recordado": cliente_recordado},
    )
    if cookie_invalida:
        response.delete_cookie(COOKIE_CLIENTE_RECORDADO)
    return response


@require_POST
def checkin_confirmar(request):
    cliente_recordado, cookie_invalida = _resolver_cliente_recordado(request)

    if cliente_recordado is None:
        response = redirect("gestion:checkin")
        if cookie_invalida:
            response.delete_cookie(COOKIE_CLIENTE_RECORDADO)
        return response

    resultado = asistencias.registrar_asistencia(cliente_recordado, OrigenAsistencia.CLIENTE)
    return render(request, "gestion/checkin/resultado.html", _contexto_resultado(resultado))


@require_POST
def checkin_olvidar(request):
    response = redirect("gestion:checkin")
    response.delete_cookie(COOKIE_CLIENTE_RECORDADO)
    return response


@login_required
def asistencia_lista(request):
    query = request.GET.get("q", "").strip()
    fecha_str = request.GET.get("fecha", "").strip()

    asistencias_qs = Asistencia.objects.select_related("cliente").order_by("-fecha_hora")

    if query:
        filtro = Q(cliente__nombre_completo__icontains=query)
        if query.isdigit():
            filtro |= Q(cliente__id_acceso=int(query))
        asistencias_qs = asistencias_qs.filter(filtro)

    fecha_valida = True
    if fecha_str:
        fecha_filtro = parse_date(fecha_str)
        if fecha_filtro is None:
            fecha_valida = False
        else:
            asistencias_qs = asistencias_qs.filter(fecha_hora__date=fecha_filtro)

    paginator = Paginator(asistencias_qs, ASISTENCIAS_POR_PAGINA)
    pagina = paginator.get_page(request.GET.get("page"))

    hoy = timezone.localdate()
    total_hoy = Asistencia.objects.filter(fecha_hora__date=hoy).count()

    querystring = request.GET.copy()
    querystring.pop("page", None)

    return render(
        request,
        "gestion/asistencias/lista.html",
        {
            "asistencias": pagina,
            "query": query,
            "fecha": fecha_str,
            "fecha_valida": fecha_valida,
            "total_hoy": total_hoy,
            "querystring": querystring.urlencode(),
        },
    )


@login_required
def membresia_lista(request):
    hoy = timezone.localdate()
    q = request.GET.get("q", "").strip()
    estado_seleccionado = request.GET.get("estado", "").strip()
    if estado_seleccionado not in _ESTADOS_VALIDOS:
        estado_seleccionado = ""

    clientes_activos = Cliente.objects.filter(activo=True)

    if q:
        filtro = Q(nombre_completo__icontains=q)
        if q.isdigit():
            filtro |= Q(id_acceso=int(q))
        clientes_busqueda = clientes_activos.filter(filtro)
        conteos = contar_por_estado(
            calcular_estados_membresia(queryset=clientes_activos, fecha_referencia=hoy)
        )
        resultados = calcular_estados_membresia(queryset=clientes_busqueda, fecha_referencia=hoy)
    else:
        resultados = calcular_estados_membresia(queryset=clientes_activos, fecha_referencia=hoy)
        conteos = contar_por_estado(resultados)

    if estado_seleccionado:
        resultados = [resultado for resultado in resultados if resultado.estado == estado_seleccionado]

    resultados.sort(key=lambda r: (_PRIORIDAD_ESTADO[r.estado], r.cliente.nombre_completo.lower()))

    paginator = Paginator(resultados, MEMBRESIAS_POR_PAGINA)
    page_obj = paginator.get_page(request.GET.get("page"))

    querystring = request.GET.copy()
    querystring.pop("page", None)

    conteos_por_estado = [
        (valor, etiqueta, conteos.get(valor, 0)) for valor, etiqueta in ESTADO_MEMBRESIA_CHOICES
    ]

    return render(
        request,
        "gestion/membresias/lista.html",
        {
            "page_obj": page_obj,
            "conteos": conteos,
            "conteos_por_estado": conteos_por_estado,
            "q": q,
            "estado_seleccionado": estado_seleccionado,
            "querystring": querystring.urlencode(),
        },
    )
