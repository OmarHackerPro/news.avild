/**
 * Priority pills + main Filter dropdown.
 * Uses event delegation — safe after dynamic content injection.
 */
(function() {
  'use strict';

  // Globals read by news-grid.js itemPassesFilter
  window.selectedPriority = 'all';
  window.mainFilterTime   = '24h';
  window.mainFilterType   = null;
  window.mainFilterSource = 'all';

  function reapply() {
    if (typeof window.applyFilters === 'function') window.applyFilters();
  }

  function updateFilterCount() {
    var count = 0;
    if (window.mainFilterTime !== '24h') count++;
    if (window.mainFilterType !== null)  count++;
    if (window.mainFilterSource !== 'all') count++;
    var badge = document.getElementById('mainFilterCount');
    if (!badge) return;
    badge.textContent = count;
    badge.hidden = count === 0;
  }

  /* ── Priority pills ── */
  document.addEventListener('click', function(e) {
    var pill = e.target.closest('.priority-pill');
    if (!pill) return;
    document.querySelectorAll('.priority-pill').forEach(function(p) {
      p.classList.remove('active');
    });
    pill.classList.add('active');
    window.selectedPriority = pill.getAttribute('data-priority') || 'all';
    reapply();
  });

  /* ── Filter main button toggle ── */
  document.addEventListener('click', function(e) {
    var trigger  = document.getElementById('mainFilterTrigger');
    var dropdown = document.getElementById('mainFilterDropdown');
    if (!trigger || !dropdown) return;

    if (e.target === trigger || trigger.contains(e.target)) {
      var isOpen = dropdown.classList.contains('open');
      dropdown.classList.toggle('open', !isOpen);
      trigger.setAttribute('aria-expanded', String(!isOpen));
      return;
    }
    if (!dropdown.contains(e.target)) {
      dropdown.classList.remove('open');
      trigger.setAttribute('aria-expanded', 'false');
    }
  });

  /* ── Filter panel option buttons ── */
  document.addEventListener('click', function(e) {
    var opt = e.target.closest('.fmd-opt');
    if (!opt) return;
    var filterKey = opt.getAttribute('data-fmd-filter');
    var filterVal = opt.getAttribute('data-fmd-value');

    document.querySelectorAll('.fmd-opt[data-fmd-filter="' + filterKey + '"]').forEach(function(s) {
      s.classList.remove('active');
    });
    opt.classList.add('active');

    if (filterKey === 'time')   window.mainFilterTime   = filterVal;
    if (filterKey === 'type')   window.mainFilterType   = (filterVal === 'all') ? null : filterVal;
    if (filterKey === 'source') window.mainFilterSource = filterVal;

    updateFilterCount();
    reapply();
  });

  /* ── Reset button ── */
  document.addEventListener('click', function(e) {
    if (!e.target.closest('#mainFilterReset')) return;
    window.mainFilterTime   = '24h';
    window.mainFilterType   = null;
    window.mainFilterSource = 'all';

    document.querySelectorAll('.fmd-opt').forEach(function(opt) {
      var k = opt.getAttribute('data-fmd-filter');
      var v = opt.getAttribute('data-fmd-value');
      var isDefault = (k === 'time' && v === '24h') || (k === 'type' && v === 'all') || (k === 'source' && v === 'all');
      opt.classList.toggle('active', isDefault);
    });

    updateFilterCount();
    reapply();
  });

})();
