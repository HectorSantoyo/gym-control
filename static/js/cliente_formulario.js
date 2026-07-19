document.addEventListener("DOMContentLoaded", function () {
    var input = document.getElementById("id_fotografia");
    if (!input) {
        return;
    }

    document.querySelectorAll("[data-foto-accion]").forEach(function (boton) {
        boton.addEventListener("click", function () {
            if (boton.dataset.fotoAccion === "camara") {
                input.setAttribute("capture", "environment");
            } else {
                input.removeAttribute("capture");
            }
            input.click();
        });
    });
});
