(function () {
  'use strict';

  function t(key) {
    return window.CyberNews && window.CyberNews.t ? window.CyberNews.t(key) : key;
  }

  window.selectedTypes = [];

  function setupFilterDropdown(triggerId, dropdownId, optionClass, labelSelector, useGreenSelected, allowMultiSelect) {
    const trigger = document.getElementById(triggerId);
    const dropdown = document.getElementById(dropdownId);
    if (!trigger || !dropdown) return;
    const labelEl = trigger.querySelector(labelSelector || '.filter-btn-label');
    const options = dropdown.querySelectorAll('.filter-dropdown-option');

    function close() {
      trigger.setAttribute('aria-expanded', 'false');
      dropdown.classList.remove('open');
    }
    function open() {
      document.querySelectorAll('.filter-dropdown.open').forEach(function (d) { d.classList.remove('open'); });
      document.querySelectorAll('.filter-btn[aria-expanded="true"]').forEach(function (t) { t.setAttribute('aria-expanded', 'false'); });
      trigger.setAttribute('aria-expanded', 'true');
      dropdown.classList.add('open');
    }

    trigger.addEventListener('click', function (e) {
      e.stopPropagation();
      if (dropdown.classList.contains('open')) close();
      else open();
    });

    options.forEach(function (opt) {
      opt.addEventListener('click', function (e) {
        e.stopPropagation();
        if (allowMultiSelect) {
          const value = this.getAttribute('data-value');
          const isSelected = this.classList.contains('selected');
          if (isSelected) {
            this.classList.remove('selected');
            const index = window.selectedTypes.indexOf(value);
            if (index > -1) window.selectedTypes.splice(index, 1);
          } else {
            this.classList.add('selected');
            if (window.selectedTypes.indexOf(value) === -1) window.selectedTypes.push(value);
          }
          if (labelEl) {
            if (window.selectedTypes.length === 0) labelEl.textContent = t('filters.type.label');
            else if (window.selectedTypes.length === 1) {
              const selectedOption = dropdown.querySelector('.filter-dropdown-option.selected');
              labelEl.textContent = selectedOption ? selectedOption.textContent.trim() : t('filters.type.label');
            } else labelEl.textContent = t('filter.selected').replace('{n}', window.selectedTypes.length);
          }
          updateFilterCount();
        } else {
          options.forEach(function (o) { o.classList.remove('selected'); });
          this.classList.add('selected');
          if (labelEl) labelEl.textContent = this.textContent.trim();
          close();
        }
        if (typeof window.refreshFeed === 'function') window.refreshFeed();
        else if (typeof window.applyFilters === 'function') window.applyFilters();
      });
    });

    document.addEventListener('click', function () { if (dropdown.classList.contains('open')) close(); });
    dropdown.addEventListener('click', function (e) { e.stopPropagation(); });
  }

  setupFilterDropdown('timeTrigger', 'timeDropdown', null, '.filter-btn-label', true, false);
  setupFilterDropdown('typeTrigger', 'typeDropdown', null, '.filter-btn-label', false, true);
  setupFilterDropdown('sourcesTrigger', 'sourcesDropdown', null, '.filter-btn-label', false, false);

  const filterClearBtn = document.getElementById('filterClearBtn');
  const filterCount = document.getElementById('filterCount');

  function updateFilterCount() {
    const count = window.selectedTypes.length;
    if (filterCount) {
      filterCount.textContent = count;
      filterCount.classList.toggle('show', count > 0);
    }
    if (filterClearBtn) {
      filterClearBtn.classList.toggle('show', count > 0);
      filterClearBtn.disabled = count === 0;
    }
  }

  function clearAllFilters() {
    window.selectedTypes = [];
    document.querySelectorAll('#typeDropdown .filter-dropdown-option').forEach(function (opt) { opt.classList.remove('selected'); });
    const typeLabel = document.querySelector('#typeTrigger .filter-btn-label');
    if (typeLabel) typeLabel.textContent = t('filters.type.label');
    const defaultOption = document.querySelector('#typeDropdown .filter-dropdown-option[data-value="news"]');
    if (defaultOption) defaultOption.classList.add('selected');
    const typeDropdown = document.getElementById('typeDropdown');
    const typeTrigger = document.getElementById('typeTrigger');
    if (typeDropdown && typeDropdown.classList.contains('open')) {
      typeDropdown.classList.remove('open');
      if (typeTrigger) typeTrigger.setAttribute('aria-expanded', 'false');
    }
    updateFilterCount();
    if (typeof window.refreshFeed === 'function') window.refreshFeed();
    else if (typeof window.applyFilters === 'function') window.applyFilters();
  }

  if (filterClearBtn) filterClearBtn.addEventListener('click', function (e) { e.stopPropagation(); clearAllFilters(); });
  updateFilterCount();

  var moreTrigger = document.getElementById('moreTrigger');
  var moreDropdown = document.getElementById('moreDropdown');

  if (moreTrigger && moreDropdown) {
    const moreOptions = moreDropdown.querySelectorAll('.more-card-option');
    let isMoreOpen = false;

    function updateMoreDropdownPosition() {
      if (isMoreOpen || moreDropdown.classList.contains('open')) {
        const triggerRect = moreTrigger.getBoundingClientRect();
        const wrapperRect = moreTrigger.closest('.nav-more-wrap').getBoundingClientRect();
        moreDropdown.style.position = 'fixed';
        moreDropdown.style.top = (triggerRect.bottom + 6) + 'px';
        moreDropdown.style.left = wrapperRect.left + 'px';
        moreDropdown.style.width = wrapperRect.width + 'px';
        moreDropdown.style.right = 'auto';
      }
    }

    function closeMore() {
      isMoreOpen = false;
      moreTrigger.setAttribute('aria-expanded', 'false');
      moreDropdown.classList.remove('open');
      moreDropdown.style.position = ''; moreDropdown.style.top = ''; moreDropdown.style.left = ''; moreDropdown.style.width = ''; moreDropdown.style.right = '';
    }

    function openMore() {
      document.querySelectorAll('.filter-dropdown.open').forEach(function (d) { d.classList.remove('open'); });
      document.querySelectorAll('.filter-btn[aria-expanded="true"]').forEach(function (t) { t.setAttribute('aria-expanded', 'false'); });
      isMoreOpen = true;
      moreTrigger.setAttribute('aria-expanded', 'true');
      moreDropdown.classList.add('open');
      updateMoreDropdownPosition();
    }

    moreTrigger.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      e.stopImmediatePropagation();
      if (isMoreOpen || moreDropdown.classList.contains('open')) closeMore();
      else openMore();
    }, true);

    moreOptions.forEach(function (opt) {
      opt.addEventListener('click', function (e) {
        if (opt.tagName === 'A') {
          closeMore();
          return;
        }
        e.preventDefault();
        e.stopPropagation();
        moreOptions.forEach(function (o) { o.classList.remove('active'); });
        this.classList.add('active');
        window.currentMoreCategory = this.getAttribute('data-category');
        closeMore();
        if (typeof window.refreshFeed === 'function') window.refreshFeed();
        else if (typeof window.applyFilters === 'function') window.applyFilters();
      });
    });

    window.addEventListener('scroll', function () { if (isMoreOpen || moreDropdown.classList.contains('open')) updateMoreDropdownPosition(); }, true);
    window.addEventListener('resize', function () { if (isMoreOpen || moreDropdown.classList.contains('open')) updateMoreDropdownPosition(); });
    document.addEventListener('click', function (e) {
      if ((isMoreOpen || moreDropdown.classList.contains('open')) && !moreDropdown.contains(e.target) && !moreTrigger.contains(e.target)) closeMore();
    }, true);
    moreDropdown.addEventListener('click', function (e) { e.stopPropagation(); });
    moreTrigger.addEventListener('mousedown', function (e) { e.stopPropagation(); });
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') {
      document.querySelectorAll('.filter-dropdown.open').forEach(function (d) { d.classList.remove('open'); });
      document.querySelectorAll('.filter-btn[aria-expanded="true"]').forEach(function (t) { t.setAttribute('aria-expanded', 'false'); });
      if (moreDropdown && moreDropdown.classList.contains('open')) {
        moreDropdown.classList.remove('open');
        if (moreTrigger) moreTrigger.setAttribute('aria-expanded', 'false');
      }
    }
  });
})();
