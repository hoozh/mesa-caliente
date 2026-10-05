/* Filtros y buscador de las propuestas de diseño. Sin librerías.
   Cada tarjeta lleva sus datos en atributos data-*; los grupos (días y destacado)
   se ocultan cuando se quedan sin tarjetas visibles. */
(function () {
  "use strict";

  function normalizar(texto) {
    return (texto || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  }

  var tarjetas = Array.prototype.slice.call(document.querySelectorAll("[data-tarjeta]"));
  var grupos = Array.prototype.slice.call(document.querySelectorAll("[data-grupo]"));
  var buscar = document.getElementById("f-buscar");
  var clasif = document.getElementById("f-clasif");
  var fuente = document.getElementById("f-fuente");
  var dia = document.getElementById("f-dia");
  var latam = document.getElementById("f-latam");
  var limpiar = document.getElementById("f-limpiar");
  var contador = document.getElementById("f-contador");
  var mostrar = document.getElementById("f-mostrar");
  var caja = document.querySelector(".filtros");
  var vacio = document.getElementById("f-vacio");
  var total = tarjetas.length;

  function aplicar() {
    var palabras = normalizar(buscar.value).split(/\s+/).filter(Boolean);
    var c = clasif.value, f = fuente.value, d = dia.value, l = latam.checked;
    var visibles = 0;
    tarjetas.forEach(function (t) {
      var ok = (!c || t.dataset.clasif === c) &&
               (!f || t.dataset.fuentes.split("|").indexOf(f) !== -1) &&
               (!d || t.dataset.dia === d) &&
               (!l || t.dataset.latam === "si") &&
               palabras.every(function (p) { return t.dataset.buscar.indexOf(p) !== -1; });
      t.hidden = !ok;
      if (ok) { visibles += 1; }
    });
    grupos.forEach(function (g) {
      g.hidden = !g.querySelector("[data-tarjeta]:not([hidden])");
    });
    var activos = palabras.length || c || f || d || l;
    contador.textContent = activos
      ? visibles + " de " + total + (total === 1 ? " tarjeta" : " tarjetas")
      : total + (total === 1 ? " tarjeta en el historial" : " tarjetas en el historial");
    limpiar.disabled = !activos;
    if (vacio) { vacio.hidden = visibles !== 0; }
  }

  var espera;
  buscar.addEventListener("input", function () {
    clearTimeout(espera);
    espera = setTimeout(aplicar, 120);
  });
  [clasif, fuente, dia, latam].forEach(function (el) { el.addEventListener("change", aplicar); });

  limpiar.addEventListener("click", function () {
    buscar.value = ""; clasif.value = ""; fuente.value = ""; dia.value = ""; latam.checked = false;
    aplicar();
    buscar.focus();
  });

  if (mostrar && caja) {
    mostrar.addEventListener("click", function () {
      var abierto = caja.classList.toggle("filtros--abierto");
      mostrar.setAttribute("aria-expanded", abierto ? "true" : "false");
    });
  }

  /* Resúmenes plegados en la propuesta compacta. */
  document.addEventListener("click", function (e) {
    var boton = e.target.closest("[data-mas]");
    if (!boton) { return; }
    var tarjeta = boton.closest("[data-tarjeta]");
    var abierta = tarjeta.classList.toggle("abierta");
    boton.setAttribute("aria-expanded", abierta ? "true" : "false");
    boton.textContent = abierta ? "Ver menos" : "Ver más";
  });

  /* "Ver más" solo cuando el resumen está recortado. */
  function revisarRecortes() {
    document.querySelectorAll("[data-mas]").forEach(function (boton) {
      var tarjeta = boton.closest("[data-tarjeta]");
      if (tarjeta.classList.contains("abierta") || tarjeta.hidden) { return; }
      var resumen = tarjeta.querySelector(".fila__resumen");
      boton.hidden = !resumen || resumen.scrollHeight <= resumen.clientHeight + 1;
    });
  }

  aplicar();
  revisarRecortes();
  var esperaRecorte;
  window.addEventListener("resize", function () { clearTimeout(esperaRecorte); esperaRecorte = setTimeout(revisarRecortes, 150); });
  [buscar, clasif, fuente, dia, latam, limpiar].forEach(function (el) {
    el.addEventListener(el === buscar ? "input" : (el === limpiar ? "click" : "change"), function () {
      setTimeout(revisarRecortes, 160);
    });
  });
})();
