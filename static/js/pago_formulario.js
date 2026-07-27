document.addEventListener("DOMContentLoaded", function () {
    function activarToggle(botonId, campoId) {
        var boton = document.getElementById(botonId);
        var campo = document.getElementById(campoId);
        if (!boton || !campo) {
            return;
        }
        boton.addEventListener("click", function () {
            campo.classList.remove("d-none");
            boton.classList.add("d-none");
            var entrada = campo.querySelector("input, select, textarea");
            if (entrada) {
                entrada.focus();
            }
        });
    }

    activarToggle("btn-agregar-ajuste", "otros-ajustes-campo");

    var btnCambiarFecha = document.getElementById("btn-cambiar-fecha");
    var fechaResumen = document.getElementById("fecha-pago-resumen");
    var fechaCampo = document.getElementById("fecha-pago-campo");
    if (btnCambiarFecha && fechaResumen && fechaCampo) {
        btnCambiarFecha.addEventListener("click", function () {
            fechaCampo.classList.remove("d-none");
            fechaResumen.classList.add("d-none");
            var entrada = fechaCampo.querySelector("input");
            if (entrada) {
                entrada.focus();
            }
        });
    }

    var metodoSelect = document.getElementById("id_metodo_pago");
    var metodoOtroCampo = document.getElementById("metodo-otro-campo");
    if (metodoSelect && metodoOtroCampo) {
        var actualizarMetodoOtro = function () {
            if (metodoSelect.value === "otro") {
                metodoOtroCampo.classList.remove("d-none");
            } else {
                metodoOtroCampo.classList.add("d-none");
            }
        };
        metodoSelect.addEventListener("change", actualizarMetodoOtro);
        actualizarMetodoOtro();
    }

    var opcionesTarjeta = document.querySelectorAll(".opcion-tarjeta");
    opcionesTarjeta.forEach(function (opcion) {
        var entrada = opcion.querySelector("input");
        if (!entrada) {
            return;
        }
        var refrescar = function () {
            opcion.classList.toggle("is-seleccionada", entrada.checked);
        };
        entrada.addEventListener("change", function () {
            var grupo = document.querySelectorAll('.opcion-tarjeta input[name="' + entrada.name + '"]');
            grupo.forEach(function (otraEntrada) {
                otraEntrada.closest(".opcion-tarjeta").classList.toggle("is-seleccionada", otraEntrada.checked);
            });
        });
        refrescar();
    });

    var totalEl = document.getElementById("total-estimado");
    if (!totalEl) {
        return;
    }

    var mora = parseFloat(totalEl.dataset.mora) || 0;
    var montoInscripcion = parseFloat(totalEl.dataset.inscripcionMonto) || 0;
    var mensualidadEl = document.getElementById("valor-mensualidad");
    var mensualidad = mensualidadEl ? parseFloat(mensualidadEl.dataset.monto) || 0 : 0;

    var checkInscripcion = document.getElementById("id_cobrar_inscripcion");
    var radiosReajuste = document.querySelectorAll('input[name="reajuste_inicial"]');
    var campoOtrosAjustes = document.getElementById("id_otros_ajustes");

    function formatoMonto(valor) {
        if (Number.isInteger(valor)) {
            return "$" + valor;
        }
        return "$" + valor.toFixed(2);
    }

    function actualizarTotal() {
        var inscripcion = checkInscripcion && checkInscripcion.checked ? montoInscripcion : 0;

        var reajuste = 0;
        radiosReajuste.forEach(function (radio) {
            if (radio.checked) {
                reajuste = parseFloat(radio.value) || 0;
            }
        });

        var otros = campoOtrosAjustes && campoOtrosAjustes.value ? parseFloat(campoOtrosAjustes.value) || 0 : 0;

        var total = mensualidad + inscripcion + reajuste + mora + otros;

        var resumenMensualidad = document.getElementById("resumen-mensualidad");
        if (resumenMensualidad) {
            resumenMensualidad.textContent = formatoMonto(mensualidad);
        }
        var resumenInscripcion = document.getElementById("resumen-inscripcion");
        if (resumenInscripcion) {
            resumenInscripcion.textContent = formatoMonto(inscripcion);
        }
        var resumenReajuste = document.getElementById("resumen-reajuste");
        if (resumenReajuste) {
            resumenReajuste.textContent = formatoMonto(reajuste);
        }
        var resumenOtros = document.getElementById("resumen-otros");
        if (resumenOtros) {
            resumenOtros.textContent = formatoMonto(otros);
        }
        totalEl.textContent = formatoMonto(total);
    }

    if (checkInscripcion) {
        checkInscripcion.addEventListener("change", actualizarTotal);
    }
    radiosReajuste.forEach(function (radio) {
        radio.addEventListener("change", actualizarTotal);
    });
    if (campoOtrosAjustes) {
        campoOtrosAjustes.addEventListener("input", actualizarTotal);
        campoOtrosAjustes.addEventListener("change", actualizarTotal);
    }

    actualizarTotal();
});
