/**
 * Sidebar: newsletter close (persisted), frequency buttons, email subscribe
 */
(function() {
  'use strict';
  var DISMISSED_KEY = 'newsletter_dismissed';
  var card = document.getElementById('newsletterCard');

  // Hide permanently if already dismissed
  if (card) {
    try {
      if (localStorage.getItem(DISMISSED_KEY) === '1') {
        card.style.display = 'none';
      }
    } catch (e) {}
  }

  var closeBtn = document.getElementById('newsletterClose');
  if (closeBtn && card) {
    closeBtn.addEventListener('click', function() {
      card.style.display = 'none';
      try { localStorage.setItem(DISMISSED_KEY, '1'); } catch (e) {}
    });
  }

  var freqButtons = document.querySelectorAll('.freq-btn');
  freqButtons.forEach(function(btn) {
    btn.addEventListener('click', function() {
      freqButtons.forEach(function(b) { b.classList.remove('active'); });
      this.classList.add('active');
    });
  });

  var newsletterInput = document.querySelector('.newsletter-input');
  if (newsletterInput) {
    newsletterInput.addEventListener('keypress', function(e) {
      if (e.key === 'Enter') {
        var email = this.value.trim();
        var activeFreqBtn = document.querySelector('.freq-btn.active');
        var frequency = activeFreqBtn ? activeFreqBtn.textContent.trim().toLowerCase() : 'daily';
        if (email && email.indexOf('@') !== -1) {
          window.location.href = '/digest';
        }
      }
    });
  }
})();
