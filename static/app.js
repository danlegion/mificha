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

  document.addEventListener("DOMContentLoaded", function () {
    window.MIFicha.initConfirmDialogs();
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
