/* Amber Console — three.js point-grid terrain. Landing + auth only.
   Classic script (prototypes open over file://, where local ES modules are
   CORS-blocked). Loads three from CDN via dynamic import; degrades to the
   static CSS gradient already painted behind the canvas if WebGL or the
   network is unavailable. */

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

window.mountScene = async function mountScene(host, { opacity = 1 } = {}) {
  let THREE;
  try {
    THREE = await import('https://unpkg.com/three@0.161.0/build/three.module.js');
  } catch {
    return; // offline: keep CSS fallback
  }

  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  Object.assign(canvas.style, {
    position: 'absolute', inset: '0', width: '100%', height: '100%',
    pointerEvents: 'none', opacity: String(opacity),
  });
  host.appendChild(canvas);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch {
    canvas.remove();
    return;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.5));

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x111111, 8, 30);

  const camera = new THREE.PerspectiveCamera(55, 1, 0.1, 60);
  camera.position.set(0, 2.1, 9);
  camera.lookAt(0, 0.4, 0);

  // sparse grid-plane of points
  const COLS = 90, ROWS = 50, GAP = 0.42;
  const count = COLS * ROWS;
  const pos = new Float32Array(count * 3);
  let i = 0;
  for (let r = 0; r < ROWS; r++) {
    for (let c = 0; c < COLS; c++) {
      pos[i++] = (c - COLS / 2) * GAP;
      pos[i++] = 0;
      pos[i++] = -r * GAP * 1.15 + 4;
    }
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  const mat = new THREE.PointsMaterial({
    color: 0xe5b13d, size: 0.028, sizeAttenuation: true,
    transparent: true, opacity: 0.85, fog: true,
  });
  scene.add(new THREE.Points(geo, mat));

  function resize() {
    const { clientWidth: w, clientHeight: h } = host;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  resize();
  addEventListener('resize', resize);

  const positions = geo.attributes.position;
  function wave(t) {
    let j = 0;
    for (let r = 0; r < ROWS; r++) {
      for (let c = 0; c < COLS; c++) {
        const x = (c - COLS / 2) * GAP;
        const z = r * GAP;
        positions.array[j * 3 + 1] =
          Math.sin(x * 0.55 + t) * 0.16 +
          Math.sin(z * 0.7 + t * 0.6) * 0.22;
        j++;
      }
    }
    positions.needsUpdate = true;
  }

  if (REDUCED) {
    wave(1.4);                       // one static, composed frame
    renderer.render(scene, camera);
    return;
  }

  let raf;
  const t0 = performance.now();
  (function tick(now) {
    wave((now - t0) / 2400);
    renderer.render(scene, camera);
    raf = requestAnimationFrame(tick);
  })(t0);

  // pause offscreen / hidden tab
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) cancelAnimationFrame(raf);
    else raf = requestAnimationFrame(function tick(now) {
      wave((now - t0) / 2400);
      renderer.render(scene, camera);
      raf = requestAnimationFrame(tick);
    });
  });
};
