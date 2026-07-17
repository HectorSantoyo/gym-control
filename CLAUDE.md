cat > CLAUDE.md <<'EOF'
# Proyecto Gym Control

## Objetivo del proyecto

Construir un sistema web sencillo y profesional para un gimnasio pequeño.

El sistema permitirá:

- Registrar clientes.
- Generar automáticamente un ID único para cada cliente.
- Registrar pagos y calcular vigencias.
- Registrar asistencias mediante un QR fijo.
- Permitir check-in con ID.
- Recordar al cliente en su dispositivo.
- Mostrar al encargado la fotografía y el estado de membresía del cliente.
- Consultar clientes vigentes, próximos a vencer y vencidos.

El proyecto se desarrollará primero en local. El despliegue se decidirá después.

---

## Stack técnico

- Python 3.12.
- Django 5.2.
- SQLite durante desarrollo.
- Django Templates.
- Bootstrap 5.
- CSS propio.
- JavaScript tradicional.
- Git y GitHub.

No usar:

- React.
- Vue.
- Angular.
- Django REST Framework.
- Docker.
- PostgreSQL por ahora.
- APIs externas.
- Librerías innecesarias.

---

## Estructura actual

- Proyecto Django: `gym_control`.
- Aplicación principal: `gestion`.
- Plantillas globales: `templates/`.
- Archivos estáticos globales: `static/`.
- CSS global: `static/css/styles.css`.

La aplicación `gestion` ya está registrada en `INSTALLED_APPS`.

Las URLs de `gestion` ya están conectadas al proyecto.

En `settings.py` ya existen las siguientes configuraciones:

    "DIRS": [BASE_DIR / "templates"]

    STATICFILES_DIRS = [BASE_DIR / "static"]

No duplicar estas configuraciones.

---

## Diseño y UX/UI

El sistema debe ser:

- Mobile-first.
- Responsive.
- Claro.
- Moderno.
- Rápido.
- Sencillo de usar.
- Adecuado para una tablet vieja y teléfonos móviles.

### Paleta base

- Principal: `#2563EB`.
- Fondo: `#F5F7FA`.
- Texto principal: `#1F2937`.
- Tarjetas: `#FFFFFF`.
- Bordes: `#E5E7EB`.
- Vigente: `#16A34A`.
- Por vencer: `#F59E0B`.
- Vencida: `#DC2626`.

Usar componentes consistentes:

- Botones.
- Tarjetas.
- Badges.
- Formularios.
- Navegación.
- Estados visuales.

Evitar:

- Animaciones innecesarias.
- Tablas difíciles de usar en móvil.
- Interfaces saturadas.
- JavaScript complejo.

---

## Sprint actual: Sprint 0

### Objetivo

Dejar una base técnica y visual funcional.

### Alcance

- Estructura global de plantillas.
- Página inicial.
- Navegación básica.
- Estilos globales.
- Bootstrap.
- Configuración local de idioma y zona horaria.
- Configuración de archivos multimedia para futuras fotografías.
- Acceso al panel administrativo.
- Validación responsive.

### Fuera del alcance

No implementar todavía:

- Modelos de clientes.
- Pagos.
- Membresías.
- Asistencias.
- Check-in.
- QR.
- Fotografías de clientes.
- Dashboard real.
- Importación de datos.

---

## Reglas de trabajo

Antes de modificar el proyecto:

1. Inspeccionar los archivos actuales.
2. No sobrescribir cambios existentes sin necesidad.
3. No ampliar el alcance solicitado.
4. Mantener el código simple.
5. Explicar qué archivos se modificarán antes de hacerlo.
6. Al terminar, mostrar un resumen y el diff relevante.
7. Ejecutar o indicar los comandos de validación.
8. No crear migraciones si no hay cambios en modelos.
9. No agregar funcionalidades fuera del Sprint 0.
10. No duplicar configuraciones existentes.

Validación mínima:

    python manage.py check

Cuando corresponda:

    python manage.py test

---

## Estado actual conocido

- El entorno virtual ya existe.
- Django 5.2 está instalado.
- Las migraciones iniciales de Django ya fueron aplicadas.
- El proyecto se ejecuta correctamente.
- La aplicación `gestion` ya fue creada.
- `gestion` ya está registrada en `INSTALLED_APPS`.
- La vista `gestion.views.inicio` ya existe.
- `gestion/urls.py` ya existe.
- `gym_control/urls.py` ya debe incluir las URLs de `gestion`.
- `TEMPLATES["DIRS"]` ya está configurado.
- `STATICFILES_DIRS` ya está configurado.
