/*
 * Chart theming and builders.
 *
 * The palette was validated with the data-viz colour checks against this app's
 * white card surface (#ffffff):
 *
 *   slots 1-3, all-pairs, light
 *   CVD separation      worst ΔE 9.2 (deutan)   [target >= 8]
 *   Normal-vision floor worst ΔE 27.5           [floor >= 15]
 *   Contrast vs surface aqua 2.82               [WARN, below 3:1]
 *
 * The contrast warning is why every chart on the page also ships a table view:
 * identity never rests on colour alone. Series are capped at three for the same
 * reason - a fourth slot would not clear the all-pairs floors.
 *
 * Single-series charts use one hue and carry their categories on the axis, so
 * they need no categorical palette at all.
 *
 * The donut palette is a separate, wider set, because a ring's neighbours
 * change as categories come and go. It was validated all-pairs so any subset in
 * any order still holds:
 *
 *   6 slots, all-pairs, light, surface #ffffff
 *   CVD separation      worst #e87ba4↔#199e70 ΔE 8.3 (protan)   [target >= 8]
 *   Normal-vision floor worst #7a3e8f↔#1b6bdb ΔE 17.9           [floor >= 15]
 *   Contrast vs surface yellow 2.17, magenta 2.69  [WARN, below 3:1]
 *
 * That warning is covered the same way: the donut's legend spells out every
 * slice with its count, and the table view is one click away.
 */
(function (global) {
  "use strict";

  const PALETTE = {
    // Categorical, in fixed order. Never cycled, never extended by generating
    // a new hue.
    series: ["#1b6bdb", "#eb6834", "#1baf7a"],

    // Sequential hue for single-series magnitude charts.
    primary: "#1b6bdb",
    primaryFill: "rgba(27, 107, 219, 0.14)",

    // Ordinal ramp, urgent (dark) to distant (light). Stops no lighter than
    // step 250, which is the lightest that still clears 2:1 on white.
    ordinal: ["#0d366b", "#184f95", "#256abf", "#3987e5", "#86b6ef"],

    // Donut slices. Six slots, safe in any order or subset. Never cycled: a
    // seventh category takes `sliceOther` rather than a repeat.
    slices: ["#1b6bdb", "#199e70", "#eda100", "#e87ba4", "#7a3e8f", "#b5651d"],
    sliceOther: "#5c6b7a",

    ink: "#1f2933",
    muted: "#5c6b7a",
    faint: "#8695a4",
    grid: "#e8ebef",
    axis: "#ccd3db",
    surface: "#ffffff",
  };

  const FONT =
    'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif';

  /** Apply the shared look to every chart on the page. */
  function applyDefaults() {
    if (!global.Chart) return;

    const C = global.Chart;
    C.defaults.font.family = FONT;
    C.defaults.font.size = 12;
    C.defaults.color = PALETTE.muted;
    C.defaults.borderColor = PALETTE.grid;
    C.defaults.maintainAspectRatio = false;
    C.defaults.animation = { duration: 240 };

    // The page draws its own legends, so identity is readable before any
    // JavaScript runs and survives the chart failing to load.
    C.defaults.plugins.legend.display = false;

    C.defaults.plugins.tooltip.backgroundColor = "#1f2933";
    C.defaults.plugins.tooltip.titleColor = "#ffffff";
    C.defaults.plugins.tooltip.bodyColor = "#e3e8ee";
    C.defaults.plugins.tooltip.padding = 10;
    C.defaults.plugins.tooltip.cornerRadius = 3;
    C.defaults.plugins.tooltip.displayColors = true;
    C.defaults.plugins.tooltip.boxWidth = 8;
    C.defaults.plugins.tooltip.boxHeight = 8;
  }

  /** Axis styling shared by every cartesian chart. */
  function scales(options) {
    const opts = options || {};
    return {
      x: {
        grid: { display: opts.xGrid === true, color: PALETTE.grid, drawTicks: false },
        border: { color: PALETTE.axis },
        ticks: { color: PALETTE.faint, maxRotation: 0, autoSkipPadding: 12 },
        beginAtZero: opts.xZero === true,
      },
      y: {
        grid: { color: PALETTE.grid, drawTicks: false },
        border: { display: false },
        ticks: { color: PALETTE.faint, precision: 0 },
        beginAtZero: true,
      },
    };
  }

  /**
   * A time series with up to three named series.
   *
   * @param {HTMLCanvasElement} canvas target canvas
   * @param {string[]} labels x-axis labels
   * @param {{label: string, data: number[]}[]} series at most three
   */
  function lineChart(canvas, labels, series) {
    return new global.Chart(canvas, {
      type: "line",
      data: {
        labels: labels,
        datasets: series.slice(0, PALETTE.series.length).map(function (s, i) {
          return {
            label: s.label,
            data: s.data,
            borderColor: PALETTE.series[i],
            backgroundColor: PALETTE.series[i],
            borderWidth: 2,
            pointRadius: 0,
            pointHoverRadius: 4,
            pointHoverBorderWidth: 2,
            // A 2px surface ring keeps overlapping points legible.
            pointHoverBorderColor: PALETTE.surface,
            tension: 0.25,
            fill: false,
          };
        }),
      },
      options: {
        responsive: true,
        interaction: { mode: "index", intersect: false },
        scales: scales(),
      },
    });
  }

  /**
   * A single-series horizontal bar chart: magnitude, one hue, categories on
   * the axis.
   *
   * @param {HTMLCanvasElement} canvas target canvas
   * @param {string[]} labels category names
   * @param {number[]} values one value per label
   * @param {string[]} [colors] optional per-bar colours for ordinal data
   */
  function barChart(canvas, labels, values, colors) {
    return new global.Chart(canvas, {
      type: "bar",
      data: {
        labels: labels,
        datasets: [
          {
            data: values,
            backgroundColor: colors || PALETTE.primary,
            borderWidth: 0,
            borderRadius: 3,
            // Thin marks, with a gap between adjacent bars.
            barPercentage: 0.7,
            categoryPercentage: 0.82,
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        scales: {
          x: {
            grid: { color: PALETTE.grid, drawTicks: false },
            border: { display: false },
            ticks: { color: PALETTE.faint, precision: 0 },
            beginAtZero: true,
          },
          y: {
            grid: { display: false },
            border: { color: PALETTE.axis },
            ticks: {
              color: PALETTE.muted,
              // Namespace names in particular run long enough to eat the plot
              // area. Truncate the tick; the tooltip and the table view still
              // carry the full value.
              callback: function (value) {
                const label = String(this.getLabelForValue(value));
                return label.length > 22 ? label.slice(0, 21) + "…" : label;
              },
            },
          },
        },
        plugins: {
          tooltip: {
            callbacks: {
              // Restore the untruncated name in the tooltip.
              title: function (items) {
                return items.length ? items[0].label : "";
              },
            },
          },
        },
      },
    });
  }

  /**
   * Draws the running total in the hole. Registered per chart rather than
   * globally, so only donuts pay for it.
   */
  const centreTotal = {
    id: "nkpCentreTotal",
    afterDraw: function (chart) {
      const caption = chart.options.centreCaption;
      if (!caption) return;

      const area = chart.chartArea;
      const ctx = chart.ctx;
      const x = (area.left + area.right) / 2;
      const y = (area.top + area.bottom) / 2;
      const total = chart.data.datasets[0].data.reduce(function (sum, value) {
        return sum + (Number(value) || 0);
      }, 0);

      ctx.save();
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      ctx.fillStyle = PALETTE.muted;
      ctx.font = "12px " + FONT;
      ctx.fillText(caption, x, y - 11);

      // The figure is the one loud thing here, so it stays in ink and takes
      // proportional digits - this is a standalone number, not a column.
      ctx.fillStyle = PALETTE.ink;
      ctx.font = "500 22px " + FONT;
      ctx.fillText(total.toLocaleString(), x, y + 9);

      ctx.restore();
    },
  };

  /**
   * A part-to-whole ring: one arc per category, the total in the hole.
   *
   * Only for a complete whole with at most six slices. The page draws the
   * legend as a list of rows carrying each slice's count, so the ring answers
   * "what share" at a glance and never has to be read for its values.
   *
   * @param {HTMLCanvasElement} canvas target canvas
   * @param {string[]} labels category names, in a fixed order
   * @param {number[]} values one value per label
   * @param {string[]} colors one colour per label, keyed to the category
   * @param {string} [caption] word above the total, e.g. "Total"
   */
  function donutChart(canvas, labels, values, colors, caption) {
    return new global.Chart(canvas, {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [
          {
            data: values,
            backgroundColor: colors,
            // A 2px gap in the surface separates neighbouring arcs. No stroke:
            // a border would add ink that isn't data.
            borderWidth: 0,
            spacing: 2,
            hoverOffset: 4,
          },
        ],
      },
      options: {
        responsive: true,
        cutout: "78%",
        centreCaption: caption || "Total",
        layout: { padding: 4 },
        plugins: {
          tooltip: {
            callbacks: {
              label: function (item) {
                const data = item.dataset.data;
                const total = data.reduce(function (sum, value) {
                  return sum + (Number(value) || 0);
                }, 0);
                const share = total ? Math.round((item.parsed / total) * 100) : 0;
                return " " + item.parsed + " (" + share + "%)";
              },
            },
          },
        },
      },
      plugins: [centreTotal],
    });
  }

  /** Wire up the "show as table" toggles. */
  function initTableToggles() {
    document.querySelectorAll("[data-table-toggle]").forEach(function (btn) {
      const table = document.getElementById(btn.dataset.tableToggle);
      if (!table) return;

      btn.addEventListener("click", function () {
        const hidden = table.hasAttribute("hidden");
        if (hidden) {
          table.removeAttribute("hidden");
          btn.textContent = "Hide table";
        } else {
          table.setAttribute("hidden", "");
          btn.textContent = "Show as table";
        }
      });
    });
  }

  global.NkpCharts = {
    palette: PALETTE,
    applyDefaults: applyDefaults,
    line: lineChart,
    bar: barChart,
    donut: donutChart,
    initTableToggles: initTableToggles,
  };
})(window);
