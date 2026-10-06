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
    var timer = setInterval(function () {
      fetch(estadoUrl, { credentials: "same-origin", cache: "no-store" })
        .then(function (resp) {
          if (!resp.ok) throw new Error("status " + resp.status);
          return resp.json();
        })
        .then(function (data) {
          fails = 0;
          var stalled = document.getElementById("processing-stalled");
          if (stalled) stalled.hidden = true;
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
