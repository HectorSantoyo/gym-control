from django.contrib import admin

from .models import Cliente, Pago


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("id_acceso", "nombre_completo", "telefono", "activo", "fecha_alta")
    list_filter = ("activo",)
    search_fields = ("nombre_completo", "id_acceso")
    readonly_fields = ("id_acceso", "fecha_alta")


@admin.register(Pago)
class PagoAdmin(admin.ModelAdmin):
    list_display = (
        "cliente",
        "periodo_inicio",
        "periodo_fin",
        "fecha_pago",
        "mora",
        "total_pagado",
        "metodo_pago",
    )
    list_filter = ("metodo_pago",)
    search_fields = ("cliente__nombre_completo", "cliente__id_acceso")
    readonly_fields = ("mora", "total_pagado", "fecha_registro")
