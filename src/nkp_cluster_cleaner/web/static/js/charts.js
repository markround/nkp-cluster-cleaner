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
 * reason — a fourth slot would not clear the all-pairs floors.
 *
 * Single-series charts use one hue and carry their categories on the axis, so
 * they need no categorical palette at all.
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
    initTableToggles: initTableToggles,
  };
})(window);
