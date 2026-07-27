from decimal import Decimal

from django import forms

from .models import Cliente, Pago


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["nombre_completo", "telefono", "fotografia", "tipo_tarifa", "dia_pago", "notas", "activo"]
        widgets = {
            "nombre_completo": forms.TextInput(attrs={"class": "form-control"}),
            "telefono": forms.TextInput(attrs={"class": "form-control"}),
            "fotografia": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "tipo_tarifa": forms.Select(attrs={"class": "form-select"}),
            "dia_pago": forms.Select(attrs={"class": "form-select"}),
            "notas": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "activo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


METODO_PAGO_CHOICES = [
    ("", "Selecciona un método"),
    ("efectivo", "Efectivo"),
    ("transferencia", "Transferencia"),
    ("tarjeta", "Tarjeta"),
    ("otro", "Otro"),
]

REAJUSTE_INICIAL_CHOICES = [
    (Decimal("-50.00"), "-$50"),
    (Decimal("0.00"), "Sin reajuste"),
    (Decimal("50.00"), "+$50"),
]

_MESES = (
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
)


def _fecha_larga(fecha):
    return f"{fecha.day} {_MESES[fecha.month - 1]} {fecha.year}"


def etiqueta_periodo(inicio, fin):
    return f"{_fecha_larga(inicio)} – {_fecha_larga(fin)}"


class PagoForm(forms.ModelForm):
    periodo_seleccionado = forms.ChoiceField(
        required=True,
        label="Periodo a cubrir",
        widget=forms.RadioSelect,
        error_messages={"required": "Elige el periodo que cubre este primer pago."},
    )
    cobrar_inscripcion = forms.BooleanField(
        required=False,
        label="Cobrar inscripción +$50",
    )
    metodo_pago_otro = forms.CharField(
        required=False,
        label="Especifica el método de pago",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Pago
        fields = ["fecha_pago", "reajuste_inicial", "otros_ajustes", "metodo_pago", "notas"]
        widgets = {
            "fecha_pago": forms.DateInput(
                attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d"
            ),
            "reajuste_inicial": forms.RadioSelect(choices=REAJUSTE_INICIAL_CHOICES),
            "otros_ajustes": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "metodo_pago": forms.Select(choices=METODO_PAGO_CHOICES, attrs={"class": "form-select"}),
            "notas": forms.Textarea(attrs={"class": "form-control", "rows": 2}),
        }

    def __init__(
        self,
        *args,
        mostrar_periodo=False,
        periodos_candidatos=None,
        mostrar_reajuste_inicial=False,
        mostrar_inscripcion=False,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        if mostrar_periodo:
            self.fields["periodo_seleccionado"].choices = [
                (inicio.isoformat(), etiqueta_periodo(inicio, fin))
                for inicio, fin in (periodos_candidatos or [])
            ]
        else:
            del self.fields["periodo_seleccionado"]

        if mostrar_reajuste_inicial:
            if self.instance.pk is None:
                # El initial que ModelForm toma de una instancia nueva es el
                # default crudo del modelo (Decimal("0"), sin cuantizar a 2
                # decimales), que no coincide en texto con el choice
                # "0.00" y por eso "Sin reajuste" no quedaba preseleccionado.
                self.initial["reajuste_inicial"] = Decimal("0.00")
        else:
            del self.fields["reajuste_inicial"]

        if not mostrar_inscripcion:
            del self.fields["cobrar_inscripcion"]

        self.fields["otros_ajustes"].required = False
        self.fields["metodo_pago"].required = False

    def clean(self):
        cleaned_data = super().clean()

        if cleaned_data.get("otros_ajustes") is None:
            cleaned_data["otros_ajustes"] = Decimal("0")

        if cleaned_data.get("metodo_pago") == "otro":
            detalle = (cleaned_data.get("metodo_pago_otro") or "").strip()
            if not detalle:
                self.add_error("metodo_pago_otro", "Especifica el método de pago.")
            else:
                cleaned_data["metodo_pago"] = detalle

        return cleaned_data
