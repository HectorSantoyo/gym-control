from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from .models import Cliente, IdAccesoNoDisponibleError


class ClienteIdAccesoTests(TestCase):
    def test_generacion_automatica_al_guardar(self):
        cliente = Cliente.objects.create(nombre_completo="Ana Pérez")
        self.assertIsNotNone(cliente.id_acceso)

    def test_id_generado_esta_dentro_del_rango(self):
        cliente = Cliente.objects.create(nombre_completo="Luis Gómez")
        self.assertGreaterEqual(cliente.id_acceso, 1000)
        self.assertLessEqual(cliente.id_acceso, 9999)

    def test_ids_generados_son_unicos(self):
        ids = {Cliente.objects.create(nombre_completo=f"Cliente {i}").id_acceso for i in range(15)}
        self.assertEqual(len(ids), 15)

    def test_reintenta_si_el_id_generado_ya_esta_ocupado(self):
        Cliente.objects.create(nombre_completo="Primero", id_acceso=1234)
        with patch("gestion.models.random.randint", side_effect=[1234, 4321]):
            nuevo = Cliente.objects.create(nombre_completo="Segundo")
        self.assertEqual(nuevo.id_acceso, 4321)

    def test_id_acceso_no_se_puede_duplicar_en_bd(self):
        Cliente.objects.create(nombre_completo="Primero", id_acceso=1111)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cliente.objects.create(nombre_completo="Segundo", id_acceso=1111)

    def test_respeta_id_asignado_manualmente(self):
        cliente = Cliente.objects.create(nombre_completo="Manual", id_acceso=5000)
        self.assertEqual(cliente.id_acceso, 5000)

    def test_full_clean_rechaza_id_fuera_de_rango(self):
        cliente = Cliente(nombre_completo="Fuera de rango", id_acceso=100)
        with self.assertRaises(ValidationError):
            cliente.full_clean()

    def test_lanza_excepcion_si_se_agotan_los_intentos(self):
        with patch("gestion.models.random.randint", return_value=1234):
            Cliente.objects.create(nombre_completo="Ocupa el hueco", id_acceso=1234)
            with self.assertRaises(IdAccesoNoDisponibleError):
                Cliente.objects.create(nombre_completo="Sin hueco disponible")

    def test_bd_rechaza_id_fuera_de_rango_por_check_constraint(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Cliente.objects.create(nombre_completo="Fuera de rango", id_acceso=100)


class ClienteVistasTests(TestCase):
    def setUp(self):
        self.activo = Cliente.objects.create(nombre_completo="Ana Torres", telefono="5511112222")
        self.inactivo = Cliente.objects.create(nombre_completo="Luis Ramírez", activo=False)

    def test_lista_muestra_clientes(self):
        response = self.client.get(reverse("gestion:cliente_lista"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ana Torres")
        self.assertContains(response, "Luis Ramírez")

    def test_busqueda_por_nombre(self):
        response = self.client.get(reverse("gestion:cliente_lista"), {"q": "Ana"})
        self.assertContains(response, "Ana Torres")
        self.assertNotContains(response, "Luis Ramírez")

    def test_busqueda_por_id_acceso(self):
        response = self.client.get(reverse("gestion:cliente_lista"), {"q": str(self.activo.id_acceso)})
        self.assertContains(response, "Ana Torres")
        self.assertNotContains(response, "Luis Ramírez")

    def test_alta_de_cliente(self):
        response = self.client.post(reverse("gestion:cliente_crear"), {
            "nombre_completo": "Carlos Nuevo",
            "telefono": "",
            "notas": "",
            "activo": "on",
        })
        cliente = Cliente.objects.get(nombre_completo="Carlos Nuevo")
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))

    def test_edicion_de_cliente(self):
        response = self.client.post(
            reverse("gestion:cliente_editar", args=[self.activo.id_acceso]),
            {"nombre_completo": "Ana Torres Editado", "telefono": "5500000000", "notas": "", "activo": "on"},
        )
        self.activo.refresh_from_db()
        self.assertEqual(self.activo.nombre_completo, "Ana Torres Editado")
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[self.activo.id_acceso]))

    def test_activacion_desactivacion(self):
        response = self.client.post(reverse("gestion:cliente_toggle_activo", args=[self.activo.id_acceso]))
        self.activo.refresh_from_db()
        self.assertFalse(self.activo.activo)
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[self.activo.id_acceso]))

    def test_acceso_a_detalle(self):
        response = self.client.get(reverse("gestion:cliente_detalle", args=[self.activo.id_acceso]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ana Torres")
        self.assertContains(response, str(self.activo.id_acceso))
