from django.contrib import admin

from .models import Cliente


@admin.register(Cliente)
class ClienteAdmin(admin.ModelAdmin):
    list_display = ("id_acceso", "nombre_completo", "telefono", "activo", "fecha_alta")
    list_filter = ("activo",)
    search_fields = ("nombre_completo", "id_acceso")
    readonly_fields = ("id_acceso", "fecha_alta")
