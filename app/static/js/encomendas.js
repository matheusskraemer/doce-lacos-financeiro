/* Encomendas: Kanban com arrastar e soltar, formulário com itens e descontos. */
(function () {
  "use strict";
  var DL = window.DL || { parseBR: function (v) { return parseFloat(String(v).replace(/\./g, "").replace(",", ".")) || 0; },
                          brl: function (n) { return "R$ " + n.toFixed(2); } };
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || "";

  // ---------------------------------------------------------------- Kanban
  var board = document.querySelector("[data-kanban]");
  if (board) {
    var baseUrl = board.getAttribute("data-status-url"); // .../encomendas/0/status
    var dragged = null;
    board.querySelectorAll(".kcard[draggable=true]").forEach(function (card) {
      card.addEventListener("dragstart", function (e) {
        dragged = card;
        card.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
        e.dataTransfer.setData("text/plain", card.dataset.orderId);
        var allowed = (card.dataset.allowed || "").split(",");
        board.querySelectorAll(".kcol").forEach(function (col) {
          if (allowed.indexOf(col.dataset.column) >= 0) col.classList.add("can-drop");
        });
      });
      card.addEventListener("dragend", function () {
        card.classList.remove("dragging");
        board.querySelectorAll(".kcol").forEach(function (c) { c.classList.remove("can-drop", "over"); });
      });
    });
    board.querySelectorAll(".kcol").forEach(function (col) {
      col.addEventListener("dragover", function (e) {
        if (col.classList.contains("can-drop")) { e.preventDefault(); col.classList.add("over"); }
      });
      col.addEventListener("dragleave", function () { col.classList.remove("over"); });
      col.addEventListener("drop", function (e) {
        e.preventDefault();
        col.classList.remove("over");
        if (!dragged || !col.classList.contains("can-drop")) return;
        var to = col.dataset.column;
        var card = dragged;
        if (to === "entregue" && !window.confirm("Confirmar a entrega? Esta etapa é final e não registra pagamento.")) return;
        fetch(baseUrl.replace("/0/", "/" + card.dataset.orderId + "/"), {
          method: "POST",
          headers: { "Content-Type": "application/json", "Accept": "application/json", "X-CSRFToken": csrf },
          body: JSON.stringify({ to: to }),
          credentials: "same-origin",
        }).then(function (r) { return r.json().then(function (j) { return { ok: r.ok, j: j }; }); })
          .then(function (res) {
            if (!res.ok || !res.j.ok) { window.alert(res.j.error || "Não foi possível mover a encomenda."); return; }
            window.location.reload();
          })
          .catch(function () { window.alert("Sem conexão com o servidor. Tente novamente."); });
      });
    });
  }
  document.querySelectorAll(".kcard-move select").forEach(function (sel) {
    sel.addEventListener("change", function (e) {
      if (sel.value === "entregue" && !window.confirm("Confirmar a entrega? Esta etapa é final e não registra pagamento.")) {
        e.stopImmediatePropagation(); sel.value = "";
      }
    }, true);
  });

  // ---------------------------------------------------------------- formulário
  var form = document.querySelector("form[data-order-form]");
  if (!form) return;
  var tbody = form.querySelector("[data-items] tbody");
  var orderDisc = form.querySelector("#discount");
  function recalc() {
    if (window.DL) DL = window.DL;
    var gross = 0, disc = 0;
    tbody.querySelectorAll("[data-item-row]").forEach(function (row) {
      var q = DL.parseBR(row.querySelector("input[data-qty]").value || "1");
      var p = DL.parseBR(row.querySelector("input[data-price]").value);
      var d = DL.parseBR(row.querySelector("input[data-item-discount]").value);
      gross += q * p; disc += d;
      row.querySelector("[data-line-total]").textContent = DL.brl(Math.max(0, q * p - d));
    });
    disc += DL.parseBR(orderDisc ? orderDisc.value : 0);
    form.querySelector("[data-gross]").textContent = DL.brl(gross);
    form.querySelector("[data-discounts]").textContent = "− " + DL.brl(disc);
    form.querySelector("[data-net]").textContent = DL.brl(Math.max(0, gross - disc));
  }
  function wire(row) {
    var sel = row.querySelector("[data-product-select]");
    sel.addEventListener("change", function () {
      var opt = sel.selectedOptions[0];
      var desc = row.querySelector("[name=item_desc]"), price = row.querySelector("input[data-price]");
      if (opt && opt.value) {
        if (!desc.value.trim()) desc.value = opt.getAttribute("data-name") || "";
        if (!price.value.trim() && opt.getAttribute("data-price")) price.value = opt.getAttribute("data-price");
      }
      recalc();
    });
    row.querySelectorAll("input").forEach(function (i) { i.addEventListener("input", recalc); });
    var rm = row.querySelector("[data-remove-item]");
    rm.addEventListener("click", function () {
      if (tbody.querySelectorAll("[data-item-row]").length > 1) row.remove();
      else { row.querySelectorAll("input:not([type=hidden])").forEach(function (i) { i.value = i.hasAttribute("data-qty") ? "1" : ""; }); sel.value = ""; }
      recalc();
    });
  }
  tbody.querySelectorAll("[data-item-row]").forEach(wire);
  var add = form.querySelector("[data-add-item]");
  if (add) add.addEventListener("click", function () {
    var row = tbody.querySelector("[data-item-row]").cloneNode(true);
    row.querySelectorAll("input").forEach(function (i) { i.value = i.hasAttribute("data-qty") ? "1" : ""; });
    row.querySelector("[data-product-select]").value = "";
    tbody.appendChild(row); wire(row); recalc();
    row.querySelector("[data-product-select]").focus();
  });
  if (orderDisc) orderDisc.addEventListener("input", recalc);
  recalc();

  var mode = form.querySelector("[data-delivery-mode]");
  function applyMode() {
    form.querySelectorAll("[data-when-mode]").forEach(function (el) {
      var show = el.getAttribute("data-when-mode").split(",").indexOf(mode.value) >= 0;
      el.hidden = !show;
      var addr = el.querySelector("#delivery_address");
      if (addr) addr.required = show;
    });
  }
  if (mode) { mode.addEventListener("change", applyMode); applyMode(); }
})();
