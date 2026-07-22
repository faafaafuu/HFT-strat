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
    candleUp: "#1f9254",
    candleDown: "#d1453b",
    pointBadge: "#5a544a",
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

  // Chart.js has no candlestick type without the financial plugin and a date adapter,
  // and the series already carries OHLC, so the bodies and wicks are drawn here.
  const candlesticks = {
    id: "candlesticks",
    beforeDatasetsDraw(chart, args, options) {
      const series = (options && options.series) || [];
      if (!series.length || !options.enabled) return;
      const { ctx, chartArea, scales } = chart;
      if (!chartArea) return;

      const first = Math.max(0, Math.floor(scales.x.min));
      const last = Math.min(series.length - 1, Math.ceil(scales.x.max));
      const slotWidth = (chartArea.right - chartArea.left) / Math.max(1, last - first);
      const bodyWidth = Math.max(1, Math.min(14, slotWidth * 0.62));

      ctx.save();
      ctx.beginPath();
      ctx.rect(chartArea.left, chartArea.top, chartArea.width, chartArea.height);
      ctx.clip();

      for (let index = first; index <= last; index += 1) {
        const point = series[index];
        if (!point || point.o === undefined || point.o === null) continue;
        const x = scales.x.getPixelForValue(index);
        const up = point.c >= point.o;
        const color = up ? COLORS.candleUp : COLORS.candleDown;
        const high = scales.y.getPixelForValue(point.h);
        const low = scales.y.getPixelForValue(point.l);
        const open = scales.y.getPixelForValue(point.o);
        const close = scales.y.getPixelForValue(point.c);

        ctx.strokeStyle = color;
        ctx.fillStyle = color;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(x, high);
        ctx.lineTo(x, low);
        ctx.stroke();

        const top = Math.min(open, close);
        const height = Math.max(1, Math.abs(close - open));
        ctx.fillRect(x - bodyWidth / 2, top, bodyWidth, height);
      }
      ctx.restore();
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
        if (options.outcome === "losers" && channel.won) return;
        if (options.outcome === "winners" && !channel.won) return;
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

        ctx.globalAlpha = 0.9;
        ctx.strokeStyle = color;
        ctx.lineWidth = 1.4;
        ctx.setLineDash([]);
        [[upper1, upper2], [lower1, lower2]].forEach(function (pair) {
          ctx.beginPath();
          ctx.moveTo(x1, pair[0]);
          ctx.lineTo(x2, pair[1]);
          ctx.stroke();
        });

        // Risk and reward boxes from the entry, the way a chart tool draws a position.
        const entryX = scales.x.getPixelForValue(indexOf(channel.entry));
        const exitX = scales.x.getPixelForValue(indexOf(channel.exit || channel.end));
        const boxWidth = Math.max(6, exitX - entryX);
        if (channel.entry_price && channel.take_price) {
          const entryY = scales.y.getPixelForValue(channel.entry_price);
          const takeY = scales.y.getPixelForValue(channel.take_price);
          ctx.globalAlpha = 0.16;
          ctx.fillStyle = COLORS.take;
          ctx.fillRect(entryX, Math.min(entryY, takeY), boxWidth, Math.abs(takeY - entryY));
        }
        if (channel.entry_price && channel.stop_price) {
          const entryY = scales.y.getPixelForValue(channel.entry_price);
          const stopY = scales.y.getPixelForValue(channel.stop_price);
          ctx.globalAlpha = 0.16;
          ctx.fillStyle = COLORS.stop;
          ctx.fillRect(entryX, Math.min(entryY, stopY), boxWidth, Math.abs(stopY - entryY));
        }

        // The four touches, numbered as the strategy counts them.
        ctx.globalAlpha = 1;
        ctx.font = "700 11px system-ui, sans-serif";
        ctx.textAlign = "center";
        (channel.points || []).forEach(function (point) {
          const px = scales.x.getPixelForValue(indexOf(point.t));
          const py = scales.y.getPixelForValue(point.value);
          if (px < chartArea.left || px > chartArea.right) return;
          const isEntry = point.number === 4;
          const size = isEntry ? 9 : 8;
          ctx.fillStyle = isEntry ? color : COLORS.pointBadge;
          ctx.strokeStyle = "#fff";
          ctx.lineWidth = 1.5;
          ctx.beginPath();
          ctx.rect(px - size, py - size / 2 - 8, size * 2, size + 6);
          ctx.fill();
          ctx.stroke();
          ctx.fillStyle = "#fff";
          ctx.fillText(String(point.number), px, py - 3);
        });
        ctx.textAlign = "left";
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

    // Candles need OHLC; a snapshot series only has closes, so it stays a line.
    const hasOhlc = series.length > 0 && series[0].o !== undefined && series[0].o !== null;

    return new Chart(canvas.getContext("2d"), {
      type: "line",
      data: {
        labels: labels,
        datasets: [
          {
            label: "Цена",
            data: prices,
            borderColor: hasOhlc ? "rgba(0,0,0,0)" : COLORS.price,
            backgroundColor: hasOhlc ? "rgba(0,0,0,0)" : "rgba(47, 111, 237, 0.08)",
            borderWidth: hasOhlc ? 0 : 1.8,
            pointRadius: 0,
            tension: 0.15,
            fill: !hasOhlc,
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
          candlesticks: { series: series, enabled: hasOhlc },
          levelLines: { levels: payload.levels || [] },
          channelBands: {
            channels: payload.channels || [],
            indexOf: indexOfTime,
            hidden: false,
            outcome: "all",
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
      plugins: [candlesticks, channelBands, levelLines],
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
    const markerDataset = chart.data.datasets[1];
    const allMarkers = markerDataset ? markerDataset.data.slice() : [];
    const wonById = {};
    (payload.channels || []).forEach(function (channel) {
      wonById[channel.trade_id] = channel.won;
    });
    (payload.markers || []).forEach(function (marker) {
      if (marker.trade_id !== undefined && marker.won !== undefined) {
        wonById[marker.trade_id] = marker.won;
      }
    });

    const channelToggle = document.getElementById("toggle-channels");
    if (channelToggle) {
      channelToggle.addEventListener("change", function () {
        bands.hidden = !channelToggle.checked;
        chart.update("none");
      });
    }

    // One filter drives the markers, the channels and the trade table together.
    function applyOutcome(outcome) {
      bands.outcome = outcome;
      if (markerDataset) {
        markerDataset.data = allMarkers.filter(function (point) {
          if (outcome === "all") return true;
          const won = wonById[point.tradeId];
          if (won === undefined) return true;
          return outcome === "winners" ? won : !won;
        });
      }
      chart.update("none");
      document.querySelectorAll("[data-trade-row]").forEach(function (row) {
        const won = row.dataset.won === "true";
        const show = outcome === "all" || (outcome === "winners") === won;
        row.style.display = show ? "" : "none";
      });
    }

    document.querySelectorAll("[data-outcome-filter]").forEach(function (control) {
      control.addEventListener("click", function () {
        document.querySelectorAll("[data-outcome-filter]").forEach(function (other) {
          other.classList.remove("active");
        });
        control.classList.add("active");
        applyOutcome(control.dataset.outcomeFilter);
      });
    });

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
