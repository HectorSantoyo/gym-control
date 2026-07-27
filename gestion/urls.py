from django.urls import path

from . import views

app_name = "gestion"

urlpatterns = [
    path("", views.inicio, name="inicio"),
    path("clientes/", views.lista_clientes, name="cliente_lista"),
    path("clientes/nuevo/", views.cliente_crear, name="cliente_crear"),
    path("clientes/<int:id_acceso>/", views.cliente_detalle, name="cliente_detalle"),
    path("clientes/<int:id_acceso>/editar/", views.cliente_editar, name="cliente_editar"),
    path("clientes/<int:id_acceso>/estado/", views.cliente_toggle_activo, name="cliente_toggle_activo"),
    path("clientes/<int:id_acceso>/pagos/nuevo/", views.pago_registrar, name="pago_registrar"),
    path("clientes/<int:id_acceso>/pagos/", views.pago_historial, name="pago_historial"),
    path("pagos/<int:pago_id>/editar/", views.pago_editar, name="pago_editar"),
    path("pagos/<int:pago_id>/eliminar/", views.pago_eliminar, name="pago_eliminar"),
]
