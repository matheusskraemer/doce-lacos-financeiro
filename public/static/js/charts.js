/* Gráficos (Chart.js). Dados vêm de blocos JSON gerados pelo servidor a partir do banco. */
(function () {
  "use strict";
  if (!window.Chart) return;
  var css = getComputedStyle(document.documentElement);
  function v(name, fb) { var x = css.getPropertyValue(name).trim(); return x || fb; }
  var C = {
    brand: v("--brand", "#8E4A67"), accent: v("--accent", "#B8915A"), ink: v("--ink", "#2E2433"),
    pos: v("--pos", "#2E7A5A"), neg: v("--neg", "#AE3B4A"), proj: v("--proj", "#5B6B8C"),
    muted: v("--muted", "#6E6574"), line: v("--line", "#E6E0E8"), brandSoft: v("--brand-soft", "#f4ebef"),
  };
  var palette = [C.brand, C.accent, C.proj, "#7C9A86", "#C27C8F", "#8C7AA8", "#B5A27A", "#6E8FA0"];
  var brl = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
  var compact = new Intl.NumberFormat("pt-BR", { notation: "compact", maximumFractionDigits: 2 });

  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.font.size = 12;
  Chart.defaults.color = C.muted;
  Chart.defaults.borderColor = C.line;
  Chart.defaults.plugins.legend.labels.boxWidth = 10;
  Chart.defaults.plugins.legend.labels.boxHeight = 10;
  Chart.defaults.plugins.tooltip.callbacks.label = function (ctx) {
    var val = ctx.parsed && typeof ctx.parsed === "object" ? ctx.parsed.y : ctx.parsed;
    if (val == null) return null;
    return (ctx.dataset.label ? ctx.dataset.label + ": " : (ctx.label ? ctx.label + ": " : "")) + brl.format(val);
  };
  Chart.defaults.maintainAspectRatio = false;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  if (reduce) Chart.defaults.animation = false;

  var yMoney = { ticks: { callback: function (x) { return "R$ " + compact.format(x); } }, grid: { color: C.line } };
  var xPlain = { grid: { display: false } };

  function el(id) { return document.getElementById(id); }
  function data(id) { var n = el(id); return n ? JSON.parse(n.textContent) : null; }

  var d = data("dash-data");
  if (d) {
    if (el("chart-cash")) new Chart(el("chart-cash"), {
      data: { labels: d.cash.labels, datasets: [
        { type: "bar", label: "Entradas", data: d.cash.in, backgroundColor: C.pos, borderRadius: 3, maxBarThickness: 18, order: 2 },
        { type: "bar", label: "Saídas", data: d.cash.out, backgroundColor: C.neg, borderRadius: 3, maxBarThickness: 18, order: 2 },
        { type: "line", label: "Saldo", data: d.cash.balance, borderColor: C.ink, backgroundColor: C.ink, pointRadius: 0, tension: .25, borderWidth: 2, yAxisID: "y1", order: 1, spanGaps: true },
      ] },
      options: { interaction: { mode: "index", intersect: false }, scales: { x: xPlain, y: yMoney, y1: Object.assign({}, yMoney, { position: "right", grid: { display: false } }) } },
    });
    if (el("chart-sales")) new Chart(el("chart-sales"), {
      type: "line",
      data: { labels: d.sales.labels, datasets: [{ label: "Vendas", data: d.sales.values, borderColor: C.brand, backgroundColor: C.brandSoft, fill: true, tension: .3, pointRadius: 0, borderWidth: 2 }] },
      options: { plugins: { legend: { display: false } }, interaction: { mode: "index", intersect: false }, scales: { x: xPlain, y: yMoney } },
    });
    if (el("chart-months")) new Chart(el("chart-months"), {
      type: "bar",
      data: { labels: d.months.labels, datasets: [
        { label: "Recebido", data: d.months.received, backgroundColor: C.pos, borderRadius: 3, maxBarThickness: 22 },
        { label: "Pago", data: d.months.paid, backgroundColor: C.neg, borderRadius: 3, maxBarThickness: 22 },
        { type: "line", label: "Vendas", data: d.months.sales, borderColor: C.brand, backgroundColor: C.brand, pointRadius: 3, tension: .25 },
      ] },
      options: { interaction: { mode: "index", intersect: false }, scales: { x: xPlain, y: yMoney } },
    });
    function doughnut(id, set, colors, emptyText) {
      var node = el(id);
      if (!node) return;
      var total = set.values.reduce(function (a, b) { return a + b; }, 0);
      if (!total) { node.parentNode.innerHTML = '<p class="text-muted mb-0">' + emptyText + "</p>"; return; }
      new Chart(node, {
        type: "doughnut",
        data: { labels: set.labels, datasets: [{ data: set.values, backgroundColor: colors, borderColor: "#fff", borderWidth: 2 }] },
        options: { cutout: "62%", plugins: { legend: { position: "right" } } },
      });
    }
    doughnut("chart-categories", d.categories, palette, "Nenhuma despesa paga neste período.");
    doughnut("chart-split", d.split, [C.proj, C.accent], "Nenhum pagamento neste período.");
  }

  var f = data("flow-data");
  if (f && el("chart-flow")) {
    new Chart(el("chart-flow"), {
      data: { labels: f.labels, datasets: [
        { type: "line", label: "Saldo realizado", data: f.realized, borderColor: C.ink, backgroundColor: C.ink, pointRadius: 0, borderWidth: 2, tension: .2, spanGaps: false },
        { type: "line", label: "Saldo projetado", data: f.projected, borderColor: C.proj, backgroundColor: C.proj, borderDash: [6, 4], pointRadius: 0, borderWidth: 2, tension: .2 },
        { type: "bar", label: "Entradas", data: f.in, backgroundColor: C.pos, maxBarThickness: 14, borderRadius: 2, yAxisID: "y1" },
        { type: "bar", label: "Saídas", data: f.out.map(function (x) { return -x; }), backgroundColor: C.neg, maxBarThickness: 14, borderRadius: 2, yAxisID: "y1" },
        { type: "bar", label: "Previsto a receber", data: f.pin, backgroundColor: "rgba(46,122,90,.28)", maxBarThickness: 14, borderRadius: 2, yAxisID: "y1" },
        { type: "bar", label: "Previsto a pagar", data: f.pout.map(function (x) { return -x; }), backgroundColor: "rgba(174,59,74,.28)", maxBarThickness: 14, borderRadius: 2, yAxisID: "y1" },
      ] },
      options: { interaction: { mode: "index", intersect: false }, scales: { x: Object.assign({ stacked: true }, xPlain), y: yMoney, y1: Object.assign({}, yMoney, { position: "right", grid: { display: false } }) } },
    });
  }
})();
