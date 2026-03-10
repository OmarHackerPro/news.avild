/**
 * Auth: navbar user widget.
 * Fetches current user from API, updates nav link/avatar/name.
 */
(function () {
  'use strict';

  function getToken() {
    try { return localStorage.getItem('auth_token') || ''; } catch (e) { return ''; }
  }

  function avatarUrl(pic) {
    if (!pic) return '';
    if (pic.startsWith('http') || pic.startsWith('/')) return pic;
    return '/static/' + pic;
  }

  function setUserState(user) {
    var link       = document.getElementById('navUserLink');
    var iconEl     = document.getElementById('navUserIcon');
    var avatarWrap = document.getElementById('navUserAvatar');
    var avatarImg  = document.getElementById('navUserAvatarImg');
    var nameEl     = document.getElementById('navUserName');
    if (!link) return;

    if (user && user.name) {
      link.href = '/profile';
      link.setAttribute('aria-label', 'Account: ' + user.name);
      if (nameEl) { nameEl.textContent = user.name; nameEl.hidden = false; }

      var hasPic = typeof user.profile_picture === 'string' && user.profile_picture.trim().length > 0;
      if (hasPic && avatarWrap && avatarImg) {
        avatarImg.src = avatarUrl(user.profile_picture);
        avatarImg.alt   = user.name;
        avatarWrap.hidden = false;
        if (iconEl) iconEl.hidden = true;
      } else {
        if (avatarWrap) avatarWrap.hidden = true;
        if (iconEl)     iconEl.hidden     = false;
      }
    } else {
      link.href = '/login';
      link.setAttribute('aria-label', 'Log in or account');
      if (nameEl)     { nameEl.textContent = ''; nameEl.hidden = true; }
      if (avatarWrap) avatarWrap.hidden = true;
      if (iconEl)     iconEl.hidden     = false;
    }
  }

  function fetchMe() {
    var token = getToken();
    if (!token) { setUserState(null); return; }

    // Show cached user immediately
    var cached = null;
    try { cached = JSON.parse(localStorage.getItem('auth_user') || 'null'); } catch (e) {}
    if (cached) setUserState(cached);

    // Then verify with API
    fetch('/api/auth/me', {
      headers: { 'Authorization': 'Bearer ' + token }
    })
    .then(function(res) {
      if (res.status === 401) {
        try { localStorage.removeItem('auth_token'); localStorage.removeItem('auth_user'); } catch(e) {}
        setUserState(null);
        return null;
      }
      return res.json();
    })
    .then(function(user) {
      if (user) {
        try { localStorage.setItem('auth_user', JSON.stringify(user)); } catch(e) {}
        setUserState(user);
      }
    })
    .catch(function() {
      // Network error — keep cached state
    });
  }

  // Listen for postMessage from popup login/signup windows
  window.addEventListener('message', function (e) {
    if (e.origin !== window.location.origin) return;
    if (e.data && e.data.type === 'auth' && e.data.user) {
      setUserState(e.data.user);
    }
    if (e.data && e.data.type === 'auth_logout') {
      setUserState(null);
    }
  });

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', fetchMe);
  } else {
    fetchMe();
  }
})();
