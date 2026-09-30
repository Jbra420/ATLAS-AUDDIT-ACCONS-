/*
 * calendario.js — Calendario del paso 1 del Requerimiento inicial.
 *
 * Mejora los <input type="date" class="dp-input"> que están dentro de un
 * <div class="dp" data-dp="single|range">: los oculta (siguen enviando la
 * fecha AAAA-MM-DD en el formulario) y muestra un botón con la fecha en
 * español y un calendario desplegable. Sin JavaScript quedan los campos de
 * fecha nativos del navegador.
 *
 *   data-dp="single"    una fecha; respeta min y max del input.
 *   data-dp="range"     dos inputs (desde, hasta): primer clic el inicio,
 *                       segundo el fin; data-dp-texto="id" muestra el texto
 *                       del rango ("entre el 15 de octubre y el 15 de
 *                       diciembre").
 *   data-dp-anio="id"   (en el input) limita la fecha al año que tiene el
 *                       campo con ese id y avisa si queda fuera.
 *
 * Teclado: flechas (día / semana), Inicio/Fin (semana), RePág/AvPág (mes;
 * con Mayús, año), Intro o Espacio (elegir), Esc (cerrar).
 *
 * También mantiene al día la hoja de datos del paso 1 (celdas con
 * data-resumen) mientras el auditor edita el formulario.
 */
(function () {
  'use strict';

  var MESES = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto', 'septiembre',
    'octubre', 'noviembre', 'diciembre'];
  var DIAS = ['lunes', 'martes', 'miércoles', 'jueves', 'viernes', 'sábado', 'domingo'];
  var ICONO = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" ' +
    'height="18" rx="2"></rect><line x1="16" y1="2" x2="16" y2="6"></line><line x1="8" y1="2" x2="8" ' +
    'y2="6"></line><line x1="3" y1="10" x2="21" y2="10"></line></svg>';
  var FLECHA = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" ' +
    'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polyline points="15 18 9 12 15 6">' +
    '</polyline></svg>';
  var contador = 0;

  // ── Fechas (siempre locales, sin horas) ───────────────────────────────

  function dos(n) { return (n < 10 ? '0' : '') + n; }
  function aIso(f) { return f.getFullYear() + '-' + dos(f.getMonth() + 1) + '-' + dos(f.getDate()); }
  function deIso(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(iso || '');
    if (!m) return null;
    var f = new Date(+m[1], +m[2] - 1, +m[3]);
    return f.getMonth() === +m[2] - 1 ? f : null;
  }
  function hoyIso() { return aIso(new Date()); }
  function sumarDias(iso, n) { var f = deIso(iso); f.setDate(f.getDate() + n); return aIso(f); }
  function sumarMeses(iso, n) {
    var f = deIso(iso), dia = f.getDate();
    var destino = new Date(f.getFullYear(), f.getMonth() + n, 1);
    var ultimo = new Date(destino.getFullYear(), destino.getMonth() + 1, 0).getDate();
    destino.setDate(Math.min(dia, ultimo));
    return aIso(destino);
  }
  function diaSemana(iso) { return (deIso(iso).getDay() + 6) % 7; } // 0 = lunes

  // "01 de septiembre de 2026": el formato de la carta (services.requerimiento.fecha_larga).
  function fechaLarga(iso) {
    var f = deIso(iso);
    return f ? dos(f.getDate()) + ' de ' + MESES[f.getMonth()] + ' de ' + f.getFullYear() : '';
  }
  function fechaCorta(iso) {
    var f = deIso(iso);
    return f ? f.getDate() + ' ' + MESES[f.getMonth()].slice(0, 3) + ' ' + f.getFullYear() : '';
  }
  // Igual que services.requerimiento.texto_inventario.
  function textoInventario(desde, hasta) {
    var a = deIso(desde), b = deIso(hasta);
    if (!a || !b) return '';
    var cruza = a.getFullYear() !== b.getFullYear();
    function dia(f) {
      return f.getDate() + ' de ' + MESES[f.getMonth()] + (cruza ? ' de ' + f.getFullYear() : '');
    }
    if (desde === hasta) return 'el ' + dia(a);
    return 'entre el ' + dia(a) + ' y el ' + dia(b);
  }

  function crear(etiqueta, clase, html) {
    var el = document.createElement(etiqueta);
    if (clase) el.className = clase;
    if (html) el.innerHTML = html;
    return el;
  }

  // ── Calendario ─────────────────────────────────────────────────────────

  function Calendario(raiz) {
    this.raiz = raiz;
    this.rango = raiz.getAttribute('data-dp') === 'range';
    this.inputs = Array.prototype.slice.call(raiz.querySelectorAll('.dp-input'));
    if (!this.inputs.length || (this.rango && this.inputs.length < 2)) return;
    this.id = 'dp-' + (++contador);
    this.anioFuente = document.getElementById(this.inputs[0].getAttribute('data-dp-anio') || '');
    this.inicioPendiente = null; // rango: inicio elegido, falta el fin
    this.construir();
    this.actualizarBoton();
  }

  Calendario.prototype.construir = function () {
    var self = this;
    this.inputs.forEach(function (input) { input.required = false; input.tabIndex = -1; });

    this.boton = crear('button', 'dp-trigger');
    this.boton.type = 'button';
    this.boton.id = this.id + '-boton';
    this.boton.setAttribute('aria-haspopup', 'dialog');
    this.boton.setAttribute('aria-expanded', 'false');
    this.boton.setAttribute('aria-controls', this.id + '-panel');
    this.boton.innerHTML = ICONO + '<span class="dp-texto"></span>';
    this.texto = this.boton.querySelector('.dp-texto');

    this.limpiar = crear('button', 'dp-clear', '&times;');
    this.limpiar.type = 'button';
    this.limpiar.setAttribute('aria-label', this.rango ? 'Borrar el rango' : 'Borrar la fecha');

    this.error = crear('div', 'dp-error');
    this.error.setAttribute('role', 'alert');

    this.panel = crear('div', 'dp-panel');
    this.panel.id = this.id + '-panel';
    this.panel.hidden = true;
    this.panel.setAttribute('role', 'dialog');
    this.panel.setAttribute('aria-label', this.rango ? 'Elegir rango de fechas' : 'Elegir fecha');

    var cabecera = crear('div', 'dp-head');
    this.anterior = crear('button', 'dp-nav', FLECHA);
    this.anterior.type = 'button';
    this.anterior.setAttribute('aria-label', 'Mes anterior');
    this.siguiente = crear('button', 'dp-nav dp-nav-next', FLECHA);
    this.siguiente.type = 'button';
    this.siguiente.setAttribute('aria-label', 'Mes siguiente');
    this.selMes = crear('select', 'dp-select');
    this.selMes.setAttribute('aria-label', 'Mes');
    MESES.forEach(function (mes, i) {
      var opcion = crear('option');
      opcion.value = i;
      opcion.textContent = mes.charAt(0).toUpperCase() + mes.slice(1);
      self.selMes.appendChild(opcion);
    });
    this.selAnio = crear('select', 'dp-select');
    this.selAnio.setAttribute('aria-label', 'Año');
    cabecera.appendChild(this.anterior);
    cabecera.appendChild(this.selMes);
    cabecera.appendChild(this.selAnio);
    cabecera.appendChild(this.siguiente);

    this.rejilla = crear('div', 'dp-grid');
    this.rejilla.setAttribute('role', 'grid');
    this.pie = crear('div', 'dp-foot');
    this.estado = crear('span', 'dp-status');
    this.estado.setAttribute('aria-live', 'polite');
    this.hoy = crear('button', 'dp-link', 'Hoy');
    this.hoy.type = 'button';
    this.borrar = crear('button', 'dp-link', 'Limpiar');
    this.borrar.type = 'button';
    this.pie.appendChild(this.estado);
    if (!this.rango) this.pie.appendChild(this.hoy);
    this.pie.appendChild(this.borrar);

    this.panel.appendChild(cabecera);
    this.panel.appendChild(this.rejilla);
    this.panel.appendChild(this.pie);

    var control = crear('div', 'dp-control');
    control.appendChild(this.boton);
    control.appendChild(this.limpiar);
    this.raiz.appendChild(control);
    this.raiz.appendChild(this.panel);
    this.raiz.appendChild(this.error);
    this.raiz.classList.add('dp-ready');

    // La etiqueta del campo apunta al botón visible.
    var etiqueta = document.querySelector('label[for="' + this.inputs[0].id + '"]');
    if (etiqueta) {
      etiqueta.htmlFor = this.boton.id;
      if (!etiqueta.id) etiqueta.id = this.id + '-etiqueta';
      this.texto.id = this.id + '-texto';
      this.boton.setAttribute('aria-labelledby', etiqueta.id + ' ' + this.texto.id);
    }

    this.boton.addEventListener('click', function () { self.panel.hidden ? self.abrir() : self.cerrar(true); });
    this.limpiar.addEventListener('click', function () { self.poner('', ''); self.boton.focus(); });
    this.borrar.addEventListener('click', function () { self.poner('', ''); self.inicioPendiente = null; self.pintar(); });
    this.hoy.addEventListener('click', function () {
      if (self.permitido(hoyIso())) { self.poner(hoyIso()); self.cerrar(true); }
    });
    this.anterior.addEventListener('click', function () { self.moverVista(-1); });
    this.siguiente.addEventListener('click', function () { self.moverVista(1); });
    this.selMes.addEventListener('change', function () {
      self.enfoque = self.ajustar(self.selAnio.value + '-' + dos(+self.selMes.value + 1) + '-01');
      self.pintar();
    });
    this.selAnio.addEventListener('change', function () {
      self.enfoque = self.ajustar(self.selAnio.value + '-' + dos(+self.selMes.value + 1) + '-01');
      self.pintar();
    });
    this.rejilla.addEventListener('click', function (e) {
      var dia = e.target.closest('.dp-day');
      if (dia && !dia.disabled) self.elegir(dia.getAttribute('data-iso'));
    });
    this.rejilla.addEventListener('keydown', function (e) { self.teclado(e); });
    if (this.rango) {
      this.rejilla.addEventListener('mouseover', function (e) {
        var dia = e.target.closest('.dp-day');
        if (dia) self.vistaPrevia(dia.getAttribute('data-iso'));
      });
    }
    this.panel.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { e.preventDefault(); self.cerrar(true); }
    });
    this.raiz.addEventListener('focusout', function (e) {
      if (!self.panel.hidden && e.relatedTarget && !self.raiz.contains(e.relatedTarget)) self.cerrar(false);
    });
    document.addEventListener('pointerdown', function (e) {
      if (!self.panel.hidden && !self.raiz.contains(e.target)) self.cerrar(false);
    });
    if (this.anioFuente) {
      this.anioFuente.addEventListener('input', function () { self.aplicarAnio(); self.actualizarBoton(); });
      this.aplicarAnio();
    }
  };

  // ── Valores y límites ──────────────────────────────────────────────────

  Calendario.prototype.valores = function () {
    return this.inputs.map(function (input) { return deIso(input.value) ? input.value : ''; });
  };

  Calendario.prototype.aplicarAnio = function () {
    var anio = (this.anioFuente.value || '').trim();
    var input = this.inputs[0];
    if (/^\d{4}$/.test(anio)) {
      input.min = anio + '-01-01';
      input.max = anio + '-12-31';
    } else {
      input.removeAttribute('min');
      input.removeAttribute('max');
    }
  };

  Calendario.prototype.limites = function () {
    var input = this.inputs[0];
    return { min: deIso(input.min) ? input.min : '', max: deIso(input.max) ? input.max : '' };
  };

  Calendario.prototype.permitido = function (iso) {
    var l = this.limites();
    return (!l.min || iso >= l.min) && (!l.max || iso <= l.max);
  };

  Calendario.prototype.ajustar = function (iso) {
    var l = this.limites();
    if (l.min && iso < l.min) return l.min;
    if (l.max && iso > l.max) return l.max;
    return iso;
  };

  Calendario.prototype.poner = function (a, b) {
    var self = this;
    var nuevos = this.rango ? [a, b] : [a];
    nuevos.forEach(function (valor, i) {
      if (self.inputs[i].value !== valor) {
        self.inputs[i].value = valor;
        self.inputs[i].dispatchEvent(new Event('change', { bubbles: true }));
      }
    });
    this.actualizarBoton();
  };

  Calendario.prototype.actualizarBoton = function () {
    var v = this.valores();
    var lleno = this.rango ? (v[0] && v[1]) : v[0];
    if (this.rango) {
      this.texto.textContent = lleno ? fechaCorta(v[0]) + '  →  ' + fechaCorta(v[1]) : 'Elija el rango de fechas';
      var destino = document.getElementById(this.raiz.getAttribute('data-dp-texto') || '');
      if (destino) destino.textContent = textoInventario(v[0], v[1]) || 'elija el rango';
    } else {
      this.texto.textContent = lleno ? fechaLarga(v[0]) : 'Elija una fecha';
    }
    this.boton.classList.toggle('dp-vacio', !lleno);
    this.limpiar.hidden = !(v[0] || v[1]);
    var fuera = !this.rango && v[0] && !this.permitido(v[0]);
    this.boton.classList.toggle('dp-invalido', !!fuera);
    this.error.textContent = fuera ? 'La fecha debe estar dentro del año de auditoría (' +
      (this.anioFuente ? this.anioFuente.value : '') + ').' : '';
  };

  // ── Abrir, cerrar y pintar ─────────────────────────────────────────────

  Calendario.prototype.abrir = function () {
    var v = this.valores();
    this.inicioPendiente = null;
    this.enfoque = this.ajustar(v[0] || hoyIso());
    this.panel.hidden = false;
    this.boton.setAttribute('aria-expanded', 'true');
    this.raiz.classList.add('dp-abierto');
    this.pintar();
    this.ubicar();
    this.enfocarDia();
  };

  Calendario.prototype.cerrar = function (devolverFoco) {
    if (this.panel.hidden) return;
    this.panel.hidden = true;
    this.inicioPendiente = null;
    this.boton.setAttribute('aria-expanded', 'false');
    this.raiz.classList.remove('dp-abierto');
    this.actualizarBoton();
    if (devolverFoco) this.boton.focus();
  };

  // Abre hacia arriba si no cabe debajo y lo alinea a la derecha si se sale.
  Calendario.prototype.ubicar = function () {
    this.panel.classList.remove('dp-arriba', 'dp-derecha');
    var caja = this.panel.getBoundingClientRect();
    var boton = this.boton.getBoundingClientRect();
    if (caja.bottom > window.innerHeight && boton.top > caja.height + 12) this.panel.classList.add('dp-arriba');
    if (caja.right > document.documentElement.clientWidth) this.panel.classList.add('dp-derecha');
  };

  Calendario.prototype.moverVista = function (meses) {
    this.enfoque = this.ajustar(sumarMeses(this.enfoque, meses));
    this.pintar();
  };

  Calendario.prototype.pintar = function () {
    var self = this;
    var f = deIso(this.enfoque), anio = f.getFullYear(), mes = f.getMonth();
    var l = this.limites(), v = this.valores(), hoy = hoyIso();

    // Años disponibles: los límites, o una ventana amplia alrededor de hoy.
    var actual = new Date().getFullYear();
    var desde = l.min ? +l.min.slice(0, 4) : Math.min(actual - 10, anio);
    var hasta = l.max ? +l.max.slice(0, 4) : Math.max(actual + 5, anio);
    this.selAnio.innerHTML = '';
    for (var a = desde; a <= hasta; a++) {
      var opcion = crear('option');
      opcion.value = a;
      opcion.textContent = a;
      this.selAnio.appendChild(opcion);
    }
    this.selAnio.value = anio;
    this.selMes.value = mes;
    Array.prototype.forEach.call(this.selMes.options, function (o) {
      var ultimo = aIso(new Date(anio, +o.value + 1, 0));
      var primero = anio + '-' + dos(+o.value + 1) + '-01';
      o.disabled = (l.min && ultimo < l.min) || (l.max && primero > l.max);
    });
    var primeroMes = anio + '-' + dos(mes + 1) + '-01';
    this.anterior.disabled = !!(l.min && sumarDias(primeroMes, -1) < l.min);
    this.siguiente.disabled = !!(l.max && aIso(new Date(anio, mes + 1, 1)) > l.max);

    var inicio = this.rango ? (this.inicioPendiente || v[0]) : v[0];
    var fin = this.rango ? (this.inicioPendiente ? '' : v[1]) : '';
    var html = '<div class="dp-row dp-weekdays" role="row">' + DIAS.map(function (d) {
      return '<span role="columnheader" aria-label="' + d + '">' + d.slice(0, 2) + '</span>';
    }).join('') + '</div>';
    var dia = sumarDias(primeroMes, -diaSemana(primeroMes));
    for (var semana = 0; semana < 6; semana++) {
      html += '<div class="dp-row" role="row">';
      for (var d = 0; d < 7; d++) {
        var fecha = deIso(dia), clases = ['dp-day'];
        if (fecha.getMonth() !== mes) clases.push('dp-fuera');
        if (dia === hoy) clases.push('dp-hoy');
        var elegido = dia === inicio || dia === fin;
        if (elegido) clases.push('dp-elegido');
        if (this.rango && inicio && fin && dia > inicio && dia < fin) clases.push('dp-en-rango');
        if (this.rango && dia === inicio && fin) clases.push('dp-inicio');
        if (this.rango && dia === fin && inicio) clases.push('dp-fin');
        var etiqueta = DIAS[d] + ' ' + fechaLarga(dia) + (dia === hoy ? ', hoy' : '');
        html += '<button type="button" role="gridcell" class="' + clases.join(' ') + '" data-iso="' + dia + '"' +
          ' tabindex="' + (dia === this.enfoque ? '0' : '-1') + '" aria-label="' + etiqueta + '"' +
          ' aria-selected="' + elegido + '"' + (dia === hoy ? ' aria-current="date"' : '') +
          (this.permitido(dia) ? '' : ' disabled') + '>' + fecha.getDate() + '</button>';
        dia = sumarDias(dia, 1);
      }
      html += '</div>';
    }
    this.rejilla.innerHTML = html;
    this.rejilla.setAttribute('aria-label', MESES[mes] + ' de ' + anio);
    this.hoy.disabled = !this.permitido(hoy);

    if (this.rango) {
      this.estado.textContent = this.inicioPendiente ? 'Ahora elija la fecha final'
        : (v[0] && v[1] ? textoInventario(v[0], v[1]) : 'Elija la fecha de inicio');
    } else {
      this.estado.textContent = '';
    }
    self.panel.classList.toggle('dp-eligiendo-fin', !!this.inicioPendiente);
  };

  Calendario.prototype.enfocarDia = function () {
    var boton = this.rejilla.querySelector('[data-iso="' + this.enfoque + '"]');
    if (boton) boton.focus();
  };

  // Rango: mientras se elige el fin, sombrea desde el inicio hasta el día señalado.
  Calendario.prototype.vistaPrevia = function (iso) {
    var inicio = this.inicioPendiente;
    Array.prototype.forEach.call(this.rejilla.querySelectorAll('.dp-day'), function (b) {
      var d = b.getAttribute('data-iso');
      var dentro = !!inicio && ((d > inicio && d < iso) || (d < inicio && d > iso));
      b.classList.toggle('dp-previa', dentro);
      b.classList.toggle('dp-previa-fin', !!inicio && d === iso && d !== inicio);
    });
  };

  Calendario.prototype.elegir = function (iso) {
    if (!this.rango) {
      this.poner(iso);
      this.cerrar(true);
      return;
    }
    if (!this.inicioPendiente) {
      this.inicioPendiente = iso;
      this.enfoque = iso;
      this.pintar();
      this.enfocarDia();
      return;
    }
    var a = this.inicioPendiente, b = iso;
    if (b < a) { var t = a; a = b; b = t; }
    this.inicioPendiente = null;
    this.poner(a, b);
    this.cerrar(true);
  };

  Calendario.prototype.teclado = function (e) {
    var saltos = { ArrowLeft: -1, ArrowRight: 1, ArrowUp: -7, ArrowDown: 7 };
    var destino = null;
    if (e.key in saltos) destino = sumarDias(this.enfoque, saltos[e.key]);
    else if (e.key === 'Home') destino = sumarDias(this.enfoque, -diaSemana(this.enfoque));
    else if (e.key === 'End') destino = sumarDias(this.enfoque, 6 - diaSemana(this.enfoque));
    else if (e.key === 'PageUp') destino = sumarMeses(this.enfoque, e.shiftKey ? -12 : -1);
    else if (e.key === 'PageDown') destino = sumarMeses(this.enfoque, e.shiftKey ? 12 : 1);
    if (destino === null) return;
    e.preventDefault();
    this.enfoque = this.ajustar(destino);
    this.pintar();
    if (this.rango) this.vistaPrevia(this.enfoque);
    this.enfocarDia();
  };

  // ── Hoja de datos en vivo ──────────────────────────────────────────────

  function hojaEnVivo() {
    var form = document.getElementById('req-datos-form');
    if (!form) return;
    function valor(nombre) {
      var campo = form.elements[nombre];
      return campo ? (campo.value || '').trim() : '';
    }
    var calculos = {
      anio_auditado: function () { return valor('anio_auditado'); },
      empresa: function () { return valor('empresa').toUpperCase(); },
      representante_nombre: function () { return valor('representante_nombre').toUpperCase(); },
      fecha_documentos: function () { return fechaLarga(valor('fecha_documentos')); },
      fecha_corte: function () { return fechaLarga(valor('fecha_corte')); },
      inventario: function () { return textoInventario(valor('inventario_desde'), valor('inventario_hasta')); },
      representante_identificacion: function () { return valor('representante_identificacion'); },
      ruc: function () { return valor('ruc'); }
    };
    function refrescar() {
      Array.prototype.forEach.call(document.querySelectorAll('[data-resumen]'), function (celda) {
        var calculo = calculos[celda.getAttribute('data-resumen')];
        if (calculo) celda.textContent = calculo() || '—';
      });
    }
    form.addEventListener('input', refrescar);
    form.addEventListener('change', refrescar);
  }

  function iniciar() {
    var editor = document.getElementById('req-datos-editor');
    if (editor && window.location.hash === '#datos' &&
        new URLSearchParams(window.location.search).has('err')) editor.open = true;
    Array.prototype.forEach.call(document.querySelectorAll('.dp[data-dp]'), function (raiz) {
      new Calendario(raiz); // eslint-disable-line no-new
    });
    hojaEnVivo();
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', iniciar);
  else iniciar();
})();
