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

  /* ---------- Vault filters: member, view and search, all in place ----
     The page carries every document; the name pills, the Exams /
     Vaccines toggle and the search box only show and hide. Nothing
     navigates, so the scroll position never resets. The URL (?miembro=,
     ?vista=) stays in sync, so a refresh or a shared link lands on
     the same filter. */
  window.MIFicha.applyVaultFilters = function () {
    var panel = document.querySelector(".vault-panel");
    if (!panel) return;
    var input = document.querySelector("[data-vault-search]");
    var q = input ? input.value.trim().toLowerCase() : "";
    window.MIFicha._vaultQuery = q;
    var member = panel.getAttribute("data-member") || "";
    panel.querySelectorAll("[data-view-section]").forEach(function (section) {
      var visible = 0;
      section.querySelectorAll("[data-search]").forEach(function (el) {
        var matchesQuery = !q || (el.getAttribute("data-search") || "").toLowerCase().indexOf(q) !== -1;
        var group = el.closest("[data-vax-group]");
        var owner = group ? group.getAttribute("data-member") : el.getAttribute("data-member");
        var matchesMember = !member || (owner || "") === member;
        var hit = matchesQuery && matchesMember;
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
      if (empty) {
        empty.hidden = visible !== 0;
        if (visible === 0) {
          var h = empty.querySelector("h2");
          if (h) h.textContent = q ? empty.getAttribute("data-empty-search")
                                   : empty.getAttribute("data-empty-member");
        }
      }
    });
  };

  /* The Exams / Vaccines pill numbers count the selected member only,
     so they move when the name filter does. */
  window.MIFicha.updateVaultCounts = function () {
    var panel = document.querySelector(".vault-panel");
    if (!panel) return;
    var member = panel.getAttribute("data-member") || "";
    var exams = 0, vax = 0;
    panel.querySelectorAll(".doc-card").forEach(function (card) {
      if (!member || (card.getAttribute("data-member") || "") === member) exams++;
    });
    panel.querySelectorAll(".vax-row").forEach(function (row) {
      var g = row.closest("[data-vax-group]");
      if (!member || (g && g.getAttribute("data-member") === member)) vax++;
    });
    var e = panel.querySelector('[data-count="exams"]');
    if (e) e.textContent = exams;
    var v = panel.querySelector('[data-count="vaccines"]');
    if (v) v.textContent = vax;
  };

  window.MIFicha.setVaultMember = function (memberId, push) {
    var panel = document.querySelector(".vault-panel");
    if (!panel) return;
    if (memberId) panel.setAttribute("data-member", memberId);
    else panel.removeAttribute("data-member");
    panel.querySelectorAll("[data-member-filter]").forEach(function (pill) {
      pill.setAttribute("aria-pressed",
        pill.getAttribute("data-member-filter") === (memberId || "") ? "true" : "false");
    });
    if (push) {
      var u = new URL(window.location.href);
      if (memberId) u.searchParams.set("miembro", memberId);
      else u.searchParams.delete("miembro");
      history.pushState(null, "", u.pathname + u.search);
    }
    window.MIFicha.updateVaultCounts();
    window.MIFicha.applyVaultFilters();
  };

  window.MIFicha.initVaultMemberFilter = function () {
    var panel = document.querySelector(".vault-panel");
    if (!panel) return;
    panel.querySelectorAll("[data-member-filter]").forEach(function (pill) {
      pill.addEventListener("click", function () {
        window.MIFicha.setVaultMember(pill.getAttribute("data-member-filter"), true);
      });
    });
  };

  /* ---------- Vault layout: cards or list, same documents ---------- */
  window.MIFicha.initVaultLayout = function () {
    var panel = document.querySelector(".vault-panel");
    if (!panel) return;
    var buttons = panel.querySelectorAll("[data-vault-layout]");
    if (!buttons.length) return;
    function setLayout(layout, push) {
      panel.setAttribute("data-layout", layout);
      buttons.forEach(function (b) {
        b.setAttribute("aria-pressed",
          b.getAttribute("data-vault-layout") === layout ? "true" : "false");
      });
      if (push) {
        var u = new URL(window.location.href);
        if (layout === "list") u.searchParams.set("layout", "list");
        else u.searchParams.delete("layout");
        history.pushState(null, "", u.pathname + u.search);
      }
      if (window.MIFicha.applyVaultFilters) window.MIFicha.applyVaultFilters();
    }
    buttons.forEach(function (b) {
      b.addEventListener("click", function () {
        setLayout(b.getAttribute("data-vault-layout"), true);
      });
    });
  };

  window.MIFicha.initVaultSearch = function () {
    var input = document.querySelector("[data-vault-search]");
    if (!input || input.dataset.vaultSearchBound) return;
    input.dataset.vaultSearchBound = "1";
    input.addEventListener("input", window.MIFicha.applyVaultFilters);
    var saved = window.MIFicha._vaultQuery;
    if (saved) { input.value = saved; window.MIFicha.applyVaultFilters(); }
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
      if (push) {
        var u = new URL(window.location.href);
        if (view === "vacunas") u.searchParams.set("vista", "vacunas");
        else u.searchParams.delete("vista");
        history.pushState(null, "", u.pathname + u.search);
      }
      if (window.MIFicha.applyVaultFilters) window.MIFicha.applyVaultFilters();
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
    window.MIFicha.initVaultMemberFilter();
    window.MIFicha.initVaultLayout();
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
