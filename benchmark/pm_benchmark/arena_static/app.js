/* Forecast Arena — interactive layer.
 *
 * Progressive enhancement over the fully server-rendered page:
 *   1. WebGL globe hero (three.js; the bare specifier "three" resolves via
 *      the page's import map, whose "integrity" field — plus the
 *      modulepreload links' integrity attributes — enforces the pin's SRI
 *      on both three.module.js and its ./three.core.js import).
 *   2. D3 scatter "AI vs the crowd" replacing the static SVG fallback.
 *   3. D3 leaderboard charts (alpha forest plot + calibration).
 * Every step is optional: any failure leaves the static markup untouched.
 * Board strings reach the DOM only through textContent / d3 .text() /
 * attribute values — never through HTML parsing.
 */
"use strict";

const CROWD_ID = "crowd";
const CI_LO = -0.15;
const CI_HI = 0.15;
const TIP_W = 270;

const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)");
const darkScheme = matchMedia("(prefers-color-scheme: dark)");

function readBoard() {
  const el = document.getElementById("board-data");
  if (!el) return null;
  try {
    const data = JSON.parse(el.textContent);
    return data && typeof data === "object" ? data : null;
  } catch {
    return null;
  }
}

// null, "", booleans and absent fields are missing — not zero
const MAX_PULSES = 240;

function toNum(v) {
  if (typeof v === "string" ? v.trim() === "" : typeof v !== "number") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function toProb(v) {
  const n = toNum(v);
  return n !== null && n >= 0 && n <= 1 ? n : null;
}

function fmtPct(v) {
  return `${Math.round(v * 100)}%`;
}

function fmtSigned(v) {
  return `${v >= 0 ? "+" : ""}${v.toFixed(3)}`;
}

function fmtGap(ai, crowd) {
  const g = Math.round(ai * 100) - Math.round(crowd * 100);
  return `${g >= 0 ? "+" : ""}${g} pts`;
}

function safeUrl(v) {
  // same policy as arena_site._safe_url: https only
  return typeof v === "string" && /^https:\/\//i.test(v) ? v : null;
}

function shortLabel(info, eid) {
  const label = String((info && info.label) || eid || "?");
  const cut = label.split("(")[0].trim();
  return cut || label.trim();
}

/* Mirror of arena_site._clean_rationale: drop blanks, baselines, and
 * rationales that merely restate the entrant's own id/label/model. */
function cleanRationale(text, info) {
  if (typeof text !== "string" || !text.trim()) return null;
  if (info && (info.kind === "baseline" || info.id === CROWD_ID)) return null;
  const t = text.trim();
  const src = [info && info.id, info && info.label, info && info.model]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  const srcTokens = new Set(src.match(/[a-z0-9]+/g) || []);
  const tokens = t.toLowerCase().match(/[a-z0-9]+/g) || [];
  if (tokens.length && tokens.every((tok) => srcTokens.has(tok))) return null;
  return t;
}

function excerpt(text, limit) {
  const t = text.trim();
  return t.length <= limit ? t : `${t.slice(0, limit).trimEnd()}…`;
}

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text != null) node.textContent = text;
  return node;
}

/* eid -> board index / record / CSS color. The color comes from the same
 * .e{index}{--ec:…} rules the static markup uses, so JS visuals track the
 * theme automatically. */
function entrantMeta(board) {
  const info = new Map();
  const index = new Map();
  const color = new Map();
  const list = Array.isArray(board.entrants) ? board.entrants : [];
  list.forEach((e, i) => {
    if (e && e.id != null) {
      info.set(e.id, e);
      index.set(e.id, i);
    }
  });
  const probe = document.createElement("i");
  probe.style.cssText = "position:absolute;left:-9999px;visibility:hidden";
  const refreshColors = () => {
    document.body.appendChild(probe);
    for (const [eid, i] of index) {
      probe.className = `e${i}`;
      color.set(eid, getComputedStyle(probe).getPropertyValue("--ec").trim());
    }
    probe.remove();
  };
  refreshColors();
  return {
    info,
    index,
    color,
    refreshColors,
    cls: (eid) => (index.has(eid) ? `e${index.get(eid)}` : ""),
  };
}

function aiEntrants(meta) {
  return [...meta.info.keys()].filter(
    (eid) => meta.info.get(eid).kind === "ai"
  );
}

/* ------------------------------------------------------------------ */
/* Hero: WebGL globe                                                    */
/* ------------------------------------------------------------------ */

function openForecastEntrants(board, meta) {
  /* one pulse per open AI forecast, tagged with the entrant id so the
   * sprite color can track a prefers-color-scheme flip */
  const eids = [];
  const open = Array.isArray(board.open) ? board.open : [];
  const ais = aiEntrants(meta);
  for (const m of open) {
    if (!m || typeof m !== "object") continue;
    const fcs = m.forecasts && typeof m.forecasts === "object" ? m.forecasts : {};
    for (const eid of ais) {
      const fc = fcs[eid];
      if (fc && toProb(fc.prob) != null && meta.color.get(eid)) {
        eids.push(eid);
      }
    }
  }
  return eids;
}

function makeDiscTexture(THREE) {
  const c = document.createElement("canvas");
  c.width = c.height = 64;
  const g = c.getContext("2d");
  const grad = g.createRadialGradient(32, 32, 0, 32, 32, 32);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.55, "rgba(255,255,255,0.75)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  g.fillStyle = grad;
  g.fillRect(0, 0, 64, 64);
  return new THREE.CanvasTexture(c);
}

function fibSphere(n, out) {
  const step = Math.PI * (3 - Math.sqrt(5));
  for (let i = 0; i < n; i += 1) {
    const y = 1 - (2 * i) / (n - 1);
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const th = step * i;
    out[i * 3] = Math.cos(th) * r;
    out[i * 3 + 1] = y;
    out[i * 3 + 2] = Math.sin(th) * r;
  }
  return out;
}

/* the i-th direction of an n-point fibonacci sphere */
function fibDir(i, n) {
  const step = Math.PI * (3 - Math.sqrt(5));
  const y = 1 - (2 * i) / Math.max(1, n - 1);
  const r = Math.sqrt(Math.max(0, 1 - y * y));
  const th = step * i;
  return [Math.cos(th) * r, y, Math.sin(th) * r];
}

async function initGlobe(board, meta) {
  const host = document.querySelector(".hero-art");
  if (!host) return;
  let THREE;
  try {
    THREE = await import("three");
  } catch {
    return; // CDN or integrity failure: keep the static SVG dot sphere
  }
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  } catch {
    return; // no WebGL: keep the SVG
  }

  const css = getComputedStyle(document.documentElement);
  const theme = () => ({
    accent: new THREE.Color(css.getPropertyValue("--accent").trim() || "#6ea8fe"),
    fg: new THREE.Color(css.getPropertyValue("--fg").trim() || "#e8edf4"),
  });

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 20);
  camera.position.set(0, 0, 3.15);
  const globe = new THREE.Group();
  globe.rotation.z = -0.24;
  scene.add(globe);

  const disc = makeDiscTexture(THREE);

  // dot sphere: a fibonacci shell drawn as small hard-edged points. The
  // shader fades alpha and size with view depth, so the far hemisphere
  // dims instead of filling the disc — normal blending, capped alpha,
  // and no interior fill, so the globe stays a transparent point shell.
  // fewer points in the small phone-size box so it still reads as a shell
  const box = host.querySelector("svg");
  const N = ((box && box.clientWidth) || 320) < 200 ? 700 : 2800;
  const pos = fibSphere(N, new Float32Array(N * 3));
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const dotMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: {
      uColor: { value: new THREE.Color("#93a0b0") },
      uPx: { value: 3 },
      uDist: { value: camera.position.z },
    },
    vertexShader: [
      "uniform float uPx;",
      "uniform float uDist;",
      "varying float vFace;",
      "void main() {",
      "  vec4 mv = modelViewMatrix * vec4(position, 1.0);",
      "  // 1 at the point nearest the camera, 0 on the far hemisphere",
      "  vFace = clamp((uDist + 1.0 + mv.z) * 0.5, 0.0, 1.0);",
      "  gl_PointSize = uPx * (uDist / -mv.z);",
      "  gl_Position = projectionMatrix * mv;",
      "}",
    ].join("\n"),
    fragmentShader: [
      "uniform vec3 uColor;",
      "varying float vFace;",
      "void main() {",
      "  float d = length(gl_PointCoord - vec2(0.5));",
      "  float edge = smoothstep(0.5, 0.42, d);",
      "  if (edge <= 0.0) discard;",
      "  float f = vFace * vFace * (3.0 - 2.0 * vFace);",
      "  gl_FragColor = vec4(uColor, edge * mix(0.12, 0.9, f));",
      "}",
    ].join("\n"),
  });
  const dots = new THREE.Points(geo, dotMat);
  globe.add(dots);

  // thin fresnel rim hugging the silhouette — the exponent keeps it to a
  // narrow band and the capped alpha/normal blending means it can only
  // glow at the edge, never fill the ball
  const rimMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    uniforms: {
      uColor: { value: new THREE.Color("#6ea8fe") },
      uStrength: { value: 0.4 },
    },
    vertexShader: [
      "varying vec3 vN;",
      "varying vec3 vV;",
      "void main() {",
      "  vN = normalize(normalMatrix * normal);",
      "  vec4 mv = modelViewMatrix * vec4(position, 1.0);",
      "  vV = -mv.xyz;",
      "  gl_Position = projectionMatrix * mv;",
      "}",
    ].join("\n"),
    fragmentShader: [
      "varying vec3 vN;",
      "varying vec3 vV;",
      "uniform vec3 uColor;",
      "uniform float uStrength;",
      "void main() {",
      "  float rim = pow(1.0 - abs(dot(normalize(vN), normalize(vV))), 3.5);",
      "  gl_FragColor = vec4(uColor, rim * uStrength);",
      "}",
    ].join("\n"),
  });
  const rim = new THREE.Mesh(new THREE.SphereGeometry(1.03, 48, 48), rimMat);
  globe.add(rim);

  // one looping pulse per open AI forecast, in the entrant's color
  // capped so a huge board cannot add unbounded per-frame sprite work
  const pulseEids = openForecastEntrants(board, meta).slice(0, MAX_PULSES);
  const pulses = pulseEids.map((eid, i) => {
    const mat = new THREE.SpriteMaterial({
      map: disc,
      color: new THREE.Color(meta.color.get(eid)),
      transparent: true,
      opacity: 0,
      depthWrite: false,
    });
    const s = new THREE.Sprite(mat);
    const v = fibDir(i, Math.max(1, pulseEids.length));
    s.position.set(v[0] * 1.03, v[1] * 1.03, v[2] * 1.03);
    s.userData.phase = pulseEids.length ? i / pulseEids.length : 0;
    globe.add(s);
    return s;
  });

  function paint() {
    const t = theme();
    // ink-leaning dots: pale slate on dark, dark ink on light theme
    dotMat.uniforms.uColor.value.copy(t.fg).lerp(t.accent, 0.35);
    rimMat.uniforms.uColor.value.copy(t.accent);
    rimMat.uniforms.uStrength.value = darkScheme.matches ? 0.4 : 0.28;
  }

  paint();

  // swap the static SVG for the canvas in the same reserved box; keep the
  // detached nodes so a bfcache restore (after pagehide dispose) can put
  // the fallback back instead of leaving a dead canvas
  const fallback = [...host.childNodes];
  host.replaceChildren(renderer.domElement);

  function sizeCanvas() {
    const w = renderer.domElement.clientWidth || host.clientWidth;
    const h = renderer.domElement.clientHeight || host.clientHeight;
    if (!w || !h) return;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(w, h, false);
    // gl_PointSize is in device px — scale the base dot with the DPR, and
    // shrink it in the small phone-size box
    dotMat.uniforms.uPx.value = 2.3 * Math.min(1, w / 200) * renderer.getPixelRatio();
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
  }

  const clock = new THREE.Clock();
  let raf = 0;
  let visible = true;
  let disposed = false;

  function frame() {
    raf = 0;
    if (disposed) return;
    const dt = Math.min(clock.getDelta(), 0.1);
    const t = clock.elapsedTime;
    globe.rotation.y += dt * 0.12;
    for (const s of pulses) {
      const k = (t * 0.4 + s.userData.phase) % 1;
      s.material.opacity = Math.sin(Math.PI * k) * 0.55;
      const g = 0.04 + 0.11 * k;
      s.scale.set(g, g, 1);
    }
    renderer.render(scene, camera);
    schedule();
  }

  function schedule() {
    if (
      !raf &&
      !disposed &&
      !reducedMotion.matches &&
      visible &&
      !document.hidden
    ) {
      raf = requestAnimationFrame(frame);
    }
  }

  const io = new IntersectionObserver((entries) => {
    visible = entries[0].isIntersecting;
    schedule();
  });
  io.observe(host);
  document.addEventListener("visibilitychange", schedule);
  const onMotion = () => {
    if (reducedMotion.matches) {
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      renderer.render(scene, camera); // one static frame
    } else {
      schedule();
    }
  };
  const onScheme = () => {
    meta.refreshColors();
    paint();
    pulses.forEach((s, i) => {
      const c = meta.color.get(pulseEids[i]);
      if (c) s.material.color.set(c);
    });
    renderer.render(scene, camera);
  };
  reducedMotion.addEventListener("change", onMotion);
  darkScheme.addEventListener("change", onScheme);
  const ro = new ResizeObserver(sizeCanvas);
  ro.observe(host);

  function dispose() {
    if (disposed) return;
    disposed = true;
    if (raf) cancelAnimationFrame(raf);
    io.disconnect();
    ro.disconnect();
    document.removeEventListener("visibilitychange", schedule);
    reducedMotion.removeEventListener("change", onMotion);
    darkScheme.removeEventListener("change", onScheme);
    geo.dispose();
    dotMat.dispose();
    rim.geometry.dispose();
    rimMat.dispose();
    for (const s of pulses) s.material.dispose();
    disc.dispose();
    renderer.dispose();
    renderer.forceContextLoss();
    renderer.domElement.remove();
    host.replaceChildren(...fallback);
  }
  window.addEventListener("pagehide", dispose);

  sizeCanvas();
  schedule();
}

/* ------------------------------------------------------------------ */
/* AI vs the crowd scatter                                             */
/* ------------------------------------------------------------------ */

function scatterPoints(board, meta) {
  const pts = [];
  const open = Array.isArray(board.open) ? board.open : [];
  const ais = aiEntrants(meta);
  for (const m of open) {
    if (!m || typeof m !== "object") continue;
    const crowd = toProb(m.market_prob);
    if (crowd == null) continue;
    const fcs = m.forecasts && typeof m.forecasts === "object" ? m.forecasts : {};
    for (const eid of ais) {
      const fc = fcs[eid];
      const ai = toProb(fc && fc.prob);
      if (ai == null) continue;
      pts.push({
        eid,
        crowd,
        ai,
        question:
          (typeof m.question === "string" && m.question) ||
          (typeof m.slug === "string" && m.slug) ||
          "Untitled market",
        url: safeUrl(m.url),
        rationale: cleanRationale(fc.rationale, meta.info.get(eid)),
      });
    }
  }
  return pts;
}

function initScatter(board, meta) {
  const host = document.getElementById("versus-viz");
  const d3 = window.d3;
  if (!host || !d3) return;
  const pts = scatterPoints(board, meta);
  if (!pts.length) return;

  // build detached; the static fallback stays in place until the first
  // draw succeeds
  const svgHost = el("div", "viz-plot");
  const tip = el("div", "viz-tip");
  tip.hidden = true;
  const legend = el("div", "viz-legend");

  const present = [];
  for (const p of pts) {
    if (!present.includes(p.eid)) present.push(p.eid);
  }
  const hiddenE = new Set();
  let pinned = null;
  let geom = null;

  for (const eid of present) {
    const info = meta.info.get(eid);
    const btn = el("button");
    btn.type = "button";
    btn.setAttribute("aria-pressed", "true");
    const dot = el("i", `kdot ${meta.cls(eid)}`);
    btn.appendChild(dot);
    btn.appendChild(el("span", "", shortLabel(info, eid)));
    const dim = (on) => {
      svgHost.querySelectorAll("circle.vdot").forEach((c) => {
        c.style.opacity = on && c.__data__.eid !== eid ? "0.12" : "";
      });
    };
    btn.addEventListener("click", () => {
      if (hiddenE.has(eid)) hiddenE.delete(eid);
      else hiddenE.add(eid);
      btn.setAttribute("aria-pressed", String(!hiddenE.has(eid)));
      if (pinned && hiddenE.has(pinned.eid)) {
        pinned = null;
        hideTip();
      }
      draw();
    });
    btn.addEventListener("mouseenter", () => dim(true));
    btn.addEventListener("mouseleave", () => dim(false));
    btn.addEventListener("focus", () => dim(true));
    btn.addEventListener("blur", () => dim(false));
    legend.appendChild(btn);
  }

  function tipRow(label, value, cls) {
    const row = el("div", "t-row");
    const l = el("span");
    if (cls) l.appendChild(el("i", `kdot ${cls}`));
    l.appendChild(document.createTextNode(label));
    row.appendChild(l);
    row.appendChild(el("b", "", value));
    return row;
  }

  function showTip(d) {
    tip.textContent = "";
    tip.style.left = "0px";
    tip.style.top = "0px";
    tip.appendChild(el("p", "t-q", d.question));
    tip.appendChild(tipRow("Crowd", fmtPct(d.crowd), meta.cls(CROWD_ID)));
    tip.appendChild(
      tipRow(
        shortLabel(meta.info.get(d.eid), d.eid),
        fmtPct(d.ai),
        meta.cls(d.eid)
      )
    );
    tip.appendChild(el("p", "t-gap", `${fmtGap(d.ai, d.crowd)} vs crowd`));
    if (d.rationale) {
      tip.appendChild(
        el("p", `t-why ${meta.cls(d.eid)}`, excerpt(d.rationale, 180))
      );
    }
    if (d.url) {
      const a = el("a", "t-link", "View on Polymarket →");
      a.href = d.url;
      a.target = "_blank";
      a.rel = "noopener noreferrer";
      tip.appendChild(a);
    }
    tip.hidden = false;
    tip.classList.toggle("pin", pinned === d);
    if (geom && geom.node) {
      // map viewBox coords through the rendered svg rect — the sheet
      // clamps it to max-width 620px and centers it inside the host
      const srect = geom.node.getBoundingClientRect();
      const hrect = host.getBoundingClientRect();
      const k = srect.width / geom.size;
      const px = srect.left - hrect.left + geom.x(d.crowd) * k;
      const py = srect.top - hrect.top + geom.y(d.ai) * k;
      const hw = host.clientWidth;
      tip.style.left = `${Math.max(6, Math.min(px + 16, hw - TIP_W - 6))}px`;
      const h = tip.offsetHeight;
      tip.style.top = `${Math.max(6, py - h - 14)}px`;
    }
  }

  function hideTip() {
    tip.hidden = true;
    tip.classList.remove("pin");
  }

  function draw() {
    svgHost.textContent = "";
    const size = Math.max(280, Math.min(svgHost.clientWidth || 560, 620));
    const m = { t: 18, r: 16, b: 44, l: 48 };
    const x = d3.scaleLinear().domain([0, 1]).range([m.l, size - m.r]);
    const y = d3.scaleLinear().domain([0, 1]).range([size - m.b, m.t]);

    const svg = d3
      .select(svgHost)
      .append("svg")
      .attr("viewBox", `0 0 ${size} ${size}`)
      .attr("role", "group")
      .attr(
        "aria-label",
        "Scatter of AI vs crowd probabilities — one dot per AI forecast on an open market"
      );
    geom = { x, y, size, node: svg.node() };

    svg
      .append("rect")
      .attr("class", "viz-frame")
      .attr("x", m.l)
      .attr("y", m.t)
      .attr("width", size - m.l - m.r)
      .attr("height", size - m.t - m.b);

    for (const t of [0.25, 0.5, 0.75]) {
      svg
        .append("line")
        .attr("class", "viz-grid")
        .attr("x1", x(t))
        .attr("x2", x(t))
        .attr("y1", m.t)
        .attr("y2", size - m.b);
      svg
        .append("line")
        .attr("class", "viz-grid")
        .attr("x1", m.l)
        .attr("x2", size - m.r)
        .attr("y1", y(t))
        .attr("y2", y(t));
    }

    // shaded halves: above the diagonal the AI is more bullish than the crowd
    svg
      .append("path")
      .attr("class", "viz-zone up")
      .attr("d", `M${x(0)},${y(0)}L${x(0)},${y(1)}L${x(1)},${y(1)}Z`);
    svg
      .append("path")
      .attr("class", "viz-zone down")
      .attr("d", `M${x(0)},${y(0)}L${x(1)},${y(0)}L${x(1)},${y(1)}Z`);

    svg
      .append("line")
      .attr("class", "viz-diag")
      .attr("x1", x(0))
      .attr("y1", y(0))
      .attr("x2", x(1))
      .attr("y2", y(1));

    svg
      .append("text")
      .attr("class", "viz-band")
      .attr("x", m.l + 10)
      .attr("y", m.t + 16)
      .text("AI more bullish");
    svg
      .append("text")
      .attr("class", "viz-band")
      .attr("x", size - m.r - 10)
      .attr("y", size - m.b - 10)
      .attr("text-anchor", "end")
      .text("AI more bearish");
    svg
      .append("text")
      .attr("class", "viz-band")
      .attr("x", x(0.62))
      .attr("y", y(0.62) - 8)
      .attr("text-anchor", "middle")
      .attr("transform", `rotate(-45 ${x(0.62)} ${y(0.62) - 8})`)
      .text("agreement y = x");

    const ticks = [0, 0.25, 0.5, 0.75, 1];
    const fmt = (t) => `${Math.round(t * 100)}%`;
    svg
      .append("g")
      .attr("class", "viz-axis")
      .attr("transform", `translate(0,${size - m.b})`)
      .call(
        d3.axisBottom(x).tickValues(ticks).tickFormat(fmt).tickSize(0)
      );
    svg
      .append("g")
      .attr("class", "viz-axis")
      .attr("transform", `translate(${m.l},0)`)
      .call(
        d3.axisLeft(y).tickValues(ticks).tickFormat(fmt).tickSize(0)
      );
    svg
      .append("text")
      .attr("class", "viz-band")
      .attr("x", m.l + (size - m.l - m.r) / 2)
      .attr("y", size - 6)
      .attr("text-anchor", "middle")
      .text("Crowd probability");
    svg
      .append("text")
      .attr("class", "viz-band")
      .attr("x", 12)
      .attr("y", m.t + (size - m.t - m.b) / 2)
      .attr("text-anchor", "middle")
      .attr(
        "transform",
        `rotate(-90 12 ${m.t + (size - m.t - m.b) / 2})`
      )
      .text("AI probability");

    const visiblePts = pts.filter((p) => !hiddenE.has(p.eid));
    svg
      .append("g")
      .selectAll("circle")
      .data(visiblePts)
      .join("circle")
      .attr("class", (d) => `vdot ${meta.cls(d.eid)}`)
      .attr("cx", (d) => x(d.crowd))
      .attr("cy", (d) => y(d.ai))
      .attr("r", 5.5)
      .attr("tabindex", 0)
      .attr("role", "button")
      .attr("aria-label", (d) => {
        const who = shortLabel(meta.info.get(d.eid), d.eid);
        return `${d.question}: crowd ${fmtPct(d.crowd)}, ${who} ${fmtPct(d.ai)}`;
      })
      .on("pointerenter", (e, d) => showTip(d))
      .on("pointerleave", () => {
        if (!pinned) hideTip();
      })
      .on("focus", (e, d) => showTip(d))
      .on("blur", () => {
        if (!pinned) hideTip();
      })
      .on("click", (e, d) => {
        pinned = pinned === d ? null : d;
        if (pinned) showTip(d);
        else hideTip();
      })
      .on("keydown", (e, d) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          pinned = pinned === d ? null : d;
          if (pinned) showTip(d);
          else hideTip();
        } else if (e.key === "Escape") {
          pinned = null;
          hideTip();
        }
      });
  }

  // Escape dismisses a pinned tip even after focus moved elsewhere
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && pinned) {
      pinned = null;
      hideTip();
    }
  });

  let lastW = 0;
  const ro = new ResizeObserver(() => {
    const w = svgHost.clientWidth;
    if (Math.abs(w - lastW) > 4) {
      lastW = w;
      draw();
    }
  });
  draw(); // detached: bail with the fallback intact if this throws
  const fallback = [...host.childNodes];
  host.replaceChildren(svgHost, tip, legend);
  lastW = svgHost.clientWidth;
  try {
    draw(); // real width now that the host is attached
  } catch (e) {
    host.replaceChildren(...fallback); // never leave empty boxes behind
    throw e;
  }
  ro.observe(svgHost);
}

/* ------------------------------------------------------------------ */
/* Leaderboard: alpha forest plot + calibration                        */
/* ------------------------------------------------------------------ */

function perfPanel(title, sub) {
  const box = el("div");
  box.appendChild(el("h3", "", title));
  box.appendChild(el("p", "perf-sub", sub));
  const body = el("div", "viz-plot");
  box.appendChild(body);
  return { box, body };
}

function drawForest(host, rows, meta) {
  const d3 = window.d3;
  const w = Math.max(300, host.clientWidth || 540);
  const m = { t: 8, r: 96, b: 30, l: 130 };
  const rowH = 30;
  const h = m.t + rows.length * rowH + m.b;
  const x = d3.scaleLinear().domain([CI_LO, CI_HI]).range([m.l, w - m.r]);
  const svg = d3
    .select(host)
    .append("svg")
    .attr("viewBox", `0 0 ${w} ${h}`)
    .attr("role", "img")
    .attr("aria-label", "Forest plot of alpha vs crowd with 95% CI");

  svg
    .append("line")
    .attr("class", "viz-diag")
    .attr("x1", x(0))
    .attr("x2", x(0))
    .attr("y1", m.t)
    .attr("y2", h - m.b);

  const clamp = (v) => Math.max(CI_LO, Math.min(CI_HI, v));
  rows.forEach((row, i) => {
    const cy = m.t + i * rowH + rowH / 2;
    const eid = row.entrant;
    const cls = meta.cls(eid);
    svg
      .append("text")
      .attr("class", "viz-band")
      .attr("x", m.l - 10)
      .attr("y", cy + 4)
      .attr("text-anchor", "end")
      .text(shortLabel(meta.info.get(eid), eid));

    const ci = Array.isArray(row.alpha_ci) ? row.alpha_ci : null;
    const lo = ci && toNum(ci[0]);
    const hi = ci && toNum(ci[1]);
    if (lo != null && hi != null) {
      const g = svg.append("g");
      for (const v of [clamp(lo), clamp(hi)]) {
        g.append("line")
          .attr("class", `vstroke ${cls}`)
          .attr("x1", x(v))
          .attr("x2", x(v))
          .attr("y1", cy - 5)
          .attr("y2", cy + 5)
          .attr("stroke-width", 2)
          .attr("opacity", 0.6);
      }
      g.append("line")
        .attr("class", `vstroke ${cls}`)
        .attr("x1", x(clamp(lo)))
        .attr("x2", x(clamp(hi)))
        .attr("y1", cy)
        .attr("y2", cy)
        .attr("stroke-width", 3)
        .attr("opacity", 0.45);
    }

    const alpha = toNum(row.alpha);
    if (alpha != null) {
      svg
        .append("circle")
        .attr("class", `vdot ${cls}`)
        .attr("cx", x(clamp(alpha)))
        .attr("cy", cy)
        .attr("r", 5);
      svg
        .append("text")
        .attr("class", "viz-band")
        .attr("x", w - m.r + 8)
        .attr("y", cy + 4)
        .text(fmtSigned(alpha));
    }
    let note = "";
    if (eid === CROWD_ID) note = "baseline";
    else if (row.significant === null || row.significant === undefined)
      note = "too few events";
    else if (row.significant === false) note = "not significant";
    else if (row.significant === true) note = "significant";
    if (note) {
      svg
        .append("text")
        .attr("class", "viz-band")
        .attr("x", w - m.r + 8)
        .attr("y", cy + 16)
        .text(note);
    }
  });

  const fmtTick = (v) => (v === 0 ? "0" : fmtSigned(v).slice(0, 5));
  svg
    .append("g")
    .attr("class", "viz-axis")
    .attr("transform", `translate(0,${h - m.b})`)
    .call(
      d3
        .axisBottom(x)
        .tickValues([-0.15, -0.1, -0.05, 0, 0.05, 0.1, 0.15])
        .tickFormat(fmtTick)
        .tickSize(0)
    );
}

function calibrationRows(board) {
  const seen = new Set();
  const rows = [];
  for (const [srcName, src] of [
    ["duels", board.duels],
    ["hall", board.hall_of_wrong],
  ]) {
    if (!Array.isArray(src)) continue;
    src.forEach((d, i) => {
      if (!d || typeof d !== "object") return;
      const outcome = d.outcome === 0 || d.outcome === 1 ? d.outcome : null;
      const p = toProb(d.prob);
      if (outcome == null || p == null) return;
      // slugless rows key by position so they can't merge with each other
      const key = `${d.slug || `${srcName}#${i}`}|${d.entrant}`;
      if (seen.has(key)) return;
      seen.add(key);
      rows.push({ eid: d.entrant, prob: p, outcome });
    });
  }
  return rows;
}

function drawCalibration(host, board, meta) {
  const d3 = window.d3;
  const raw = calibrationRows(board);
  if (!raw.length) {
    host.appendChild(
      el(
        "p",
        "viz-cap",
        "No featured resolved forecasts yet — bins draw from the disagreements and Hall of Wrong shown on this page."
      )
    );
    return;
  }
  const BINS = 5;
  const byEntrant = new Map();
  for (const r of raw) {
    if (!byEntrant.has(r.eid)) byEntrant.set(r.eid, []);
    byEntrant.get(r.eid).push(r);
  }
  const w = Math.max(300, Math.min(host.clientWidth || 540, 560));
  const m = { t: 10, r: 14, b: 40, l: 44 };
  const h = w;
  const x = d3.scaleLinear().domain([0, 1]).range([m.l, w - m.r]);
  const y = d3.scaleLinear().domain([0, 1]).range([h - m.b, m.t]);
  const svg = d3
    .select(host)
    .append("svg")
    .attr("viewBox", `0 0 ${w} ${h}`)
    .attr("role", "img")
    .attr("aria-label", "Calibration — predicted probability vs observed frequency");

  for (const t of [0.25, 0.5, 0.75]) {
    svg
      .append("line")
      .attr("class", "viz-grid")
      .attr("x1", x(t))
      .attr("x2", x(t))
      .attr("y1", m.t)
      .attr("y2", h - m.b);
    svg
      .append("line")
      .attr("class", "viz-grid")
      .attr("x1", m.l)
      .attr("x2", w - m.r)
      .attr("y1", y(t))
      .attr("y2", y(t));
  }
  svg
    .append("rect")
    .attr("class", "viz-frame")
    .attr("x", m.l)
    .attr("y", m.t)
    .attr("width", w - m.l - m.r)
    .attr("height", h - m.t - m.b);
  svg
    .append("line")
    .attr("class", "viz-diag")
    .attr("x1", x(0))
    .attr("y1", y(0))
    .attr("x2", x(1))
    .attr("y2", y(1));
  svg
    .append("text")
    .attr("class", "viz-band")
    .attr("x", x(0.55))
    .attr("y", y(0.55) - 8)
    .attr("text-anchor", "middle")
    .attr("transform", `rotate(-45 ${x(0.55)} ${y(0.55) - 8})`)
    .text("perfect calibration");

  const ticks = [0, 0.25, 0.5, 0.75, 1];
  const fmt = (t) => `${Math.round(t * 100)}%`;
  svg
    .append("g")
    .attr("class", "viz-axis")
    .attr("transform", `translate(0,${h - m.b})`)
    .call(d3.axisBottom(x).tickValues(ticks).tickFormat(fmt).tickSize(0));
  svg
    .append("g")
    .attr("class", "viz-axis")
    .attr("transform", `translate(${m.l},0)`)
    .call(d3.axisLeft(y).tickValues(ticks).tickFormat(fmt).tickSize(0));
  svg
    .append("text")
    .attr("class", "viz-band")
    .attr("x", m.l + (w - m.l - m.r) / 2)
    .attr("y", h - 6)
    .attr("text-anchor", "middle")
    .text("Predicted probability");
  svg
    .append("text")
    .attr("class", "viz-band")
    .attr("x", 12)
    .attr("y", m.t + (h - m.t - m.b) / 2)
    .attr("text-anchor", "middle")
    .attr("transform", `rotate(-90 12 ${m.t + (h - m.t - m.b) / 2})`)
    .text("Observed frequency");

  for (const [eid, rs] of byEntrant) {
    const cls = meta.cls(eid);
    const bins = new Map();
    for (const r of rs) {
      const b = Math.min(BINS - 1, Math.floor(r.prob * BINS));
      const agg = bins.get(b) || { sp: 0, so: 0, n: 0 };
      agg.sp += r.prob;
      agg.so += r.outcome;
      agg.n += 1;
      bins.set(b, agg);
    }
    const pts = [...bins.values()]
      .map((a) => ({ px: a.sp / a.n, oy: a.so / a.n, n: a.n }))
      .sort((a, b) => a.px - b.px);
    svg
      .append("path")
      .attr("class", `vstroke ${cls}`)
      .attr("fill", "none")
      .attr("stroke-width", 1.5)
      .attr("opacity", 0.6)
      .attr(
        "d",
        d3
          .line()
          .x((p) => x(p.px))
          .y((p) => y(p.oy))(pts)
      );
    svg
      .selectAll(null)
      .data(pts)
      .join("circle")
      .attr("class", `vdot ${cls}`)
      .attr("cx", (p) => x(p.px))
      .attr("cy", (p) => y(p.oy))
      .attr("r", (p) => 3 + 2 * Math.sqrt(p.n))
      .attr("tabindex", 0)
      .attr("aria-label", (p) => {
        const who = shortLabel(meta.info.get(eid), eid);
        return `${who}: forecast ${fmtPct(p.px)}, resolved yes ${fmtPct(
          p.oy
        )} of ${p.n}`;
      });
  }

  const legend = el("p", "viz-fb-legend");
  for (const eid of byEntrant.keys()) {
    const s = el("span", `leg ${meta.cls(eid)}`);
    s.appendChild(el("i", "kdot"));
    s.appendChild(
      document.createTextNode(shortLabel(meta.info.get(eid), eid))
    );
    legend.appendChild(s);
  }
  host.appendChild(legend);
  host.appendChild(
    el(
      "p",
      "viz-cap",
      `${raw.length} resolved forecast${raw.length === 1 ? "" : "s"} — ` +
        "the disagreements and Hall of Wrong entries featured on this page."
    )
  );
}

function initPerformance(board, meta) {
  const host = document.getElementById("perf-viz");
  if (!host || !window.d3) return;
  const rows = (Array.isArray(board.leaderboard) ? board.leaderboard : []).filter(
    (r) => r && typeof r === "object"
  );
  if (!rows.length) return; // the server-rendered empty state stays
  const grid = el("div", "perf-grid");
  const forest = perfPanel(
    "Alpha vs crowd",
    "95% CI — left of zero beats the crowd"
  );
  const calib = perfPanel(
    "Calibration",
    "Predicted probability vs observed frequency"
  );
  grid.append(forest.box, calib.box);
  // draw detached first: a throw here leaves the fallback note intact
  drawForest(forest.body, rows, meta);
  drawCalibration(calib.body, board, meta);
  host.textContent = "";
  host.appendChild(grid);
}

/* ------------------------------------------------------------------ */

const board = readBoard();
if (board) {
  const meta = entrantMeta(board);
  try {
    initScatter(board, meta);
  } catch {
    /* keep the static SVG fallback */
  }
  try {
    initPerformance(board, meta);
  } catch {
    /* keep the fallback note */
  }
  initGlobe(board, meta).catch(() => {
    /* keep the SVG dot sphere */
  });
}
