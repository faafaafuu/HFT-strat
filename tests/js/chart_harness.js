// Renders the real app.js chart against stub canvas/Chart.js and records draw calls,
// so "is the channel actually drawn" can be answered without a browser.
const fs = require("fs");
const path = process.argv[2];

const calls = [];
function ctxStub() {
  const rec = (name) => (...args) => calls.push({ name, args });
  return {
    save: rec("save"), restore: rec("restore"), beginPath: rec("beginPath"),
    moveTo: rec("moveTo"), lineTo: rec("lineTo"), stroke: rec("stroke"),
    fill: rec("fill"), fillRect: rec("fillRect"), rect: rec("rect"),
    clip: rec("clip"), closePath: rec("closePath"), setLineDash: rec("setLineDash"),
    fillText: rec("fillText"), measureText: () => ({ width: 40 }),
    set fillStyle(v) {}, set strokeStyle(v) {}, set lineWidth(v) {},
    set font(v) {}, set globalAlpha(v) {}, set textAlign(v) {},
  };
}

const registered = [];
let capturedConfig = null;

global.window = { location: {} };
global.document = {
  getElementById(id) {
    if (id === "chart-data") return { textContent: JSON.stringify(payload) };
    if (id === "trade-chart") return canvasStub;
    return null;
  },
  querySelectorAll: () => [],
  addEventListener: (name, fn) => { if (name === "DOMContentLoaded") ready = fn; },
  body: { addEventListener: () => {} },
};

const canvasStub = {
  dataset: {},
  style: {},
  getContext: () => ctxStub(),
  addEventListener: () => {},
  scrollIntoView: () => {},
};

// Stands in for the UMD global the zoom plugin exposes.
global.ChartZoom = { id: "zoom" };
const globallyRegistered = [];

global.Chart = class {
  constructor(ctx, config) {
    capturedConfig = config;
    registered.push(...(config.plugins || []));
    this.options = config.options;
    this.data = config.data;
    this.canvas = canvasStub;
    this.ctx = ctx;
    this.chartArea = { left: 0, top: 0, right: 800, bottom: 400, width: 800, height: 400 };
    this.scales = {
      x: { min: 0, max: 199, getPixelForValue: (v) => 4 * v },
      y: { getPixelForValue: (v) => 400 - v },
    };
  }
  update() {}
  resetZoom() {}
  zoomScale() {}
};
global.Chart.register = (plugin) => globallyRegistered.push(plugin.id);
global.Chart.registry = { plugins: { get: (id) => globallyRegistered.includes(id) } };

// 200 candles, one channel over bars 40..80 with four touches.
const series = [];
for (let i = 0; i < 200; i += 1) {
  const base = 100 + Math.sin(i / 8) * 3;
  series.push({
    t: new Date(Date.UTC(2026, 0, 1, i)).toISOString(),
    o: base, h: base + 1, l: base - 1, c: base + 0.4,
  });
}
const payload = {
  series,
  markers: [
    { t: series[80].t, value: 101, kind: "entry", label: "вход", trade_id: 7, won: false },
    { t: series[95].t, value: 99, kind: "stop", label: "выход", trade_id: 7, won: false },
  ],
  levels: [],
  channels: [{
    trade_id: 7, direction: "LONG", won: false,
    start: series[40].t, entry: series[80].t, end: series[95].t, exit: series[95].t,
    upper_start: 104, upper_end: 106, lower_start: 98, lower_end: 100,
    entry_price: 101, stop_price: 99.5, take_price: 104,
    points: [
      { number: 1, t: series[40].t, value: 104 },
      { number: 2, t: series[55].t, value: 98.5 },
      { number: 3, t: series[70].t, value: 105 },
      { number: 4, t: series[80].t, value: 101 },
    ],
  }],
};

let ready = null;
eval(fs.readFileSync(path, "utf8"));
ready();

const names = registered.map((p) => p.id);
console.log("PLUGINS=" + names.join(","));

const chart = new global.Chart(ctxStub(), capturedConfig);
const before = calls.length;
registered.forEach((plugin) => {
  if (plugin.beforeDatasetsDraw) {
    plugin.beforeDatasetsDraw(chart, {}, chart.options.plugins[plugin.id]);
  }
});
const drawn = calls.slice(before);

const strokes = drawn.filter((c) => c.name === "stroke").length;
const rects = drawn.filter((c) => c.name === "fillRect").length;
const labels = drawn.filter((c) => c.name === "fillText").map((c) => c.args[0]);
console.log("STROKES=" + strokes);
console.log("FILLED_RECTS=" + rects);
console.log("POINT_LABELS=" + [...new Set(labels)].sort().join(","));
// Candle bodies are one fillRect each; the channel adds the two risk/reward boxes.
console.log("CANDLE_BODIES=" + drawn.filter((c) => c.name === "fillRect").length);
console.log("GLOBAL_PLUGINS=" + globallyRegistered.join(","));
const zoomOptions = (capturedConfig.options.plugins || {}).zoom || {};
console.log("PAN_ENABLED=" + Boolean(zoomOptions.pan && zoomOptions.pan.enabled));
console.log("WHEEL_ZOOM=" + Boolean(zoomOptions.zoom && zoomOptions.zoom.wheel.enabled));
console.log("CANVAS_CURSOR=" + (canvasStub.style ? canvasStub.style.cursor : ""));
