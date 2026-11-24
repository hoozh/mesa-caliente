var tarjeta = boton.closest("[data-tarjeta]");
    var abierta = tarjeta.classList.toggle("abierta");
    boton.setAttribute("aria-expanded", abierta ? "true" : "false");
    boton.textContent = abierta ? "Ver menos" : "Ver más";
  });
})();