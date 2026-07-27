from datetime import date
from decimal import Decimal
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from . import membresia
from .forms import ClienteForm
from .models import Cliente, DiaPago, IdAccesoNoDisponibleError, Pago, TipoTarifa


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
