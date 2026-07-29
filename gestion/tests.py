from datetime import date, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from . import asistencias, membresia
from .forms import ClienteForm
from .membresia_masiva import calcular_estados_membresia, contar_por_estado
from .models import Asistencia, Cliente, DiaPago, IdAccesoNoDisponibleError, OrigenAsistencia, Pago, TipoTarifa


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
        self.usuario = User.objects.create_user(username="encargado", password="clave-super-12345")
        self.client.force_login(self.usuario)
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
            "tipo_tarifa": TipoTarifa.GENERAL,
            "dia_pago": DiaPago.DIA_1,
            "notas": "",
            "activo": "on",
        })
        cliente = Cliente.objects.get(nombre_completo="Carlos Nuevo")
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))

    def test_edicion_de_cliente(self):
        response = self.client.post(
            reverse("gestion:cliente_editar", args=[self.activo.id_acceso]),
            {
                "nombre_completo": "Ana Torres Editado",
                "telefono": "5500000000",
                "tipo_tarifa": TipoTarifa.GENERAL,
                "dia_pago": DiaPago.DIA_1,
                "notas": "",
                "activo": "on",
            },
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


class MembresiaFuncionesTests(TestCase):
    def test_tarifa_base_general(self):
        self.assertEqual(membresia.tarifa_base("general"), Decimal("330"))

    def test_tarifa_base_estudiante(self):
        self.assertEqual(membresia.tarifa_base("estudiante"), Decimal("280"))

    def test_calcular_periodo_natural_dia_pago_1(self):
        inicio, fin = membresia.calcular_periodo_natural(1, date(2026, 3, 10))
        self.assertEqual(inicio, date(2026, 3, 1))
        self.assertEqual(fin, date(2026, 3, 31))

    def test_calcular_periodo_natural_dia_pago_15(self):
        inicio, fin = membresia.calcular_periodo_natural(15, date(2026, 3, 10))
        self.assertEqual(inicio, date(2026, 3, 15))
        self.assertEqual(fin, date(2026, 4, 14))

    def test_primer_periodo_usa_periodo_natural(self):
        fecha = date(2026, 3, 10)
        self.assertEqual(
            membresia.primer_periodo(1, fecha),
            membresia.calcular_periodo_natural(1, fecha),
        )

    def test_siguiente_periodo_dia_pago_1_no_depende_de_cuando_se_paga(self):
        siguiente = membresia.siguiente_periodo(date(2026, 1, 31), 1)
        self.assertEqual(siguiente, (date(2026, 2, 1), date(2026, 2, 28)))

    def test_siguiente_periodo_dia_pago_15_tras_pago_tardio(self):
        # Último periodo cubierto: 15-mayo a 14-junio. Pagar hasta el 3 de
        # julio no debe adelantar el periodo a 15-julio/14-agosto.
        siguiente = membresia.siguiente_periodo(date(2026, 6, 14), 15)
        self.assertEqual(siguiente, (date(2026, 6, 15), date(2026, 7, 14)))

    def test_periodos_candidatos_dia_pago_1_incluye_anterior_actual_siguiente(self):
        candidatos = membresia.periodos_candidatos(1, date(2026, 7, 22))
        self.assertEqual(
            candidatos,
            [
                (date(2026, 6, 1), date(2026, 6, 30)),
                (date(2026, 7, 1), date(2026, 7, 31)),
                (date(2026, 8, 1), date(2026, 8, 31)),
            ],
        )

    def test_periodos_candidatos_dia_pago_15_incluye_anterior_actual_siguiente(self):
        candidatos = membresia.periodos_candidatos(15, date(2026, 7, 22))
        self.assertEqual(
            candidatos,
            [
                (date(2026, 6, 15), date(2026, 7, 14)),
                (date(2026, 7, 15), date(2026, 8, 14)),
                (date(2026, 8, 15), date(2026, 9, 14)),
            ],
        )

    def test_fecha_vencimiento_es_el_inicio_del_periodo(self):
        self.assertEqual(membresia.fecha_vencimiento(date(2026, 6, 15)), date(2026, 6, 15))

    def test_periodo_gracia_tres_dias_completos(self):
        vencimiento = date(2026, 1, 1)
        for fecha_referencia in (date(2026, 1, 2), date(2026, 1, 3), date(2026, 1, 4)):
            with self.subTest(fecha_referencia=fecha_referencia):
                self.assertEqual(
                    membresia.calcular_estado(vencimiento, fecha_referencia),
                    membresia.ESTADO_EN_GRACIA,
                )
                self.assertEqual(membresia.calcular_mora(vencimiento, fecha_referencia), Decimal("0"))

    def test_mora_inicia_el_dia_correcto(self):
        vencimiento = date(2026, 1, 1)
        self.assertEqual(
            membresia.calcular_estado(vencimiento, date(2026, 1, 5)),
            membresia.ESTADO_VENCIDA_CON_MORA,
        )
        self.assertEqual(membresia.calcular_mora(vencimiento, date(2026, 1, 5)), Decimal("10"))

    def test_mora_acumulada_por_dia(self):
        vencimiento = date(2026, 1, 1)
        self.assertEqual(membresia.calcular_mora(vencimiento, date(2026, 1, 10)), Decimal("60"))

    def test_estados_membresia_limites(self):
        vencimiento = date(2026, 2, 1)
        casos = [
            (date(2026, 1, 20), membresia.ESTADO_VIGENTE),
            (date(2026, 1, 29), membresia.ESTADO_POR_VENCER),
            (date(2026, 2, 1), membresia.ESTADO_POR_VENCER),
            (date(2026, 2, 2), membresia.ESTADO_EN_GRACIA),
            (date(2026, 2, 4), membresia.ESTADO_EN_GRACIA),
            (date(2026, 2, 5), membresia.ESTADO_VENCIDA_CON_MORA),
        ]
        for fecha_referencia, estado_esperado in casos:
            with self.subTest(fecha_referencia=fecha_referencia):
                self.assertEqual(membresia.calcular_estado(vencimiento, fecha_referencia), estado_esperado)

    def test_calcular_total_sugerido_incluye_inscripcion_y_mora(self):
        total = membresia.calcular_total_sugerido("general", True, date(2026, 1, 1), date(2026, 1, 5))
        self.assertEqual(total, Decimal("330") + Decimal("50") + Decimal("10"))

    def test_calcular_total_sugerido_sin_inscripcion_ni_mora(self):
        total = membresia.calcular_total_sugerido("estudiante", False, date(2026, 1, 1), date(2026, 1, 1))
        self.assertEqual(total, Decimal("280"))


class ClienteMembresiaTests(TestCase):
    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def test_sin_pagos_previos_no_se_inventa_un_periodo(self):
        cliente = self._crear_cliente()
        self.assertIsNone(cliente.ultimo_periodo_pagado())
        self.assertIsNone(cliente.siguiente_periodo_pendiente())
        self.assertIsNone(cliente.fecha_vencimiento_pendiente())
        self.assertEqual(cliente.estado_actual(), membresia.ESTADO_SIN_PAGOS)
        self.assertEqual(cliente.mora_actual(), Decimal("0"))
        self.assertIsNone(cliente.total_sugerido())

    def test_pago_tardio_no_modifica_el_periodo_pendiente(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_15)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 5, 15),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 5, 15),
            periodo_fin=date(2026, 6, 14),
        )

        siguiente = cliente.siguiente_periodo_pendiente()
        self.assertEqual(siguiente, (date(2026, 6, 15), date(2026, 7, 14)))

        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 7, 3),
            mensualidad_base=Decimal("330"),
            periodo_inicio=siguiente[0],
            periodo_fin=siguiente[1],
        )
        cliente.refresh_from_db()
        self.assertEqual(cliente.ultimo_periodo_pagado(), (date(2026, 6, 15), date(2026, 7, 14)))

    def test_estado_actual_segun_fecha_de_referencia(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.assertEqual(cliente.fecha_vencimiento_pendiente(), date(2026, 2, 1))
        self.assertEqual(cliente.estado_actual(date(2026, 1, 20)), membresia.ESTADO_VIGENTE)
        self.assertEqual(cliente.estado_actual(date(2026, 2, 2)), membresia.ESTADO_EN_GRACIA)
        self.assertEqual(cliente.estado_actual(date(2026, 2, 5)), membresia.ESTADO_VENCIDA_CON_MORA)

    def test_mora_actual(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.assertEqual(cliente.mora_actual(date(2026, 2, 5)), Decimal("10"))
        self.assertEqual(cliente.mora_actual(date(2026, 2, 10)), Decimal("60"))

    def test_total_sugerido_incluye_inscripcion_si_no_esta_aplicada(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1, inscripcion_aplicada=False)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            inscripcion=Decimal("50"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.assertEqual(cliente.total_sugerido(date(2026, 2, 1)), Decimal("380"))

    def test_total_sugerido_excluye_inscripcion_si_ya_esta_aplicada(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1, inscripcion_aplicada=True)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            inscripcion=Decimal("50"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.assertEqual(cliente.total_sugerido(date(2026, 2, 1)), Decimal("330"))

    def test_total_sugerido_incluye_mora_cuando_aplica(self):
        cliente = self._crear_cliente(
            dia_pago=DiaPago.DIA_1, tipo_tarifa=TipoTarifa.ESTUDIANTE, inscripcion_aplicada=True
        )
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("280"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.assertEqual(cliente.total_sugerido(date(2026, 2, 5)), Decimal("290"))


class PagoModelTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre_completo="Cliente Pago", dia_pago=DiaPago.DIA_1)

    def test_mora_se_calcula_automaticamente_al_guardar(self):
        # Se crea un primer pago previo para que el pago bajo prueba sea el
        # segundo del cliente: la mora normal solo aplica a partir de ahí.
        Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        pago = Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 2, 5),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        self.assertEqual(pago.mora, Decimal("10"))

    def test_primer_pago_no_genera_mora_aunque_se_pague_tarde(self):
        pago = Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 7, 23),
            mensualidad_base=Decimal("330"),
            inscripcion=Decimal("50"),
            reajuste_inicial=Decimal("50"),
            periodo_inicio=date(2026, 6, 15),
            periodo_fin=date(2026, 7, 14),
        )
        self.assertEqual(pago.mora, Decimal("0"))
        self.assertEqual(pago.total_pagado, Decimal("430"))

    def test_segundo_pago_si_aplica_mora_por_retraso(self):
        Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 6, 15),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 6, 15),
            periodo_fin=date(2026, 7, 14),
        )
        segundo_pago = Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 7, 23),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 7, 15),
            periodo_fin=date(2026, 8, 14),
        )
        # vencimiento 2026-07-15; gracia hasta 2026-07-18; 2026-07-23 son 8
        # días de retraso -> 5 días con mora * $10 = $50.
        self.assertEqual(segundo_pago.mora, Decimal("50"))

    def test_total_pagado_se_calcula_automaticamente_al_guardar(self):
        pago = Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            inscripcion=Decimal("50"),
            reajuste_inicial=Decimal("-50"),
            otros_ajustes=Decimal("5"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.assertEqual(pago.total_pagado, Decimal("335"))

    def test_recalculo_al_editar_no_depende_de_la_fecha_actual(self):
        # Igual que arriba: se antepone un primer pago para que este sea el
        # segundo y aplique la lógica normal de mora.
        Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        pago = Pago.objects.create(
            cliente=self.cliente,
            fecha_pago=date(2026, 2, 5),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        self.assertEqual(pago.mora, Decimal("10"))
        pago.notas = "actualizado mucho después"
        pago.save()
        self.assertEqual(pago.mora, Decimal("10"))

    def test_reajuste_inicial_acepta_valores_del_catalogo(self):
        for valor in (Decimal("-50"), Decimal("0"), Decimal("50")):
            with self.subTest(valor=valor):
                pago = Pago(
                    cliente=self.cliente,
                    fecha_pago=date(2026, 1, 1),
                    mensualidad_base=Decimal("330"),
                    reajuste_inicial=valor,
                    periodo_inicio=date(2026, 1, 1),
                    periodo_fin=date(2026, 1, 31),
                )
                pago.full_clean(validate_constraints=False)

    def test_reajuste_inicial_rechaza_valor_fuera_del_catalogo(self):
        pago = Pago(
            cliente=self.cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            reajuste_inicial=Decimal("25"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        with self.assertRaises(ValidationError):
            pago.full_clean(validate_constraints=False)

    def test_bd_rechaza_reajuste_inicial_fuera_del_catalogo(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Pago.objects.create(
                    cliente=self.cliente,
                    fecha_pago=date(2026, 1, 1),
                    mensualidad_base=Decimal("330"),
                    reajuste_inicial=Decimal("25"),
                    periodo_inicio=date(2026, 1, 1),
                    periodo_fin=date(2026, 1, 31),
                )

    def test_bd_rechaza_inscripcion_fuera_del_catalogo(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Pago.objects.create(
                    cliente=self.cliente,
                    fecha_pago=date(2026, 1, 1),
                    mensualidad_base=Decimal("330"),
                    inscripcion=Decimal("500"),
                    periodo_inicio=date(2026, 1, 1),
                    periodo_fin=date(2026, 1, 31),
                )

    def test_bd_rechaza_periodo_fin_anterior_a_periodo_inicio(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Pago.objects.create(
                    cliente=self.cliente,
                    fecha_pago=date(2026, 1, 1),
                    mensualidad_base=Decimal("330"),
                    periodo_inicio=date(2026, 1, 31),
                    periodo_fin=date(2026, 1, 1),
                )

    def test_bd_rechaza_total_pagado_inconsistente_con_los_conceptos(self):
        pago = Pago(
            cliente=self.cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            mora=Decimal("0"),
            total_pagado=Decimal("999"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Pago.objects.bulk_create([pago])


class PagoFlujoTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="encargado", password="clave-super-12345")
        self.client.force_login(self.usuario)

    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _datos_pago(self, **overrides):
        datos = {
            "fecha_pago": "2026-01-01",
            "reajuste_inicial": "0",
            "otros_ajustes": "0",
            "metodo_pago": "efectivo",
            "notas": "",
        }
        datos.update(overrides)
        return datos

    def test_cliente_form_incluye_tarifa_y_dia_sin_exponer_banderas(self):
        campos = set(ClienteForm.Meta.fields)
        self.assertIn("tipo_tarifa", campos)
        self.assertIn("dia_pago", campos)
        self.assertNotIn("inscripcion_aplicada", campos)
        self.assertNotIn("reajuste_inicial_aplicado", campos)

    def test_registrar_primer_pago_requiere_periodo_seleccionado(self):
        cliente = self._crear_cliente()
        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("periodo_seleccionado"))
        self.assertEqual(Pago.objects.count(), 0)

    @patch("gestion.views.timezone.localdate")
    def test_formulario_primer_pago_no_preselecciona_periodo(self, mock_localdate):
        mock_localdate.return_value = date(2026, 7, 22)
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_15)
        response = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        form = response.context["form"]
        self.assertIsNone(form["periodo_seleccionado"].value())
        self.assertEqual(
            [valor for valor, _ in form.fields["periodo_seleccionado"].choices],
            ["2026-06-15", "2026-07-15", "2026-08-15"],
        )

    @patch("gestion.views.timezone.localdate")
    def test_seleccion_primer_periodo_dia_pago_1(self, mock_localdate):
        mock_localdate.return_value = date(2026, 7, 15)
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(periodo_seleccionado="2026-07-01", fecha_pago="2026-07-01"),
        )
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        pago = Pago.objects.get(cliente=cliente)
        self.assertEqual(pago.periodo_inicio, date(2026, 7, 1))
        self.assertEqual(pago.periodo_fin, date(2026, 7, 31))

    @patch("gestion.views.timezone.localdate")
    def test_seleccion_primer_periodo_dia_pago_15(self, mock_localdate):
        mock_localdate.return_value = date(2026, 7, 22)
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_15)
        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(periodo_seleccionado="2026-07-15", fecha_pago="2026-07-15"),
        )
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        pago = Pago.objects.get(cliente=cliente)
        self.assertEqual(pago.periodo_inicio, date(2026, 7, 15))
        self.assertEqual(pago.periodo_fin, date(2026, 8, 14))

    @patch("gestion.views.membresia.primer_periodo", wraps=membresia.primer_periodo)
    @patch("gestion.views.timezone.localdate")
    def test_primer_pago_usa_membresia_primer_periodo(self, mock_localdate, mock_primer_periodo):
        # calcular_periodo_natural() y primer_periodo() producen hoy el mismo
        # resultado, así que una prueba que solo verifique fechas no detectaría
        # si la vista volviera a saltarse el wrapper. Este mock protege
        # específicamente esa decisión de diseño (ver membresia.py).
        mock_localdate.return_value = date(2026, 7, 15)
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(periodo_seleccionado="2026-07-01", fecha_pago="2026-07-01"),
        )
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        mock_primer_periodo.assert_called_once_with(cliente.dia_pago, date(2026, 7, 1))
        pago = Pago.objects.get(cliente=cliente)
        self.assertEqual(pago.periodo_inicio, date(2026, 7, 1))
        self.assertEqual(pago.periodo_fin, date(2026, 7, 31))

    @patch("gestion.views.timezone.localdate")
    def test_seleccion_periodo_anterior_permite_pago_historico(self, mock_localdate):
        mock_localdate.return_value = date(2026, 3, 10)
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(periodo_seleccionado="2026-02-01", fecha_pago="2026-02-01"),
        )
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        pago = Pago.objects.get(cliente=cliente)
        self.assertEqual(pago.periodo_inicio, date(2026, 2, 1))
        self.assertEqual(pago.periodo_fin, date(2026, 2, 28))
        self.assertEqual(pago.fecha_pago, date(2026, 2, 1))

    def test_pago_posterior_usa_periodo_automatico(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        siguiente_esperado = cliente.siguiente_periodo_pendiente()

        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(fecha_pago="2026-02-01"),
        )
        self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        segundo_pago = Pago.objects.exclude(periodo_inicio=date(2026, 1, 1)).get(cliente=cliente)
        self.assertEqual((segundo_pago.periodo_inicio, segundo_pago.periodo_fin), siguiente_esperado)

    def test_mora_en_pago_tardio(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(fecha_pago="2026-02-05"),
        )
        segundo_pago = Pago.objects.exclude(periodo_inicio=date(2026, 1, 1)).get(cliente=cliente)
        self.assertEqual(segundo_pago.mora, Decimal("10"))

    def test_mensualidad_base_no_es_editable_manualmente(self):
        cliente = self._crear_cliente(tipo_tarifa=TipoTarifa.GENERAL, dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(fecha_pago="2026-02-01", mensualidad_base="1"),
        )
        segundo_pago = Pago.objects.exclude(periodo_inicio=date(2026, 1, 1)).get(cliente=cliente)
        self.assertEqual(segundo_pago.mensualidad_base, Decimal("330"))

    @patch("gestion.views.timezone.localdate")
    def test_inscripcion_solo_se_ofrece_cuando_no_se_ha_aplicado(self, mock_localdate):
        mock_localdate.return_value = date(2026, 1, 1)
        cliente = self._crear_cliente()
        respuesta_inicial = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        self.assertIn("cobrar_inscripcion", respuesta_inicial.context["form"].fields)

        self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(periodo_seleccionado="2026-01-01", cobrar_inscripcion="on"),
        )
        cliente.refresh_from_db()
        self.assertTrue(cliente.inscripcion_aplicada)

        respuesta_siguiente = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        self.assertNotIn("cobrar_inscripcion", respuesta_siguiente.context["form"].fields)

    @patch("gestion.views.timezone.localdate")
    def test_reajuste_inicial_solo_se_ofrece_en_el_primer_pago(self, mock_localdate):
        mock_localdate.return_value = date(2026, 1, 1)
        cliente = self._crear_cliente()
        respuesta_inicial = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        self.assertIn("reajuste_inicial", respuesta_inicial.context["form"].fields)

        self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(periodo_seleccionado="2026-01-01", reajuste_inicial="0"),
        )
        cliente.refresh_from_db()
        # reajuste_inicial_aplicado es True aunque el valor elegido haya sido 0:
        # significa "ya se resolvió", no "fue distinto de cero".
        self.assertTrue(cliente.reajuste_inicial_aplicado)

        respuesta_siguiente = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        self.assertNotIn("reajuste_inicial", respuesta_siguiente.context["form"].fields)

    def test_reajuste_inicial_opciones_derivan_de_membresia_reajustes_validos(self):
        cliente = self._crear_cliente()
        response = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        opciones = response.context["form"].fields["reajuste_inicial"].widget.choices

        # Comportamiento actual: mismo orden, cuantización a centavos y
        # etiquetas que antes de derivar las opciones desde membresia.py.
        self.assertEqual(
            opciones,
            [
                (Decimal("-50.00"), "-$50"),
                (Decimal("0.00"), "Sin reajuste"),
                (Decimal("50.00"), "+$50"),
            ],
        )
        # Fuente única: los valores deben coincidir exactamente con
        # membresia.REAJUSTES_VALIDOS (cuantizados), no con literales propios.
        self.assertEqual(
            [valor for valor, _ in opciones],
            [valor.quantize(Decimal("0.01")) for valor in membresia.REAJUSTES_VALIDOS],
        )

    def test_reajuste_inicial_valor_inicial_es_sin_reajuste(self):
        cliente = self._crear_cliente()
        response = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        campo = response.context["form"]["reajuste_inicial"]
        self.assertEqual(campo.value(), Decimal("0.00"))

        seleccionadas = [opcion.choice_label for opcion in campo if opcion.data["selected"]]
        self.assertEqual(seleccionadas, ["Sin reajuste"])

    @patch("gestion.views.timezone.localdate")
    def test_reajuste_inicial_acepta_los_tres_valores_permitidos(self, mock_localdate):
        mock_localdate.return_value = date(2026, 1, 15)
        casos = [("-50", Decimal("280")), ("0", Decimal("330")), ("50", Decimal("380"))]
        for valor, total_esperado in casos:
            with self.subTest(valor=valor):
                cliente = self._crear_cliente(nombre_completo=f"Cliente reajuste {valor}")
                response = self.client.post(
                    reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
                    self._datos_pago(periodo_seleccionado="2026-01-01", reajuste_inicial=valor),
                )
                self.assertRedirects(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
                pago = Pago.objects.get(cliente=cliente)
                self.assertEqual(pago.reajuste_inicial, Decimal(valor).quantize(Decimal("0.01")))
                self.assertEqual(pago.total_pagado, total_esperado)

    @patch("gestion.views.timezone.localdate")
    def test_reajuste_inicial_rechaza_valor_fuera_del_catalogo(self, mock_localdate):
        mock_localdate.return_value = date(2026, 1, 15)
        cliente = self._crear_cliente()
        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(periodo_seleccionado="2026-01-01", reajuste_inicial="25"),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("reajuste_inicial"))
        self.assertEqual(Pago.objects.count(), 0)

    @patch("gestion.views.timezone.localdate")
    def test_fecha_pago_por_defecto_es_hoy(self, mock_localdate):
        mock_localdate.return_value = date(2026, 3, 10)
        cliente = self._crear_cliente()
        response = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        self.assertEqual(response.context["form"].initial["fecha_pago"], date(2026, 3, 10))

    def test_metodo_pago_otro_requiere_especificar_detalle(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        response = self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(fecha_pago="2026-02-01", metodo_pago="otro", metodo_pago_otro=""),
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors.get("metodo_pago_otro"))
        self.assertEqual(Pago.objects.filter(cliente=cliente).count(), 1)

    def test_metodo_pago_otro_guarda_el_detalle_especificado(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(fecha_pago="2026-02-01", metodo_pago="otro", metodo_pago_otro="Depósito bancario"),
        )
        segundo_pago = Pago.objects.exclude(periodo_inicio=date(2026, 1, 1)).get(cliente=cliente)
        self.assertEqual(segundo_pago.metodo_pago, "Depósito bancario")

    @patch("gestion.views.timezone.localdate")
    def test_total_pagado_se_calcula_en_servidor(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 10)
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1, tipo_tarifa=TipoTarifa.ESTUDIANTE)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("280"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        self.client.post(
            reverse("gestion:pago_registrar", args=[cliente.id_acceso]),
            self._datos_pago(fecha_pago="2026-02-10", otros_ajustes="15"),
        )
        segundo_pago = Pago.objects.exclude(periodo_inicio=date(2026, 1, 1)).get(cliente=cliente)
        # vencimiento 2026-02-01; gracia hasta el 2026-02-04; 2026-02-10 son
        # 9 días de retraso -> 6 días con mora * $10 = $60.
        self.assertEqual(segundo_pago.mora, Decimal("60"))
        self.assertEqual(
            segundo_pago.total_pagado,
            segundo_pago.mensualidad_base
            + segundo_pago.inscripcion
            + segundo_pago.reajuste_inicial
            + segundo_pago.mora
            + segundo_pago.otros_ajustes,
        )
        self.assertEqual(segundo_pago.total_pagado, Decimal("355"))

    def test_historial_muestra_pagos(self):
        cliente = self._crear_cliente()
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 2, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        response = self.client.get(reverse("gestion:pago_historial", args=[cliente.id_acceso]))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "01/01/2026")
        self.assertContains(response, "01/02/2026")

    def test_editar_pago_mas_reciente(self):
        cliente = self._crear_cliente()
        pago = Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        response = self.client.post(
            reverse("gestion:pago_editar", args=[pago.pk]),
            self._datos_pago(fecha_pago="2026-01-10"),
        )
        self.assertRedirects(response, reverse("gestion:pago_historial", args=[cliente.id_acceso]))
        pago.refresh_from_db()
        self.assertEqual(pago.fecha_pago, date(2026, 1, 10))
        # Es el único pago del cliente (el primero): no debe generar mora
        # aunque la fecha de pago quede lejos del inicio del periodo.
        self.assertEqual(pago.mora, Decimal("0"))

    def test_editar_segundo_pago_mas_reciente_recalcula_mora(self):
        cliente = self._crear_cliente()
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        segundo_pago = Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 2, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        response = self.client.post(
            reverse("gestion:pago_editar", args=[segundo_pago.pk]),
            self._datos_pago(fecha_pago="2026-02-10"),
        )
        self.assertRedirects(response, reverse("gestion:pago_historial", args=[cliente.id_acceso]))
        segundo_pago.refresh_from_db()
        self.assertEqual(segundo_pago.fecha_pago, date(2026, 2, 10))
        self.assertEqual(segundo_pago.mora, Decimal("60"))

    def test_editar_pago_bloqueado_si_hay_pagos_posteriores(self):
        cliente = self._crear_cliente()
        primero = Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 2, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        response = self.client.post(
            reverse("gestion:pago_editar", args=[primero.pk]),
            self._datos_pago(fecha_pago="2026-01-15"),
        )
        self.assertRedirects(response, reverse("gestion:pago_historial", args=[cliente.id_acceso]))
        primero.refresh_from_db()
        self.assertEqual(primero.fecha_pago, date(2026, 1, 1))

    def test_eliminar_pago_mas_reciente_resetea_banderas(self):
        cliente = self._crear_cliente()
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            inscripcion=Decimal("50"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        cliente.recalcular_banderas_pago()
        self.assertTrue(cliente.inscripcion_aplicada)
        self.assertTrue(cliente.reajuste_inicial_aplicado)

        pago = cliente.ultimo_pago()
        response = self.client.post(reverse("gestion:pago_eliminar", args=[pago.pk]))
        self.assertRedirects(response, reverse("gestion:pago_historial", args=[cliente.id_acceso]))

        cliente.refresh_from_db()
        self.assertEqual(Pago.objects.filter(cliente=cliente).count(), 0)
        self.assertFalse(cliente.inscripcion_aplicada)
        self.assertFalse(cliente.reajuste_inicial_aplicado)

    def test_eliminar_pago_bloqueado_si_hay_pagos_posteriores(self):
        cliente = self._crear_cliente()
        primero = Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 2, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        response = self.client.post(reverse("gestion:pago_eliminar", args=[primero.pk]))
        self.assertRedirects(response, reverse("gestion:pago_historial", args=[cliente.id_acceso]))
        self.assertEqual(Pago.objects.filter(cliente=cliente).count(), 2)

    def test_tiene_pagos_posteriores(self):
        cliente = self._crear_cliente()
        primero = Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        segundo = Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 2, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        self.assertTrue(primero.tiene_pagos_posteriores())
        self.assertFalse(segundo.tiene_pagos_posteriores())

    def test_detalle_muestra_badge_segun_estado(self):
        sin_pagos = self._crear_cliente(nombre_completo="Sin Pagos")
        response = self.client.get(reverse("gestion:cliente_detalle", args=[sin_pagos.id_acceso]))
        self.assertContains(response, "badge-sin-pagos")

        vigente = self._crear_cliente(nombre_completo="Vigente")
        Pago.objects.create(
            cliente=vigente,
            fecha_pago=date(2099, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2099, 1, 1),
            periodo_fin=date(2099, 1, 31),
        )
        response = self.client.get(reverse("gestion:cliente_detalle", args=[vigente.id_acceso]))
        self.assertContains(response, "badge-vigente")

    @patch("gestion.views.timezone.localdate")
    def test_detalle_usa_etiqueta_larga_en_periodo_de_gracia(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 3)
        cliente = self._crear_cliente(nombre_completo="En Gracia", dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        response = self.client.get(reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        self.assertContains(response, "En periodo de gracia")


class AsistenciaModeloTests(TestCase):
    def setUp(self):
        self.cliente = Cliente.objects.create(nombre_completo="Cliente de prueba")

    def test_bd_rechaza_mora_negativa(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asistencia.objects.create(
                    cliente=self.cliente,
                    estado_membresia=membresia.ESTADO_SIN_PAGOS,
                    mora_al_ingresar=Decimal("-1"),
                )

    def test_bd_rechaza_estado_membresia_invalido(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asistencia.objects.create(
                    cliente=self.cliente,
                    estado_membresia="no_existe",
                )

    def test_bd_rechaza_origen_invalido(self):
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Asistencia.objects.create(
                    cliente=self.cliente,
                    estado_membresia=membresia.ESTADO_SIN_PAGOS,
                    origen="otro",
                )

    def test_origen_por_defecto_es_cliente(self):
        asistencia = Asistencia.objects.create(
            cliente=self.cliente,
            estado_membresia=membresia.ESTADO_SIN_PAGOS,
        )
        self.assertEqual(asistencia.origen, OrigenAsistencia.CLIENTE)

    def test_ordering_mas_reciente_primero(self):
        primera = Asistencia.objects.create(
            cliente=self.cliente,
            fecha_hora=timezone.make_aware(datetime(2026, 1, 1, 8, 0)),
            estado_membresia=membresia.ESTADO_SIN_PAGOS,
        )
        segunda = Asistencia.objects.create(
            cliente=self.cliente,
            fecha_hora=timezone.make_aware(datetime(2026, 1, 1, 9, 0)),
            estado_membresia=membresia.ESTADO_SIN_PAGOS,
        )
        self.assertEqual(list(self.cliente.asistencias.all()), [segunda, primera])
        self.assertEqual(self.cliente.ultima_asistencia(), segunda)


class AsistenciaServicioTests(TestCase):
    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _ahora(self, *args):
        return timezone.make_aware(datetime(*args))

    def test_registro_exitoso(self):
        cliente = self._crear_cliente()
        ahora = self._ahora(2026, 1, 10, 8, 0)
        resultado = asistencias.procesar_checkin(cliente.id_acceso, ahora=ahora)
        self.assertTrue(resultado.ok)
        self.assertEqual(resultado.motivo, asistencias.MOTIVO_CREADA)
        self.assertEqual(resultado.asistencia.cliente, cliente)
        self.assertEqual(resultado.asistencia.fecha_hora, ahora)
        self.assertEqual(cliente.asistencias.count(), 1)

    def test_cliente_inexistente(self):
        resultado = asistencias.procesar_checkin(9999)
        self.assertFalse(resultado.ok)
        self.assertEqual(resultado.motivo, asistencias.MOTIVO_CLIENTE_NO_ENCONTRADO)
        self.assertIsNone(resultado.asistencia)

    def test_cliente_inactivo(self):
        cliente = self._crear_cliente(activo=False)
        resultado = asistencias.procesar_checkin(cliente.id_acceso)
        self.assertFalse(resultado.ok)
        self.assertEqual(resultado.motivo, asistencias.MOTIVO_CLIENTE_INACTIVO)
        self.assertEqual(cliente.asistencias.count(), 0)

    def test_duplicado_antes_de_cinco_minutos(self):
        cliente = self._crear_cliente()
        primero = self._ahora(2026, 1, 10, 8, 0)
        asistencias.procesar_checkin(cliente.id_acceso, ahora=primero)
        segundo = primero + timedelta(minutes=4, seconds=59)
        resultado = asistencias.procesar_checkin(cliente.id_acceso, ahora=segundo)
        self.assertFalse(resultado.ok)
        self.assertEqual(resultado.motivo, asistencias.MOTIVO_DUPLICADO)
        self.assertEqual(cliente.asistencias.count(), 1)

    def test_permitido_exactamente_a_los_cinco_minutos(self):
        cliente = self._crear_cliente()
        primero = self._ahora(2026, 1, 10, 8, 0)
        asistencias.procesar_checkin(cliente.id_acceso, ahora=primero)
        segundo = primero + timedelta(minutes=5)
        resultado = asistencias.procesar_checkin(cliente.id_acceso, ahora=segundo)
        self.assertTrue(resultado.ok)
        self.assertEqual(cliente.asistencias.count(), 2)

    def test_permitido_despues_de_cinco_minutos(self):
        cliente = self._crear_cliente()
        primero = self._ahora(2026, 1, 10, 8, 0)
        asistencias.procesar_checkin(cliente.id_acceso, ahora=primero)
        segundo = primero + timedelta(minutes=6)
        resultado = asistencias.procesar_checkin(cliente.id_acceso, ahora=segundo)
        self.assertTrue(resultado.ok)
        self.assertEqual(cliente.asistencias.count(), 2)

    def test_snapshot_historico_no_cambia_tras_un_pago_posterior(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        ahora = self._ahora(2026, 2, 10, 8, 0)
        resultado = asistencias.procesar_checkin(cliente.id_acceso, ahora=ahora)
        asistencia = resultado.asistencia
        self.assertEqual(asistencia.estado_membresia, membresia.ESTADO_VENCIDA_CON_MORA)
        self.assertEqual(asistencia.fecha_vencimiento, date(2026, 2, 1))
        self.assertGreater(asistencia.mora_al_ingresar, Decimal("0"))

        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 2, 10),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 2, 1),
            periodo_fin=date(2026, 2, 28),
        )
        asistencia.refresh_from_db()
        self.assertEqual(asistencia.estado_membresia, membresia.ESTADO_VENCIDA_CON_MORA)
        self.assertEqual(asistencia.fecha_vencimiento, date(2026, 2, 1))
        self.assertGreater(asistencia.mora_al_ingresar, Decimal("0"))

    def test_origen_cliente_y_manual_se_guardan_correctamente(self):
        cliente = self._crear_cliente()
        resultado_cliente = asistencias.procesar_checkin(
            cliente.id_acceso, origen=OrigenAsistencia.CLIENTE, ahora=self._ahora(2026, 1, 10, 8, 0)
        )
        resultado_manual = asistencias.procesar_checkin(
            cliente.id_acceso, origen=OrigenAsistencia.MANUAL, ahora=self._ahora(2026, 1, 10, 8, 10)
        )
        self.assertEqual(resultado_cliente.asistencia.origen, OrigenAsistencia.CLIENTE)
        self.assertEqual(resultado_manual.asistencia.origen, OrigenAsistencia.MANUAL)

    def test_capturar_estado_membresia_usa_fecha_referencia_coherente(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        ahora = self._ahora(2026, 2, 5, 8, 0)
        snapshot = asistencias.capturar_estado_membresia(cliente, ahora)
        fecha_referencia = timezone.localtime(ahora).date()
        self.assertEqual(snapshot.estado_membresia, cliente.estado_actual(fecha_referencia))
        self.assertEqual(snapshot.mora, cliente.mora_actual(fecha_referencia))
        self.assertEqual(snapshot.fecha_vencimiento, cliente.fecha_vencimiento_pendiente())

    def test_fecha_vencimiento_pendiente_no_depende_de_la_fecha_actual(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=date(2026, 1, 1),
            mensualidad_base=Decimal("330"),
            periodo_inicio=date(2026, 1, 1),
            periodo_fin=date(2026, 1, 31),
        )
        vencimiento_original = cliente.fecha_vencimiento_pendiente()
        with patch("django.utils.timezone.localdate", return_value=date(2030, 1, 1)):
            self.assertEqual(cliente.fecha_vencimiento_pendiente(), vencimiento_original)


class CheckinVistaTests(TestCase):
    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def test_get_formulario(self):
        response = self.client.get(reverse("gestion:checkin"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registrar entrada")

    def test_post_exitoso_registra_asistencia(self):
        cliente = self._crear_cliente()
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": str(cliente.id_acceso)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, cliente.nombre_completo)
        self.assertEqual(Asistencia.objects.count(), 1)
        asistencia = Asistencia.objects.first()
        self.assertEqual(asistencia.cliente, cliente)
        self.assertEqual(asistencia.estado_membresia, membresia.ESTADO_SIN_PAGOS)

    def test_id_inexistente_no_registra(self):
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": "9999"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ID no encontrado")
        self.assertEqual(Asistencia.objects.count(), 0)

    def test_cliente_inactivo_no_registra(self):
        cliente = self._crear_cliente(activo=False)
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": str(cliente.id_acceso)})
        self.assertContains(response, "Tu cuenta no está activa")
        self.assertEqual(Asistencia.objects.count(), 0)

    def test_duplicado_no_registra_segunda_vez(self):
        cliente = self._crear_cliente()
        url = reverse("gestion:checkin")
        self.client.post(url, {"id_acceso": str(cliente.id_acceso)})
        response = self.client.post(url, {"id_acceso": str(cliente.id_acceso)})
        self.assertContains(response, "Ya registraste tu entrada")
        self.assertEqual(Asistencia.objects.count(), 1)

    def test_cliente_vencido_si_registra(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        hoy = timezone.localdate()
        periodo_fin = hoy - timedelta(days=60)
        periodo_inicio = periodo_fin - timedelta(days=29)
        Pago.objects.create(
            cliente=cliente,
            fecha_pago=periodo_inicio,
            mensualidad_base=Decimal("330"),
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
        )
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": str(cliente.id_acceso)})
        self.assertEqual(Asistencia.objects.count(), 1)
        asistencia = Asistencia.objects.first()
        self.assertEqual(asistencia.estado_membresia, membresia.ESTADO_VENCIDA_CON_MORA)
        self.assertContains(response, "vencida")

    def test_id_no_numerico_no_registra(self):
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": "abcd"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Ingresa un ID de 4 dígitos.")
        self.assertEqual(Asistencia.objects.count(), 0)

    def test_id_con_menos_de_4_digitos_no_registra(self):
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": "12"})
        self.assertContains(response, "Ingresa un ID de 4 dígitos.")
        self.assertEqual(Asistencia.objects.count(), 0)

    def test_id_con_mas_de_4_digitos_no_registra(self):
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": "123456"})
        self.assertContains(response, "Ingresa un ID de 4 dígitos.")
        self.assertEqual(Asistencia.objects.count(), 0)

    def test_origen_guardado_como_cliente(self):
        cliente = self._crear_cliente()
        self.client.post(reverse("gestion:checkin"), {"id_acceso": str(cliente.id_acceso)})
        asistencia = Asistencia.objects.first()
        self.assertEqual(asistencia.origen, OrigenAsistencia.CLIENTE)


class DispositivoRecordadoTests(TestCase):
    COOKIE = "gym_cliente_recordado"

    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _checkin_con_recordarme(self, cliente, recordarme=True):
        data = {"id_acceso": str(cliente.id_acceso)}
        if recordarme:
            data["recordarme"] = "on"
        return self.client.post(reverse("gestion:checkin"), data)

    def test_checkbox_recordarme_visible_en_formulario(self):
        response = self.client.get(reverse("gestion:checkin"))
        self.assertContains(response, "Recordarme en este dispositivo")

    def test_cookie_creada_cuando_se_marca_recordarme(self):
        cliente = self._crear_cliente()
        response = self._checkin_con_recordarme(cliente, recordarme=True)
        self.assertIn(self.COOKIE, response.cookies)

    def test_cookie_no_creada_si_no_se_marca_recordarme(self):
        cliente = self._crear_cliente()
        response = self._checkin_con_recordarme(cliente, recordarme=False)
        self.assertNotIn(self.COOKIE, response.cookies)

    def test_cookie_no_creada_para_id_inexistente(self):
        response = self.client.post(
            reverse("gestion:checkin"), {"id_acceso": "9999", "recordarme": "on"}
        )
        self.assertNotIn(self.COOKIE, response.cookies)

    def test_cookie_no_creada_para_cliente_inactivo(self):
        cliente = self._crear_cliente(activo=False)
        response = self._checkin_con_recordarme(cliente, recordarme=True)
        self.assertNotIn(self.COOKIE, response.cookies)

    def test_get_posterior_reconoce_cliente_recordado(self):
        cliente = self._crear_cliente()
        self._checkin_con_recordarme(cliente, recordarme=True)
        response = self.client.get(reverse("gestion:checkin"))
        self.assertContains(response, cliente.nombre_completo)
        self.assertContains(response, "Registrar mi entrada")
        self.assertContains(response, "No soy esta persona")

    def test_get_no_registra_asistencia_automaticamente(self):
        cliente = self._crear_cliente()
        self._checkin_con_recordarme(cliente, recordarme=True)
        self.assertEqual(Asistencia.objects.count(), 1)
        self.client.get(reverse("gestion:checkin"))
        self.assertEqual(Asistencia.objects.count(), 1)

    def test_post_confirmar_registra_entrada(self):
        cliente = self._crear_cliente()
        ahora = timezone.now()
        with patch("django.utils.timezone.now", return_value=ahora - timedelta(minutes=10)):
            self._checkin_con_recordarme(cliente, recordarme=True)

        response = self.client.post(reverse("gestion:checkin_confirmar"))
        self.assertEqual(Asistencia.objects.count(), 2)
        self.assertContains(response, cliente.nombre_completo)

    def test_confirmar_dentro_de_cinco_minutos_es_duplicado(self):
        cliente = self._crear_cliente()
        self._checkin_con_recordarme(cliente, recordarme=True)

        response = self.client.post(reverse("gestion:checkin_confirmar"))
        self.assertEqual(Asistencia.objects.count(), 1)
        self.assertContains(response, "Ya registraste tu entrada")

    def test_olvidar_elimina_cookie_y_redirige(self):
        cliente = self._crear_cliente()
        self._checkin_con_recordarme(cliente, recordarme=True)

        response = self.client.post(reverse("gestion:checkin_olvidar"))
        self.assertRedirects(response, reverse("gestion:checkin"))
        self.assertEqual(response.cookies[self.COOKIE]["max-age"], 0)

        response = self.client.get(reverse("gestion:checkin"))
        self.assertNotContains(response, cliente.nombre_completo)
        self.assertContains(response, "Registra tu entrada")

    def test_cookie_manipulada_se_ignora_y_elimina(self):
        self.client.cookies[self.COOKIE] = "valor-invalido"
        response = self.client.get(reverse("gestion:checkin"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registra tu entrada")
        self.assertEqual(response.cookies[self.COOKIE]["max-age"], 0)

    def test_cookie_para_cliente_eliminado_se_ignora(self):
        cliente = self._crear_cliente()
        self._checkin_con_recordarme(cliente, recordarme=True)
        Asistencia.objects.filter(cliente=cliente).delete()
        cliente.delete()

        response = self.client.get(reverse("gestion:checkin"))
        self.assertContains(response, "Registra tu entrada")
        self.assertEqual(response.cookies[self.COOKIE]["max-age"], 0)

    def test_cliente_inactivo_despues_de_recordar_se_ignora(self):
        cliente = self._crear_cliente()
        self._checkin_con_recordarme(cliente, recordarme=True)
        cliente.activo = False
        cliente.save()

        response = self.client.get(reverse("gestion:checkin"))
        self.assertContains(response, "Registra tu entrada")
        self.assertNotContains(response, cliente.nombre_completo)
        self.assertEqual(response.cookies[self.COOKIE]["max-age"], 0)

    def test_confirmar_con_cookie_invalida_redirige_y_elimina_cookie(self):
        self.client.cookies[self.COOKIE] = "valor-invalido"
        response = self.client.post(reverse("gestion:checkin_confirmar"))
        self.assertRedirects(response, reverse("gestion:checkin"))
        self.assertEqual(response.cookies[self.COOKIE]["max-age"], 0)
        self.assertEqual(Asistencia.objects.count(), 0)

    def test_atributos_cookie_recordarme(self):
        cliente = self._crear_cliente()
        response = self._checkin_con_recordarme(cliente, recordarme=True)
        cookie = response.cookies[self.COOKIE]
        self.assertEqual(cookie["max-age"], 60 * 60 * 24 * 180)
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")

    @override_settings(DEBUG=True)
    def test_cookie_no_segura_en_debug(self):
        cliente = self._crear_cliente()
        response = self._checkin_con_recordarme(cliente, recordarme=True)
        cookie = response.cookies[self.COOKIE]
        self.assertFalse(cookie["secure"])

    @override_settings(DEBUG=False)
    def test_cookie_segura_fuera_de_debug(self):
        cliente = self._crear_cliente()
        response = self._checkin_con_recordarme(cliente, recordarme=True)
        cookie = response.cookies[self.COOKIE]
        self.assertTrue(cookie["secure"])


class AutenticacionTests(TestCase):
    CREDENCIALES = {"username": "encargado", "password": "clave-super-12345"}

    def _crear_usuario(self):
        return User.objects.create_user(**self.CREDENCIALES)

    def test_get_login(self):
        response = self.client.get(reverse("login"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Iniciar sesión")

    def test_login_credenciales_validas(self):
        self._crear_usuario()
        response = self.client.post(reverse("login"), self.CREDENCIALES)
        self.assertRedirects(response, reverse("gestion:inicio"))

    def test_login_credenciales_invalidas(self):
        self._crear_usuario()
        response = self.client.post(
            reverse("login"), {"username": "encargado", "password": "incorrecta"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)

    def test_login_respeta_next(self):
        self._crear_usuario()
        destino = reverse("gestion:cliente_lista")
        response = self.client.post(reverse("login"), {**self.CREDENCIALES, "next": destino})
        self.assertRedirects(response, destino)

    def test_logout_por_post(self):
        usuario = self._crear_usuario()
        self.client.force_login(usuario)
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("login"))

        response = self.client.get(reverse("gestion:inicio"))
        self.assertEqual(response.status_code, 302)

    def test_logout_rechaza_get(self):
        usuario = self._crear_usuario()
        self.client.force_login(usuario)
        response = self.client.get(reverse("logout"))
        self.assertEqual(response.status_code, 405)

    def test_pantalla_interna_redirige_sin_autenticacion(self):
        response = self.client.get(reverse("gestion:cliente_lista"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))

    def test_pantalla_interna_funciona_autenticado(self):
        usuario = self._crear_usuario()
        self.client.force_login(usuario)
        response = self.client.get(reverse("gestion:cliente_lista"))
        self.assertEqual(response.status_code, 200)

    def test_checkin_sigue_publico(self):
        response = self.client.get(reverse("gestion:checkin"))
        self.assertEqual(response.status_code, 200)

    def test_navegacion_cambia_segun_autenticacion(self):
        response = self.client.get(reverse("gestion:checkin"))
        self.assertNotContains(response, "Cerrar sesión")
        self.assertNotContains(response, reverse("gestion:cliente_lista"))
        self.assertNotContains(response, reverse("gestion:membresia_lista"))
        self.assertNotContains(response, reverse("gestion:asistencia_lista"))

        usuario = self._crear_usuario()
        self.client.force_login(usuario)
        response = self.client.get(reverse("gestion:inicio"))
        self.assertContains(response, "Power Center Gym")
        self.assertContains(response, "Cerrar sesión")
        self.assertContains(response, "encargado")
        self.assertContains(response, reverse("gestion:cliente_lista"))
        self.assertContains(response, reverse("gestion:membresia_lista"))
        self.assertContains(response, reverse("gestion:asistencia_lista"))
        self.assertContains(response, reverse("gestion:checkin"))


class AsistenciaListaVistaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="encargado", password="clave-super-12345")
        self.client.force_login(self.usuario)

    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def test_requiere_login_redirige_sin_sesion(self):
        self.client.logout()
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse("login")))

    def test_acceso_autenticado(self):
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertEqual(response.status_code, 200)

    def test_orden_descendente(self):
        cliente = self._crear_cliente()
        primera = Asistencia.objects.create(
            cliente=cliente,
            fecha_hora=timezone.make_aware(datetime(2026, 1, 1, 8, 0)),
            estado_membresia=membresia.ESTADO_SIN_PAGOS,
        )
        segunda = Asistencia.objects.create(
            cliente=cliente,
            fecha_hora=timezone.make_aware(datetime(2026, 1, 1, 9, 0)),
            estado_membresia=membresia.ESTADO_SIN_PAGOS,
        )
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertEqual(list(response.context["asistencias"]), [segunda, primera])

    def test_datos_historicos_no_se_recalculan(self):
        cliente = self._crear_cliente()
        Asistencia.objects.create(
            cliente=cliente,
            fecha_hora=timezone.now(),
            estado_membresia=membresia.ESTADO_VENCIDA_CON_MORA,
            fecha_vencimiento=date(2020, 1, 1),
            mora_al_ingresar=Decimal("150"),
            origen=OrigenAsistencia.MANUAL,
        )
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertContains(response, "150")
        self.assertContains(response, "01/01/2020")
        self.assertContains(response, "Vencida con mora")

    def test_lista_usa_etiqueta_corta_en_gracia(self):
        cliente = self._crear_cliente()
        Asistencia.objects.create(cliente=cliente, estado_membresia=membresia.ESTADO_EN_GRACIA)
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertContains(response, "En gracia")
        self.assertNotContains(response, "En periodo de gracia")

    def test_busqueda_por_nombre(self):
        cliente_a = self._crear_cliente(nombre_completo="Ana Pérez")
        cliente_b = self._crear_cliente(nombre_completo="Beto Gómez")
        Asistencia.objects.create(cliente=cliente_a, estado_membresia=membresia.ESTADO_SIN_PAGOS)
        Asistencia.objects.create(cliente=cliente_b, estado_membresia=membresia.ESTADO_SIN_PAGOS)
        response = self.client.get(reverse("gestion:asistencia_lista"), {"q": "Ana"})
        self.assertContains(response, "Ana Pérez")
        self.assertNotContains(response, "Beto Gómez")

    def test_busqueda_por_id_acceso(self):
        cliente_a = self._crear_cliente(nombre_completo="Ana Pérez")
        cliente_b = self._crear_cliente(nombre_completo="Beto Gómez")
        Asistencia.objects.create(cliente=cliente_a, estado_membresia=membresia.ESTADO_SIN_PAGOS)
        Asistencia.objects.create(cliente=cliente_b, estado_membresia=membresia.ESTADO_SIN_PAGOS)
        response = self.client.get(reverse("gestion:asistencia_lista"), {"q": str(cliente_a.id_acceso)})
        self.assertContains(response, "Ana Pérez")
        self.assertNotContains(response, "Beto Gómez")

    def test_filtro_por_fecha(self):
        cliente = self._crear_cliente()
        hoy = timezone.localdate()
        ayer = hoy - timedelta(days=1)
        asistencia_hoy = Asistencia.objects.create(
            cliente=cliente, fecha_hora=timezone.now(), estado_membresia=membresia.ESTADO_SIN_PAGOS
        )
        asistencia_ayer = Asistencia.objects.create(
            cliente=cliente,
            fecha_hora=timezone.make_aware(datetime(ayer.year, ayer.month, ayer.day, 10, 0)),
            estado_membresia=membresia.ESTADO_SIN_PAGOS,
        )
        response = self.client.get(reverse("gestion:asistencia_lista"), {"fecha": ayer.isoformat()})
        self.assertEqual(list(response.context["asistencias"]), [asistencia_ayer])

    def test_fecha_invalida_no_produce_error(self):
        cliente = self._crear_cliente()
        Asistencia.objects.create(cliente=cliente, estado_membresia=membresia.ESTADO_SIN_PAGOS)
        response = self.client.get(reverse("gestion:asistencia_lista"), {"fecha": "no-es-una-fecha"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fecha no válida")
        self.assertEqual(len(response.context["asistencias"]), 1)

    def test_contador_de_hoy(self):
        cliente = self._crear_cliente()
        hoy = timezone.localdate()
        ayer = hoy - timedelta(days=1)
        Asistencia.objects.create(cliente=cliente, fecha_hora=timezone.now(), estado_membresia=membresia.ESTADO_SIN_PAGOS)
        Asistencia.objects.create(cliente=cliente, fecha_hora=timezone.now(), estado_membresia=membresia.ESTADO_SIN_PAGOS)
        Asistencia.objects.create(
            cliente=cliente,
            fecha_hora=timezone.make_aware(datetime(ayer.year, ayer.month, ayer.day, 10, 0)),
            estado_membresia=membresia.ESTADO_SIN_PAGOS,
        )
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertEqual(response.context["total_hoy"], 2)

    def test_paginacion_20_por_pagina(self):
        cliente = self._crear_cliente()
        for i in range(25):
            Asistencia.objects.create(
                cliente=cliente,
                fecha_hora=timezone.make_aware(datetime(2026, 1, 1, 8, 0)) + timedelta(minutes=i * 10),
                estado_membresia=membresia.ESTADO_SIN_PAGOS,
            )
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertEqual(len(response.context["asistencias"]), 20)
        self.assertTrue(response.context["asistencias"].has_next())

    def test_filtros_se_preservan_en_paginacion(self):
        cliente = self._crear_cliente(nombre_completo="Ana Pérez")
        for i in range(25):
            Asistencia.objects.create(
                cliente=cliente,
                fecha_hora=timezone.make_aware(datetime(2026, 1, 1, 8, 0)) + timedelta(minutes=i * 10),
                estado_membresia=membresia.ESTADO_SIN_PAGOS,
            )
        response = self.client.get(reverse("gestion:asistencia_lista"), {"q": "Ana"})
        self.assertContains(response, "q=Ana")
        self.assertContains(response, "page=2")
        self.assertNotContains(response, "?&page=2")

    def test_enlace_a_detalle_cliente(self):
        cliente = self._crear_cliente()
        Asistencia.objects.create(cliente=cliente, estado_membresia=membresia.ESTADO_SIN_PAGOS)
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertContains(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))

    def test_estado_vacio_sin_asistencias(self):
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertContains(response, "No se encontraron asistencias")

    def test_enlace_asistencias_no_visible_para_anonimo(self):
        self.client.logout()
        response = self.client.get(reverse("gestion:checkin"))
        self.assertNotContains(response, reverse("gestion:asistencia_lista"))


class ClienteUltimoPeriodoFinPrecargadoTests(TestCase):
    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _crear_pago(self, cliente, periodo_inicio, periodo_fin):
        return Pago.objects.create(
            cliente=cliente,
            fecha_pago=periodo_inicio,
            mensualidad_base=Decimal("330"),
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
        )

    def test_ultimo_periodo_fin_es_keyword_only(self):
        cliente = self._crear_cliente()
        metodos_con_dos_posicionales = [
            lambda: cliente.estado_actual(None, date(2026, 1, 31)),
            lambda: cliente.mora_actual(None, date(2026, 1, 31)),
            lambda: cliente.total_sugerido(None, date(2026, 1, 31)),
        ]
        for llamada in metodos_con_dos_posicionales:
            with self.subTest(llamada=llamada):
                with self.assertRaises(TypeError):
                    llamada()
        with self.assertRaises(TypeError):
            cliente.siguiente_periodo_pendiente(date(2026, 1, 31))
        with self.assertRaises(TypeError):
            cliente.fecha_vencimiento_pendiente(date(2026, 1, 31))

    def test_sin_pagos_con_ultimo_periodo_fin_none_no_consulta_pagos(self):
        cliente = self._crear_cliente()
        with self.assertNumQueries(0):
            self.assertIsNone(cliente.siguiente_periodo_pendiente(ultimo_periodo_fin=None))
            self.assertIsNone(cliente.fecha_vencimiento_pendiente(ultimo_periodo_fin=None))
            self.assertEqual(cliente.estado_actual(ultimo_periodo_fin=None), membresia.ESTADO_SIN_PAGOS)
            self.assertEqual(cliente.mora_actual(ultimo_periodo_fin=None), Decimal("0"))
            self.assertIsNone(cliente.total_sugerido(ultimo_periodo_fin=None))

    def test_con_pagos_ultimo_periodo_fin_precargado_no_consulta_pagos(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))
        with self.assertNumQueries(0):
            vencimiento = cliente.fecha_vencimiento_pendiente(ultimo_periodo_fin=date(2026, 1, 31))
            estado = cliente.estado_actual(date(2026, 2, 5), ultimo_periodo_fin=date(2026, 1, 31))
            mora = cliente.mora_actual(date(2026, 2, 5), ultimo_periodo_fin=date(2026, 1, 31))
        self.assertEqual(vencimiento, date(2026, 2, 1))
        self.assertEqual(estado, membresia.ESTADO_VENCIDA_CON_MORA)
        self.assertEqual(mora, Decimal("10"))

    def test_resultado_equivalente_entre_calculo_tradicional_y_precargado(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))
        fecha_referencia = date(2026, 2, 5)
        ultimo_periodo_fin = cliente.ultimo_pago().periodo_fin

        self.assertEqual(
            cliente.estado_actual(fecha_referencia),
            cliente.estado_actual(fecha_referencia, ultimo_periodo_fin=ultimo_periodo_fin),
        )
        self.assertEqual(
            cliente.fecha_vencimiento_pendiente(),
            cliente.fecha_vencimiento_pendiente(ultimo_periodo_fin=ultimo_periodo_fin),
        )
        self.assertEqual(
            cliente.mora_actual(fecha_referencia),
            cliente.mora_actual(fecha_referencia, ultimo_periodo_fin=ultimo_periodo_fin),
        )
        self.assertEqual(
            cliente.total_sugerido(fecha_referencia),
            cliente.total_sugerido(fecha_referencia, ultimo_periodo_fin=ultimo_periodo_fin),
        )

    def test_metodos_sin_parametro_conservan_comportamiento_actual(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))
        # Sin ultimo_periodo_fin, cada llamada sigue consultando pagos, como hoy.
        with self.assertNumQueries(1):
            vencimiento = cliente.fecha_vencimiento_pendiente()
        self.assertEqual(vencimiento, date(2026, 2, 1))
        self.assertEqual(cliente.estado_actual(date(2026, 1, 20)), membresia.ESTADO_VIGENTE)


class MembresiaMasivaTests(TestCase):
    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _crear_pago(self, cliente, periodo_inicio, periodo_fin):
        return Pago.objects.create(
            cliente=cliente,
            fecha_pago=periodo_inicio,
            mensualidad_base=Decimal("330"),
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
        )

    def test_cliente_sin_pagos(self):
        cliente = self._crear_cliente()
        resultados = calcular_estados_membresia(
            queryset=Cliente.objects.filter(pk=cliente.pk), fecha_referencia=date(2026, 2, 5)
        )
        self.assertEqual(len(resultados), 1)
        resultado = resultados[0]
        self.assertEqual(resultado.cliente.pk, cliente.pk)
        self.assertEqual(resultado.estado, membresia.ESTADO_SIN_PAGOS)
        self.assertIsNone(resultado.fecha_vencimiento)
        self.assertEqual(resultado.mora, Decimal("0"))

    def test_cliente_con_pagos_calcula_vencimiento_y_mora(self):
        cliente = self._crear_cliente(dia_pago=DiaPago.DIA_1)
        self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))
        resultados = calcular_estados_membresia(
            queryset=Cliente.objects.filter(pk=cliente.pk), fecha_referencia=date(2026, 2, 5)
        )
        resultado = resultados[0]
        self.assertEqual(resultado.fecha_vencimiento, date(2026, 2, 1))
        self.assertEqual(resultado.mora, Decimal("10"))
        self.assertEqual(resultado.estado, membresia.ESTADO_VENCIDA_CON_MORA)

    def test_los_cinco_estados_posibles(self):
        cliente_sin_pagos = self._crear_cliente(nombre_completo="Sin pagos")
        resultado_sin_pagos = calcular_estados_membresia(
            queryset=Cliente.objects.filter(pk=cliente_sin_pagos.pk), fecha_referencia=date(2026, 2, 5)
        )[0]
        self.assertEqual(resultado_sin_pagos.estado, membresia.ESTADO_SIN_PAGOS)

        # Vencimiento fijo en 2026-02-01 (periodo_fin=2026-01-31, dia_pago=1);
        # solo cambia la fecha de referencia, igual que en
        # MembresiaFuncionesTests.test_estados_membresia_limites.
        cliente = self._crear_cliente(nombre_completo="Con pagos", dia_pago=DiaPago.DIA_1)
        self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))
        queryset = Cliente.objects.filter(pk=cliente.pk)

        casos = [
            (date(2026, 1, 20), membresia.ESTADO_VIGENTE),
            (date(2026, 1, 29), membresia.ESTADO_POR_VENCER),
            (date(2026, 2, 1), membresia.ESTADO_POR_VENCER),
            (date(2026, 2, 2), membresia.ESTADO_EN_GRACIA),
            (date(2026, 2, 4), membresia.ESTADO_EN_GRACIA),
            (date(2026, 2, 5), membresia.ESTADO_VENCIDA_CON_MORA),
        ]
        for fecha_referencia, estado_esperado in casos:
            with self.subTest(fecha_referencia=fecha_referencia):
                resultado = calcular_estados_membresia(queryset=queryset, fecha_referencia=fecha_referencia)[0]
                self.assertEqual(resultado.estado, estado_esperado)

    def test_queryset_none_usa_clientes_activos_por_defecto(self):
        activo = self._crear_cliente(nombre_completo="Activo", activo=True)
        inactivo = self._crear_cliente(nombre_completo="Inactivo", activo=False)
        resultados = calcular_estados_membresia(fecha_referencia=date(2026, 2, 5))
        pks = {resultado.cliente.pk for resultado in resultados}
        self.assertIn(activo.pk, pks)
        self.assertNotIn(inactivo.pk, pks)

    def test_acepta_otro_queryset_de_cliente(self):
        uno = self._crear_cliente(nombre_completo="Uno")
        self._crear_cliente(nombre_completo="Dos")
        resultados = calcular_estados_membresia(
            queryset=Cliente.objects.filter(nombre_completo="Uno"), fecha_referencia=date(2026, 2, 5)
        )
        self.assertEqual([r.cliente.pk for r in resultados], [uno.pk])

    def test_contar_por_estado(self):
        self._crear_cliente(nombre_completo="Sin pagos")
        for i in range(2):
            cliente = self._crear_cliente(nombre_completo=f"Con mora {i}", dia_pago=DiaPago.DIA_1)
            self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))

        resultados = calcular_estados_membresia(fecha_referencia=date(2026, 2, 5))
        conteo = contar_por_estado(resultados)
        self.assertEqual(conteo[membresia.ESTADO_SIN_PAGOS], 1)
        self.assertEqual(conteo[membresia.ESTADO_VENCIDA_CON_MORA], 2)

    def test_una_sola_consulta_para_varios_clientes_sin_queries_adicionales(self):
        for i in range(6):
            cliente = self._crear_cliente(nombre_completo=f"Cliente {i}", dia_pago=DiaPago.DIA_1)
            if i % 2 == 0:
                self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))

        with self.assertNumQueries(1):
            resultados = calcular_estados_membresia(fecha_referencia=date(2026, 2, 5))
            # Acceder a los campos ya calculados no debe disparar queries nuevas.
            for resultado in resultados:
                resultado.estado
                resultado.fecha_vencimiento
                resultado.mora

        self.assertEqual(len(resultados), 6)


class MembresiaListaVistaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="encargado", password="clave-super-12345")
        self.url = reverse("gestion:membresia_lista")

    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _crear_pago(self, cliente, periodo_inicio, periodo_fin):
        return Pago.objects.create(
            cliente=cliente,
            fecha_pago=periodo_inicio,
            mensualidad_base=Decimal("330"),
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
        )

    def test_requiere_login(self):
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    @patch("gestion.views.timezone.localdate")
    def test_solo_incluye_clientes_activos(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        activo = self._crear_cliente(nombre_completo="Activo Uno", activo=True)
        self._crear_cliente(nombre_completo="Inactivo Uno", activo=False)

        response = self.client.get(self.url)
        pks = {resultado.cliente.pk for resultado in response.context["page_obj"]}
        self.assertEqual(pks, {activo.pk})
        self.assertEqual(sum(response.context["conteos"].values()), 1)

    @patch("gestion.views.timezone.localdate")
    def test_busqueda_por_nombre(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Ana Torres")
        self._crear_cliente(nombre_completo="Luis Ramírez")

        response = self.client.get(self.url, {"q": "Ana"})
        nombres = {resultado.cliente.nombre_completo for resultado in response.context["page_obj"]}
        self.assertEqual(nombres, {"Ana Torres"})

    @patch("gestion.views.timezone.localdate")
    def test_busqueda_por_id_acceso(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        objetivo = self._crear_cliente(nombre_completo="Ana Torres")
        self._crear_cliente(nombre_completo="Luis Ramírez")

        response = self.client.get(self.url, {"q": str(objetivo.id_acceso)})
        pks = {resultado.cliente.pk for resultado in response.context["page_obj"]}
        self.assertEqual(pks, {objetivo.pk})

    @patch("gestion.views.timezone.localdate")
    def test_busqueda_no_afecta_los_conteos_globales(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Ana Torres")
        self._crear_cliente(nombre_completo="Luis Ramírez")

        response = self.client.get(self.url, {"q": "Ana"})
        self.assertEqual(sum(response.context["conteos"].values()), 2)

    @patch("gestion.views.timezone.localdate")
    def test_conteo_y_filtro_estado_sin_pagos(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        objetivo = self._crear_cliente(nombre_completo="Sin Pagos")
        vigente = self._crear_cliente(nombre_completo="Con Pagos", dia_pago=DiaPago.DIA_1)
        self._crear_pago(vigente, date(2026, 2, 1), date(2026, 2, 28))

        response = self.client.get(self.url)
        self.assertEqual(response.context["conteos"][membresia.ESTADO_SIN_PAGOS], 1)

        response = self.client.get(self.url, {"estado": membresia.ESTADO_SIN_PAGOS})
        pks = {resultado.cliente.pk for resultado in response.context["page_obj"]}
        self.assertEqual(pks, {objetivo.pk})
        self.assertEqual(response.context["estado_seleccionado"], membresia.ESTADO_SIN_PAGOS)

    @patch("gestion.views.timezone.localdate")
    def test_conteo_y_filtro_estado_vigente(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Sin Pagos")
        objetivo = self._crear_cliente(nombre_completo="Vigente", dia_pago=DiaPago.DIA_1)
        self._crear_pago(objetivo, date(2026, 2, 1), date(2026, 2, 28))

        response = self.client.get(self.url)
        self.assertEqual(response.context["conteos"][membresia.ESTADO_VIGENTE], 1)

        response = self.client.get(self.url, {"estado": membresia.ESTADO_VIGENTE})
        pks = {resultado.cliente.pk for resultado in response.context["page_obj"]}
        self.assertEqual(pks, {objetivo.pk})

    @patch("gestion.views.timezone.localdate")
    def test_conteo_y_filtro_estado_por_vencer(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 1)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Sin Pagos")
        objetivo = self._crear_cliente(nombre_completo="Por Vencer", dia_pago=DiaPago.DIA_1)
        self._crear_pago(objetivo, date(2026, 1, 1), date(2026, 1, 31))

        response = self.client.get(self.url)
        self.assertEqual(response.context["conteos"][membresia.ESTADO_POR_VENCER], 1)

        response = self.client.get(self.url, {"estado": membresia.ESTADO_POR_VENCER})
        pks = {resultado.cliente.pk for resultado in response.context["page_obj"]}
        self.assertEqual(pks, {objetivo.pk})

    @patch("gestion.views.timezone.localdate")
    def test_conteo_y_filtro_estado_en_gracia(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 4)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Sin Pagos")
        objetivo = self._crear_cliente(nombre_completo="En Gracia", dia_pago=DiaPago.DIA_1)
        self._crear_pago(objetivo, date(2026, 1, 1), date(2026, 1, 31))

        response = self.client.get(self.url)
        self.assertEqual(response.context["conteos"][membresia.ESTADO_EN_GRACIA], 1)

        response = self.client.get(self.url, {"estado": membresia.ESTADO_EN_GRACIA})
        pks = {resultado.cliente.pk for resultado in response.context["page_obj"]}
        self.assertEqual(pks, {objetivo.pk})

    @patch("gestion.views.timezone.localdate")
    def test_conteo_y_filtro_estado_vencida_con_mora(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Sin Pagos")
        objetivo = self._crear_cliente(nombre_completo="Vencida", dia_pago=DiaPago.DIA_1)
        self._crear_pago(objetivo, date(2026, 1, 1), date(2026, 1, 31))

        response = self.client.get(self.url)
        self.assertEqual(response.context["conteos"][membresia.ESTADO_VENCIDA_CON_MORA], 1)

        response = self.client.get(self.url, {"estado": membresia.ESTADO_VENCIDA_CON_MORA})
        pks = {resultado.cliente.pk for resultado in response.context["page_obj"]}
        self.assertEqual(pks, {objetivo.pk})

    @patch("gestion.views.timezone.localdate")
    def test_estado_invalido_se_trata_como_todos(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Sin Pagos")
        vigente = self._crear_cliente(nombre_completo="Vigente", dia_pago=DiaPago.DIA_1)
        self._crear_pago(vigente, date(2026, 2, 1), date(2026, 2, 28))

        response = self.client.get(self.url, {"estado": "no_existe"})
        self.assertEqual(response.context["estado_seleccionado"], "")
        self.assertEqual(len(response.context["page_obj"].paginator.object_list), 2)

    @patch("gestion.views.timezone.localdate")
    def test_orden_por_prioridad_de_estado(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 4)
        self.client.force_login(self.usuario)

        sin_pagos = self._crear_cliente(nombre_completo="Zeta Sin Pagos")

        vencida = self._crear_cliente(nombre_completo="Yolanda Vencida", dia_pago=DiaPago.DIA_1)
        self._crear_pago(vencida, date(2025, 11, 1), date(2025, 11, 30))

        gracia = self._crear_cliente(nombre_completo="Xavier Gracia", dia_pago=DiaPago.DIA_1)
        self._crear_pago(gracia, date(2026, 1, 1), date(2026, 1, 31))

        vigente = self._crear_cliente(nombre_completo="Wendy Vigente", dia_pago=DiaPago.DIA_1)
        self._crear_pago(vigente, date(2026, 2, 1), date(2026, 2, 28))

        response = self.client.get(self.url)
        orden = [resultado.cliente.pk for resultado in response.context["page_obj"]]
        self.assertEqual(orden, [vencida.pk, gracia.pk, sin_pagos.pk, vigente.pk])

    @patch("gestion.views.timezone.localdate")
    def test_orden_alfabetico_dentro_del_mismo_estado(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        zoe = self._crear_cliente(nombre_completo="Zoe Sin Pagos")
        ana = self._crear_cliente(nombre_completo="ana Sin Pagos")
        beto = self._crear_cliente(nombre_completo="Beto Sin Pagos")

        response = self.client.get(self.url)
        orden = [resultado.cliente.pk for resultado in response.context["page_obj"]]
        self.assertEqual(orden, [ana.pk, beto.pk, zoe.pk])

    @patch("gestion.views.timezone.localdate")
    def test_paginacion_20_por_pagina(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        for i in range(25):
            self._crear_cliente(nombre_completo=f"Cliente {i:02d}")

        response = self.client.get(self.url)
        self.assertEqual(len(response.context["page_obj"]), 20)
        self.assertTrue(response.context["page_obj"].has_next())

        response = self.client.get(self.url, {"page": 2})
        self.assertEqual(len(response.context["page_obj"]), 5)

    @patch("gestion.views.timezone.localdate")
    def test_preserva_filtros_en_querystring(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        for i in range(25):
            self._crear_cliente(nombre_completo=f"Sin Pagos {i:02d}")

        response = self.client.get(
            self.url, {"q": "Sin Pagos", "estado": membresia.ESTADO_SIN_PAGOS, "page": 2}
        )
        querystring = response.context["querystring"]
        self.assertIn("q=Sin+Pagos", querystring)
        self.assertIn(f"estado={membresia.ESTADO_SIN_PAGOS}", querystring)
        self.assertNotIn("page=", querystring)

    @patch("gestion.views.timezone.localdate")
    def test_cantidad_de_queries_no_crece_con_el_numero_de_clientes(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)

        for i in range(3):
            cliente = self._crear_cliente(nombre_completo=f"Pocos {i}", dia_pago=DiaPago.DIA_1)
            self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))
        with CaptureQueriesContext(connection) as pocos:
            self.client.get(self.url)

        for i in range(30):
            cliente = self._crear_cliente(nombre_completo=f"Muchos {i}", dia_pago=DiaPago.DIA_1)
            self._crear_pago(cliente, date(2026, 1, 1), date(2026, 1, 31))
        with CaptureQueriesContext(connection) as muchos:
            self.client.get(self.url)

        self.assertEqual(len(pocos.captured_queries), len(muchos.captured_queries))

    @patch("gestion.views.timezone.localdate")
    def test_una_sola_query_extra_con_busqueda_y_ninguna_sin_ella(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        for i in range(5):
            self._crear_cliente(nombre_completo=f"Cliente {i}")

        with CaptureQueriesContext(connection) as sin_busqueda:
            self.client.get(self.url)
        with CaptureQueriesContext(connection) as con_busqueda:
            self.client.get(self.url, {"q": "Cliente"})

        self.assertEqual(len(con_busqueda.captured_queries), len(sin_busqueda.captured_queries) + 1)

    @patch("gestion.views.timezone.localdate")
    def test_acciones_ver_cliente_y_registrar_pago_presentes(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        cliente = self._crear_cliente(nombre_completo="Ana Torres")

        response = self.client.get(self.url)
        self.assertContains(response, "Ver cliente")
        self.assertContains(response, "Registrar pago")
        self.assertContains(response, reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        self.assertContains(response, reverse("gestion:pago_registrar", args=[cliente.id_acceso]))

    @patch("gestion.views.timezone.localdate")
    def test_estado_vacio_sin_clientes_activos(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)

        response = self.client.get(self.url)
        self.assertContains(response, "No hay clientes activos registrados.")

    @patch("gestion.views.timezone.localdate")
    def test_estado_vacio_con_filtros_ofrece_limpiar(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Ana Torres")

        response = self.client.get(self.url, {"q": "Nombre que no existe"})
        self.assertContains(response, "No se encontraron clientes con esos filtros.")
        self.assertContains(response, "Limpiar filtros")
        self.assertContains(response, reverse("gestion:membresia_lista"))

    @patch("gestion.views.timezone.localdate")
    def test_filtros_seleccionados_se_conservan_en_el_formulario(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)
        self.client.force_login(self.usuario)
        self._crear_cliente(nombre_completo="Ana Torres")

        response = self.client.get(self.url, {"q": "Ana", "estado": membresia.ESTADO_SIN_PAGOS})
        self.assertContains(response, 'value="Ana"')
        self.assertContains(
            response,
            f'<option value="{membresia.ESTADO_SIN_PAGOS}" selected>',
        )


class InicioVistaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="encargado", password="clave-super-12345")
        self.client.force_login(self.usuario)
        self.url = reverse("gestion:inicio")

    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def _crear_pago(self, cliente, periodo_inicio, periodo_fin):
        return Pago.objects.create(
            cliente=cliente,
            fecha_pago=periodo_inicio,
            mensualidad_base=Decimal("330"),
            periodo_inicio=periodo_inicio,
            periodo_fin=periodo_fin,
        )

    def test_requiere_login(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertRedirects(response, f"{reverse('login')}?next={self.url}")

    def test_muestra_power_center_gym(self):
        response = self.client.get(self.url)
        self.assertContains(response, "Power Center Gym")

    def test_muestra_los_cuatro_accesos_rapidos(self):
        response = self.client.get(self.url)
        self.assertContains(response, reverse("gestion:cliente_lista"))
        self.assertContains(response, reverse("gestion:membresia_lista"))
        self.assertContains(response, reverse("gestion:asistencia_lista"))
        self.assertContains(response, reverse("gestion:checkin"))

    @patch("gestion.views.timezone.localdate")
    def test_metricas_reflejan_los_datos_creados(self, mock_localdate):
        hoy = date(2026, 1, 29)
        mock_localdate.return_value = hoy

        self._crear_cliente(nombre_completo="Inactivo", activo=False)

        sin_pagos = self._crear_cliente(nombre_completo="Sin pagos")

        vigente = self._crear_cliente(nombre_completo="Vigente")
        self._crear_pago(vigente, date(2026, 6, 1), date(2026, 6, 30))

        por_vencer = self._crear_cliente(nombre_completo="Por vencer")
        self._crear_pago(por_vencer, date(2026, 1, 1), date(2026, 1, 31))

        Asistencia.objects.create(
            cliente=vigente,
            fecha_hora=timezone.make_aware(datetime(2026, 1, 29, 9, 0)),
            estado_membresia=membresia.ESTADO_VIGENTE,
        )
        Asistencia.objects.create(
            cliente=vigente,
            fecha_hora=timezone.make_aware(datetime(2026, 1, 28, 9, 0)),
            estado_membresia=membresia.ESTADO_VIGENTE,
        )

        response = self.client.get(self.url)
        self.assertEqual(response.context["total_clientes_activos"], 3)
        self.assertEqual(response.context["membresias_vigentes"], 1)
        self.assertEqual(response.context["membresias_por_vencer"], 1)
        self.assertEqual(response.context["asistencias_hoy"], 1)
        self.assertContains(response, '<span class="resumen-membresias-numero">3</span>')

    @patch("gestion.views.timezone.localdate")
    def test_no_genera_una_consulta_por_cliente(self, mock_localdate):
        mock_localdate.return_value = date(2026, 2, 5)

        for i in range(3):
            self._crear_cliente(nombre_completo=f"Pocos {i}")
        with CaptureQueriesContext(connection) as pocos:
            self.client.get(self.url)

        for i in range(30):
            self._crear_cliente(nombre_completo=f"Muchos {i}")
        with CaptureQueriesContext(connection) as muchos:
            self.client.get(self.url)

        self.assertEqual(len(pocos.captured_queries), len(muchos.captured_queries))


class NavegacionActivaTests(TestCase):
    def setUp(self):
        self.usuario = User.objects.create_user(username="encargado", password="clave-super-12345")
        self.client.force_login(self.usuario)

    def _crear_cliente(self, **kwargs):
        defaults = {
            "nombre_completo": "Cliente de prueba",
            "dia_pago": DiaPago.DIA_1,
            "tipo_tarifa": TipoTarifa.GENERAL,
        }
        defaults.update(kwargs)
        return Cliente.objects.create(**defaults)

    def test_inicio_activa_inicio(self):
        response = self.client.get(reverse("gestion:inicio"))
        self.assertEqual(response.context["seccion_activa"], "inicio")

    def test_lista_clientes_activa_clientes(self):
        response = self.client.get(reverse("gestion:cliente_lista"))
        self.assertEqual(response.context["seccion_activa"], "clientes")

    def test_detalle_cliente_mantiene_clientes_activo(self):
        cliente = self._crear_cliente()
        response = self.client.get(reverse("gestion:cliente_detalle", args=[cliente.id_acceso]))
        self.assertEqual(response.context["seccion_activa"], "clientes")

    def test_registrar_pago_mantiene_clientes_activo(self):
        cliente = self._crear_cliente()
        response = self.client.get(reverse("gestion:pago_registrar", args=[cliente.id_acceso]))
        self.assertEqual(response.context["seccion_activa"], "clientes")

    def test_membresias_activa_membresias(self):
        response = self.client.get(reverse("gestion:membresia_lista"))
        self.assertEqual(response.context["seccion_activa"], "membresias")

    def test_asistencias_activa_asistencias(self):
        response = self.client.get(reverse("gestion:asistencia_lista"))
        self.assertEqual(response.context["seccion_activa"], "asistencias")

    def test_checkin_activa_checkin(self):
        response = self.client.get(reverse("gestion:checkin"))
        self.assertEqual(response.context["seccion_activa"], "checkin")

    def test_resultado_checkin_mantiene_checkin_activo(self):
        cliente = self._crear_cliente()
        response = self.client.post(reverse("gestion:checkin"), {"id_acceso": str(cliente.id_acceso)})
        self.assertEqual(response.context["seccion_activa"], "checkin")

    def test_nav_marca_la_clase_active_en_el_enlace_correspondiente(self):
        response = self.client.get(reverse("gestion:cliente_lista"))
        self.assertContains(response, 'class="nav-link active"')
        self.assertContains(response, 'aria-current="page"')
