/*
 * lista_editable.js — Equipo de auditoría del paso 1 del Requerimiento inicial.
 *
 * Cada integrante es una fila <li class="req-lista-fila"> con su campo y un
 * botón "Quitar"; el botón ".req-lista-agregar" (visible solo con
 * JavaScript) agrega filas hasta data-max. Sin JavaScript el servidor manda
 * dos filas vacías de reserva; aquí se quitan para no confundir.
 */
(function () {
  'use strict';

  function iniciar(lista) {
    var agregar = lista.parentNode.querySelector('.req-lista-agregar');
    var maximo = parseInt(lista.getAttribute('data-max'), 10) || 12;
    var modelo = lista.querySelector('.req-lista-fila').cloneNode(true);
    modelo.querySelector('input').value = '';

    function filas() { return Array.prototype.slice.call(lista.querySelectorAll('.req-lista-fila')); }

    function actualizar() {
      filas().forEach(function (fila, i) {
        var input = fila.querySelector('input');
        input.setAttribute('aria-label', 'Integrante ' + (i + 1) + ' del equipo');
        fila.querySelector('.req-lista-quitar').setAttribute(
          'aria-label', 'Quitar ' + (input.value.trim() || 'integrante ' + (i + 1)));
      });
      if (agregar) agregar.disabled = filas().length >= maximo;
    }

    function nuevaFila() {
      var fila = modelo.cloneNode(true);
      lista.appendChild(fila);
      actualizar();
      return fila;
    }

    // Las filas vacías de reserva sobran con JavaScript (queda una si no hay nadie).
    var vacias = filas().filter(function (f) { return !f.querySelector('input').value.trim(); });
    vacias.slice(filas().length === vacias.length ? 1 : 0).forEach(function (f) { f.remove(); });

    lista.addEventListener('click', function (e) {
      var boton = e.target.closest('.req-lista-quitar');
      if (!boton) return;
      var fila = boton.closest('.req-lista-fila');
      var siguiente = fila.nextElementSibling || fila.previousElementSibling;
      fila.remove();
      if (!filas().length) siguiente = nuevaFila();
      actualizar();
      (siguiente ? siguiente.querySelector('input') : agregar).focus();
    });
    lista.addEventListener('input', actualizar);
    if (agregar) {
      agregar.hidden = false;
      agregar.addEventListener('click', function () {
        if (filas().length < maximo) nuevaFila().querySelector('input').focus();
      });
    }
    actualizar();
  }

  function arrancar() {
    Array.prototype.forEach.call(document.querySelectorAll('.req-lista'), iniciar);
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancar);
  else arrancar();
})();
