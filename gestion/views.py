from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import membresia
from .forms import ClienteForm, PagoForm
from .models import Cliente, Pago


def inicio(request):
    return render(request, "gestion/inicio.html")


def lista_clientes(request):
    query = request.GET.get("q", "").strip()
    clientes = Cliente.objects.all().order_by("nombre_completo")
    if query:
        clientes = clientes.filter(
            Q(nombre_completo__icontains=query) | Q(id_acceso__icontains=query)
        )
    return render(request, "gestion/clientes/lista.html", {"clientes": clientes, "query": query})


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


@require_POST
def cliente_toggle_activo(request, id_acceso):
    cliente = get_object_or_404(Cliente, id_acceso=id_acceso)
    cliente.activo = not cliente.activo
    cliente.save()
    estado = "activado" if cliente.activo else "desactivado"
    messages.success(request, f"Cliente {estado}.")
    return redirect("gestion:cliente_detalle", id_acceso=cliente.id_acceso)


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


def pago_historial(request, id_acceso):
    cliente = get_object_or_404(Cliente, id_acceso=id_acceso)
    return render(
        request,
        "gestion/pagos/historial.html",
        {"cliente": cliente, "pagos": cliente.pagos.all(), "ultimo": cliente.ultimo_pago()},
    )


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
