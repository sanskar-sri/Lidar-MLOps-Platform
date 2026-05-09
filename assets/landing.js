/*
 * assets/landing.js
 * Dash auto-includes every file in assets/ on every page.
 * We guard all logic with an element check so this is a no-op
 * on the Data Explorer page or any other page.
 *
 * What this does on the home page (path="/"):
 *   1. Full-page rotating LiDAR point cloud on <canvas id="lp-cv">
 *      – Building geometry, corridor points, ground plane, and street scatter
 *      – Colored with the CloudCompare B→G→Y→R colormap by height
 *      – Slow Y-axis rotation at ~0.2 rpm via requestAnimationFrame
 *   2. Animated stat counters (50K, 6, 9, 6) with easeOutCubic
 */

function initLandingPage() {

    // ── Guard: only run on the home/landing page ─────────────────
    const cv = document.getElementById('lp-cv');
    if (!cv) return false;
    if (cv.dataset.lpStarted === '1') return true;
    cv.dataset.lpStarted = '1';

    // ── 1. Canvas — Rotating Point Cloud ─────────────────────────

    const ctx = cv.getContext('2d');

    function resize() {
        cv.width  = cv.offsetWidth  || cv.parentElement.offsetWidth;
        cv.height = cv.offsetHeight || cv.parentElement.offsetHeight;
    }
    resize();
    window.addEventListener('resize', resize);

    // Generate scene geometry.
    // Coordinates are deliberately wider than the visible camera so the cloud
    // reads as a page background instead of a small hero-only object.
    const pts = [];

    // Wide ground plane (flat, y ≈ -0.5)
    for (let i = 0; i < 1350; i++) {
        pts.push([
            (Math.random() - 0.5) * 9.8,
            -0.5 + (Math.random() - 0.5) * 0.1,
            (Math.random() - 0.5) * 5.8
        ]);
    }

    // Main building — 4 wall faces, heights 0 → 1.2 m above ground
    for (let i = 0; i < 640; i++) {
        const face = i % 4;
        const h    = Math.random() * 1.2;
        if      (face === 0) pts.push([ 0.50, -0.5 + h, (Math.random() - 0.5) * 0.92]);
        else if (face === 1) pts.push([-0.50, -0.5 + h, (Math.random() - 0.5) * 0.92]);
        else if (face === 2) pts.push([(Math.random() - 0.5) * 0.92, -0.5 + h,  0.46]);
        else                 pts.push([(Math.random() - 0.5) * 0.92, -0.5 + h, -0.46]);
    }

    // Rooftop
    for (let i = 0; i < 90; i++) {
        pts.push([
            (Math.random() - 0.5) * 0.9,
            0.70 + (Math.random() - 0.5) * 0.04,
            (Math.random() - 0.5) * 0.9
        ]);
    }

    // Street scatter (trees, vehicles, facade fragments) — avoids building footprint
    for (let i = 0; i < 900; i++) {
        const x = (Math.random() - 0.5) * 8.8;
        const z = (Math.random() - 0.5) * 5.0;
        if (Math.abs(x) < 0.65 && Math.abs(z) < 0.65) continue;   // skip building area
        pts.push([x, -0.5 + Math.random() * 0.5, z]);
    }

    // Vertical scan corridors on both sides of the route.
    for (let i = 0; i < 760; i++) {
        const side = i % 2 === 0 ? -1 : 1;
        const x = side * (2.1 + Math.random() * 2.6);
        const y = -0.45 + Math.random() * 1.7;
        const z = (Math.random() - 0.5) * 5.8;
        pts.push([x, y, z]);
    }

    // CloudCompare B→G→Y→R colormap
    // t=0 → blue, t=0.33 → green, t=0.66 → yellow, t=1 → red
    function ccRGB(t) {
        const c = Math.max(0, Math.min(1, t));
        if (c < 0.33) {
            const s = c / 0.33;
            return [0, Math.round(s * 255), 255];
        } else if (c < 0.66) {
            const s = (c - 0.33) / 0.33;
            return [Math.round(s * 255), 255, Math.round((1 - s) * 255)];
        } else {
            const s = (c - 0.66) / 0.34;
            return [255, Math.round((1 - s) * 255), 0];
        }
    }

    const Y_MIN   = -0.5;
    const Y_RANGE =  1.25;
    let angle     =  0;

    function drawCloud() {
        const W  = cv.width;
        const H  = cv.height;
        const cx = W / 2;
        const cy = H * 0.52;
        const sc = Math.min(W, H) * 0.18;

        ctx.clearRect(0, 0, W, H);

        const cos = Math.cos(angle);
        const sin = Math.sin(angle);

        // Project 3D → 2D (orthographic with slight Y-tilt for depth)
        const proj = pts.map(function (p) {
            const rx = p[0] * cos - p[2] * sin;
            const rz = p[0] * sin + p[2] * cos;
            return {
                sx: cx + rx * sc,
                sy: cy - p[1] * sc * 0.72 + rz * sc * 0.17,
                rz: rz,
                t:  (p[1] - Y_MIN) / Y_RANGE
            };
        });

        // Painter's algorithm: far → near
        proj.sort(function (a, b) { return a.rz - b.rz; });

        for (let i = 0; i < proj.length; i++) {
            const p = proj[i];
            const rgb = ccRGB(p.t);
            ctx.beginPath();
            const radius = 1.05 + Math.max(0, p.t) * 0.45;
            ctx.arc(p.sx, p.sy, radius, 0, Math.PI * 2);
            ctx.fillStyle = 'rgba(' + rgb[0] + ',' + rgb[1] + ',' + rgb[2] + ',0.52)';
            ctx.fill();
        }

        angle += 0.0035;    // ~0.2 rpm — slow, readable
        requestAnimationFrame(drawCloud);
    }

    drawCloud();

    // ── 2. Stat counters ──────────────────────────────────────────

    function easeOutCubic(t) { return 1 - Math.pow(1 - t, 3); }

    function animateCounter(id, target, durationMs, formatFn) {
        const el = document.getElementById(id);
        if (!el) return;
        const t0 = performance.now();
        (function tick(now) {
            const progress = Math.min((now - t0) / durationMs, 1);
            el.textContent = formatFn(Math.round(easeOutCubic(progress) * target));
            if (progress < 1) requestAnimationFrame(tick);
        })(t0);
    }

    // Delay slightly so the page finishes painting first
    setTimeout(function () {
        animateCounter('lp-s-p', 50000, 1200, function (v) {
            return v >= 50000 ? '50K' : Math.round(v / 1000) + 'K';
        });
        animateCounter('lp-s-m', 6, 900,  function (v) { return String(v); });
        animateCounter('lp-s-v', 9, 900,  function (v) { return String(v); });
        animateCounter('lp-s-s', 6, 900,  function (v) { return String(v); });
    }, 350);

    return true;
}

function bootLandingPage() {
    if (initLandingPage()) return;

    let tries = 0;
    const timer = setInterval(function () {
        tries += 1;
        if (initLandingPage() || tries > 40) clearInterval(timer);
    }, 125);
}

window.addEventListener('load', bootLandingPage);
document.addEventListener('DOMContentLoaded', bootLandingPage);
document.addEventListener('dash:rendered', bootLandingPage);
