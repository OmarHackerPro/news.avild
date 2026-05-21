(function () {
  'use strict';

  var gp = document.createElement('div');
  gp.id = 'cs-popover-global';
  document.body.appendChild(gp);

  var active = null;

  function show(wrap) {
    var tmpl = wrap.querySelector('.cs-popover');
    if (!tmpl) return;
    gp.innerHTML = tmpl.innerHTML;

    var r = wrap.getBoundingClientRect();
    var left = r.left + r.width / 2 - 95; // 190px / 2
    left = Math.max(8, Math.min(left, window.innerWidth - 198));
    gp.style.left = left + 'px';
    gp.style.top = (r.bottom + 10) + 'px';
    gp.classList.add('is-visible');
    active = wrap;
  }

  function hide() {
    gp.classList.remove('is-visible');
    active = null;
  }

  document.addEventListener('mouseover', function (e) {
    var wrap = e.target.closest && e.target.closest('.cs-wrap');
    if (!wrap) { if (active) hide(); return; }
    if (wrap === active) return;
    show(wrap);
  });

  document.addEventListener('mouseout', function (e) {
    if (!active) return;
    var to = e.relatedTarget;
    if (!to || !active.contains(to)) hide();
  });
})();
