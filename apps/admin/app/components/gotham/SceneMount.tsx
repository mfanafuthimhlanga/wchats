'use client'

/* =========================================================================
   SceneMount — the centerpiece.

   A luminous agent core held inside a bone armature. Every scenario in the
   eval suite is a point of light riding the cage; the ones that fail burn
   seal red. Shut the gate and the whole instrument goes with it (the room
   changes, not a badge).

   Client-only: `three` is dynamically imported inside the mount effect,
   never at module scope (Pitfall 2 — a top-level `import ... from 'three'`
   crashes SSR with `window is not defined` and pulls the ~600KB chunk into
   every route's first-load JS). Degrades silently to the CSS `.bloom` layer
   already painted behind the host (PageChrome) if `three` fails to load or
   the WebGL context cannot be created. Four draw calls, no postprocessing —
   ported from prototypes/gotham/scene.js `mountGotham`.

   The bone-colour constants below carry the "Bone on Graphite" naming this
   design settled on (must-fix 5) — the source prototype's scene.js used an
   earlier, retired material-metaphor name for the same two hex values; only
   the identifiers changed here, not the colours.
   ========================================================================= */

import { useEffect, useRef } from 'react'
import { useGate } from './GateProvider'

const LIVE = 0xe7e5e1 // bone: live is brightness, not a hue
const LIVE_HOT = 0xffffff
const SEAL = 0xe5484d
const SEAL_HOT = 0xff6369

interface SceneMountProps {
  scenarios?: number
  fails?: number
}

export default function SceneMount({ scenarios = 64, fails = 3 }: SceneMountProps) {
  const hostRef = useRef<HTMLDivElement>(null)
  const { gate } = useGate()
  const gateRef = useRef(gate)

  // The mount effect below only runs once per scenarios/fails pair — the
  // per-frame render loop reads gateRef so a gate flip elsewhere on the page
  // (the landing gate demo, useGate()-driven) lerps the specimen too without
  // tearing down and remounting the whole scene.
  useEffect(() => {
    gateRef.current = gate
  }, [gate])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    let disposed = false
    let cleanup: (() => void) | undefined

    import('three')
      .then((THREE) => {
        if (disposed || !host) return

        const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)').matches

        const canvas = document.createElement('canvas')
        canvas.setAttribute('aria-hidden', 'true')
        Object.assign(canvas.style, {
          position: 'absolute',
          inset: '0',
          width: '100%',
          height: '100%',
          pointerEvents: 'none',
        })
        host.appendChild(canvas)

        let renderer: import('three').WebGLRenderer | null = null
        try {
          renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true })
        } catch {
          canvas.remove()
          return
        }
        if (!renderer) {
          canvas.remove()
          return
        }
        renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75))
        // Pitfall 3 (version drift r161 -> current npm): pin colour
        // management explicitly rather than leaving it to a version default.
        renderer.outputColorSpace = THREE.SRGBColorSpace

        const scene = new THREE.Scene()
        const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 40)
        camera.position.set(0, 0.15, 7.4)
        camera.lookAt(0, 0, 0)

        const group = new THREE.Group()
        scene.add(group)

        /* ---- 1. the core: the agent itself -------------------------------- */
        const coreGeo = new THREE.IcosahedronGeometry(1.18, 24)
        const coreMat = new THREE.ShaderMaterial({
          transparent: true,
          depthWrite: false,
          blending: THREE.AdditiveBlending,
          uniforms: {
            uTime: { value: 0 },
            uWarm: { value: 0.5 },
            uRigor: { value: 0.5 },
            uCandor: { value: 0.5 },
            uGate: { value: 0 }, // 0 open, 1 blocked
            uLive: { value: new THREE.Color(LIVE) },
            uHot: { value: new THREE.Color(LIVE_HOT) },
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
              // warmth pushes the core from a cold porcelain toward bone
              vec3 cold = vec3(0.62, 0.70, 0.72);
              vec3 body = mix(cold, uLive, 0.35 + uWarm * 0.5);
              vec3 col = body * 0.30 + uHot * fres * 1.25;
              float a = 0.16 + fres * 0.82;
              gl_FragColor = vec4(col, a);
            }
          `,
        })
        group.add(new THREE.Mesh(coreGeo, coreMat))

        /* ---- 2. the armature: the eval harness holding it ------------------ */
        const cageGeo = new THREE.IcosahedronGeometry(1.95, 1)
        const cageMat = new THREE.MeshBasicMaterial({
          color: LIVE,
          wireframe: true,
          transparent: true,
          opacity: 0.3,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        })
        const cage = new THREE.Mesh(cageGeo, cageMat)
        group.add(cage)

        /* ---- 3. the armillary rings (the gate) ------------------------------ */
        const ringGeo = new THREE.TorusGeometry(2.5, 0.006, 8, 180)
        const ringMat = new THREE.MeshBasicMaterial({
          color: LIVE,
          transparent: true,
          opacity: 0.5,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
        })
        const rings = new THREE.Group()
        const r1 = new THREE.Mesh(ringGeo, ringMat)
        r1.rotation.x = Math.PI / 2
        const r2 = new THREE.Mesh(ringGeo, ringMat)
        r2.rotation.x = Math.PI / 2.35
        r2.rotation.z = 0.5
        rings.add(r1, r2)
        group.add(rings)

        /* ---- 4. the scenarios: one point of light each ----------------------- */
        const pos = new Float32Array(scenarios * 3)
        const col = new Float32Array(scenarios * 3)
        const cLive = new THREE.Color(LIVE_HOT)
        const cSeal = new THREE.Color(SEAL_HOT)
        const failSet = new Set<number>()
        // deterministic failures, so the same scenarios fail on every reload
        for (let k = 0; k < fails; k++) failSet.add(((k + 1) * 17) % scenarios)

        for (let i = 0; i < scenarios; i++) {
          // fibonacci sphere — the scenarios ride the cage evenly
          const y = 1 - (i / (scenarios - 1)) * 2
          const r = Math.sqrt(Math.max(0, 1 - y * y))
          const phi = i * 2.399963229728653
          pos[i * 3] = Math.cos(phi) * r * 2.02
          pos[i * 3 + 1] = y * 2.02
          pos[i * 3 + 2] = Math.sin(phi) * r * 2.02
          const c = failSet.has(i) ? cSeal : cLive
          col[i * 3] = c.r
          col[i * 3 + 1] = c.g
          col[i * 3 + 2] = c.b
        }
        const ptGeo = new THREE.BufferGeometry()
        ptGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
        ptGeo.setAttribute('color', new THREE.BufferAttribute(col, 3))
        const ptMat = new THREE.PointsMaterial({
          size: 0.055,
          vertexColors: true,
          transparent: true,
          opacity: 0.95,
          blending: THREE.AdditiveBlending,
          depthWrite: false,
          sizeAttenuation: true,
        })
        const points = new THREE.Points(ptGeo, ptMat)
        group.add(points)

        /* ---- resize ---------------------------------------------------------- */
        function resize() {
          if (!host || !renderer) return
          const { clientWidth: w, clientHeight: h } = host
          if (!w || !h) return
          renderer.setSize(w, h, false)
          camera.aspect = w / h
          camera.updateProjectionMatrix()
        }
        resize()
        window.addEventListener('resize', resize)

        /* ---- the loop -------------------------------------------------------- */
        const liveCol = new THREE.Color(LIVE)
        const hotCol = new THREE.Color(LIVE_HOT)
        const openLive = new THREE.Color(LIVE)
        const openHot = new THREE.Color(LIVE_HOT)
        const shutLive = new THREE.Color(SEAL)
        const shutHot = new THREE.Color(SEAL_HOT)

        let gateT = 0
        let t = 0
        const clock = new THREE.Clock()
        let raf = 0

        function frame() {
          if (!renderer) return
          const dt = Math.min(clock.getDelta(), 0.05)
          t += REDUCED ? dt * 0.2 : dt

          const targetGate = gateRef.current === 'blocked' ? 1 : 0
          gateT += (targetGate - gateT) * Math.min(1, dt * 3.4)

          // THE FLIP: every bone fitting in the instrument lerps to seal red
          liveCol.copy(openLive).lerp(shutLive, gateT)
          hotCol.copy(openHot).lerp(shutHot, gateT)
          cageMat.color.copy(liveCol)
          ringMat.color.copy(liveCol)
          coreMat.uniforms.uLive.value.copy(liveCol)
          coreMat.uniforms.uHot.value.copy(hotCol)
          coreMat.uniforms.uGate.value = gateT

          coreMat.uniforms.uTime.value = t
          coreMat.uniforms.uWarm.value = 0.5
          coreMat.uniforms.uRigor.value = 0.5
          coreMat.uniforms.uCandor.value = 0.5

          // rigor steadies the spin; a loose agent wobbles
          const spin = REDUCED ? 0.02 : 0.1 + 0.5 * 0.1
          group.rotation.y += spin * dt
          group.rotation.x = Math.sin(t * 0.22) * 0.1 * 0.5
          rings.rotation.z += dt * (REDUCED ? 0.01 : 0.05)

          // a blocked gate tightens the cage
          const k = 1 - gateT * 0.06
          cage.scale.setScalar(k)

          renderer.render(scene, camera)
          raf = requestAnimationFrame(frame)
        }

        raf = requestAnimationFrame(frame)

        function handleVisibility() {
          if (document.hidden) {
            cancelAnimationFrame(raf)
          } else {
            clock.getDelta()
            raf = requestAnimationFrame(frame)
          }
        }
        document.addEventListener('visibilitychange', handleVisibility)

        cleanup = () => {
          cancelAnimationFrame(raf)
          window.removeEventListener('resize', resize)
          document.removeEventListener('visibilitychange', handleVisibility)
          coreGeo.dispose()
          coreMat.dispose()
          cageGeo.dispose()
          cageMat.dispose()
          ringGeo.dispose()
          ringMat.dispose()
          ptGeo.dispose()
          ptMat.dispose()
          renderer?.dispose()
          canvas.remove()
        }
      })
      .catch(() => {
        // three failed to load, or WebGL context creation threw inside the
        // .then above and returned early — the CSS .bloom layer already
        // painted behind this host (PageChrome) stands in. Nothing to clean
        // up since nothing was mounted.
      })

    return () => {
      disposed = true
      cleanup?.()
    }
  }, [scenarios, fails])

  return <div ref={hostRef} aria-hidden="true" style={{ position: 'absolute', inset: 0 }} />
}
