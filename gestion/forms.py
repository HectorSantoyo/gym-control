from django import forms

from .models import Cliente


class ClienteForm(forms.ModelForm):
    class Meta:
        model = Cliente
        fields = ["nombre_completo", "telefono", "fotografia", "notas", "activo"]
        widgets = {
            "nombre_completo": forms.TextInput(attrs={"class": "form-control"}),
            "telefono": forms.TextInput(attrs={"class": "form-control"}),
            "fotografia": forms.ClearableFileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "notas": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "activo": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
