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
     filters both sections client-side. Each section has its own
     "nothing matches" message; apply() is exposed so the view filter
     can re-run it after switching Exams / Vaccines. */
  window.MIFicha.initVaultSearch = function () {
    var input = document.querySelector("[data-vault-search]");
    if (!input || input.dataset.vaultSearchBound) return;
    input.dataset.vaultSearchBound = "1";
    function apply() {
      var q = input.value.trim().toLowerCase();
      window.MIFicha._vaultQuery = q;
      var panel = input.closest(".vault-panel");
      if (!panel) return;
      panel.querySelectorAll("[data-view-section]").forEach(function (section) {
        var visible = 0;
        section.querySelectorAll("[data-search]").forEach(function (el) {
          var hit = !q || (el.getAttribute("data-search") || "").toLowerCase().indexOf(q) !== -1;
          el.style.display = hit ? "" : "none";
          if (hit) visible++;
        });
        section.querySelectorAll("[data-vax-group]").forEach(function (group) {
          var any = Array.prototype.some.call(
            group.querySelectorAll("[data-search]"),
            function (row) { return row.style.display !== "none"; });
          group.style.display = any ? "" : "none";
        });
        var empty = section.querySelector("[data-vault-empty]");
        if (empty) empty.hidden = visible !== 0;
      });
    }
    window.MIFicha.applyVaultSearch = apply;
    input.addEventListener("input", apply);
    var saved = window.MIFicha._vaultQuery;
    if (saved) { input.value = saved; apply(); }
  };

  /* ---------- Vault filter: Exams / Vaccines is one client-side filter ----
     Both sections are already on the page; the buttons only switch which
     one shows. The URL (?vista=vacunas) and the member pills are kept in
     sync, so a tap on a family member or a refresh doesn't reset the
     view. No fetch, no reload. */
  window.MIFicha.initVaultFilter = function () {
    var panel = document.querySelector(".vault-panel");
    if (!panel) return;
    var buttons = panel.querySelectorAll("[data-vault-view]");
    if (!buttons.length) return;
    function setView(view, push) {
      panel.setAttribute("data-view", view);
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed",
          b.getAttribute("data-vault-view") === view ? "true" : "false");
      });
      panel.querySelectorAll(".member-pill").forEach(function (pill) {
        var u = new URL(pill.getAttribute("href"), window.location.origin);
        if (view === "vacunas") u.searchParams.set("vista", "vacunas");
        else u.searchParams.delete("vista");
        pill.setAttribute("href", u.pathname + u.search);
      });
      if (push) {
        var u = new URL(window.location.href);
        if (view === "vacunas") u.searchParams.set("vista", "vacunas");
        else u.searchParams.delete("vista");
        history.pushState(null, "", u.pathname + u.search);
      }
      if (window.MIFicha.applyVaultSearch) window.MIFicha.applyVaultSearch();
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        setView(b.getAttribute("data-vault-view"), true);
      });
    });
  };

  document.addEventListener("DOMContentLoaded", function () {
    window.MIFicha.initConfirmDialogs();
    window.MIFicha.initVaultFilter();
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
