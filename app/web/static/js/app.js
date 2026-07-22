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
  const levelLines = {
    id: "levelLines",
    afterDatasetsDraw(chart, args, options) {
      const levels = (options && options.levels) || [];
      const { ctx, chartArea, scales } = chart;
      if (!chartArea) return;
      levels.forEach(function (level) {
        const y = scales.y.getPixelForValue(level.value);
        if (y < chartArea.top || y > chartArea.bottom) return;
        const color = COLORS[level.kind] || COLORS.invalidation;
        ctx.save();
        ctx.beginPath();
        ctx.setLineDash(level.kind === "entry" ? [] : [5, 4]);
        ctx.strokeStyle = color;
        ctx.lineWidth = level.kind === "entry" ? 1.6 : 1.2;
        ctx.moveTo(chartArea.left, y);
        ctx.lineTo(chartArea.right, y);
        ctx.stroke();

        const label = level.label + " " + level.value;
        ctx.font = "600 11px system-ui, sans-serif";
        const width = ctx.measureText(label).width + 10;
        ctx.fillStyle = color;
        ctx.fillRect(chartArea.right - width, y - 15, width, 14);
        ctx.fillStyle = "#fff";
        ctx.fillText(label, chartArea.right - width + 5, y - 4);
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

    const markerPoints = (payload.markers || [])
      .filter(function (marker) {
        return marker.t;
      })
      .map(function (marker) {
        let index = 0;
        let best = Infinity;
        const target = new Date(marker.t).getTime();
        series.forEach(function (point, i) {
          const delta = Math.abs(new Date(point.t).getTime() - target);
          if (delta < best) {
            best = delta;
            index = i;
          }
        });
        return { x: index, y: marker.value, label: marker.label, kind: marker.kind };
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
            pointRadius: 7,
            pointHoverRadius: 9,
            pointStyle: "triangle",
            pointBackgroundColor: markerPoints.map(function (point) {
              return COLORS[point.kind] || COLORS.entry;
            }),
            pointBorderColor: "#fff",
            pointBorderWidth: 2,
            parsing: false,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            type: "category",
            grid: { color: COLORS.grid },
            ticks: { maxTicksLimit: 10, font: { size: 11 } },
          },
          y: {
            position: "right",
            grid: { color: COLORS.grid },
            ticks: { font: { size: 11 } },
          },
        },
        plugins: {
          legend: { display: false },
          levelLines: { levels: payload.levels || [] },
          tooltip: {
            callbacks: {
              label: function (context) {
                if (context.datasetIndex === 1) {
                  const marker = markerPoints[context.dataIndex];
                  return marker ? marker.label + ": " + marker.y : "";
                }
                return "Цена: " + context.formattedValue;
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
    buildChart(canvas, payload);
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
