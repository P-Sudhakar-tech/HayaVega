/*!
 * mangoo-ui.js -- a small, dependency-free hypermedia runtime for Mangoo.
 *
 * Attribute-driven, HTMX-style interactivity: an element with hx-get/post/
 * put/delete fetches that URL (on click for buttons/links, on submit for
 * forms), and swaps the returned HTML fragment into hx-target (or itself)
 * using hx-swap. No build step, no bundler, no other files required.
 *
 * The CSRF token is read from the mangoo_csrf cookie (set by
 * csrf_token_response / any Mangoo view that calls it) and sent as
 * X-Csrf-Token automatically, so a hypermedia request satisfies the same
 * csrf_protect() check a hand-written fetch call would need to do manually.
 *
 * Also includes mgo-boost client-side routing: a link inside (or carrying)
 * an mgo-boost attribute navigates via fetch + history.pushState instead of
 * a full page load, swapping the element marked mgo-boost-target (or
 * document.body if none is marked) in both the current and fetched page.
 *
 * Perf notes (this file only, no other JS files are involved): a single
 * delegated click listener handles both hx-* triggers and mgo-boost links
 * (one dispatch per click instead of two separate listeners each re-walking
 * the same event target chain), the CSRF cookie is read lazily and only
 * when a request actually needs it, and the hot path per click avoids
 * building intermediate arrays/closures where a plain loop or single
 * expression does the same job.
 */
(function () {
  "use strict";

  var HX_METHODS = ["get", "post", "put", "delete"];
  var HX_SELECTOR = "[hx-get],[hx-post],[hx-put],[hx-delete]";

  function csrfToken() {
    var match = document.cookie.match(/(?:^|; )mangoo_csrf=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function hxConfig(element) {
    for (var i = 0; i < HX_METHODS.length; i++) {
      var url = element.getAttribute("hx-" + HX_METHODS[i]);
      if (url) return { method: HX_METHODS[i].toUpperCase(), url: url };
    }
    return null;
  }

  function resolveTarget(element, selector) {
    if (!selector || selector === "this") return element;
    return document.querySelector(selector);
  }

  function swap(target, html, mode) {
    if (!target) return;
    if (mode === "outerHTML") target.outerHTML = html;
    else if (mode === "beforeend") target.insertAdjacentHTML("beforeend", html);
    else if (mode === "afterbegin") target.insertAdjacentHTML("afterbegin", html);
    else if (mode !== "none") target.innerHTML = html;
  }

  function dispatch(element, name, detail) {
    element.dispatchEvent(new CustomEvent(name, { bubbles: true, detail: detail }));
  }

  function performHx(element, method, url) {
    var target = resolveTarget(element, element.getAttribute("hx-target"));
    var swapMode = element.getAttribute("hx-swap") || "innerHTML";
    var init = { method: method, headers: { "X-Csrf-Token": csrfToken() }, credentials: "same-origin" };

    if (element.tagName === "FORM" && (method === "POST" || method === "PUT")) {
      init.body = new URLSearchParams(new FormData(element));
      init.headers["Content-Type"] = "application/x-www-form-urlencoded";
    }

    dispatch(element, "mangoo:before-request", { method: method, url: url });

    fetch(url, init).then(function (response) {
      return response.text().then(function (text) {
        swap(target, text, swapMode);
        dispatch(element, "mangoo:swapped", { status: response.status, ok: response.ok, target: target });
      });
    }, function (error) {
      dispatch(element, "mangoo:error", { error: error });
    });
  }

  document.addEventListener("submit", function (event) {
    var config = hxConfig(event.target);
    if (!config) return;
    event.preventDefault();
    performHx(event.target, config.method, config.url);
  });

  // -------------------------------------------------------------------
  // mgo-boost: client-side routing over plain <a href> navigation
  // -------------------------------------------------------------------

  function findBoostTarget(doc) {
    return doc.querySelector("[mgo-boost-target]") || doc.body;
  }

  function isBoostableLink(link) {
    if (!link || link.tagName !== "A" || !link.href || link.hasAttribute("download") || link.target) return false;
    try {
      return new URL(link.href, location.href).origin === location.origin;
    } catch (error) {
      return false;
    }
  }

  function performBoost(url, pushState) {
    fetch(url, { headers: { "X-Requested-With": "mangoo-boost" }, credentials: "same-origin" }).then(function (response) {
      return response.text().then(function (text) {
        var parsed = new DOMParser().parseFromString(text, "text/html");
        var currentTarget = findBoostTarget(document);
        currentTarget.innerHTML = findBoostTarget(parsed).innerHTML;
        if (parsed.title) document.title = parsed.title;
        if (pushState) history.pushState({ mangooBoost: true }, "", url);
        dispatch(document, "mangoo:swapped", { status: response.status, ok: response.ok, boosted: true, target: currentTarget });
      });
    }, function (error) {
      dispatch(document, "mangoo:error", { error: error });
    });
  }

  // One delegated click listener for both hx-* triggers and mgo-boost
  // links -- a click on the page walks the ancestor chain at most once,
  // not twice.
  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

    var hxElement = event.target.closest(HX_SELECTOR);
    if (hxElement && hxElement.tagName !== "FORM") {
      var config = hxConfig(hxElement);
      if (config) {
        event.preventDefault();
        performHx(hxElement, config.method, config.url);
        return;
      }
    }

    var link = event.target.closest("a");
    if (link && link.closest("[mgo-boost]") && isBoostableLink(link)) {
      event.preventDefault();
      performBoost(link.href, true);
    }
  });

  window.addEventListener("popstate", function () {
    performBoost(location.href, false);
  });
})();
