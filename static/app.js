/* MiFicha app.js — tiny vanilla JS. No frameworks. */
(function () {
  "use strict";

  window.MIFicha = window.MIFicha || {};

  /* ---------- Processing page: poll backend for readiness ---------- */
  window.MIFicha.startPolling = function () {
    if (window.MIFicha._polling) return; /* ya hay un polling activo */
    window.MIFicha._polling = true;
    var estadoUrl = window.MIFicha.estadoUrl;
    var resultadoUrl = window.MIFicha.resultadoUrl;
    var errorBox = document.getElementById("processing-error");
    var errorMsg = document.getElementById("processing-error-msg");
    if (!estadoUrl || !resultadoUrl) { window.MIFicha._polling = false; return; }

    var fails = 0;
    var polls = 0;
    var timer = setInterval(function () {
      fetch(estadoUrl, { credentials: "same-origin", cache: "no-store" })
        .then(function (resp) {
          if (!resp.ok) throw new Error("status " + resp.status);
          return resp.json();
        })
        .then(function (data) {
          fails = 0;
          polls++;
          var stalled = document.getElementById("processing-stalled");
          if (stalled) stalled.hidden = true;
          /* A los ~3 min se avisa que sigue trabajando: los escaneos tardan. */
          if (polls === 90) {
            var slow = document.getElementById("processing-slow");
            if (slow) slow.hidden = false;
          }
          if (data.status === "ready") {
            clearInterval(timer);
            window.MIFicha._polling = false;
            window.location.href = resultadoUrl;
          } else if (data.status === "rejected" || data.status === "failed") {
            clearInterval(timer);
            window.MIFicha._polling = false;
            if (errorBox) errorBox.hidden = false;
            if (errorMsg && data.message) errorMsg.textContent = data.message;
            var spinner = document.querySelector(".spinner");
            if (spinner) spinner.style.display = "none";
          }
          /* otherwise keep polling: "processing", "queued", etc. */
        })
        .catch(function (err) {
          /* Transient network hiccup: keep polling, don't alarm the user. */
          fails++;
          if (fails === 15) {
            var stalled = document.getElementById("processing-stalled");
            if (stalled) stalled.hidden = false;
          }
        });
    }, 2000);
  };

  /* If the page is restored from bfcache (e.g. back button after a failed
     navigation to the result), the old timer is gone: restart polling. */
  window.addEventListener("pageshow", function (e) {
    if (e.persisted && window.MIFicha.estadoUrl) {
      window.MIFicha._polling = false;
      window.MIFicha.startPolling();
    }
  });

  /* ---------- Diálogos de confirmación ---------- */
  window.MIFicha.initConfirmDialogs = function () {
    document.querySelectorAll("form[data-confirm-dialog]").forEach(function (form) {
      var dlg = document.getElementById(form.getAttribute("data-confirm-dialog"));
      var text = form.getAttribute("data-confirm-text") || "";
      form.addEventListener("submit", function (e) {
        if (form.dataset.confirmed === "1") return; /* ya confirmado */
        e.preventDefault();
        if (dlg && typeof dlg.showModal === "function") {
          dlg.showModal();
          var cancel = dlg.querySelector("[data-close-dialog]");
          if (cancel) cancel.focus();
        } else if (text && confirm(text)) {
          /* Navegador sin <dialog>: se usa el confirm nativo. */
          form.dataset.confirmed = "1";
          form.submit();
        }
      });
    });
    document.querySelectorAll("dialog.confirm-dialog").forEach(function (dlg) {
      dlg.addEventListener("click", function (e) {
        if (e.target === dlg) dlg.close(); /* clic en el overlay */
      });
      dlg.querySelectorAll("[data-close-dialog]").forEach(function (b) {
        b.addEventListener("click", function () { dlg.close(); });
      });
      var ok = dlg.querySelector("[data-confirm-submit]");
      if (ok) ok.addEventListener("click", function () {
        var form = document.querySelector(
          'form[data-confirm-dialog="' + dlg.id + '"]');
        if (form) { form.dataset.confirmed = "1"; form.submit(); }
      });
    });
  };

  /* ---------- Vault search: filter cards/rows in place, no reload ----
     The member pills are server links (real navigation); the search box
     filters the visible panel client-side. The query is kept across tab
     swaps so the filter survives switching Exams / Vaccines. */
  window.MIFicha.initVaultSearch = function () {
    var input = document.querySelector("[data-vault-search]");
    if (!input) return;
    function apply() {
      var q = input.value.trim().toLowerCase();
      window.MIFicha._vaultQuery = q;
      var panel = input.closest(".vault-panel");
      if (!panel) return;
      var visible = 0;
      panel.querySelectorAll("[data-search]").forEach(function (el) {
        var hit = !q || (el.getAttribute("data-search") || "").toLowerCase().indexOf(q) !== -1;
        el.style.display = hit ? "" : "none";
        if (hit) visible++;
      });
      panel.querySelectorAll("[data-vax-group]").forEach(function (group) {
        var any = Array.prototype.some.call(
          group.querySelectorAll("[data-search]"),
          function (row) { return row.style.display !== "none"; });
        group.style.display = any ? "" : "none";
      });
      var empty = panel.querySelector("[data-vault-empty]");
      if (empty) empty.hidden = visible !== 0;
    }
    input.addEventListener("input", apply);
    var saved = window.MIFicha._vaultQuery;
    if (saved) { input.value = saved; apply(); }
  };

  /* ---------- Vault tabs: switch Exams / Vaccines without a full reload ----
     The two vault pages are views of the same file. Clicking a tab fetches
     the other page, swaps only <main>, and updates the URL — no white
     flash, no fresh document. Without JS (or on any failure) the links
     work as plain navigation. */
  window.MIFicha.initVaultTabs = function () {
    var main = document.querySelector("main.container");
    if (!main) return;
    var current = 0; /* guards against out-of-order responses */

    function isVaultPage(url) {
      var path = new URL(url, window.location.origin).pathname;
      return path === "/ficha" || path === "/vacunas";
    }

    function swap(url, push, retried) {
      var n = ++current;
      var retrying = false;
      main.setAttribute("aria-busy", "true");
      fetch(url, { credentials: "same-origin", cache: "no-store" })
        .then(function (resp) {
          if (!resp.ok) throw new Error("status " + resp.status);
          return resp.text();
        })
        .then(function (html) {
          if (n !== current) return; /* a newer tab click already won */
          var doc = new DOMParser().parseFromString(html, "text/html");
          var next = doc.querySelector("main.container");
          if (!next) throw new Error("no main in response");
          main.innerHTML = next.innerHTML;
          document.title = doc.title || document.title;
          window.MIFicha.initVaultSearch();
          if (push) history.pushState(null, "", url);
          window.scrollTo(0, 0);
        })
        .catch(function () {
          if (n !== current) return;
          if (!retried) {
            /* Transient blip (cold start, hiccup): one retry before
               giving up and navigating for real. */
            retrying = true;
            setTimeout(function () {
              if (n !== current) return;
              swap(url, push, true);
            }, 1200);
            return;
          }
          window.location.href = url; /* fall back to a real navigation */
        })
        .finally(function () {
          if (n === current && !retrying) main.removeAttribute("aria-busy");
        });
    }

    /* Delegated: the tabs are replaced on every swap, the listener stays. */
    document.addEventListener("click", function (e) {
      var link = e.target.closest(".vault-tabs a");
      if (!link) return;
      if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button !== 0) return;
      var url = new URL(link.getAttribute("href"), window.location.origin);
      /* Keep the member filter when switching views. */
      var member = new URLSearchParams(window.location.search).get("miembro");
      if (member) url.searchParams.set("miembro", member);
      /* The current tab is a no-op, never a reload: prevent the
         navigation before checking whether it is the tab we're on. */
      e.preventDefault();
      if (url.href === window.location.href) return;
      swap(url.href, true);
    });

    window.addEventListener("popstate", function () {
      if (isVaultPage(window.location.href)) swap(window.location.href, false);
    });
  };

  document.addEventListener("DOMContentLoaded", function () {
    window.MIFicha.initConfirmDialogs();
    window.MIFicha.initVaultTabs();
    window.MIFicha.initVaultSearch();
  });

  /* ---------- File input: show chosen filename ---------- */
  document.addEventListener("change", function (e) {
    if (e.target && e.target.type === "file") {
      var drop = e.target.closest(".file-drop");
      if (drop && e.target.files.length) {
        drop.classList.add("file-chosen");
        var label = drop.querySelector(".file-text");
        if (label) label.textContent = "📄 " + e.target.files[0].name;
      }
    }
  });

  /* ---------- Share: copy link to clipboard ---------- */
  document.addEventListener("click", function (e) {
    var btn = e.target.closest(".copy-btn");
    if (!btn) return;
    var url = btn.getAttribute("data-copy");
    function done() {
      var original = btn.textContent;
      btn.textContent = btn.getAttribute("data-copied-text") || "¡Copiado! ✅";
      setTimeout(function () { btn.textContent = original; }, 2000);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(url).then(done, function () { fallbackCopy(url); done(); });
    } else {
      fallbackCopy(url);
      done();
    }
  });

  function fallbackCopy(text) {
    var ta = document.createElement("textarea");
    ta.value = text;
    ta.setAttribute("readonly", "");
    ta.style.position = "absolute";
    ta.style.left = "-9999px";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (e) { /* best effort */ }
    document.body.removeChild(ta);
  }

  /* ---------- Service worker (offline shell only) ---------- */
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* optional */ });
    });
  }
})();
