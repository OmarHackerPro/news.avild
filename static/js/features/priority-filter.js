/**
 * Priority pills + main Filter dropdown + saved filter presets.
 * Uses event delegation — safe after dynamic content injection.
 */
(function() {
  'use strict';

  var PRESETS_KEY = 'cn.filterPresets.v1';

  // Default filter state — used for reset and to compute "is non-default"
  var DEFAULTS = {
    time: '24h',
    severity: 'all',
    state: 'all',
    sort: 'latest'
  };

  // Globals read by news-grid.js
  window.selectedPriority = 'all';
  window.mainFilterTime = DEFAULTS.time;
  window.mainFilterSeverity = DEFAULTS.severity;
  window.mainFilterState = DEFAULTS.state;
  window.currentSort = DEFAULTS.sort;

  // Hide priority pills (backend does not expose priority yet)
  var priorityPills = document.querySelector('.priority-pills');
  if (priorityPills) priorityPills.hidden = true;

  function reapplyFeed() {
    if (typeof window.refreshFeed === 'function') window.refreshFeed();
    else if (typeof window.applyFilters === 'function') window.applyFilters();
  }

  function updateFilterCount() {
    var count = 0;
    if (window.mainFilterTime !== DEFAULTS.time) count++;
    if (window.mainFilterSeverity !== DEFAULTS.severity) count++;
    if (window.mainFilterState !== DEFAULTS.state) count++;
    if (window.currentSort !== DEFAULTS.sort) count++;
    var badge = document.getElementById('mainFilterCount');
    if (!badge) return;
    badge.textContent = count;
    badge.hidden = count === 0;
  }

  /* ── Client-side severity/state filter on rendered cards ── */
  window.applyClientSideFilter = function() {
    var sev = window.mainFilterSeverity;
    var st = window.mainFilterState;
    document.querySelectorAll('.news-card').forEach(function(card) {
      var cSev = card.getAttribute('data-severity') || '';
      var cSt = card.getAttribute('data-state') || '';
      var hideBySev = sev && sev !== 'all' && cSev !== sev;
      var hideBySt = st && st !== 'all' && cSt !== st;
      card.classList.toggle('filtered-out', hideBySev || hideBySt);
    });
  };

  /* ── Sync UI option buttons to current state ── */
  function syncOptionsToState() {
    document.querySelectorAll('.fmd-opt').forEach(function(opt) {
      var k = opt.getAttribute('data-fmd-filter');
      var v = opt.getAttribute('data-fmd-value');
      var active = false;
      if (k === 'time') active = (v === window.mainFilterTime);
      else if (k === 'severity') active = (v === window.mainFilterSeverity);
      else if (k === 'state') active = (v === window.mainFilterState);
      else if (k === 'sort') active = (v === window.currentSort);
      opt.classList.toggle('active', active);
    });
    // Keep the external sort-toggle in sync as well
    document.querySelectorAll('.sort-btn').forEach(function(b) {
      b.classList.toggle('active', b.getAttribute('data-sort') === window.currentSort);
    });
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
    reapplyFeed();
  });

  /* ── Filter main button toggle ── */
  document.addEventListener('click', function(e) {
    var trigger = document.getElementById('mainFilterTrigger');
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
      cancelPresetSaveUI('dropdown');
      cancelRenameUI();
    }
  });

  /* ── Click outside the visible saved-filters bar: close inline UIs ── */
  document.addEventListener('click', function(e) {
    var bar = document.getElementById('savedFiltersBar');
    if (!bar) return;
    if (bar.contains(e.target)) return;
    var saveRow = document.getElementById('savedFiltersSaveRow');
    if (saveRow && !saveRow.hidden) cancelPresetSaveUI('bar');
    if (barRenameState.id) cancelBarRename();
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

    var needsFeedReload = false;
    if (filterKey === 'time') {
      window.mainFilterTime = filterVal;
      needsFeedReload = true;
    } else if (filterKey === 'severity') {
      window.mainFilterSeverity = filterVal;
    } else if (filterKey === 'state') {
      window.mainFilterState = filterVal;
    } else if (filterKey === 'sort') {
      window.currentSort = filterVal;
      needsFeedReload = true;
    }

    updateFilterCount();
    renderPresets();
    if (needsFeedReload) reapplyFeed();
    else window.applyClientSideFilter();

    // Keep the external sort-toggle in sync
    if (filterKey === 'sort') {
      document.querySelectorAll('.sort-btn').forEach(function(b) {
        b.classList.toggle('active', b.getAttribute('data-sort') === filterVal);
      });
    }
  });

  /* ── Reset button ── */
  document.addEventListener('click', function(e) {
    if (!e.target.closest('#mainFilterReset')) return;
    window.mainFilterTime = DEFAULTS.time;
    window.mainFilterSeverity = DEFAULTS.severity;
    window.mainFilterState = DEFAULTS.state;
    window.currentSort = DEFAULTS.sort;
    syncOptionsToState();
    updateFilterCount();
    renderPresets();
    reapplyFeed();
  });

  /* ── Sort toggle (external pills above the dropdown) ── */
  document.addEventListener('click', function(e) {
    var btn = e.target.closest('.sort-btn');
    if (!btn) return;
    document.querySelectorAll('.sort-btn').forEach(function(b) {
      b.classList.remove('active');
    });
    btn.classList.add('active');
    window.currentSort = btn.getAttribute('data-sort') || 'latest';
    syncOptionsToState();
    updateFilterCount();
    renderPresets();
    reapplyFeed();
  });

  /* ═══════════════════════════════════════════════════════════
   *  SAVED FILTER PRESETS
   * ═══════════════════════════════════════════════════════════ */

  function loadPresets() {
    try {
      var raw = localStorage.getItem(PRESETS_KEY);
      if (!raw) return [];
      var arr = JSON.parse(raw);
      return Array.isArray(arr) ? arr : [];
    } catch (e) {
      return [];
    }
  }

  function savePresets(list) {
    try {
      localStorage.setItem(PRESETS_KEY, JSON.stringify(list));
    } catch (e) {}
  }

  function currentFilterSnapshot() {
    return {
      time: window.mainFilterTime,
      severity: window.mainFilterSeverity,
      state: window.mainFilterState,
      sort: window.currentSort
    };
  }

  function presetsEqual(a, b) {
    if (!a || !b) return false;
    return a.time === b.time &&
           a.severity === b.severity &&
           a.state === b.state &&
           a.sort === b.sort;
  }

  function escHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#039;');
  }

  function renderPresets() {
    var presets = loadPresets();
    var current = currentFilterSnapshot();

    // ── Dropdown list ──
    var list = document.getElementById('fmdPresetsList');
    if (list) {
      if (presets.length === 0) {
        list.innerHTML = '<div class="fmd-presets-empty">No saved filters yet. Configure options below and click "Save as preset".</div>';
      } else {
        list.innerHTML = presets.map(function(p) {
          var isActive = presetsEqual(current, p.filters);
          return (
            '<div class="fmd-preset-chip' + (isActive ? ' active' : '') + '" data-preset-id="' + escHtml(p.id) + '">' +
              '<button type="button" class="fmd-preset-apply" title="Apply filter">' +
                '<i class="fas fa-bookmark"></i>' +
                '<span class="fmd-preset-name">' + escHtml(p.name) + '</span>' +
              '</button>' +
              '<button type="button" class="fmd-preset-edit" aria-label="Rename preset" title="Rename"><i class="fas fa-pen"></i></button>' +
              '<button type="button" class="fmd-preset-del" aria-label="Delete preset" title="Delete"><i class="fas fa-times"></i></button>' +
            '</div>'
          );
        }).join('');
      }
    }

    // ── Visible saved-filters bar (main UI) ──
    var bar = document.getElementById('savedFiltersBar');
    var chips = document.getElementById('savedFiltersChips');
    if (bar && chips) {
      bar.hidden = false; // always visible so users can discover the "Save current" button
      if (presets.length === 0) {
        chips.innerHTML = '<span class="sfb-empty">No saved filters yet</span>';
      } else {
        chips.innerHTML = presets.map(function(p) {
          var isActive = presetsEqual(current, p.filters);
          return (
            '<div class="sfb-chip' + (isActive ? ' active' : '') + '" data-preset-id="' + escHtml(p.id) + '">' +
              '<button type="button" class="sfb-chip-apply" title="Apply filter">' +
                '<i class="fas fa-bookmark"></i>' +
                '<span class="sfb-chip-name">' + escHtml(p.name) + '</span>' +
              '</button>' +
              '<button type="button" class="sfb-chip-edit" aria-label="Rename preset" title="Rename"><i class="fas fa-pen"></i></button>' +
              '<button type="button" class="sfb-chip-del" aria-label="Delete preset" title="Delete"><i class="fas fa-times"></i></button>' +
            '</div>'
          );
        }).join('');
      }
    }
  }

  function applyPreset(preset) {
    if (!preset || !preset.filters) return;
    var f = preset.filters;
    window.mainFilterTime = f.time || DEFAULTS.time;
    window.mainFilterSeverity = f.severity || DEFAULTS.severity;
    window.mainFilterState = f.state || DEFAULTS.state;
    window.currentSort = f.sort || DEFAULTS.sort;
    syncOptionsToState();
    updateFilterCount();
    renderPresets();
    reapplyFeed();
  }

  function showPresetSaveUI(target) {
    // target: 'dropdown' | 'bar'
    var rowId = target === 'bar' ? 'savedFiltersSaveRow' : 'fmdPresetSaveRow';
    var inputId = target === 'bar' ? 'sfbPresetName' : 'fmdPresetName';
    var row = document.getElementById(rowId);
    var input = document.getElementById(inputId);
    if (!row || !input) return;
    row.hidden = false;
    input.value = '';
    input.focus();

    if (target === 'bar') {
      // Hide the "+ Save current" trigger while the input row is active
      var addBtn = document.getElementById('savedFiltersAddBtn');
      if (addBtn) addBtn.hidden = true;
    }
  }

  function cancelPresetSaveUI(target) {
    if (target === 'bar' || !target) {
      var barRow = document.getElementById('savedFiltersSaveRow');
      if (barRow) barRow.hidden = true;
      var addBtn = document.getElementById('savedFiltersAddBtn');
      if (addBtn) addBtn.hidden = false;
    }
    if (target === 'dropdown' || !target) {
      var ddRow = document.getElementById('fmdPresetSaveRow');
      if (ddRow) ddRow.hidden = true;
    }
  }

  function confirmPresetSave(target) {
    var inputId = target === 'bar' ? 'sfbPresetName' : 'fmdPresetName';
    var input = document.getElementById(inputId);
    if (!input) return;
    var name = (input.value || '').trim();
    if (!name) { input.focus(); return; }
    var presets = loadPresets();
    var id = 'p_' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
    presets.push({ id: id, name: name, filters: currentFilterSnapshot() });
    savePresets(presets);
    cancelPresetSaveUI(target);
    renderPresets();
  }

  function deletePreset(id) {
    var presets = loadPresets().filter(function(p) { return p.id !== id; });
    savePresets(presets);
    renderPresets();
  }

  /* ── Inline rename UI (dropdown chips) ── */
  var renameState = { id: null, original: null };

  function cancelRenameUI() {
    if (!renameState.id) return;
    renameState.id = null;
    renameState.original = null;
    renderPresets();
  }

  function startRename(chip, presetId) {
    var presets = loadPresets();
    var preset = presets.find(function(p) { return p.id === presetId; });
    if (!preset) return;
    renameState.id = presetId;
    renameState.original = preset.name;
    chip.innerHTML =
      '<input type="text" class="fmd-preset-input fmd-preset-rename-input" maxlength="40" value="' + escHtml(preset.name) + '" autocomplete="off">' +
      '<button type="button" class="fmd-preset-confirm" aria-label="Save rename"><i class="fas fa-check"></i></button>' +
      '<button type="button" class="fmd-preset-cancel" aria-label="Cancel rename"><i class="fas fa-times"></i></button>';
    var input = chip.querySelector('input');
    if (input) { input.focus(); input.select(); }
  }

  function commitRename(newName) {
    if (!renameState.id) return;
    var name = (newName || '').trim();
    if (!name) { cancelRenameUI(); return; }
    var presets = loadPresets();
    var p = presets.find(function(p) { return p.id === renameState.id; });
    if (p) { p.name = name; savePresets(presets); }
    renameState.id = null;
    renameState.original = null;
    renderPresets();
  }

  /* ── Inline rename UI (visible bar chips) ── */
  var barRenameState = { id: null, original: null };

  function cancelBarRename() {
    if (!barRenameState.id) return;
    barRenameState.id = null;
    barRenameState.original = null;
    renderPresets();
  }

  function startBarRename(chip, presetId) {
    var presets = loadPresets();
    var preset = presets.find(function(p) { return p.id === presetId; });
    if (!preset) return;
    barRenameState.id = presetId;
    barRenameState.original = preset.name;
    chip.innerHTML =
      '<input type="text" class="fmd-preset-input sfb-rename-input" maxlength="40" value="' + escHtml(preset.name) + '" autocomplete="off">' +
      '<button type="button" class="fmd-preset-confirm sfb-chip-confirm" aria-label="Save rename"><i class="fas fa-check"></i></button>' +
      '<button type="button" class="fmd-preset-cancel sfb-chip-cancel" aria-label="Cancel rename"><i class="fas fa-times"></i></button>';
    var input = chip.querySelector('input');
    if (input) { input.focus(); input.select(); }
  }

  function commitBarRename(newName) {
    if (!barRenameState.id) return;
    var name = (newName || '').trim();
    if (!name) { cancelBarRename(); return; }
    var presets = loadPresets();
    var p = presets.find(function(p) { return p.id === barRenameState.id; });
    if (p) { p.name = name; savePresets(presets); }
    barRenameState.id = null;
    barRenameState.original = null;
    renderPresets();
  }

  /* ── Delegated click handlers for presets ── */
  document.addEventListener('click', function(e) {
    // Dropdown "Save as preset" footer button
    if (e.target.closest('#mainFilterSaveBtn')) {
      showPresetSaveUI('dropdown');
      return;
    }
    if (e.target.closest('#fmdPresetSaveConfirm')) {
      confirmPresetSave('dropdown');
      return;
    }
    if (e.target.closest('#fmdPresetSaveCancel')) {
      cancelPresetSaveUI('dropdown');
      return;
    }

    // Visible bar controls
    if (e.target.closest('#savedFiltersAddBtn')) {
      showPresetSaveUI('bar');
      return;
    }
    if (e.target.closest('#sfbPresetSaveConfirm')) {
      confirmPresetSave('bar');
      return;
    }
    if (e.target.closest('#sfbPresetSaveCancel')) {
      cancelPresetSaveUI('bar');
      return;
    }

    // Visible bar chip interactions
    var barChip = e.target.closest('.sfb-chip');
    if (barChip) {
      var barId = barChip.getAttribute('data-preset-id');
      if (!barId) return;
      if (e.target.closest('.sfb-chip-del')) {
        e.stopPropagation();
        if (window.confirm('Delete this saved filter?')) deletePreset(barId);
        return;
      }
      if (e.target.closest('.sfb-chip-edit')) {
        e.stopPropagation();
        startBarRename(barChip, barId);
        return;
      }
      if (e.target.closest('.sfb-chip-apply')) {
        if (barRenameState.id === barId) return;
        var barPreset = loadPresets().find(function(p) { return p.id === barId; });
        if (barPreset) applyPreset(barPreset);
        return;
      }
      // Confirm/cancel inline bar rename
      if (barRenameState.id === barId) {
        if (e.target.closest('.sfb-chip-confirm')) {
          var barInput = barChip.querySelector('input.sfb-rename-input');
          commitBarRename(barInput ? barInput.value : barRenameState.original);
          return;
        }
        if (e.target.closest('.sfb-chip-cancel')) {
          cancelBarRename();
          return;
        }
      }
      return;
    }

    // Dropdown chip interactions
    var chip = e.target.closest('.fmd-preset-chip');
    if (!chip) return;
    var presetId = chip.getAttribute('data-preset-id');
    if (!presetId) return;

    if (e.target.closest('.fmd-preset-del')) {
      e.stopPropagation();
      if (window.confirm('Delete this saved filter?')) deletePreset(presetId);
      return;
    }
    if (e.target.closest('.fmd-preset-edit')) {
      e.stopPropagation();
      startRename(chip, presetId);
      return;
    }
    if (e.target.closest('.fmd-preset-apply')) {
      if (renameState.id === presetId) return;
      var preset = loadPresets().find(function(p) { return p.id === presetId; });
      if (preset) applyPreset(preset);
      return;
    }

    if (renameState.id) {
      if (e.target.closest('.fmd-preset-confirm')) {
        var input = chip.querySelector('input.fmd-preset-rename-input');
        commitRename(input ? input.value : renameState.original);
        return;
      }
      if (e.target.closest('.fmd-preset-cancel')) {
        cancelRenameUI();
        return;
      }
    }
  });

  /* ── Keyboard: Enter/Escape inside preset inputs ── */
  document.addEventListener('keydown', function(e) {
    var target = e.target;
    if (!target || !target.classList) return;

    if (target.id === 'fmdPresetName') {
      if (e.key === 'Enter') { e.preventDefault(); confirmPresetSave('dropdown'); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelPresetSaveUI('dropdown'); }
    } else if (target.id === 'sfbPresetName') {
      if (e.key === 'Enter') { e.preventDefault(); confirmPresetSave('bar'); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelPresetSaveUI('bar'); }
    } else if (target.classList.contains('fmd-preset-rename-input')) {
      if (e.key === 'Enter') { e.preventDefault(); commitRename(target.value); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelRenameUI(); }
    } else if (target.classList.contains('sfb-rename-input')) {
      if (e.key === 'Enter') { e.preventDefault(); commitBarRename(target.value); }
      else if (e.key === 'Escape') { e.preventDefault(); cancelBarRename(); }
    }
  });

  /* ── Initial render ── */
  syncOptionsToState();
  updateFilterCount();
  renderPresets();

})();
