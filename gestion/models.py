import random

from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

ID_ACCESO_MIN = 1000
ID_ACCESO_MAX = 9999
ID_ACCESO_MAX_INTENTOS = 100


class IdAccesoNoDisponibleError(Exception):
    """No fue posible generar un id_acceso disponible tras agotar los intentos permitidos."""


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

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(id_acceso__gte=ID_ACCESO_MIN) & models.Q(id_acceso__lte=ID_ACCESO_MAX),
                name="cliente_id_acceso_en_rango",
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
