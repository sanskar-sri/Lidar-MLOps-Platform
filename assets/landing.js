/*
 * assets/landing.js
 * Dash loads this globally, so every initializer checks for page-specific
 * canvas and status elements before doing any work.
 */

(function () {
    function initLandingCanvas() {
        const cv = document.querySelector(
            '#lp-cv, #data-explorer-cv, #preprocessing-cv, #training-cv, #control-cv, #postprocessing-cv'
        );
        if (!cv) return false;
        if (cv.dataset.lpStarted === '1') return true;
        cv.dataset.lpStarted = '1';
        if (typeof cv.getContext !== 'function') {
            cv.classList.add('lp-cv-fallback');
            return true;
        }

        const ctx = cv.getContext('2d');
        const parent = cv.parentElement;
        const particles = [];
        const cloudPoints = [];
        let width = 0;
        let height = 0;
        let mouseX = -9999;
        let mouseY = -9999;
        let scanStartedAt = performance.now();
        let raf = null;

        function resize() {
            const w = parent ? parent.clientWidth : cv.offsetWidth;
            const h = parent ? parent.clientHeight : cv.offsetHeight;
            if (w === 0 || h === 0) return false;
            width = w;
            height = h;
            cv.width = Math.max(1, Math.floor(width * window.devicePixelRatio));
            cv.height = Math.max(1, Math.floor(height * window.devicePixelRatio));
            cv.style.width = width + 'px';
            cv.style.height = height + 'px';
            ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);
            return true;
        }

        function makeParticles() {
            particles.length = 0;
            const count = Math.max(320, Math.floor(width * 0.44));
            for (let i = 0; i < count; i += 1) {
                const layer = Math.floor(Math.random() * 3);
                const speed = layer === 0 ? 0.18 : layer === 1 ? 0.48 : 0.95;
                const radius = layer === 0 ? 0.55 : layer === 1 ? 1.15 : 2.05;
                const alpha = layer === 0 ? 0.26 : layer === 1 ? 0.58 : 0.95;
                particles.push({
                    x: Math.random() * width,
                    y: Math.random() * height,
                    vx: (Math.random() - 0.5) * speed,
                    vy: (Math.random() - 0.5) * speed * 0.45,
                    radius,
                    alpha,
                    hue: 188 + Math.random() * 70,
                    layer,
                });
            }
        }

        function seededRandom(seed) {
            let value = seed;
            return function () {
                value = (value * 1664525 + 1013904223) % 4294967296;
                return value / 4294967296;
            };
        }

        function addBuildingPoints(rng, centerX, centerZ, boxWidth, boxDepth, boxHeight, count, hue) {
            for (let i = 0; i < count; i += 1) {
                const face = i % 6;
                let x = (rng() - 0.5) * boxWidth;
                let y = -rng() * boxHeight;
                let z = (rng() - 0.5) * boxDepth;

                if (face === 0) z = -boxDepth / 2;
                if (face === 1) z = boxDepth / 2;
                if (face === 2) x = -boxWidth / 2;
                if (face === 3) x = boxWidth / 2;
                if (face === 4) y = -boxHeight;

                cloudPoints.push({
                    x: centerX + x,
                    y,
                    z: centerZ + z,
                    hue: hue + rng() * 24,
                    size: 1.1 + rng() * 1.6,
                    alpha: 0.48 + rng() * 0.34,
                    pulse: rng() * Math.PI * 2,
                });
            }
        }

        function addGroundPoints(rng, count) {
            for (let i = 0; i < count; i += 1) {
                const angle = rng() * Math.PI * 2;
                const radius = Math.sqrt(rng()) * 1.55;
                cloudPoints.push({
                    x: Math.cos(angle) * radius,
                    y: 0.04 + (rng() - 0.5) * 0.05,
                    z: Math.sin(angle) * radius * 0.72,
                    hue: 178 + rng() * 70,
                    size: 0.9 + rng() * 1.1,
                    alpha: 0.28 + rng() * 0.32,
                    pulse: rng() * Math.PI * 2,
                });
            }
        }

        function addOrbitPoints(count) {
            for (let i = 0; i < count; i += 1) {
                const t = i / count;
                const angle = t * Math.PI * 2;
                const radius = 1.18 + 0.2 * Math.sin(t * Math.PI * 6);
                cloudPoints.push({
                    x: Math.cos(angle) * radius,
                    y: -0.62 + 0.42 * Math.sin(t * Math.PI * 4),
                    z: Math.sin(angle) * radius * 0.82,
                    hue: 195 + 38 * Math.sin(t * Math.PI * 2),
                    size: 1.05,
                    alpha: 0.44,
                    pulse: t * Math.PI * 2,
                    orbit: true,
                });
            }
        }

        function makePointCloud() {
            cloudPoints.length = 0;
            const rng = seededRandom(260516);
            const density = width < 760 ? 0.62 : 1;

            addBuildingPoints(rng, -0.62, -0.08, 0.34, 0.42, 0.78, Math.floor(180 * density), 196);
            addBuildingPoints(rng, -0.14, 0.18, 0.42, 0.36, 1.12, Math.floor(240 * density), 205);
            addBuildingPoints(rng, 0.4, -0.16, 0.32, 0.46, 0.92, Math.floor(190 * density), 186);
            addBuildingPoints(rng, 0.78, 0.12, 0.25, 0.28, 0.62, Math.floor(120 * density), 214);
            addGroundPoints(rng, Math.floor(240 * density));
            addOrbitPoints(Math.floor(180 * density));
        }

        function drawGrid() {
            const horizon = height * 0.64;
            const bottom = height;
            ctx.strokeStyle = 'rgba(79,179,255,0.085)';
            ctx.lineWidth = 0.6;

            for (let i = 0; i <= 10; i += 1) {
                const t = i / 10;
                const bottomX = width / 2 + (t - 0.5) * width * 3.3;
                const topX = width / 2 + (t - 0.5) * width * 0.28;
                ctx.beginPath();
                ctx.moveTo(bottomX, bottom);
                ctx.lineTo(topX, horizon);
                ctx.stroke();
            }

            for (let j = 1; j <= 7; j += 1) {
                const y = horizon + j * ((bottom - horizon) / 7);
                const scale = (y - horizon) / (bottom - horizon);
                const gridWidth = width * 0.28 + scale * width * 2.95;
                ctx.beginPath();
                ctx.moveTo(width / 2 - gridWidth / 2, y);
                ctx.lineTo(width / 2 + gridWidth / 2, y);
                ctx.stroke();
            }
        }

        function projectCloudPoint(point, rotation, scale, centerX, centerY) {
            const cos = Math.cos(rotation);
            const sin = Math.sin(rotation);
            const x = point.x * cos - point.z * sin;
            const z = point.x * sin + point.z * cos;
            const perspective = 1.65 / (2.4 + z);
            return {
                x: centerX + x * scale * perspective,
                y: centerY + point.y * scale * perspective,
                z,
                perspective,
                source: point,
            };
        }

        function drawPointCloud(now, scanX) {
            if (!cloudPoints.length) return;

            const scale = Math.min(width, height) * (width < 760 ? 0.48 : 0.52);
            const centerX = width / 2;
            const centerY = height * (width < 760 ? 0.64 : 0.66);
            const rotation = now * 0.00052;
            const points = cloudPoints
                .map(function (point) {
                    return projectCloudPoint(point, rotation, scale, centerX, centerY);
                })
                .sort(function (a, b) {
                    return a.z - b.z;
                });

            ctx.save();
            ctx.globalCompositeOperation = 'lighter';

            const halo = ctx.createRadialGradient(centerX, centerY - scale * 0.38, 0, centerX, centerY - scale * 0.22, scale * 1.4);
            halo.addColorStop(0, 'rgba(79,179,255,0.22)');
            halo.addColorStop(0.45, 'rgba(0,229,160,0.08)');
            halo.addColorStop(1, 'rgba(79,179,255,0)');
            ctx.fillStyle = halo;
            ctx.fillRect(centerX - scale * 1.55, centerY - scale * 1.4, scale * 3.1, scale * 2.4);

            for (const projected of points) {
                const point = projected.source;
                let x = projected.x;
                let y = projected.y;

                if (mouseX > -1000) {
                    const dx = x - mouseX;
                    const dy = y - mouseY;
                    const distance = Math.sqrt(dx * dx + dy * dy);
                    if (distance > 0 && distance < 96) {
                        const push = ((96 - distance) / 96) * 15;
                        x += (dx / distance) * push;
                        y += (dy / distance) * push;
                    }
                }

                const depthAlpha = Math.max(0.42, Math.min(1, (projected.z + 1.7) / 3.1));
                const scanBoost = Math.max(0, 1 - Math.abs(x - scanX) / 90);
                const pulse = 0.76 + Math.sin(now * 0.003 + point.pulse) * 0.24;
                const alpha = Math.min(1, point.alpha * depthAlpha * pulse + scanBoost * 0.45);
                const radius = Math.max(0.8, point.size * projected.perspective * (point.orbit ? 1.15 : 1.55));

                ctx.beginPath();
                ctx.arc(x, y, radius, 0, Math.PI * 2);
                ctx.fillStyle = 'hsla(' + point.hue + ',92%,66%,' + alpha + ')';
                ctx.fill();

                if (scanBoost > 0.58 && !point.orbit) {
                    ctx.beginPath();
                    ctx.arc(x, y, radius * 2.2, 0, Math.PI * 2);
                    ctx.strokeStyle = 'rgba(124,207,255,' + (scanBoost * 0.28) + ')';
                    ctx.lineWidth = 0.7;
                    ctx.stroke();
                }
            }

            ctx.restore();
        }

        function animate() {
            if (!document.body.contains(cv)) {
                if (raf) cancelAnimationFrame(raf);
                return;
            }

            const now = performance.now();
            ctx.clearRect(0, 0, width, height);
            drawGrid();
            const cycleMs = 4500;
            const progress = ((now - scanStartedAt) % cycleMs) / cycleMs;
            const scanX = -80 + progress * (width + 160);

            for (const particle of particles) {
                if (mouseX > -1000) {
                    const dx = particle.x - mouseX;
                    const dy = particle.y - mouseY;
                    const dist = Math.sqrt(dx * dx + dy * dy);
                    const repelRadius = 68 + particle.layer * 18;
                    if (dist > 0 && dist < repelRadius) {
                        const force = ((repelRadius - dist) / repelRadius) * (1.35 + particle.layer * 0.25);
                        particle.x += (dx / dist) * force;
                        particle.y += (dy / dist) * force;
                    }
                }

                particle.x += particle.vx;
                particle.y += particle.vy;

                if (particle.x < -5) particle.x = width + 5;
                if (particle.x > width + 5) particle.x = -5;
                if (particle.y < -5) particle.y = height + 5;
                if (particle.y > height + 5) particle.y = -5;

                let alpha = particle.alpha;
                const distance = Math.abs(particle.x - scanX);
                if (distance < 32) {
                    alpha = Math.min(1, alpha + (1 - distance / 32) * 0.78);
                }

                ctx.beginPath();
                ctx.arc(particle.x, particle.y, particle.radius, 0, Math.PI * 2);
                ctx.fillStyle = 'hsla(' + particle.hue + ',78%,65%,' + alpha + ')';
                ctx.fill();
            }

            drawPointCloud(now, scanX);

            const beam = ctx.createLinearGradient(scanX - 72, 0, scanX + 72, 0);
            beam.addColorStop(0, 'rgba(79,179,255,0)');
            beam.addColorStop(0.38, 'rgba(79,179,255,0.12)');
            beam.addColorStop(0.5, 'rgba(79,179,255,0.36)');
            beam.addColorStop(0.62, 'rgba(0,229,160,0.12)');
            beam.addColorStop(1, 'rgba(79,179,255,0)');
            ctx.fillStyle = beam;
            ctx.fillRect(scanX - 72, 0, 144, height);

            ctx.strokeStyle = 'rgba(124,207,255,0.82)';
            ctx.lineWidth = 1.5;
            ctx.beginPath();
            ctx.moveTo(scanX, 0);
            ctx.lineTo(scanX, height);
            ctx.stroke();

            raf = requestAnimationFrame(animate);
        }

        function pointerMove(event) {
            const rect = cv.getBoundingClientRect();
            mouseX = event.clientX - rect.left;
            mouseY = event.clientY - rect.top;
        }

        function pointerLeave() {
            mouseX = -9999;
            mouseY = -9999;
        }

        function start() {
            if (!document.body.contains(cv)) return;
            if (!resize()) {
                requestAnimationFrame(start);
                return;
            }
            makeParticles();
            makePointCloud();
            window.addEventListener('resize', function () {
                resize();
                makeParticles();
                makePointCloud();
            });
            cv.addEventListener('mousemove', pointerMove);
            cv.addEventListener('mouseleave', pointerLeave);
            animate();
        }

        start();
        return true;
    }

    function initStats() {
        document.querySelectorAll('.lp-stat-value').forEach(function (el) {
            const kind = el.dataset.statKind;
            const rawValue = el.dataset.statValue;
            if (kind !== 'number') return;
            if (el.dataset.animatedValue === rawValue) return;
            el.dataset.animatedValue = rawValue;

            const target = Number(rawValue || 0);
            const duration = 900;
            const start = performance.now();

            function tick(now) {
                const t = Math.min(1, (now - start) / duration);
                const eased = 1 - Math.pow(1 - t, 3);
                const value = Math.round(target * eased);
                el.textContent = value.toLocaleString();
                if (t < 1) requestAnimationFrame(tick);
            }

            el.textContent = '0';
            requestAnimationFrame(tick);
        });
    }

    function initCountdown() {
        const el = document.getElementById('lp-refresh-countdown');
        if (!el || el.dataset.countdownStarted === '1') return;
        el.dataset.countdownStarted = '1';
        let remaining = 30;
        setInterval(function () {
            remaining -= 1;
            if (remaining <= 0) remaining = 30;
            el.textContent = remaining + 's';
        }, 1000);
    }

    function initToasts() {
        const toasts = Array.from(document.querySelectorAll('.lp-toast'));
        if (!toasts.length) return;
        toasts.forEach(function (toast, index) {
            if (toast.dataset.toastShown === '1') return;
            toast.dataset.toastShown = '1';
            setTimeout(function () {
                toast.classList.add('lp-toast-show');
                setTimeout(function () {
                    toast.classList.remove('lp-toast-show');
                }, 3400);
            }, 900 + index * 1250);
        });
    }

    function bootLandingPage() {
        initLandingCanvas();
        initStats();
        initCountdown();
        initToasts();
    }

    window.addEventListener('load', bootLandingPage);
    document.addEventListener('DOMContentLoaded', bootLandingPage);
    document.addEventListener('dash:rendered', bootLandingPage);

    if (document.readyState !== 'loading') {
        requestAnimationFrame(bootLandingPage);
    }

    if (document.body && typeof MutationObserver === 'function') {
        const observer = new MutationObserver(function () {
            bootLandingPage();
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }
})();
