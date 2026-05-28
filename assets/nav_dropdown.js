/**
 * nav_dropdown.js
 *
 * Adds click-to-toggle behaviour for .ops-nav-dropdown groups so they work
 * on touch devices where CSS :hover is unreliable.
 *
 * Strategy:
 *  - Mark each toggle with [data-nav-init] after attaching its listener so
 *    re-runs (after Dash SPA navigation) only wire newly-added elements.
 *  - Toggle .open on the parent .ops-nav-group when its button is clicked.
 *  - Close all groups when the user clicks anywhere outside the nav.
 *  - A single delegated document listener (attached once) handles the close.
 *
 * Dataset ID preservation:
 *  - After each init cycle, reads dataset_id from the current URL query string.
 *  - Appends ?dataset_id=<value> to the href of nav links whose target path is
 *    in DATASET_ID_PATHS. This keeps dataset context when switching pages.
 */
(function () {
  'use strict';

  var outsideListenerAttached = false;
  var historyListenerAttached = false;

  /* Pages that should carry the dataset_id query param across navigation. */
  var DATASET_ID_PATHS = [
    '/data-explorer',
    '/dataset-readiness',
    '/silver-gold-outputs',
    '/preprocessing',
    '/training',
    '/postprocessing',
    '/lineage-governance',
    '/monitoring-cost',
  ];

  function closeAll(exceptGroup) {
    document.querySelectorAll('.ops-nav-dropdown.open').forEach(function (g) {
      if (g !== exceptGroup) {
        g.classList.remove('open');
      }
    });
  }

  function injectDatasetId() {
    var params = new URLSearchParams(window.location.search);
    var datasetId = params.get('dataset_id');
    if (!datasetId) return;

    document.querySelectorAll(
      '.ops-nav-dropdown-item[href], .ops-nav-link[href], .platform-compact-nav-item[href]'
    ).forEach(function (link) {
      var href = link.getAttribute('href') || '';
      var path = href.split('?')[0];
      if (DATASET_ID_PATHS.indexOf(path) !== -1) {
        link.setAttribute(
          'href',
          path + '?dataset_id=' + encodeURIComponent(datasetId)
        );
      }
    });
  }

  function initNavDropdowns() {
    var uninitialised = document.querySelectorAll(
      '.ops-nav-dropdown:not([data-nav-init])'
    );

    uninitialised.forEach(function (group) {
      group.setAttribute('data-nav-init', '1');

      var toggle = group.querySelector('.ops-nav-dropdown-toggle');
      if (!toggle) return;

      toggle.addEventListener('click', function (e) {
        e.stopPropagation();
        var isOpen = group.classList.contains('open');
        closeAll(group);
        group.classList.toggle('open', !isOpen);
      });
    });

    /* Attach the outside-click listener exactly once */
    if (!outsideListenerAttached) {
      outsideListenerAttached = true;
      document.addEventListener('click', function (e) {
        if (!e.target.closest('.ops-nav-dropdown')) {
          closeAll(null);
        }
      });
    }

    injectDatasetId();
  }

  function attachHistoryListener() {
    if (historyListenerAttached) return;
    historyListenerAttached = true;

    ['pushState', 'replaceState'].forEach(function (methodName) {
      var original = window.history[methodName];
      if (typeof original !== 'function') return;
      window.history[methodName] = function () {
        var result = original.apply(this, arguments);
        window.setTimeout(injectDatasetId, 0);
        return result;
      };
    });

    window.addEventListener('popstate', function () {
      window.setTimeout(injectDatasetId, 0);
    });
  }

  /* Initial run */
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initNavDropdowns);
  } else {
    initNavDropdowns();
  }

  attachHistoryListener();

  /*
   * Re-run after Dash SPA navigation updates the DOM.
   * Watch only direct children of <body> to avoid flooding from Plotly/DataTable.
   */
  var observer = new MutationObserver(function (mutations) {
    var needsInit = mutations.some(function (m) { return m.addedNodes.length > 0; });
    if (needsInit) initNavDropdowns();
  });

  function attachObserver() {
    observer.observe(document.body, { childList: true, subtree: false });
  }

  if (document.body) {
    attachObserver();
  } else {
    document.addEventListener('DOMContentLoaded', attachObserver);
  }
})();
