/**
 * Auth: reads user from localStorage, updates the navbar user link.
 * No backend required — all state is stored client-side.
 */
(function () {
  'use strict';

  var TOKEN_KEY = 'auth_token';
  var USER_KEY  = 'auth_user';

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
  }

  function setUserState(user) {
    var link       = document.getElementById('navUserLink');
    var iconEl     = document.getElementById('navUserIcon');
    var avatarWrap = document.getElementById('navUserAvatar');
    var avatarImg  = document.getElementById('navUserAvatarImg');
    var nameEl     = document.getElementById('navUserName');
    if (!link) return;

    if (user && user.name) {
      link.href = '/pages/profile.html';
      link.setAttribute('aria-label', 'Account: ' + user.name);
      if (nameEl) { nameEl.textContent = user.name; nameEl.hidden = false; }

      var hasPic = typeof user.profile_picture === 'string' && user.profile_picture.trim().length > 0;
      if (hasPic && avatarWrap && avatarImg) {
        avatarImg.src = user.profile_picture;
        avatarImg.alt   = user.name;
        avatarWrap.hidden = false;
        if (iconEl) iconEl.hidden = true;
      } else {
        if (avatarWrap) avatarWrap.hidden = true;
        if (iconEl)     iconEl.hidden     = false;
      }
    } else {
      link.href = '/pages/login.html';
      link.setAttribute('aria-label', 'Log in or account');
      if (nameEl)     { nameEl.textContent = ''; nameEl.hidden = true; }
      if (avatarWrap) avatarWrap.hidden = true;
      if (iconEl)     iconEl.hidden     = false;
    }
  }

  function fetchMe() {
    var token = getToken();
    if (!token) { setUserState(null); return; }
    var user = null;
    try { user = JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) {}
    setUserState(user);
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
