/* =========================================================================
   GOTHAM — the centerpiece.

   A luminous agent core held inside a brass armature. Every scenario in the
   eval suite is a point of light riding the cage; the ones that fail burn
   oxblood. Shut the gate and the whole instrument goes with it (WARDEN's law:
   the room changes, not a badge).

   The soul sliders are uniforms on the core (VESSEL's law): rigor flattens it
   and steadies its spin, warmth pushes it toward brass, candor makes it breathe.

   Classic script (prototypes open over file://, where local ES modules are
   CORS-blocked). Degrades to the CSS bloom already painted behind the canvas
   if WebGL or the CDN is unavailable. Four draw calls, no postprocessing.
   ========================================================================= */

const REDUCED = matchMedia('(prefers-reduced-motion: reduce)').matches;

const BRASS = 0xe7e5e1;   // bone: live is brightness, not a hue
const BRASS_HOT = 0xffffff;
const SEAL = 0xe5484d;
const SEAL_HOT = 0xff6369;

window.gotham = {
  gate: 'open',      // 'open' | 'blocked'
  soul: { warmth: 0.5, rigor: 0.5, candor: 0.5 },
  setGate(g) { this.gate = g; },
  setSoul(s) { Object.assign(this.soul, s); },
};

window.mountGotham = async function mountGotham(host, { scenarios = 64, fails = 3 } = {}) {
  let THREE;
  try {
    THREE = await import('https://unpkg.com/three@0.161.0/build/three.module.js');
  } catch {
    return; // offline: the CSS bloom stands in
  }

  const canvas = document.createElement('canvas');
  canvas.setAttribute('aria-hidden', 'true');
  Object.assign(canvas.style, {
    position: 'absolute', inset: '0', width: '100%', height: '100%',
    pointerEvents: 'none',
  });
  host.appendChild(canvas);

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
  } catch {
    canvas.remove();
    return;
  }
  renderer.setPixelRatio(Math.min(devicePixelRatio, 1.75));

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 40);
  camera.position.set(0, 0.15, 7.4);
  camera.lookAt(0, 0, 0);

  const group = new THREE.Group();
  scene.add(group);

  /* ---- 1. the core: the agent itself ---------------------------------- */
  const coreGeo = new THREE.IcosahedronGeometry(1.18, 24);
  const coreMat = new THREE.ShaderMaterial({
    transparent: true,
    depthWrite: false,
    blending: THREE.AdditiveBlending,
    uniforms: {
      uTime:   { value: 0 },
      uWarm:   { value: 0.5 },
      uRigor:  { value: 0.5 },
      uCandor: { value: 0.5 },
      uGate:   { value: 0 },          // 0 open, 1 blocked
      uLive:   { value: new THREE.Color(BRASS) },
      uHot:    { value: new THREE.Color(BRASS_HOT) },
    },
    vertexShader: `
      uniform float uTime, uRigor, uCandor;
      varying vec3 vN; varying vec3 vP;
      // cheap value noise
      float h(vec3 p){ return fract(sin(dot(p, vec3(12.9898,78.233,37.719))) * 43758.5453); }
      float n(vec3 p){
        vec3 i = floor(p), f = fract(p);
        f = f*f*(3.0-2.0*f);
        float a = mix(mix(mix(h(i+vec3(0,0,0)),h(i+vec3(1,0,0)),f.x),
                          mix(h(i+vec3(0,1,0)),h(i+vec3(1,1,0)),f.x),f.y),
                      mix(mix(h(i+vec3(0,0,1)),h(i+vec3(1,0,1)),f.x),
                          mix(h(i+vec3(0,1,1)),h(i+vec3(1,1,1)),f.x),f.y),f.z);
        return a;
      }
      void main(){
        vec3 p = position;
        // rigor flattens the form; a loose agent wanders
        float amp = 0.30 * (1.0 - uRigor);
        float freq = 1.6 + uRigor * 2.4;
        float d = n(normalize(p) * freq + uTime * 0.16) - 0.5;
        // candor makes it breathe
        float breathe = 1.0 + sin(uTime * 1.3) * 0.045 * uCandor;
        p += normalize(p) * d * amp;
        p *= breathe;
        vN = normalize(normalMatrix * normal);
        vP = p;
        gl_Position = projectionMatrix * modelViewMatrix * vec4(p, 1.0);
      }
    `,
    fragmentShader: `
      uniform vec3 uLive, uHot;
      uniform float uWarm, uGate;
      varying vec3 vN; varying vec3 vP;
      void main(){
        vec3 V = vec3(0.0, 0.0, 1.0);
        float fres = pow(1.0 - abs(dot(normalize(vN), V)), 2.6);
        // warmth pushes the core from a cold porcelain toward the brass
        vec3 cold = vec3(0.62, 0.70, 0.72);
        vec3 body = mix(cold, uLive, 0.35 + uWarm * 0.5);
        vec3 col = body * 0.30 + uHot * fres * 1.25;
        float a = 0.16 + fres * 0.82;
        gl_FragColor = vec4(col, a);
      }
    `,
  });
  group.add(new THREE.Mesh(coreGeo, coreMat));

  /* ---- 2. the armature: the eval harness holding it ------------------- */
  const cageGeo = new THREE.IcosahedronGeometry(1.95, 1);
  const cageMat = new THREE.MeshBasicMaterial({
    color: BRASS, wireframe: true, transparent: true,
    opacity: 0.30, blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const cage = new THREE.Mesh(cageGeo, cageMat);
  group.add(cage);

  /* ---- 3. the armillary rings (the gate) ------------------------------ */
  const ringGeo = new THREE.TorusGeometry(2.5, 0.006, 8, 180);
  const ringMat = new THREE.MeshBasicMaterial({
    color: BRASS, transparent: true, opacity: 0.5,
    blending: THREE.AdditiveBlending, depthWrite: false,
  });
  const rings = new THREE.Group();
  const r1 = new THREE.Mesh(ringGeo, ringMat); r1.rotation.x = Math.PI / 2;
  const r2 = new THREE.Mesh(ringGeo, ringMat); r2.rotation.x = Math.PI / 2.35; r2.rotation.z = 0.5;
  rings.add(r1, r2);
  group.add(rings);

  /* ---- 4. the scenarios: one point of light each ----------------------- */
  const pos = new Float32Array(scenarios * 3);
  const col = new Float32Array(scenarios * 3);
  const cBrass = new THREE.Color(BRASS_HOT);
  const cSeal = new THREE.Color(SEAL_HOT);
  const failSet = new Set();
  // deterministic failures, so the same scenarios fail on every reload
  for (let k = 0; k < fails; k++) failSet.add(((k + 1) * 17) % scenarios);

  for (let i = 0; i < scenarios; i++) {
    // fibonacci sphere — the scenarios ride the cage evenly
    const y = 1 - (i / (scenarios - 1)) * 2;
    const r = Math.sqrt(Math.max(0, 1 - y * y));
    const phi = i * 2.399963229728653;
    pos[i * 3] = Math.cos(phi) * r * 2.02;
    pos[i * 3 + 1] = y * 2.02;
    pos[i * 3 + 2] = Math.sin(phi) * r * 2.02;
    const c = failSet.has(i) ? cSeal : cBrass;
    col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b;
  }
  const ptGeo = new THREE.BufferGeometry();
  ptGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  ptGeo.setAttribute('color', new THREE.BufferAttribute(col, 3));
  const ptMat = new THREE.PointsMaterial({
    size: 0.055, vertexColors: true, transparent: true, opacity: 0.95,
    blending: THREE.AdditiveBlending, depthWrite: false, sizeAttenuation: true,
  });
  group.add(new THREE.Points(ptGeo, ptMat));

  /* ---- resize ---------------------------------------------------------- */
  function resize() {
    const { clientWidth: w, clientHeight: h } = host;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
  }
  resize();
  addEventListener('resize', resize);

  /* ---- the loop -------------------------------------------------------- */
  const liveCol = new THREE.Color(BRASS);
  const hotCol = new THREE.Color(BRASS_HOT);
  const openLive = new THREE.Color(BRASS);
  const openHot = new THREE.Color(BRASS_HOT);
  const shutLive = new THREE.Color(SEAL);
  const shutHot = new THREE.Color(SEAL_HOT);

  let gateT = 0;
  let t = 0;
  const clock = new THREE.Clock();

  function frame() {
    const dt = Math.min(clock.getDelta(), 0.05);
    t += REDUCED ? dt * 0.2 : dt;

    const s = window.gotham.soul;
    const targetGate = window.gotham.gate === 'blocked' ? 1 : 0;
    gateT += (targetGate - gateT) * Math.min(1, dt * 3.4);

    // THE FLIP: every brass fitting in the instrument lerps to oxblood
    liveCol.copy(openLive).lerp(shutLive, gateT);
    hotCol.copy(openHot).lerp(shutHot, gateT);
    cageMat.color.copy(liveCol);
    ringMat.color.copy(liveCol);
    coreMat.uniforms.uLive.value.copy(liveCol);
    coreMat.uniforms.uHot.value.copy(hotCol);
    coreMat.uniforms.uGate.value = gateT;

    coreMat.uniforms.uTime.value = t;
    coreMat.uniforms.uWarm.value = s.warmth;
    coreMat.uniforms.uRigor.value = s.rigor;
    coreMat.uniforms.uCandor.value = s.candor;

    // rigor steadies the spin; a loose agent wobbles
    const spin = REDUCED ? 0.02 : 0.10 + (1 - s.rigor) * 0.10;
    group.rotation.y += spin * dt;
    group.rotation.x = Math.sin(t * 0.22) * 0.10 * (1 - s.rigor);
    rings.rotation.z += dt * (REDUCED ? 0.01 : 0.05);

    // a blocked gate tightens the cage
    const k = 1 - gateT * 0.06;
    cage.scale.setScalar(k);

    renderer.render(scene, camera);
    raf = requestAnimationFrame(frame);
  }

  let raf = requestAnimationFrame(frame);

  document.addEventListener('visibilitychange', () => {
    if (document.hidden) cancelAnimationFrame(raf);
    else { clock.getDelta(); raf = requestAnimationFrame(frame); }
  });
};
