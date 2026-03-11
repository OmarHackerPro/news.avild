/**
 * Language selector dropdown + applyLanguage (requires data/translations.js)
 */
(function() {
  'use strict';
  var langTrigger = document.getElementById('langTrigger');
  var langDropdown = document.getElementById('langDropdown');
  var langCurrent = document.querySelector('.lang-current');
  var langOptions = document.querySelectorAll('.lang-option');
  var langSelectNative = document.getElementById('langSelect');

  function closeLangDropdown() {
    if (!langTrigger || !langDropdown) return;
    langTrigger.setAttribute('aria-expanded', 'false');
    langDropdown.classList.remove('open');
    langDropdown.style.position = '';
    langDropdown.style.top = '';
    langDropdown.style.left = '';
    langDropdown.style.width = '';
    langDropdown.style.right = '';
  }

  function openLangDropdown() {
    if (!langTrigger || !langDropdown) return;
    langTrigger.setAttribute('aria-expanded', 'true');
    var triggerRect = langTrigger.getBoundingClientRect();
    var wrapperRect = langTrigger.closest('.lang-select-wrapper').getBoundingClientRect();
    langDropdown.style.position = 'fixed';
    langDropdown.style.top = (triggerRect.bottom + 6) + 'px';
    langDropdown.style.left = wrapperRect.left + 'px';
    langDropdown.style.width = wrapperRect.width + 'px';
    langDropdown.style.right = 'auto';
    langDropdown.classList.add('open');
  }

  // Sync UI to saved language on page load
  (function() {
    var saved = null;
    try { saved = localStorage.getItem('lang'); } catch(e) {}
    if (saved && saved !== 'en') {
      var matchOpt = document.querySelector('.lang-option[data-lang="' + saved + '"]');
      if (matchOpt) {
        langOptions.forEach(function(o) { o.classList.remove('selected'); });
        matchOpt.classList.add('selected');
        if (langCurrent) langCurrent.textContent = matchOpt.getAttribute('data-short') || saved.toUpperCase();
        if (langSelectNative) langSelectNative.value = saved;
      }
    }
  })();

  if (langTrigger && langDropdown) {
    langTrigger.addEventListener('click', function(e) {
      e.stopPropagation();
      if (langDropdown.classList.contains('open')) {
        langDropdown.classList.add('lang-dropdown-closing');
        setTimeout(function() {
          closeLangDropdown();
          langDropdown.classList.remove('lang-dropdown-closing');
        }, 200);
      } else {
        openLangDropdown();
      }
    });

    langOptions.forEach(function(opt) {
      opt.addEventListener('click', function(e) {
        e.stopPropagation();
        var ripple = document.createElement('span');
        ripple.className = 'lang-ripple';
        var rect = this.getBoundingClientRect();
        ripple.style.left = (e.clientX - rect.left) + 'px';
        ripple.style.top = (e.clientY - rect.top) + 'px';
        this.appendChild(ripple);
        setTimeout(function() { ripple.remove(); }, 600);
        langOptions.forEach(function(o) { o.classList.remove('selected'); });
        this.classList.add('selected');
        if (langCurrent) langCurrent.textContent = this.getAttribute('data-short') || this.textContent;
        var chosenLang = this.getAttribute('data-lang');
        if (langSelectNative) langSelectNative.value = chosenLang;
        if (window.CyberNews && window.CyberNews.applyLanguage) window.CyberNews.applyLanguage(chosenLang || 'en');
        try { localStorage.setItem('lang', chosenLang || 'en'); } catch(e) {}
        if (langCurrent) {
          langCurrent.style.transform = 'scale(1.15)';
          setTimeout(function() { langCurrent.style.transform = 'scale(1)'; }, 200);
        }
        closeLangDropdown();
      });
      opt.addEventListener('mouseenter', function() { this.style.transform = 'translateX(4px)'; });
      opt.addEventListener('mouseleave', function() { this.style.transform = 'translateX(0)'; });
    });

    function updateDropdownPosition() {
      if (langDropdown.classList.contains('open')) {
        var triggerRect = langTrigger.getBoundingClientRect();
        var wrapperRect = langTrigger.closest('.lang-select-wrapper').getBoundingClientRect();
        langDropdown.style.top = (triggerRect.bottom + 6) + 'px';
        langDropdown.style.left = wrapperRect.left + 'px';
        langDropdown.style.width = wrapperRect.width + 'px';
      }
    }
    window.addEventListener('scroll', updateDropdownPosition, true);
    window.addEventListener('resize', updateDropdownPosition);
    document.addEventListener('click', function() {
      if (langDropdown.classList.contains('open')) closeLangDropdown();
    });
    document.addEventListener('keydown', function(e) {
      if (e.key === 'Escape' && langDropdown.classList.contains('open')) {
        closeLangDropdown();
        langTrigger.focus();
      }
    });
    langDropdown.addEventListener('click', function(e) { e.stopPropagation(); });
  }
})();
