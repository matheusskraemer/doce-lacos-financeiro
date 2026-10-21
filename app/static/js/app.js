/* Doce Laços — comportamento da interface (sem scripts inline, compatível com CSP). */
(function () {
  "use strict";

  // ---------------------------------------------------------------- dinheiro
  function parseBR(v) {
    if (v == null) return 0;
    var s = String(v).replace(/[^\d,.\-]/g, "");
    if (!s) return 0;
    if (s.indexOf(",") >= 0 && s.indexOf(".") >= 0) s = s.replace(/\./g, "").replace(",", ".");
    else if (s.indexOf(",") >= 0) s = s.replace(",", ".");
    else if (/^\d{1,3}(\.\d{3})+$/.test(s)) s = s.replace(/\./g, "");
    var n = parseFloat(s);
    return isNaN(n) ? 0 : n;
  }
  var fmt = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  function brl(n) { return fmt.format(Math.round(n * 100) / 100); }
  window.DL = { parseBR: parseBR, brl: brl };

  document.addEventListener("blur", function (e) {
    var el = e.target;
    if (!el.matches || !el.matches("[data-money]") || !el.value.trim()) return;
    var n = parseBR(el.value);
    el.value = n.toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }, true);

  // ---------------------------------------------------------------- menu lateral
  var sidebar = document.getElementById("sidebar");
  document.querySelectorAll("[data-toggle-sidebar]").forEach(function (b) {
    b.addEventListener("click", function (ev) { ev.stopPropagation(); sidebar && sidebar.classList.toggle("open"); });
  });
  document.addEventListener("click", function (e) {
    if (sidebar && sidebar.classList.contains("open") && !sidebar.contains(e.target)) sidebar.classList.remove("open");
  });

  // ---------------------------------------------------------------- mensagens
  document.querySelectorAll("[data-dismiss-flash]").forEach(function (b) {
    b.addEventListener("click", function () { b.closest(".flash").remove(); });
  });

  // ---------------------------------------------------------------- confirmações
  document.addEventListener("click", function (e) {
    var el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.getAttribute("data-confirm"))) { e.preventDefault(); e.stopPropagation(); }
  }, true);

  // ---------------------------------------------------------------- envio único (evita duplo clique)
  document.querySelectorAll("form[data-once]").forEach(function (form) {
    form.addEventListener("submit", function (e) {
      if (form.dataset.sent) { e.preventDefault(); return; }
      form.dataset.sent = "1";
      setTimeout(function () {
        form.querySelectorAll("button[type=submit]").forEach(function (b) { b.disabled = true; });
      }, 0);
    });
  });

  // ---------------------------------------------------------------- filtros que se aplicam sozinhos
  document.querySelectorAll("[data-autosubmit]").forEach(function (el) {
    el.addEventListener("change", function () { el.form.submit(); });
  });

  // ---------------------------------------------------------------- mostrar/esconder blocos por opção
  function applyReveal() {
    document.querySelectorAll("input[data-reveal]").forEach(function (r) {
      if (!r.checked) return;
      var show = document.querySelector(r.getAttribute("data-reveal"));
      var hide = document.querySelector(r.getAttribute("data-hide"));
      if (show) show.hidden = false;
      if (hide) hide.hidden = true;
    });
  }
  document.querySelectorAll("input[data-reveal]").forEach(function (r) { r.addEventListener("change", applyReveal); });
  applyReveal();

  // ---------------------------------------------------------------- barras de progresso
  document.querySelectorAll("[data-width]").forEach(function (el) {
    var w = Math.max(0, Math.min(100, parseFloat(String(el.getAttribute("data-width")).replace(",", ".")) || 0));
    el.style.width = w + "%";
  });

  // ---------------------------------------------------------------- cores (configurações)
  document.querySelectorAll("[data-color-sync]").forEach(function (picker) {
    var text = document.querySelector(picker.getAttribute("data-color-sync"));
    if (!text) return;
    picker.addEventListener("input", function () { text.value = picker.value.toUpperCase(); });
    text.addEventListener("input", function () { if (/^#[0-9a-f]{6}$/i.test(text.value)) picker.value = text.value; });
  });

  // ---------------------------------------------------------------- itens de venda/compra
  document.querySelectorAll("form[data-sale-form]").forEach(function (form) {
    var table = form.querySelector("[data-items]");
    var tbody = table && table.querySelector("tbody");
    var netEl = form.querySelector("[data-net]");
    var grossInput = form.querySelector("#gross_amount");
    var discountInput = form.querySelector("#discount");
    var grossWrap = form.querySelector("[data-gross-wrap]");

    function recalc() {
      var sum = 0, hasItems = false;
      tbody.querySelectorAll("[data-item-row]").forEach(function (row) {
        var q = parseBR(row.querySelector("[data-qty]").value || "1");
        var p = parseBR(row.querySelector("[data-price]").value);
        var desc = row.querySelector("[name=item_desc]").value.trim();
        var prod = row.querySelector("[data-product-select]").value;
        var line = q * p;
        row.querySelector("[data-line-total]").textContent = brl(line);
        if (p > 0 || desc || prod) hasItems = true;
        sum += line;
      });
      var gross = hasItems ? sum : parseBR(grossInput ? grossInput.value : 0);
      if (grossWrap && grossInput) {
        grossInput.readOnly = hasItems;
        if (hasItems) grossInput.value = sum ? sum.toLocaleString("pt-BR", { minimumFractionDigits: 2 }) : "";
      }
      var disc = discountInput ? parseBR(discountInput.value) : 0;
      if (netEl) netEl.textContent = brl(Math.max(0, gross - disc));
    }

    function wire(row) {
      var sel = row.querySelector("[data-product-select]");
      sel.addEventListener("change", function () {
        var opt = sel.selectedOptions[0];
        var price = row.querySelector("[data-price]");
        var desc = row.querySelector("[name=item_desc]");
        if (opt && opt.value) {
          if (!desc.value.trim()) desc.value = opt.getAttribute("data-name") || "";
          if (!price.value.trim() && opt.getAttribute("data-price")) price.value = opt.getAttribute("data-price");
        }
        recalc();
      });
      row.querySelectorAll("input").forEach(function (i) { i.addEventListener("input", recalc); });
      row.querySelector("[data-remove-item]").addEventListener("click", function () {
        if (tbody.querySelectorAll("[data-item-row]").length > 1) row.remove();
        else row.querySelectorAll("input").forEach(function (i) { i.value = i.hasAttribute("data-qty") ? "1" : ""; }), sel.value = "";
        recalc();
      });
    }

    if (tbody) {
      tbody.querySelectorAll("[data-item-row]").forEach(wire);
      form.querySelector("[data-add-item]").addEventListener("click", function () {
        var tpl = tbody.querySelector("[data-item-row]");
        var row = tpl.cloneNode(true);
        row.querySelectorAll("input").forEach(function (i) { i.value = i.hasAttribute("data-qty") ? "1" : ""; i.readOnly = false; });
        row.querySelector("[data-product-select]").value = "";
        tbody.appendChild(row);
        wire(row);
        row.querySelector("[data-product-select]").focus();
        recalc();
      });
    }
    [grossInput, discountInput].forEach(function (i) { if (i) i.addEventListener("input", recalc); });
    if (tbody) recalc();
  });
})();
