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
- Pillow (requerido por ImageField de fotografía de cliente).
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

También ya existen configuradas:

    MEDIA_URL = 'media/'
    MEDIA_ROOT = BASE_DIR / "media"

Y en `gym_control/urls.py`, servidas en desarrollo bajo `if settings.DEBUG`.

JavaScript propio por pantalla en `static/js/` (un archivo por
formulario: `cliente_formulario.js`, `pago_formulario.js`).

Lógica de negocio en módulos de servicio dentro de `gestion/`, sin
mezclarla en vistas ni modelos: `gestion/membresia.py` (vencimientos,
mora, tarifas) y `gestion/asistencias.py` (check-in, duplicados,
snapshot histórico).

Autenticación del panel interno: `LoginView`/`LogoutView` registradas
explícitamente en `gym_control/urls.py` bajo `/cuenta/login/` y
`/cuenta/logout/` (no se incluye `django.contrib.auth.urls` completo).
`LOGIN_URL`, `LOGIN_REDIRECT_URL` y `LOGOUT_REDIRECT_URL` ya
configurados en `settings.py`.

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

## Decisiones consolidadas (Sprints 1-3)

Estas decisiones ya están implementadas y probadas. No son parte de un
sprint específico: son contexto permanente para no reintroducir
problemas ya resueltos.

- **Selección explícita del primer periodo**: para el primer pago de un
  cliente, el encargado elige entre 3 periodos candidatos (anterior,
  actual, siguiente respecto a hoy). El sistema nunca preselecciona ni
  asume un periodo por defecto.
- **Edición/eliminación limitada al pago más reciente**: solo se puede
  editar o eliminar el pago más reciente de un cliente, para no romper
  la secuencia histórica de periodos y mora.
- **Inscripción y reajuste inicial se derivan del historial, no son
  banderas manuales**: `inscripcion_aplicada` y
  `reajuste_inicial_aplicado` siempre se recalculan desde los pagos
  existentes; si se borra el único pago de un cliente, ambas regresan a
  False automáticamente.
- **Mensualidad no editable manualmente**: el monto siempre se deriva de
  `tipo_tarifa` en el servidor, nunca se acepta directamente del
  formulario.
- **El servidor es la fuente de verdad del total**: cualquier cálculo en
  JavaScript es solo vista previa; `mora` y `total_pagado` siempre se
  recalculan en `Pago.save()`, respaldado por un `CheckConstraint` en BD.
- **`DIAS_POR_VENCER = 3`** (umbral de "por vencer") es una decisión del
  sistema, no una regla del reglamento del gimnasio — a diferencia de
  los 3 días de gracia, que sí lo son.
- **Patrón visual "tarjeta seleccionable"** (`.opcion-tarjeta`, radio o
  checkbox nativo + clase `is-seleccionada` por JS mínimo): patrón
  disponible y reutilizable para reemplazar selects genéricos en
  formularios pensados para tablet/móvil cuando sea adecuado. No es una
  obligación para todos los próximos sprints.
- **Captura de fotografía nativa**: se mantiene un solo campo de
  fotografía (`input[type=file]`), pero la interfaz ofrece dos acciones
  independientes sobre ese mismo campo:
  - "Tomar foto": agrega temporalmente el atributo `capture="environment"`
    antes de abrir el selector.
  - "Seleccionar archivo/galería": abre el selector sin `capture`.

  Esto evita forzar siempre la cámara y deja elegir galería o
  explorador de archivos cuando conviene.
- **Dispositivo recordado por cookie firmada, no sesión de Django**:
  cookie `gym_cliente_recordado`, guarda el pk interno del cliente
  (nunca `id_acceso`), firmada con un salt propio del proyecto,
  `max_age` de 180 días, `httponly=True`, `samesite="Lax"`,
  `secure=False` en desarrollo (cambiar a `True` en producción bajo
  HTTPS). Se eligió sobre una sesión persistente porque no requiere
  tabla de sesiones ni tarea de limpieza periódica, y modela mejor la
  semántica de "este dispositivo recuerda a este cliente" que la de
  "sesión iniciada".
- **Snapshot histórico de `Asistencia` congelado al momento del
  registro**: `estado_membresia`, `fecha_vencimiento` y
  `mora_al_ingresar` se calculan una sola vez con
  `asistencias.capturar_estado_membresia()` y se guardan como valores
  propios del modelo; nunca se recalculan después, ni siquiera si el
  cliente paga posteriormente. Mismo principio que ya aplican
  `Pago.mora` y `Pago.total_pagado`.
- **Ventana de duplicados de asistencia, exclusiva por el límite
  inferior**: menos de 5 minutos desde la última asistencia del
  cliente es duplicado; exactamente 5 minutos o más ya permite un
  nuevo registro. Se implementa en `gestion/asistencias.py` dentro de
  `transaction.atomic()`, aceptado como mitigación suficiente para
  SQLite en desarrollo local — no garantiza exclusión real ante
  concurrencia (en Postgres con tráfico concurrente real haría falta
  `select_for_update`).
- **Autenticación del panel interno con Django auth integrado, sin
  reimplementar nada propio**: `LoginView`/`LogoutView` registradas
  explícitamente en `/cuenta/login/` y `/cuenta/logout/` (no se incluye
  `django.contrib.auth.urls` completo, para no exponer rutas de cambio
  o recuperación de contraseña que no existen todavía). Cualquier
  usuario autenticado puede acceder hoy a todo el panel interno; los
  usuarios se crean manualmente con `python manage.py createsuperuser`
  o desde el admin; no existen roles ni permisos personalizados.

---

## Sprint anterior: Sprint 3 — Check-in y asistencias (cerrado)

### Objetivo

Permitir que un cliente registre su entrada desde el QR fijo del gimnasio usando su ID de 4 dígitos, y que el encargado consulte las asistencias recientes y el estado de membresía que tenía cada cliente al ingresar.

### Reglas de negocio

- El check-in es una pantalla pública.
- El cliente se identifica con su id_acceso de 4 dígitos.
- Solo clientes existentes y activos pueden registrar asistencia.
- La asistencia se registra aunque la membresía esté vencida.
- El sistema informa el estado de membresía, pero no bloquea el acceso.
- No registrar una nueva asistencia del mismo cliente durante los 5 minutos posteriores a su último registro.
- Un intento duplicado reciente debe mostrar un mensaje claro y no considerarse un error grave.
- Cada asistencia debe conservar una fotografía histórica del estado del cliente al ingresar:
  - estado_membresia;
  - fecha_vencimiento;
  - mora_al_ingresar.
- Los datos históricos de la asistencia no deben cambiar si el cliente paga posteriormente.
- El listado del encargado se ordena de la más reciente a la más antigua.
- No usar WebSockets ni actualización automática en tiempo real; basta con recargar la pantalla.

### Dispositivo recordado

Implementado con cookie firmada (`gym_cliente_recordado`), no con sesión
persistente de Django. Detalle completo de la decisión y sus parámetros
en "Decisiones consolidadas".

- El cliente puede elegir "Recordarme en este dispositivo" en su primer check-in.
- En visitas posteriores, se muestra su nombre y fotografía y un botón para registrar entrada.
- Incluye la opción "No soy esta persona".

### Datos de Asistencia

- cliente.
- fecha_hora.
- estado_membresia.
- fecha_vencimiento opcional.
- mora_al_ingresar.
- origen: cliente o manual.

### Alcance

- Modelo Asistencia.
- Migración.
- Django Admin.
- Reglas de registro y prevención de duplicados (`gestion/asistencias.py`).
- Pantalla pública de check-in (`/checkin/`).
- Identificación por ID.
- Confirmación de asistencia.
- Dispositivo recordado (cookie firmada).
- Autenticación propia del panel interno (`/cuenta/login/`, `/cuenta/logout/`, `@login_required`).
- Listado de asistencias para el encargado (`/asistencias/`), con búsqueda por nombre/ID, filtro por fecha y paginación.
- Fotografía del cliente en el panel.
- Integración con perfil del cliente cuando corresponde.
- Diseño mobile-first.
- Pruebas automatizadas.

### Descartado definitivamente

Estas dos decisiones son de alcance permanente, no trabajo pendiente:

- **Registro manual de asistencia por el encargado**: no se implementó
  y no se implementará.
- **Generación de QR**: la generación de QR no se implementará dentro
  de la aplicación. Cuando exista la URL pública definitiva de
  `/checkin/`, el QR se generará mediante una herramienta externa y se
  imprimirá para recepción.

### Fuera del alcance

No implementar todavía (puede retomarse en un sprint futuro):

- Reconocimiento facial.
- Geolocalización.
- Torniquetes.
- WhatsApp.
- WebSockets.
- Dashboard con estadísticas.
- Reportes avanzados.
- Exportación.
- Múltiples sucursales.
- Roles y permisos personalizados.
- Recuperación de contraseña, cambio de contraseña.
- Modelo de usuario personalizado; creación de usuarios desde la aplicación.

---

## Sprint actual: por definir

El Sprint 3 quedó cerrado. El alcance del Sprint 4 todavía no se ha
planeado ni acordado.

---

## Sprint anterior: Sprint 2 — Pagos, tarifas y membresías (cerrado)

### Objetivo

Automatizar el control de pagos y calcular automáticamente vencimiento, periodo de gracia, mora y total sugerido.

### Reglas de negocio

**Tarifas:**

- General: 330 MXN.
- Estudiante: 280 MXN.
- Inscripción inicial: 50 MXN, normalmente una sola vez.

**Fecha fija:**

- Cada cliente tiene una fecha fija de pago: día 1 o día 15.
- La selecciona el encargado al dar de alta o editar al cliente.
- Una vez asignada, no cambia automáticamente.

**Reajuste inicial:**

- Puede ser +50, -50 o 0 MXN.
- Se aplica una sola vez al incorporar al cliente.
- Debe registrarse como concepto separado.

**Periodo de gracia:**

- Hay 3 días naturales completos posteriores al vencimiento sin penalización.
- Ejemplo: vencimiento día 1 → días 2, 3 y 4 son gracia → la mora inicia el día 5.

**Mora:**

- 10 MXN por cada día natural de retraso después del periodo de gracia.
- El pago tardío no modifica la fecha fija del cliente.
- Excepción: el primer pago de un cliente nunca genera mora automática,
  sin importar qué tan alejada quede la fecha de pago del inicio del
  periodo elegido — porque ese pago es el que establece el periodo, no
  uno que lo incumple. La mora normal aplica desde el segundo pago.

**Estados:**

- Sin pagos (cliente sin ningún pago registrado todavía).
- Vigente.
- Por vencer.
- En periodo de gracia.
- Vencida con mora.

### Datos adicionales de Cliente

- tipo_tarifa: general o estudiante.
- dia_pago: 1 o 15.
- Inscripción registrada/aplicada.
- Reajuste inicial aplicado.

### Datos de Pago

- cliente.
- fecha_pago.
- mensualidad_base.
- inscripcion.
- reajuste_inicial.
- mora.
- otros_ajustes opcionales.
- total_pagado.
- metodo_pago opcional.
- periodo cubierto.
- fecha_registro automática.
- notas opcionales.

### Alcance

- Ampliar modelo Cliente.
- Crear modelo Pago.
- Migraciones.
- Django Admin.
- Lógica de vencimiento.
- Periodo de gracia.
- Mora.
- Total sugerido.
- Registro e historial de pagos.
- Edición/eliminación con recálculo.
- Integración en perfil.
- Diseño mobile-first.
- Pruebas de reglas de negocio.

### Fuera del alcance

No implementar todavía:

- Asistencias.
- Check-in.
- QR.
- Dispositivo recordado.
- Dashboard con estadísticas.
- WhatsApp.
- Pagos en línea.

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
9. No agregar funcionalidades fuera del sprint actual.
10. No duplicar configuraciones existentes.

Validación mínima:

    python manage.py check

Cuando corresponda:

    python manage.py test

---

## Estado actual conocido

- Sprint 0 (base del proyecto) y Sprint 1 (gestión de clientes):
  completos.
- Sprint 2 (pagos, tarifas y membresías): completo — modelos, admin,
  migraciones, lógica de vencimiento/gracia/mora/total sugerido,
  registro/edición/eliminación/historial de pagos, integración en
  perfil de cliente, diseño mobile-first y pruebas de reglas de
  negocio.
- Sprint 3 (check-in y asistencias): completo — modelo Asistencia con
  snapshot histórico, check-in público con prevención de duplicados,
  dispositivo recordado por cookie firmada, autenticación propia del
  panel interno y listado de asistencias con búsqueda/filtro/paginación
  para el encargado.
  - Registro manual de asistencia y generación de QR quedaron
    descartados definitivamente (ver Sprint 3 cerrado).
  - Autenticación: cualquier usuario autenticado puede acceder hoy a
    todo el panel interno; los usuarios se crean manualmente
    (`python manage.py createsuperuser` o desde el admin); no existen
    todavía roles ni permisos personalizados.
- 137 pruebas automatizadas pasan (`python manage.py test`).
- `python manage.py check` no reporta problemas.
- Migraciones aplicadas: 0001, 0002, 0003, 0004 — sin cambios de modelo
  pendientes.
- El proyecto se ejecuta correctamente en local.
