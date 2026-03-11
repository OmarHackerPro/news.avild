/**
 * Shared auth utilities used by auth pages and navbar.
 */
(function() {
  'use strict';

  var TOKEN_KEY = 'auth_token';
  var USER_KEY  = 'auth_user';

  window.AuthHelpers = {
    getToken: function() {
      try { return localStorage.getItem(TOKEN_KEY) || ''; } catch (e) { return ''; }
    },

    getUser: function() {
      try { return JSON.parse(localStorage.getItem(USER_KEY) || 'null'); } catch (e) { return null; }
    },

    saveAuth: function(data) {
      try {
        localStorage.setItem(TOKEN_KEY, data.access_token);
        localStorage.setItem(USER_KEY, JSON.stringify(data.user));
      } catch (e) {}
    },

    clearAuth: function() {
      try {
        localStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
      } catch (e) {}
    },

    getAuthHeaders: function() {
      return { 'Authorization': 'Bearer ' + this.getToken(), 'Content-Type': 'application/json' };
    },

    requireAuth: function() {
      if (!this.getToken()) {
        window.location.href = '/login?next=' + encodeURIComponent(window.location.pathname);
        return false;
      }
      return true;
    },

    avatarUrl: function(profilePicture) {
      if (!profilePicture) return '';
      if (profilePicture.startsWith('http') || profilePicture.startsWith('/')) return profilePicture;
      return '/static/' + profilePicture;
    }
  };
})();
