from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import ClienteForm
from .models import Cliente


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
    return render(request, "gestion/clientes/detalle.html", {"cliente": cliente})


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
