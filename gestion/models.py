import random
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from . import membresia

ID_ACCESO_MIN = 1000
ID_ACCESO_MAX = 9999
ID_ACCESO_MAX_INTENTOS = 100


class IdAccesoNoDisponibleError(Exception):
    """No fue posible generar un id_acceso disponible tras agotar los intentos permitidos."""


class TipoTarifa(models.TextChoices):
    GENERAL = "general", "General"
    ESTUDIANTE = "estudiante", "Estudiante"


class DiaPago(models.IntegerChoices):
    DIA_1 = 1, "Día 1"
    DIA_15 = 15, "Día 15"


class OrigenAsistencia(models.TextChoices):
    CLIENTE = "cliente", "Cliente"
    MANUAL = "manual", "Manual"


ESTADO_MEMBRESIA_CHOICES = [
    (membresia.ESTADO_VIGENTE, "Vigente"),
    (membresia.ESTADO_POR_VENCER, "Por vencer"),
    (membresia.ESTADO_EN_GRACIA, "En gracia"),
    (membresia.ESTADO_VENCIDA_CON_MORA, "Vencida con mora"),
    (membresia.ESTADO_SIN_PAGOS, "Sin pagos"),
]


def validar_reajuste_inicial(value):
    if value not in membresia.REAJUSTES_VALIDOS:
        raise ValidationError(f"El reajuste inicial debe ser uno de: {membresia.REAJUSTES_VALIDOS}.")


def validar_inscripcion(value):
    valores_validos = (Decimal("0"), membresia.INSCRIPCION_MONTO)
    if value not in valores_validos:
        raise ValidationError(f"La inscripción debe ser uno de: {valores_validos}.")


class Cliente(models.Model):
    id_acceso = models.PositiveIntegerField(
        unique=True,
        validators=[MinValueValidator(ID_ACCESO_MIN), MaxValueValidator(ID_ACCESO_MAX)],
        help_text="ID numérico de 4 dígitos generado automáticamente.",
    )
    nombre_completo = models.CharField(max_length=150)
    telefono = models.CharField(max_length=20, blank=True)
    fotografia = models.ImageField(upload_to="clientes/", blank=True, null=True)
    fecha_alta = models.DateTimeField(auto_now_add=True)
    activo = models.BooleanField(default=True)
    notas = models.TextField(blank=True)

    tipo_tarifa = models.CharField(max_length=12, choices=TipoTarifa.choices, default=TipoTarifa.GENERAL)
    dia_pago = models.PositiveSmallIntegerField(choices=DiaPago.choices, default=DiaPago.DIA_1)
    inscripcion_aplicada = models.BooleanField(default=False)
    reajuste_inicial_aplicado = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id_acceso__gte=ID_ACCESO_MIN) & models.Q(id_acceso__lte=ID_ACCESO_MAX),
                name="cliente_id_acceso_en_rango",
            ),
            models.CheckConstraint(
                condition=models.Q(tipo_tarifa__in=TipoTarifa.values),
                name="cliente_tipo_tarifa_valido",
            ),
            models.CheckConstraint(
                condition=models.Q(dia_pago__in=DiaPago.values),
                name="cliente_dia_pago_valido",
            ),
        ]

    def __str__(self):
        return f"{self.nombre_completo} ({self.id_acceso})"

    def save(self, *args, **kwargs):
        if not self.id_acceso:
            self.id_acceso = self._generar_id_acceso()
        super().save(*args, **kwargs)

    @classmethod
    def _generar_id_acceso(cls):
        for _ in range(ID_ACCESO_MAX_INTENTOS):
            candidato = random.randint(ID_ACCESO_MIN, ID_ACCESO_MAX)
            if not cls.objects.filter(id_acceso=candidato).exists():
                return candidato
        raise IdAccesoNoDisponibleError(
            f"No se encontró un id_acceso disponible tras {ID_ACCESO_MAX_INTENTOS} intentos."
        )

    def ultimo_pago(self):
        return self.pagos.order_by("-periodo_fin").first()

    def ultimo_periodo_pagado(self):
        ultimo = self.ultimo_pago()
        if ultimo is None:
            return None
        return ultimo.periodo_inicio, ultimo.periodo_fin

    def siguiente_periodo_pendiente(self):
        ultimo_periodo = self.ultimo_periodo_pagado()
        if ultimo_periodo is None:
            return None
        _inicio, fin = ultimo_periodo
        return membresia.siguiente_periodo(fin, self.dia_pago)

    def fecha_vencimiento_pendiente(self):
        siguiente = self.siguiente_periodo_pendiente()
        if siguiente is None:
            return None
        inicio, _fin = siguiente
        return membresia.fecha_vencimiento(inicio)

    def estado_actual(self, fecha_referencia=None):
        vencimiento = self.fecha_vencimiento_pendiente()
        if vencimiento is None:
            return membresia.ESTADO_SIN_PAGOS
        fecha_referencia = fecha_referencia or timezone.localdate()
        return membresia.calcular_estado(vencimiento, fecha_referencia)

    def mora_actual(self, fecha_referencia=None):
        vencimiento = self.fecha_vencimiento_pendiente()
        if vencimiento is None:
            return Decimal("0")
        fecha_referencia = fecha_referencia or timezone.localdate()
        return membresia.calcular_mora(vencimiento, fecha_referencia)

    def total_sugerido(self, fecha_referencia=None):
        vencimiento = self.fecha_vencimiento_pendiente()
        if vencimiento is None:
            return None
        fecha_referencia = fecha_referencia or timezone.localdate()
        incluir_inscripcion = not self.inscripcion_aplicada
        return membresia.calcular_total_sugerido(self.tipo_tarifa, incluir_inscripcion, vencimiento, fecha_referencia)

    def recalcular_banderas_pago(self):
        """Deriva inscripcion_aplicada y reajuste_inicial_aplicado desde el historial de pagos.

        inscripcion_aplicada: True si existe al menos un pago con inscripcion > 0
        (ya se le cobró la inscripción alguna vez).

        reajuste_inicial_aplicado: True si el cliente tiene al menos un pago
        registrado, es decir, si la decisión de reajuste inicial ya se tomó
        en su primer pago — sin importar si el valor elegido fue -50, 0 o
        +50. NO significa "hubo un reajuste distinto de cero"; significa
        "el reajuste inicial ya fue resuelto y no debe volver a ofrecerse".

        Se recalcula por completo (nunca se activa/desactiva a mano) para
        que no haya desincronización: si se elimina el único pago de un
        cliente, ambas banderas vuelven a False automáticamente.
        """
        self.inscripcion_aplicada = self.pagos.filter(inscripcion__gt=0).exists()
        self.reajuste_inicial_aplicado = self.pagos.exists()
        self.save(update_fields=["inscripcion_aplicada", "reajuste_inicial_aplicado"])

    def ultima_asistencia(self):
        return self.asistencias.first()


class Pago(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="pagos")
    fecha_pago = models.DateField()
    mensualidad_base = models.DecimalField(
        max_digits=8, decimal_places=2, validators=[MinValueValidator(Decimal("0"))]
    )
    inscripcion = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"), validators=[validar_inscripcion]
    )
    reajuste_inicial = models.DecimalField(
        max_digits=8, decimal_places=2, default=Decimal("0"), validators=[validar_reajuste_inicial]
    )
    mora = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    otros_ajustes = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    total_pagado = models.DecimalField(max_digits=8, decimal_places=2, default=Decimal("0"))
    metodo_pago = models.CharField(max_length=30, blank=True)
    periodo_inicio = models.DateField()
    periodo_fin = models.DateField()
    fecha_registro = models.DateTimeField(auto_now_add=True)
    notas = models.TextField(blank=True)

    class Meta:
        ordering = ["-periodo_inicio"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(periodo_fin__gte=models.F("periodo_inicio")),
                name="pago_periodo_valido",
            ),
            models.CheckConstraint(
                condition=models.Q(reajuste_inicial__in=list(membresia.REAJUSTES_VALIDOS)),
                name="pago_reajuste_inicial_valido",
            ),
            models.CheckConstraint(
                condition=models.Q(inscripcion__in=[Decimal("0"), membresia.INSCRIPCION_MONTO]),
                name="pago_inscripcion_valida",
            ),
            models.CheckConstraint(
                condition=models.Q(mora__gte=Decimal("0")),
                name="pago_mora_no_negativa",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    total_pagado=(
                        models.F("mensualidad_base")
                        + models.F("inscripcion")
                        + models.F("reajuste_inicial")
                        + models.F("mora")
                        + models.F("otros_ajustes")
                    )
                ),
                name="pago_total_pagado_coincide_con_conceptos",
            ),
        ]

    def __str__(self):
        return f"Pago de {self.cliente} — {self.periodo_inicio:%d/%m/%Y}"

    def tiene_pagos_posteriores(self):
        return self.cliente.pagos.filter(periodo_fin__gt=self.periodo_fin).exists()

    def es_primer_pago_del_cliente(self):
        return not Pago.objects.filter(cliente_id=self.cliente_id).exclude(pk=self.pk).exists()

    def save(self, *args, **kwargs):
        if self.es_primer_pago_del_cliente():
            # El primer pago establece el periodo inicial; no hay vencimiento
            # previo real contra el cual calcular mora.
            self.mora = Decimal("0")
        else:
            self.mora = membresia.calcular_mora(self.periodo_inicio, self.fecha_pago)
        self.total_pagado = (
            self.mensualidad_base + self.inscripcion + self.reajuste_inicial + self.mora + self.otros_ajustes
        )
        super().save(*args, **kwargs)


class Asistencia(models.Model):
    cliente = models.ForeignKey(Cliente, on_delete=models.PROTECT, related_name="asistencias")
    fecha_hora = models.DateTimeField(default=timezone.now, editable=False)
    estado_membresia = models.CharField(max_length=20, choices=ESTADO_MEMBRESIA_CHOICES)
    fecha_vencimiento = models.DateField(null=True, blank=True)
    mora_al_ingresar = models.DecimalField(
        max_digits=8,
        decimal_places=2,
        default=Decimal("0"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    origen = models.CharField(max_length=10, choices=OrigenAsistencia.choices, default=OrigenAsistencia.CLIENTE)

    class Meta:
        ordering = ["-fecha_hora"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(mora_al_ingresar__gte=Decimal("0")),
                name="asistencia_mora_no_negativa",
            ),
            models.CheckConstraint(
                condition=models.Q(estado_membresia__in=[valor for valor, _ in ESTADO_MEMBRESIA_CHOICES]),
                name="asistencia_estado_membresia_valido",
            ),
            models.CheckConstraint(
                condition=models.Q(origen__in=OrigenAsistencia.values),
                name="asistencia_origen_valido",
            ),
        ]

    def __str__(self):
        return f"Asistencia de {self.cliente} — {self.fecha_hora:%d/%m/%Y %H:%M}"
