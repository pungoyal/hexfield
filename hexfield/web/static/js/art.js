// The artwork: every picture in the game is drawn here as SVG, with no image files.
//
//   boardDefs()        gradients and the small reusable drawings (trees, sheep, peaks...)
//   terrain(hex, i)    the landscape painted on one hex; no two hexes come out the same
//   village / town / trail / bandit / token / dockBadge   the playing pieces
//   installSprites()   full-colour emblems for the resource and ventures
//
// Everything is lit from the upper left. Shadows are plain offset shapes, not filters, so
// the board stays cheap to repaint while targets pulse on top of it.

const SQ3 = Math.sqrt(3);

// a small seeded generator: the same board always gets the same trees
export function seeded(a) {
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const f1 = (n) => Math.round(n * 10) / 10;
export const hexPoints = (cx, cy, r) => Array.from({ length: 6 }, (_, i) => {
  const a = (Math.PI / 180) * (60 * i - 90);
  return `${f1(cx + Math.cos(a) * r)},${f1(cy + Math.sin(a) * r)}`;
}).join(" ");
// inside a pointy-top hex of radius r around the origin
const inHex = (x, y, r) => Math.abs(x) <= (SQ3 / 2) * r && Math.abs(x) / SQ3 + Math.abs(y) <= r;

// up to n points inside the hex, at least `gap` apart, that pass `ok`; back to front
function scatter(rand, n, gap, ok = () => true, r = 50) {
  const pts = [];
  for (let tries = 0; tries < n * 30 && pts.length < n; tries++) {
    const x = (rand() * 2 - 1) * r, y = (rand() * 2 - 1) * r;
    if (!inHex(x, y, r) || !ok(x, y)) continue;
    if (pts.some((p) => Math.hypot(p.x - x, p.y - y) < gap)) continue;
    pts.push({ x, y });
  }
  return pts.sort((a, b) => a.y - b.y);
}
// the number token covers the middle of the hex: nothing important stands there
const clearOfToken = (pad) => (x, y) => Math.hypot(x, y - 6) > pad;
const use = (id, x, y, s = 1, flip = false, extra = "") =>
  `<use href="#${id}" transform="translate(${f1(x)} ${f1(y)}) scale(${flip ? -f1(s) : f1(s)} ${f1(s)})"${extra}/>`;
const pick = (rand, list) => list[Math.floor(rand() * list.length)];

// ------------------------------------------------------------------ shared definitions
const TERRAIN_BASE = {
  timber: ["#3f9d63", "#2a7a4b", "#1a5735"],
  clay: ["#e08a5c", "#c5643f", "#96422a"],
  wool: ["#c2e584", "#9bd062", "#6fa942"],
  grain: ["#f9dc7a", "#eabd3b", "#c2921d"],
  stone: ["#b3bccb", "#8893a6", "#5f6a7e"],
  badlands: ["#f1e2b6", "#dfc98f", "#bda36a"],
};

export function boardDefs() {
  const base = Object.entries(TERRAIN_BASE).map(([id, [hi, mid, lo]]) =>
    `<radialGradient id="g-${id}" cx="36%" cy="26%" r="92%"><stop offset="0" stop-color="${hi}"/><stop offset=".55" stop-color="${mid}"/><stop offset="1" stop-color="${lo}"/></radialGradient>`).join("");
  return `
    ${base}
    <clipPath id="hexclip"><polygon points="${hexPoints(0, 0, 59)}"/></clipPath>
    <linearGradient id="rim" x1=".2" y1="0" x2=".8" y2="1"><stop offset="0" stop-color="#fff" stop-opacity=".55"/><stop offset=".45" stop-color="#fff" stop-opacity="0"/><stop offset=".6" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".45"/></linearGradient>
    <radialGradient id="vignette" cx="50%" cy="46%" r="62%"><stop offset=".62" stop-color="#000" stop-opacity="0"/><stop offset="1" stop-color="#000" stop-opacity=".3"/></radialGradient>
    <radialGradient id="g-token" cx="38%" cy="30%" r="80%"><stop offset="0" stop-color="#fffaf0"/><stop offset=".7" stop-color="#f1e6c8"/><stop offset="1" stop-color="#d9c79b"/></radialGradient>
    <radialGradient id="g-sand" cx="50%" cy="40%" r="75%"><stop offset="0" stop-color="#f0dfae"/><stop offset="1" stop-color="#d2b77c"/></radialGradient>
    <linearGradient id="g-bandit" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#5a6070"/><stop offset=".45" stop-color="#2b2f39"/><stop offset="1" stop-color="#0d0f14"/></linearGradient>
    <linearGradient id="g-mound" x1=".15" y1="0" x2=".85" y2="1"><stop offset="0" stop-color="#f3a77a"/><stop offset=".5" stop-color="#cf6a43"/><stop offset="1" stop-color="#8e3b24"/></linearGradient>
    <linearGradient id="g-dune" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fbf0cc"/><stop offset="1" stop-color="#e3cd96"/></linearGradient>
    <linearGradient id="g-wood-ring" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#b98a56"/><stop offset="1" stop-color="#6e4a28"/></linearGradient>
    <pattern id="waves" width="84" height="44" patternUnits="userSpaceOnUse">
      <path d="M2 12q9-7 18 0t18 0" fill="none" stroke="#fff" stroke-opacity=".1" stroke-width="1.8" stroke-linecap="round"/>
      <path d="M46 34q8-6 16 0t16 0" fill="none" stroke="#fff" stroke-opacity=".07" stroke-width="1.6" stroke-linecap="round"/>
      <path d="M52 9q5-4 10 0" fill="none" stroke="#9fe0ff" stroke-opacity=".08" stroke-width="1.4" stroke-linecap="round"/>
    </pattern>
    <pattern id="rows" width="5.5" height="5.5" patternUnits="userSpaceOnUse"><path d="M0 1.4h5.5" stroke="#a87a12" stroke-opacity=".5" stroke-width="1.5"/><path d="M0 3.4h5.5" stroke="#fff3b8" stroke-opacity=".4" stroke-width="1"/></pattern>

    <g id="t-pine">
      <ellipse cx="4.5" cy=".6" rx="8" ry="2.8" fill="#06200f" opacity=".42"/>
      <rect x="-1.2" y="-4.5" width="2.4" height="5.2" rx=".6" fill="#5b3a20"/>
      <path d="M0-15.5 9.2-3H-9.2z" fill="#2f8f55"/><path d="M0-15.5 9.2-3H0z" fill="#1c6b3d"/>
      <path d="M0-20.5 7.4-9.4H-7.4z" fill="#38a062"/><path d="M0-20.5 7.4-9.4H0z" fill="#217747"/>
      <path d="M0-25 5.4-15.6H-5.4z" fill="#46b472"/><path d="M0-25 5.4-15.6H0z" fill="#2a8652"/>
      <path d="M0-25-5.4-15.6h2.2zM0-20.5-7.4-9.4h2.6zM0-15.5-9.2-3h3z" fill="#8fe0a8" opacity=".5"/>
    </g>
    <g id="t-spruce">
      <ellipse cx="4.5" cy=".6" rx="7" ry="2.6" fill="#06200f" opacity=".42"/>
      <rect x="-1.1" y="-4" width="2.2" height="4.8" rx=".6" fill="#4d311c"/>
      <path d="M0-17 7.8-3H-7.8z" fill="#226f4c"/><path d="M0-17 7.8-3H0z" fill="#145238"/>
      <path d="M0-23.5 6-10.5H-6z" fill="#2a8059"/><path d="M0-23.5 6-10.5H0z" fill="#185e41"/>
      <path d="M0-29 4.2-18H-4.2z" fill="#349169"/><path d="M0-29 4.2-18H0z" fill="#1f6c4b"/>
      <path d="M0-29-4.2-18h1.8zM0-23.5-6-10.5h2.2zM0-17-7.8-3h2.6z" fill="#7fd6ae" opacity=".45"/>
    </g>
    <g id="t-oak">
      <ellipse cx="4" cy=".6" rx="8.5" ry="2.8" fill="#06200f" opacity=".42"/>
      <path d="M-1.3 .6V-6h2.6V.6z" fill="#5b3a20"/>
      <circle cx="3.6" cy="-8.6" r="6" fill="#2f8a4c"/>
      <circle cx="-3.8" cy="-9.4" r="6.2" fill="#49ad5f"/>
      <circle cx=".4" cy="-14" r="6.4" fill="#54ba69"/>
      <circle cx="3.2" cy="-10.5" r="4.6" fill="#3a9a54"/>
      <circle cx="-2.4" cy="-15.6" r="2.8" fill="#9be59f" opacity=".55"/>
    </g>
    <g id="t-sheep">
      <ellipse cx="1.2" cy="4.4" rx="7.4" ry="2.1" fill="#234a12" opacity=".4"/>
      <path d="M-3.2 1v3.6M-.8 1.4v3.4M2.2 1.4v3.4M4.4 1v3.6" stroke="#2d2623" stroke-width="1.3" stroke-linecap="round"/>
      <circle cx="-2.6" cy="-.6" r="3.4" fill="#e0e4d3"/><circle cx="3.4" cy="-.4" r="3.4" fill="#e0e4d3"/>
      <circle cx=".4" cy="-.2" r="3.8" fill="#eceedf"/>
      <circle cx="-2.4" cy="-2.4" r="3.2" fill="#fff"/><circle cx="1.2" cy="-3.2" r="3.4" fill="#fff"/><circle cx="4" cy="-2" r="2.9" fill="#fbfbf3"/>
      <ellipse cx="-6.2" cy="-1.6" rx="2.5" ry="2.1" fill="#2d2623"/>
      <path d="M-5-3.3l1.9-.9-.4 1.9z" fill="#2d2623"/><circle cx="-6.9" cy="-2" r=".5" fill="#fff"/>
    </g>
    <g id="t-tuft"><path d="M0 0l-1.8-3.6M0 0v-4.6M0 0l1.9-3.2" stroke="#4c8a2c" stroke-width="1" stroke-linecap="round" fill="none"/></g>
    <g id="t-fence">
      <path d="M-11 1.5h22M-11-2h22" stroke="#6b4525" stroke-width="1.4" stroke-linecap="round"/>
      <path d="M-9 3v-7.5M-3 3v-7.5M3 3v-7.5M9 3v-7.5" stroke="#8a5c33" stroke-width="2" stroke-linecap="round"/>
      <path d="M-9.5 3v-7.5M-3.5 3v-7.5M2.5 3v-7.5M8.5 3v-7.5" stroke="#c79a66" stroke-width=".7" stroke-linecap="round"/>
    </g>
    <g id="t-sheaf">
      <ellipse cx="2" cy="1" rx="6.5" ry="2" fill="#6b4a08" opacity=".35"/>
      <path d="M-4 1 -1.2-6h2.4L4 1z" fill="#d8a322"/><path d="M0 1 1.2-6 4 1z" fill="#b5820f"/>
      <path d="M-1.4-6-5.2-13.5M-.5-6-2-15M.5-6 2-15M1.4-6 5.2-13.5M0-6v-9.6" stroke="#f3cf52" stroke-width="1.5" stroke-linecap="round"/>
      <path d="M-5.2-13.5l-.8-2.4M-2-15l-.4-2.6M2-15l.4-2.6M5.2-13.5l.8-2.4M0-15.6v-2.6" stroke="#fff0a8" stroke-width="2.1" stroke-linecap="round"/>
      <path d="M-2-6.2h4" stroke="#a5352a" stroke-width="1.7" stroke-linecap="round"/>
    </g>
    <g id="t-hay">
      <ellipse cx="2.2" cy=".8" rx="8.5" ry="2.4" fill="#6b4a08" opacity=".38"/>
      <path d="M-7 1C-7-6-4-10 0-10s7 4 7 11z" fill="#edc13e"/><path d="M0-10c4 0 7 4 7 11H1.5C3-3 2.4-7 0-10z" fill="#c4931b"/>
      <path d="M-4.6-1.5c.6-3 1.8-5 3.4-6.2M-1.6.2c.3-2.6 1-4.6 2-6" stroke="#fff3b8" stroke-width=".9" stroke-linecap="round" fill="none" opacity=".8"/>
    </g>
    <g id="t-mound">
      <ellipse cx="3" cy=".8" rx="19" ry="3.4" fill="#4a1b0e" opacity=".35"/>
      <path d="M-18 1C-16-13-7-19 0-19S15-13 18 1z" fill="url(#g-mound)"/>
      <path d="M0-19c7 0 15 6 18 20H7C9-7 6-14 0-19z" fill="#7a2f1b" opacity=".4"/>
      <path d="M-13-5.5C-8-9-2-10 3-9M-15.5-.5C-9-4.4-1-5.6 6-4.4" stroke="#8a3a22" stroke-width="1" fill="none" opacity=".55" stroke-linecap="round"/>
      <path d="M-11.5-9.5C-8.5-13.6-4.4-16-.8-16.4" stroke="#ffd0ad" stroke-width="1.4" fill="none" opacity=".7" stroke-linecap="round"/>
    </g>
    <g id="t-bricks">
      <ellipse cx="1.5" cy="2.4" rx="10" ry="2.4" fill="#4a1b0e" opacity=".4"/>
      <g stroke="#6e2817" stroke-width=".7">
        <rect x="-9" y="-2.6" width="8.4" height="4.6" rx=".7" fill="#c9573a"/><rect x=".2" y="-2.6" width="8.4" height="4.6" rx=".7" fill="#b44a30"/>
        <rect x="-5" y="-7.2" width="8.4" height="4.6" rx=".7" fill="#d9664a"/><rect x="-9.4" y="-7.2" width="4" height="4.6" rx=".7" fill="#c9573a"/><rect x="3.8" y="-7.2" width="4.6" height="4.6" rx=".7" fill="#a8432b"/>
        <rect x="-7" y="-11.8" width="8.4" height="4.6" rx=".7" fill="#e2775a"/>
      </g>
      <path d="M-8.2-1.8h6.6M-4.2-6.4h6.6M-6.2-11h6.6" stroke="#ffc3a6" stroke-width=".8" opacity=".75" stroke-linecap="round"/>
    </g>
    <g id="t-peak">
      <ellipse cx="4" cy=".8" rx="21" ry="3.6" fill="#1e2530" opacity=".4"/>
      <path d="M-20 1-2-31l5 7 3.4-4.4L22 1z" fill="#c4ccd9"/>
      <path d="M-2-31 3-24l3.4-4.4L22 1H5.5L1.4-9.5z" fill="#69748a"/>
      <path d="M-2-31-9.4-17.8l3.8-2 2.6 3.4 3.4-3.6 2.4 2.8L3-24z" fill="#fff"/>
      <path d="M-2-31 3-24l-.2 6.8-2.4-2.8z" fill="#cfd9ea"/>
      <path d="M6.4-28.4 11.6-19l-2.8-1.2-2 2.2L3-24z" fill="#e9eff8"/>
      <path d="M-12.6-9.6-8-13.4M-8.4-3.4-3-8" stroke="#eef2f8" stroke-width="1" opacity=".65" stroke-linecap="round"/>
    </g>
    <g id="t-crag">
      <ellipse cx="3" cy=".7" rx="13" ry="2.6" fill="#1e2530" opacity=".4"/>
      <path d="M-12 1-3-17 2-11.5 5-15.5 13 1z" fill="#aab3c3"/>
      <path d="M-3-17 2-11.5 5-15.5 13 1H3L.4-6z" fill="#5d687d"/>
      <path d="M-3-17-6.4-10.4l2.6-1.2L-2-9.4.6-12 2-11.5z" fill="#f4f7fb"/>
    </g>
    <g id="t-rock">
      <ellipse cx="1.4" cy="1.4" rx="6" ry="1.7" fill="#1e2530" opacity=".4"/>
      <path d="M-5 1.4-3.6-3 .6-4.6 4.6-2 5.4 1.4z" fill="#8792a6"/><path d="M.6-4.6 4.6-2 5.4 1.4H1.6z" fill="#586278"/>
      <path d="M-3.6-3 .6-4.6 1 -1.6-2.6-.6z" fill="#c9d1de"/>
    </g>
    <g id="t-gem">
      <path d="M0-6 3.4-1.6 0 3-3.4-1.6z" fill="#6ad4f2"/><path d="M0-6 3.4-1.6H0z" fill="#b9f0ff"/><path d="M0 3 3.4-1.6H0z" fill="#2a8fb8"/>
      <path d="M-5.6-.4l1.6-1.2M5-5.6l1-1.4" stroke="#fff" stroke-width=".9" stroke-linecap="round" opacity=".9"/>
    </g>
    <g id="t-dune">
      <path d="M-24 2C-15-9-5-13 4-11c8 1.6 15 6 20 13-8-4-15-5.4-22-4.6C-9-1.8-17-.6-24 2z" fill="url(#g-dune)"/>
      <path d="M4-11c8 1.6 15 6 20 13-8-4-15-5.4-22-4.6C5-5 5.6-8 4-11z" fill="#b89355" opacity=".55"/>
    </g>
    <g id="t-cactus">
      <ellipse cx="2.4" cy=".8" rx="7" ry="2" fill="#5b441b" opacity=".4"/>
      <path d="M-2.4 1V-13a2.4 2.4 0 0 1 4.8 0V1z" fill="#4f9a57"/><path d="M0-15.4A2.4 2.4 0 0 1 2.4-13V1H.4z" fill="#2f7340"/>
      <path d="M-2.4-4.6h-2.2a1.4 1.4 0 0 1-1.4-1.4v-2.8a1.5 1.5 0 0 1 3 0V-7h.6z" fill="#4f9a57"/>
      <path d="M2.4-7.2h2a1.4 1.4 0 0 0 1.4-1.4v-2.2a1.5 1.5 0 0 0-3 0v1.4h-.4z" fill="#2f7340"/>
      <path d="M-1-12.6v11" stroke="#a5e0a0" stroke-width=".8" stroke-linecap="round" opacity=".7"/>
      <circle cx="0" cy="-15.6" r="1.3" fill="#f08aa0"/>
    </g>
    <g id="t-skull">
      <path d="M-3.4-1a3.4 3.2 0 1 1 6.8 0c0 1.2-.6 2-1.4 2.6V3h-4V1.6C-2.8 1-3.4.2-3.4-1z" fill="#f6efdc"/>
      <circle cx="-1.3" cy="-1.2" r=".9" fill="#7c6744"/><circle cx="1.3" cy="-1.2" r=".9" fill="#7c6744"/>
    </g>
  `;
}

// ------------------------------------------------------------------ terrain
const SCENES = {
  timber(rand) {
    let s = "";
    for (let i = 0; i < 9; i++) {
      const x = (rand() * 2 - 1) * 46, y = (rand() * 2 - 1) * 52;
      s += `<ellipse cx="${f1(x)}" cy="${f1(y)}" rx="${f1(9 + rand() * 12)}" ry="${f1(4 + rand() * 6)}" fill="${rand() < 0.55 ? "#12492a" : "#5cc07f"}" opacity="${rand() < 0.5 ? 0.28 : 0.18}"/>`;
    }
    for (const p of scatter(rand, 26, 11.5, clearOfToken(17), 54)) {
      s += use(pick(rand, ["t-pine", "t-pine", "t-spruce", "t-spruce", "t-oak"]), p.x, p.y + 6, 0.82 + rand() * 0.36, rand() < 0.5);
    }
    return s;
  },
  wool(rand) {
    const tilt = rand() * 10 - 5;
    let s = `<g transform="rotate(${f1(tilt)})">
      <path d="M-70-12C-40-30-8-4 28-20S62-28 72-22V-70H-70z" fill="#d3ef98" opacity=".75"/>
      <path d="M-70-12C-40-30-8-4 28-20S62-28 72-22" fill="none" stroke="#7fb548" stroke-width="1.2" opacity=".6"/>
      <path d="M-70 26C-36 8-6 34 30 18S64 12 72 16V72H-70z" fill="#79b548" opacity=".6"/>
      <path d="M-70 26C-36 8-6 34 30 18S64 12 72 16" fill="none" stroke="#5f9a36" stroke-width="1.2" opacity=".65"/>
      <path d="M-70 3C-44-8-20 12 8 2S50-6 72 4" fill="none" stroke="#e4f7b4" stroke-width="1.4" opacity=".5"/></g>`;
    for (const p of scatter(rand, 16, 9, clearOfToken(22))) s += use("t-tuft", p.x, p.y, 0.8 + rand() * 0.5, rand() < 0.5);
    for (const p of scatter(rand, 12, 8, clearOfToken(22))) {
      s += `<circle cx="${f1(p.x)}" cy="${f1(p.y)}" r="${f1(0.9 + rand() * 0.5)}" fill="${pick(rand, ["#fff", "#ffe36e", "#fff", "#ffb7d0"])}"/>`;
    }
    const fx = rand() < 0.5 ? -1 : 1;
    s += use("t-fence", fx * (22 + rand() * 6), 38 + rand() * 5, 1, false, ` opacity=".95"`);
    for (const p of scatter(rand, 5, 19, (x, y) => clearOfToken(27)(x, y) && y < 30, 46)) s += use("t-sheep", p.x, p.y, 1.05 + rand() * 0.3, rand() < 0.5);
    return s;
  },
  grain(rand) {
    const turn = Math.floor(rand() * 180);
    const cuts = [-80, -26 + rand() * 8, 2 + rand() * 8, 30 + rand() * 6, 80];
    const tints = ["#f6d457", "#e2ae2a", "#f9e184", "#d9a020"];
    const start = Math.floor(rand() * 4);
    let s = `<g transform="rotate(${turn})">`;
    for (let i = 0; i < 4; i++) {
      const y = cuts[i], h = cuts[i + 1] - cuts[i];
      s += `<rect x="-80" y="${f1(y)}" width="160" height="${f1(h)}" fill="${tints[(i + start) % 4]}" opacity=".8"/>`;
      s += `<rect x="-80" y="${f1(y)}" width="160" height="${f1(h)}" fill="url(#rows)" transform="rotate(${i % 2 ? 90 : 0} 0 ${f1(y + h / 2)})"/>`;
    }
    for (let i = 1; i < 4; i++) {
      s += `<path d="M-80 ${f1(cuts[i])}h160" stroke="#8a6a12" stroke-width="2.4" opacity=".55"/><path d="M-80 ${f1(cuts[i] - 1.3)}h160" stroke="#fff6c9" stroke-width=".9" opacity=".55"/>`;
    }
    s += `</g>`;
    for (const p of scatter(rand, 7, 20, clearOfToken(27), 46)) s += use(rand() < 0.62 ? "t-sheaf" : "t-hay", p.x, p.y + 4, 1.05 + rand() * 0.3, rand() < 0.5);
    return s;
  },
  clay(rand) {
    let s = "";
    for (let i = 0; i < 7; i++) {
      const x = (rand() * 2 - 1) * 46, y = (rand() * 2 - 1) * 52;
      s += `<ellipse cx="${f1(x)}" cy="${f1(y)}" rx="${f1(10 + rand() * 12)}" ry="${f1(4 + rand() * 5)}" fill="${rand() < 0.5 ? "#7d3019" : "#f6b089"}" opacity=".22"/>`;
    }
    const spots = scatter(rand, 9, 22, clearOfToken(15), 52);
    spots.forEach((p, i) => {
      s += i % 4 === 3 ? use("t-bricks", p.x, p.y + 2, 1 + rand() * 0.2, rand() < 0.5) : use("t-mound", p.x, p.y + 4, 0.62 + rand() * 0.5, rand() < 0.5);
    });
    return s;
  },
  stone(rand) {
    let s = "";
    for (let i = 0; i < 7; i++) {
      const x = (rand() * 2 - 1) * 46, y = (rand() * 2 - 1) * 52;
      s += `<ellipse cx="${f1(x)}" cy="${f1(y)}" rx="${f1(9 + rand() * 12)}" ry="${f1(4 + rand() * 5)}" fill="${rand() < 0.5 ? "#434d60" : "#d7deea"}" opacity=".22"/>`;
    }
    const things = [];
    for (const p of scatter(rand, 6, 25, clearOfToken(14), 50)) things.push([p.y + 8, use(rand() < 0.7 ? "t-peak" : "t-crag", p.x, p.y + 8, 0.72 + rand() * 0.42, rand() < 0.35)]);
    for (const p of scatter(rand, 8, 13, clearOfToken(24))) things.push([p.y, use("t-rock", p.x, p.y, 0.8 + rand() * 0.6, rand() < 0.5)]);
    for (const p of scatter(rand, 3, 24, clearOfToken(25), 44)) things.push([p.y + 1, use("t-gem", p.x, p.y, 0.9 + rand() * 0.3)]);
    things.sort((a, b) => a[0] - b[0]);
    return s + things.map((t) => t[1]).join("");
  },
  badlands(rand) {
    let s = "";
    for (const p of scatter(rand, 7, 24, () => true, 52)) s += use("t-dune", p.x, p.y, 0.8 + rand() * 0.6, rand() < 0.5);
    for (let i = 0; i < 16; i++) {
      const x = (rand() * 2 - 1) * 46, y = (rand() * 2 - 1) * 50;
      s += `<path d="M${f1(x)} ${f1(y)}q4-2.4 8 0" fill="none" stroke="#a88a52" stroke-width=".9" opacity=".45" stroke-linecap="round"/>`;
    }
    const things = [];
    for (const p of scatter(rand, 4, 22, clearOfToken(20), 46)) things.push([p.y, use("t-cactus", p.x, p.y + 4, 1 + rand() * 0.45, rand() < 0.5)]);
    for (const p of scatter(rand, 5, 14, clearOfToken(20))) things.push([p.y, use("t-rock", p.x, p.y, 0.6 + rand() * 0.4, rand() < 0.5, ` opacity=".8"`)]);
    for (const p of scatter(rand, 1, 10, clearOfToken(24), 40)) things.push([p.y, use("t-skull", p.x, p.y, 1.1)]);
    things.sort((a, b) => a[0] - b[0]);
    return s + things.map((t) => t[1]).join("");
  },
};

// the inside of one hex, drawn around its own centre for a radius of 60
export function terrain(hex, index) {
  const res = hex.res || "badlands";
  const rand = seeded(7919 * (index + 1) + 131 * (hex.num || 0) + res.length);
  return `
    <polygon class="tile" points="${hexPoints(0, 0, 59)}" fill="url(#g-${res})"/>
    <g class="scene" clip-path="url(#hexclip)">${SCENES[res](rand)}<polygon points="${hexPoints(0, 0, 60)}" fill="url(#vignette)"/></g>
    <polygon class="rim" points="${hexPoints(0, 0, 57.6)}" fill="none" stroke="url(#rim)" stroke-width="2.6" stroke-linejoin="round"/>
    <polygon class="edge" points="${hexPoints(0, 0, 59)}" fill="none" stroke="#1b1408" stroke-opacity=".5" stroke-width="1.4" stroke-linejoin="round"/>
    <polygon class="flash" points="${hexPoints(0, 0, 59)}"/>`;
}

// ------------------------------------------------------------------ pieces
// Wooden playing pieces in the owner's colour (--pc), shaded on the sides that face away.
export function villageArt() {
  return `
    <ellipse class="p-shadow" cx="2.5" cy="9.6" rx="13.5" ry="4"/>
    <path class="p-dark" d="M3.4 9.4V-2.2l8.4-3v11.4z"/>
    <path class="p-face" d="M-10.6 9.4V-2.2l7-8.2 7 8.2V9.4z"/>
    <path class="p-roof" d="M-3.6-10.4 4.8-13.4l7 8.2-8.4 3z"/>
    <path class="p-line" d="M-10.6 9.4V-2.2l7-8.2 8.4-3 7 8.2v11.4l-8.4 3z"/>
    <path class="p-edge" d="M-3.6-10.4l7 8.2V9.4M3.4-2.2l8.4-3"/>
    <path class="p-door" d="M-5.6 9.4V3.6a2 2 0 0 1 4 0v5.8z"/>
    <path class="p-shine" d="M-9.4-1.8-3.6-8.6"/>`;
}

export function townArt() {
  return `
    <ellipse class="p-shadow" cx="3" cy="11.6" rx="19" ry="4.6"/>
    <path class="p-dark" d="M10.6 11.4V.4l6.4-2.6v11z"/>
    <path class="p-face" d="M-2.4 11.4V.4h13v11z"/>
    <path class="p-roof" d="M-2.4.4 4-2.2h13L10.6.4z"/>
    <path class="p-dark" d="M-2.4.4V-6.6l6.4-2.6V-2.2z"/>
    <path class="p-face" d="M-15.4 11.4V-6.6l6.5-7.8 6.5 7.8v18z"/>
    <path class="p-roof" d="M-8.9-14.4-2.5-17l6.5 7.8-6.4 2.6z"/>
    <path class="p-line" d="M-15.4 11.4V-6.6l6.5-7.8L-2.5-17 4-9.2v7H17v11l-6.4 2.6z"/>
    <path class="p-edge" d="M-8.9-14.4l6.5 7.8v18M-2.4-6.6 4-9.2M-2.4.4h13v11M10.6.4 17-2.2"/>
    <path class="p-door" d="M-11 11.4V5.6a2 2 0 0 1 4 0v5.8zM1.6 4.4h2.4v3H1.6zM6 4.4h2.4v3H6z"/>
    <path class="p-win" d="M-10.3-4.6h2.8v3.4h-2.8z"/>
    <path class="p-shine" d="M-14.2-6.2-8.9-12.6"/>`;
}

// a trail is a plank between two corners, lit along its upper side
export function trailArt({ x1, y1, x2, y2 }) {
  const len = Math.hypot(x2 - x1, y2 - y1) || 1;
  let nx = -(y2 - y1) / len, ny = (x2 - x1) / len;
  if (ny > 0 || (ny === 0 && nx > 0)) { nx = -nx; ny = -ny; } // the normal that points up the screen
  const off = (k) => `x1="${f1(x1 + nx * k)}" y1="${f1(y1 + ny * k)}" x2="${f1(x2 + nx * k)}" y2="${f1(y2 + ny * k)}"`;
  return `<line class="r-shadow" ${off(-2.6)}/><line class="under" ${off(0)}/><line class="top" ${off(0)}/>
    <line class="low" ${off(-2.3)}/><line class="shine" ${off(2.1)}/>`;
}

export const BANDIT = `
  <ellipse cx="2" cy="16.5" rx="12.5" ry="3.8" fill="#000" opacity=".42"/>
  <path d="M0-19a7.4 7.4 0 0 1 7.4 7.4c0 2.6-1.3 4.8-3.3 6.1C8-3.6 10.6 1 10.6 6.6V13c0 2.2-1.7 3.4-4.4 3.4H-6.2c-2.7 0-4.4-1.2-4.4-3.4V6.6C-10.6 1-8-3.6-4.1-5.5a7.3 7.3 0 0 1-3.3-6.1A7.4 7.4 0 0 1 0-19z" fill="url(#g-bandit)" stroke="#07080b" stroke-width="1.4"/>
  <path d="M-6.4-13.6A6.4 6.4 0 0 1-1-17.9M-9 5.4C-8.6 1.6-7-1.6-4.4-3.4" fill="none" stroke="#aab2c4" stroke-width="1.5" stroke-linecap="round" opacity=".6"/>
  <path d="M-4.6-12.4h9.2a4.6 4.6 0 0 1-9.2 0z" fill="#07080b" opacity=".75"/>
  <circle class="eye" cx="-2.2" cy="-10.6" r="1.25"/><circle class="eye" cx="2.2" cy="-10.6" r="1.25"/>`;

export function tokenArt(num, pips) {
  const hot = num === 6 || num === 8;
  let s = `<circle class="t-shadow" cy="2.6" r="19.6"/><circle class="disc" r="19"/><circle class="t-ring" r="15.6"/>
    <text y="${hot ? -3.4 : -3}">${num}</text>`;
  for (let k = 0; k < pips; k++) s += `<circle class="pip" r="1.75" cx="${f1((k - (pips - 1) / 2) * 4.7)}" cy="10.2"/>`;
  return s;
}

export function dockBadge(kind) {
  const inner = kind === "any"
    ? `<text class="rate big" y="4.6">3:1</text><text class="any" y="-6.4">any</text>`
    : `<use href="#a-${kind}" x="-9.5" y="-15.5" width="19" height="19"/><text class="rate" y="12.4">2:1</text>`;
  return `<circle class="pb-shadow" cy="2.4" r="19.4"/><circle class="pb-wood" r="19"/><circle class="pb-face" r="15.4"/>${inner}`;
}

// ------------------------------------------------------------------ card emblems
// Full-colour pictures for the faces of resource and ventures.
const SPRITES = `
  <linearGradient id="a-gold" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#ffe98a"/><stop offset=".5" stop-color="#f3b92b"/><stop offset="1" stop-color="#b9780c"/></linearGradient>
  <linearGradient id="a-steel" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#f4f7fb"/><stop offset=".5" stop-color="#aeb9c9"/><stop offset="1" stop-color="#5f6b80"/></linearGradient>
  <linearGradient id="a-bark" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#8a5a33"/><stop offset="1" stop-color="#4a2c14"/></linearGradient>
  <radialGradient id="a-grain" cx="40%" cy="35%" r="75%"><stop offset="0" stop-color="#f8ddb0"/><stop offset="1" stop-color="#d59f5e"/></radialGradient>
  <linearGradient id="a-clay" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ec8460"/><stop offset="1" stop-color="#b9482b"/></linearGradient>

  <symbol id="a-timber" viewBox="0 0 48 48">
    <ellipse cx="25" cy="42.5" rx="19" ry="3" fill="#000" opacity=".22"/>
    <g id="a-log"><circle cx="19.4" cy="30.2" r="8.6" fill="#3d2410"/>
      <circle cx="14" cy="33.2" r="8.6" fill="url(#a-bark)"/><circle cx="14" cy="33.2" r="6.7" fill="url(#a-grain)"/>
      <circle cx="14" cy="33.2" r="4.3" fill="none" stroke="#b37a3f" stroke-width="1"/><circle cx="14" cy="33.2" r="1.7" fill="#b37a3f"/>
      <path d="M14 33.2l4.6-3.6" stroke="#b37a3f" stroke-width=".9" stroke-linecap="round"/></g>
    <use href="#a-log" x="17.5"/>
    <use href="#a-log" x="8.8" y="-15"/>
    <path d="M9.6 12.6a8.6 8.6 0 0 1 4-3" stroke="#d9a877" stroke-width="1.3" fill="none" stroke-linecap="round" transform="translate(8.8 0)"/>
  </symbol>

  <symbol id="a-clay" viewBox="0 0 48 48">
    <ellipse cx="25" cy="42" rx="19" ry="3" fill="#000" opacity=".22"/>
    <path d="M6 15.5 12 9.5h32l-6 6z" fill="#f4a384"/><path d="M38 15.5l6-6V34l-6 6z" fill="#8f3520"/>
    <rect x="6" y="15.5" width="32" height="24.5" fill="#7b2c18"/>
    <g fill="url(#a-clay)"><rect x="7" y="16.6" width="14.4" height="6.6" rx="1"/><rect x="22.6" y="16.6" width="14.4" height="6.6" rx="1"/>
      <rect x="7" y="24.4" width="6.6" height="6.6" rx="1"/><rect x="14.8" y="24.4" width="14.4" height="6.6" rx="1"/><rect x="30.4" y="24.4" width="6.6" height="6.6" rx="1"/>
      <rect x="7" y="32.2" width="14.4" height="6.6" rx="1"/><rect x="22.6" y="32.2" width="14.4" height="6.6" rx="1"/></g>
    <path d="M8.4 18h11.6M24 18h11.6M16.2 25.8h11.6M8.4 33.6h11.6M24 33.6h11.6M8.4 25.8h3.8M31.8 25.8h3.8" stroke="#ffd0bb" stroke-width="1" stroke-linecap="round" opacity=".8"/>
  </symbol>

  <symbol id="a-wool" viewBox="0 0 48 48">
    <ellipse cx="25" cy="42.5" rx="17" ry="3" fill="#000" opacity=".22"/>
    <path d="M17 33v8.4M22.6 34v7.4M29.6 34v7.4M35 33v8.4" stroke="#2d2623" stroke-width="3" stroke-linecap="round"/>
    <g fill="#dfe4d3"><circle cx="18" cy="29" r="7.4"/><circle cx="34" cy="29" r="7.4"/><circle cx="26" cy="31" r="7.8"/></g>
    <g fill="#fff"><circle cx="18.4" cy="23.6" r="7.2"/><circle cx="27" cy="20.6" r="8"/><circle cx="35" cy="24.4" r="6.8"/><circle cx="26.6" cy="27" r="7"/></g>
    <ellipse cx="10.6" cy="24.4" rx="5.8" ry="5" fill="#2d2623"/>
    <path d="M13.2 20.4l5-2-1.6 4.6zM6.6 21l-3.4-.8 2.2 3.4z" fill="#2d2623"/>
    <circle cx="9" cy="23.4" r="1.15" fill="#fff"/><circle cx="12.6" cy="23.4" r="1.15" fill="#fff"/>
    <path d="M9.6 27.2h2.4" stroke="#8d7f78" stroke-width="1" stroke-linecap="round"/>
  </symbol>

  <symbol id="a-grain" viewBox="0 0 48 48">
    <ellipse cx="24.6" cy="43" rx="14" ry="2.6" fill="#000" opacity=".22"/>
    <path d="M17.6 43 21.6 29h4.8L30.4 43z" fill="#d9a322"/><path d="M24 43 26.4 29l4 14z" fill="#b07d0e"/>
    <g id="a-ear"><path d="M24 30V11" stroke="#d9a322" stroke-width="1.8" stroke-linecap="round"/>
      <g fill="url(#a-gold)" stroke="#b07d0e" stroke-width=".5"><ellipse cx="24" cy="6.6" rx="2.2" ry="3.6"/>
        <ellipse cx="21.2" cy="11.2" rx="2.1" ry="3.4" transform="rotate(-32 21.2 11.2)"/><ellipse cx="26.8" cy="11.2" rx="2.1" ry="3.4" transform="rotate(32 26.8 11.2)"/>
        <ellipse cx="21.2" cy="16.4" rx="2.1" ry="3.4" transform="rotate(-32 21.2 16.4)"/><ellipse cx="26.8" cy="16.4" rx="2.1" ry="3.4" transform="rotate(32 26.8 16.4)"/></g></g>
    <use href="#a-ear" transform="rotate(-24 24 30)"/><use href="#a-ear" transform="rotate(24 24 30)"/>
    <use href="#a-ear" transform="rotate(-46 24 30) translate(0 4)"/><use href="#a-ear" transform="rotate(46 24 30) translate(0 4)"/>
    <rect x="19.6" y="28" width="8.8" height="3.6" rx="1.6" fill="#b3352b"/><path d="M20.6 29h6.8" stroke="#f08a7e" stroke-width=".8" stroke-linecap="round"/>
  </symbol>

  <symbol id="a-stone" viewBox="0 0 48 48">
    <ellipse cx="24.6" cy="42.5" rx="20" ry="3" fill="#000" opacity=".22"/>
    <path d="M4 41 11.6 22.6 20 18l7 9.4-2 13.6z" fill="#8a94a8"/><path d="M20 18l7 9.4-2 13.6H14.6l1-13.4z" fill="#566076"/><path d="M11.6 22.6 20 18l-4.4 9.6z" fill="#c9d1de"/>
    <path d="M19 41 25.4 15 33.6 8l8 13.6L44 41z" fill="#a4aebf"/><path d="M33.6 8l8 13.6L44 41H31.6l-1.4-17.6z" fill="#5d687d"/><path d="M25.4 15 33.6 8l-3.4 15.4z" fill="#dfe5ee"/>
    <path d="M24 30.6l3.6-5.4 3.6 5.4-3.6 5z" fill="#63d2f2"/><path d="M27.6 25.2l3.6 5.4h-3.6z" fill="#c2f3ff"/><path d="M27.6 35.6l3.6-5h-3.6z" fill="#2488b3"/>
    <path d="M8.6 34.6l2.4-3.6 2.4 3.6-2.4 3.2z" fill="#63d2f2"/><path d="M11 31l2.4 3.6H11z" fill="#c2f3ff"/>
    <path d="M36 22.6l2-3 2 3-2 2.8z" fill="#c2f3ff"/>
  </symbol>

  <symbol id="a-ranger" viewBox="0 0 48 48">
    <path d="M36.6 4 41 8.4 22.6 30l-4.4.4.4-4.4z" fill="url(#a-steel)" stroke="#3d4658" stroke-width="1" stroke-linejoin="round"/>
    <path d="M14 27l7.6 7.6-3 3L11 30z" fill="url(#a-gold)" stroke="#7a4d06" stroke-width="1" stroke-linejoin="round"/>
    <path d="M12.4 34.4l-5 5a2.3 2.3 0 0 0 3.2 3.2l5-5z" fill="#7a4a22" stroke="#3f230c" stroke-width="1"/>
    <path d="M25 20h17.4v10.6c0 6.4-4 10.8-8.7 12.8C29 41.400 25 37 25 30.600z" fill="url(#a-steel)" stroke="#2d3545" stroke-width="1.3" stroke-linejoin="round"/>
    <path d="M27.4 22.400h12.600v8.200c0 4.800-2.900 8.300-6.300 10-3.400-1.700-6.300-5.200-6.300-10z" fill="#3b6fd0"/>
    <path d="M33.700 22.400h6.300v8.200c0 4.800-2.900 8.300-6.300 10z" fill="#2850a4"/>
    <path d="M32.400 25h2.600v4.400h3.800v2.600H35V38h-2.600v-6h-3.800v-2.600h3.800z" fill="#fff" opacity=".92"/>
  </symbol>

  <symbol id="a-landmark" viewBox="0 0 48 48">
    <path d="M14 3h8l4 14h-8zM34 3h-8l-4 14h8z" fill="#c8323c"/><path d="M14 3h4l4 14h-4zM34 3h-4l-4 14h4z" fill="#f0606a"/>
    <circle cx="24" cy="29" r="15" fill="url(#a-gold)" stroke="#8a5a06" stroke-width="1.4"/>
    <circle cx="24" cy="29" r="11.6" fill="none" stroke="#fff3b8" stroke-width="1" opacity=".8"/>
    <path d="m24 19.600 2.900 5.900 6.500.900-4.700 4.600 1.100 6.500L24 34.400l-5.800 3.100 1.100-6.500-4.700-4.600 6.500-.900z" fill="#fff8d6" stroke="#a36c08" stroke-width="1" stroke-linejoin="round"/>
    <path d="M13.600 23.400a11.600 11.600 0 0 1 5.800-5.600" stroke="#fff" stroke-width="1.6" fill="none" stroke-linecap="round" opacity=".8"/>
  </symbol>

  <symbol id="a-pathfinder" viewBox="0 0 48 48">
    <ellipse cx="24" cy="42" rx="19" ry="3" fill="#000" opacity=".22"/>
    <g stroke="#3f230c" stroke-width="1.2" stroke-linejoin="round">
      <path d="M4 31.600 21.600 13l5 4.700L9 36.400z" fill="#c98d52"/><path d="M26.600 17.700 44 36l-5 4.700-17.400-18.300z" fill="#a8702f"/></g>
    <path d="M7 32 21.600 16.600M29 21.600l12 12.600" stroke="#f1c996" stroke-width="1.4" stroke-linecap="round" opacity=".85"/>
    <path d="M12.600 25.700l5 4.700M17.400 20.600l5 4.700M31.400 22.800l-5 4.700M36.400 28l-5 4.700" stroke="#3f230c" stroke-width="1" opacity=".55"/>
    <path d="m34 3 1.900 4.600 4.600 1.900-4.600 1.900L34 16l-1.900-4.600-4.600-1.900 4.600-1.900z" fill="url(#a-gold)" stroke="#8a5a06" stroke-width=".8" stroke-linejoin="round"/>
    <path d="m12 6 1.100 2.700 2.700 1.100-2.700 1.100L12 13.600l-1.100-2.700-2.700-1.100 2.700-1.100z" fill="#fff3b8"/>
  </symbol>

  <symbol id="a-hoard" viewBox="0 0 48 48">
    <ellipse cx="24" cy="42.500" rx="17" ry="2.800" fill="#000" opacity=".22"/>
    <path d="M6 14l9 10 9-15 9 15 9-10-3.600 22H9.600z" fill="url(#a-gold)" stroke="#8a5a06" stroke-width="1.400" stroke-linejoin="round"/>
    <path d="M24 9l9 15 9-10-3.600 22H24z" fill="#b9780c" opacity=".35"/>
    <rect x="8.600" y="35" width="30.800" height="5.600" rx="1.800" fill="url(#a-gold)" stroke="#8a5a06" stroke-width="1.400"/>
    <circle cx="6" cy="13" r="2.600" fill="#ff6b6b" stroke="#8a5a06"/><circle cx="24" cy="8" r="3" fill="#63d2f2" stroke="#8a5a06"/><circle cx="42" cy="13" r="2.600" fill="#ff6b6b" stroke="#8a5a06"/>
    <circle cx="16" cy="37.800" r="1.700" fill="#c8323c"/><circle cx="24" cy="37.800" r="1.700" fill="#2e9e62"/><circle cx="32" cy="37.800" r="1.700" fill="#3b6fd0"/>
    <path d="M10.600 20.400l4.600 5 6.400-10.600" stroke="#fff8d6" stroke-width="1.400" fill="none" stroke-linecap="round" stroke-linejoin="round" opacity=".85"/>
  </symbol>

  <symbol id="a-windfall" viewBox="0 0 48 48">
    <ellipse cx="24" cy="43" rx="18" ry="2.800" fill="#000" opacity=".22"/>
    <path d="M7 22h34v17a3 3 0 0 1-3 3H10a3 3 0 0 1-3-3z" fill="#8a5a2f" stroke="#3f230c" stroke-width="1.300"/>
    <path d="M24 22h17v17a3 3 0 0 1-3 3H24z" fill="#6d4420"/>
    <path d="M7 27.600h34M7 34.600h34" stroke="#3f230c" stroke-width="1" opacity=".5"/>
    <rect x="20.400" y="26" width="7.200" height="8" rx="1.400" fill="url(#a-gold)" stroke="#8a5a06" stroke-width="1"/>
    <path d="M5 22 9.600 9h28.800L43 22z" fill="#a8702f" stroke="#3f230c" stroke-width="1.300" stroke-linejoin="round"/>
    <circle cx="16" cy="17.600" r="5" fill="url(#a-gold)" stroke="#8a5a06"/><circle cx="25" cy="15.600" r="5.600" fill="url(#a-gold)" stroke="#8a5a06"/><circle cx="33.400" cy="17.800" r="4.600" fill="url(#a-gold)" stroke="#8a5a06"/>
    <path d="M22.400 13.600a3.600 3.600 0 0 1 3-1.600" stroke="#fff" stroke-width="1.200" fill="none" stroke-linecap="round" opacity=".85"/>
    <path d="M5 22h38v2.400H5z" fill="#3f230c" opacity=".55"/>
    <path d="m38 2 1.300 3.200 3.200 1.300-3.200 1.300L38 11l-1.300-3.200-3.200-1.300 3.200-1.300z" fill="#fff3b8"/>
  </symbol>
`;

export function installSprites() {
  const sprite = document.querySelector("svg.sprite");
  if (!sprite || sprite.querySelector("#a-timber")) return;
  sprite.insertAdjacentHTML("beforeend", SPRITES);
}
