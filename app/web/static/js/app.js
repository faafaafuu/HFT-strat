(function () {
  "use strict";

  const COLORS = {
    price: "#2f6fed",
    entry: "#23211c",
    stop: "#d1453b",
    take: "#1f9254",
    exit: "#b7791f",
    invalidation: "#8a8478",
    grid: "rgba(35, 33, 28, 0.08)",
  };

  // Prices run from 0.00001 (memecoins) to 100000 (BTC); a fixed precision would
  // either bury small ones in zeros or print unreadable tails on large ones.
  function decimalsFor(value) {
    const size = Math.abs(value);
    if (size >= 1000) return 0;
    if (size >= 100) return 1;
    if (size >= 10) return 2;
    if (size >= 1) return 3;
    if (size >= 0.01) return 4;
    return 6;
  }

  function formatPrice(value, reference) {
    if (value === null || value === undefined || Number.isNaN(value)) return "";
    return Number(value).toLocaleString("ru-RU", {
      minimumFractionDigits: 0,
      maximumFractionDigits: decimalsFor(reference === undefined ? value : reference),
    });
  }

  function formatTime(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return iso;
    return date.toLocaleString("ru-RU", {
      day: "2-digit",
      month: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      timeZone: "UTC",
    });
  }

  // Chart.js has no built-in horizontal-line annotation without the plugin, so draw them here.
  const LABEL_HEIGHT = 15;

  const levelLines = {
    id: "levelLines",
    afterDatasetsDraw(chart, args, options) {
      const levels = (options && options.levels) || [];
      const { ctx, chartArea, scales } = chart;
      if (!chartArea || !levels.length) return;

      const reference = levels[0].value;
      const placed = [];
      // Nearby levels would otherwise stack their labels on top of each other.
      const drawable = levels
        .map(function (level) {
          return { level: level, y: scales.y.getPixelForValue(level.value) };
        })
        .filter(function (item) {
          return item.y >= chartArea.top && item.y <= chartArea.bottom;
        })
        .sort(function (a, b) {
          return a.y - b.y;
        });

      drawable.forEach(function (item) {
        const level = item.level;
        const color = COLORS[level.kind] || COLORS.invalidation;

        ctx.save();
        ctx.beginPath();
        ctx.setLineDash(level.kind === "entry" ? [] : [5, 4]);
        ctx.strokeStyle = color;
        ctx.lineWidth = level.kind === "entry" ? 1.6 : 1.2;
        ctx.moveTo(chartArea.left, item.y);
        ctx.lineTo(chartArea.right, item.y);
        ctx.stroke();

        let labelY = item.y;
        placed.forEach(function (taken) {
          if (Math.abs(labelY - taken) < LABEL_HEIGHT + 3) {
            labelY = taken + LABEL_HEIGHT + 3;
          }
        });
        labelY = Math.min(labelY, chartArea.bottom - 2);
        placed.push(labelY);

        const label = level.label + " " + formatPrice(level.value, reference);
        ctx.font = "600 11px system-ui, sans-serif";
        const width = ctx.measureText(label).width + 12;
        ctx.setLineDash([]);
        ctx.fillStyle = color;
        ctx.fillRect(chartArea.right - width, labelY - LABEL_HEIGHT, width, LABEL_HEIGHT);
        ctx.fillStyle = "#fff";
        ctx.fillText(label, chartArea.right - width + 6, labelY - 4);
        ctx.restore();
      });
    },
  };

  // Channel boundaries are two sloped lines per trade, so they cannot be datasets
  // without swamping the legend and the tooltip - they are drawn straight onto the canvas.
  const channelBands = {
    id: "channelBands",
    beforeDatasetsDraw(chart, args, options) {
      const channels = (options && options.channels) || [];
      if (!channels.length || options.hidden) return;
      const { ctx, chartArea, scales } = chart;
      if (!chartArea) return;
      const indexOf = options.indexOf;

      channels.forEach(function (channel) {
        if (options.losersOnly && channel.won) return;
        const x1 = scales.x.getPixelForValue(indexOf(channel.start));
        const x2 = scales.x.getPixelForValue(indexOf(channel.end));
        const color = channel.won ? COLORS.take : COLORS.stop;

        const upper1 = scales.y.getPixelForValue(channel.upper_start);
        const upper2 = scales.y.getPixelForValue(channel.upper_end);
        const lower1 = scales.y.getPixelForValue(channel.lower_start);
        const lower2 = scales.y.getPixelForValue(channel.lower_end);

        ctx.save();
        ctx.beginPath();
        ctx.rect(chartArea.left, chartArea.top, chartArea.width, chartArea.height);
        ctx.clip();

        ctx.globalAlpha = 0.1;
        ctx.fillStyle = color;
        ctx.beginPath();
        ctx.moveTo(x1, upper1);
        ctx.lineTo(x2, upper2);
        ctx.lineTo(x2, lower2);
        ctx.lineTo(x1, lower1);
        ctx.closePath();
        ctx.fill();

        ctx.globalAlpha = 0.85;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.3;
        ctx.setLineDash([6, 4]);
        [[upper1, upper2], [lower1, lower2]].forEach(function (pair) {
          ctx.beginPath();
          ctx.moveTo(x1, pair[0]);
          ctx.lineTo(x2, pair[1]);
          ctx.stroke();
        });
        ctx.restore();
      });
    },
  };

  function buildChart(canvas, payload) {
    const series = payload.series || [];
    const labels = series.map(function (point) {
      return formatTime(point.t);
    });
    const prices = series.map(function (point) {
      return point.c;
    });
    const times = series.map(function (point) {
      return new Date(point.t).getTime();
    });

    // A run can hold hundreds of markers and channel edges over thousands of candles,
    // so each lookup is a binary search rather than a scan.
    function indexOfTime(iso) {
      if (!iso || !times.length) return 0;
      const target = new Date(iso).getTime();
      let low = 0;
      let high = times.length - 1;
      while (low < high) {
        const mid = (low + high) >> 1;
        if (times[mid] < target) low = mid + 1;
        else high = mid;
      }
      if (low > 0 && Math.abs(times[low - 1] - target) < Math.abs(times[low] - target)) {
        return low - 1;
      }
      return low;
    }

    const markerPoints = (payload.markers || [])
      .filter(function (marker) {
        return marker.t;
      })
      .map(function (marker) {
        return {
          x: indexOfTime(marker.t),
          y: marker.value,
          label: marker.label,
          kind: marker.kind,
          tradeId: marker.trade_id,
        };
      });

    return new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Цена",
            data: prices,
            borderColor: COLORS.price,
            backgroundColor: "rgba(47, 111, 237, 0.08)",
            borderWidth: 1.8,
            pointRadius: 0,
            tension: 0.15,
            fill: true,
          },
          {
            label: "События",
            data: markerPoints,
            showLine: false,
            pointRadius: markerPoints.length > 40 ? 5 : 7,
            pointHoverRadius: 10,
            pointStyle: markerPoints.map(function (point) {
              return point.kind === "entry" ? "triangle" : "rectRot";
            }),
            pointBackgroundColor: markerPoints.map(function (point) {
              return COLORS[point.kind] || COLORS.entry;
            }),
            pointBorderColor: "#fff",
            pointBorderWidth: 1.5,
            parsing: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: true },
        onClick: function (event, elements) {
          const hit = elements.find(function (item) {
            return item.datasetIndex === 1;
          });
          if (!hit) return;
          const marker = markerPoints[hit.index];
          if (marker && marker.tradeId) {
            window.location = "/trades/" + marker.tradeId;
          }
        },
        onHover: function (event, elements) {
          const clickable = elements.some(function (item) {
            return item.datasetIndex === 1 && markerPoints[item.index].tradeId;
          });
          event.native.target.style.cursor = clickable ? "pointer" : "default";
        },
        scales: {
          x: {
            type: "category",
            grid: { color: COLORS.grid },
            ticks: { maxTicksLimit: 10, font: { size: 11 }, autoSkip: true },
          },
          y: {
            position: "right",
            grid: { color: COLORS.grid },
            ticks: {
              font: { size: 11 },
              maxTicksLimit: 8,
              callback: function (value) {
                return formatPrice(value, prices[0]);
              },
            },
          },
        },
        plugins: {
          legend: { display: false },
          levelLines: { levels: payload.levels || [] },
          channelBands: {
            channels: payload.channels || [],
            indexOf: indexOfTime,
            hidden: false,
            losersOnly: false,
          },
          zoom: {
            limits: { x: { min: "original", max: "original" } },
            pan: { enabled: true, mode: "x", modifierKey: null },
            zoom: {
              wheel: { enabled: true, speed: 0.08 },
              pinch: { enabled: true },
              drag: { enabled: false },
              mode: "x",
            },
          },
          tooltip: {
            displayColors: false,
            callbacks: {
              label: function (context) {
                if (context.datasetIndex === 1) {
                  const marker = markerPoints[context.dataIndex];
                  if (!marker) return "";
                  return marker.label + ": " + formatPrice(marker.y, prices[0]);
                }
                return "Цена: " + formatPrice(context.parsed.y, prices[0]);
              },
              afterBody: function (items) {
                const marker = items.some(function (item) {
                  return item.datasetIndex === 1;
                });
                return marker ? "Нажмите, чтобы открыть сделку" : "";
              },
            },
          },
        },
      },
      plugins: [levelLines],
    });
  }

  function initCharts() {
    const holder = document.getElementById("chart-data");
    const canvas = document.getElementById("trade-chart") || document.getElementById("signal-chart");
    if (!holder || !canvas || typeof Chart === "undefined") return;
    if (canvas.dataset.rendered === "true") return;
    let payload;
    try {
      payload = JSON.parse(holder.textContent);
    } catch (error) {
      return;
    }
    if (!payload || !payload.series || !payload.series.length) return;
    canvas.dataset.rendered = "true";
    const chart = buildChart(canvas, payload);
    initChartControls(chart, payload);
  }

  function initChartControls(chart, payload) {
    const bands = chart.options.plugins.channelBands;

    const channelToggle = document.getElementById("toggle-channels");
    if (channelToggle) {
      channelToggle.addEventListener("change", function () {
        bands.hidden = !channelToggle.checked;
        chart.update("none");
      });
    }

    const losersToggle = document.getElementById("toggle-losers-only");
    if (losersToggle) {
      losersToggle.addEventListener("change", function () {
        bands.losersOnly = losersToggle.checked;
        chart.update("none");
      });
    }

    const resetButton = document.getElementById("reset-zoom");
    if (resetButton && chart.resetZoom) {
      resetButton.addEventListener("click", function () {
        chart.resetZoom();
      });
    }
    canvasDoubleClickResets(chart);

    // Clicking a row in the trade table zooms the chart onto that trade.
    window.chartFocusTrade = function (tradeId) {
      const channel = (payload.channels || []).find(function (item) {
        return item.trade_id === tradeId;
      });
      const markers = (payload.markers || []).filter(function (item) {
        return item.trade_id === tradeId;
      });
      const first = channel ? channel.start : markers.length ? markers[0].t : null;
      const last = channel ? channel.end : markers.length ? markers[markers.length - 1].t : null;
      if (!first || !last || !chart.zoomScale) return;
      const from = bands.indexOf(first);
      const to = bands.indexOf(last);
      const pad = Math.max(5, Math.round((to - from) * 0.6));
      chart.zoomScale("x", { min: Math.max(0, from - pad), max: to + pad }, "default");
      chart.canvas.scrollIntoView({ behavior: "smooth", block: "center" });
    };
  }

  function canvasDoubleClickResets(chart) {
    if (!chart.resetZoom) return;
    chart.canvas.addEventListener("dblclick", function () {
      chart.resetZoom();
    });
  }

  function initIndicator() {
    const indicator = document.getElementById("htmx-indicator");
    if (!indicator) return;
    document.body.addEventListener("htmx:beforeRequest", function () {
      indicator.classList.add("visible");
    });
    document.body.addEventListener("htmx:afterRequest", function () {
      indicator.classList.remove("visible");
    });
  }

  function initTabs() {
    document.body.addEventListener("htmx:afterOnLoad", function (event) {
      const trigger = event.detail && event.detail.elt;
      if (!trigger || !trigger.closest("#lab-tabs")) return;
      document.querySelectorAll("#lab-tabs a").forEach(function (link) {
        link.classList.remove("active");
      });
      trigger.classList.add("active");
    });
  }

  document.addEventListener("DOMContentLoaded", function () {
    initCharts();
    initIndicator();
    initTabs();
  });
})();
