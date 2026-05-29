/*!
 * W Chats embed loader — the one-line <script> a customer pastes into their site.
 *
 *   <script src="https://YOUR_CDN/wchats/widget.js"
 *           data-agent="<agent-id>"
 *           data-api="https://YOUR_API_HOST"
 *           async></script>
 *
 * It reads its own data-* attributes, injects a floating launcher button, and lazily
 * mounts the chat panel inside a sandboxed <iframe> (so the host page's CSS/JS can never
 * collide with the widget). The iframe loads index.html (served alongside this file) with
 * ?agent_id=…&api=… — exactly what apps/widget/src/index.jsx expects.
 *
 * Host-agnostic by design: works behind Vercel static, S3+CloudFront, or any origin.
 * The API base is passed at runtime (data-api / window.WCHATS_API_BASE), so flipping the
 * backend from a managed container host to AWS Fargate is a one-attribute change — no rebuild.
 */
(function () {
  "use strict";
  if (window.__wchatsLoaded) return;        // idempotent — survive double-injection
  window.__wchatsLoaded = true;

  // async scripts have a null document.currentScript at run time → locate by marker attr
  var script =
    document.currentScript ||
    document.querySelector("script[data-agent][src*='widget.js']");
  if (!script) {
    console.error("[wchats] loader could not locate its own <script data-agent> tag");
    return;
  }

  var agentId = script.getAttribute("data-agent");
  if (!agentId) {
    console.error("[wchats] missing data-agent on the embed <script> tag");
    return;
  }

  // API base resolution order: data-api  →  global override  →  empty (warn).
  var apiBase =
    script.getAttribute("data-api") ||
    window.WCHATS_API_BASE ||
    "";
  if (!apiBase) {
    console.warn(
      "[wchats] no API base set — add data-api=\"https://your-api-host\" to the embed tag"
    );
  }
  apiBase = apiBase.replace(/\/+$/, ""); // strip trailing slash

  // The chat panel page lives next to this script on the same origin/CDN folder.
  var hostUrl = new URL("index.html", script.src);
  hostUrl.searchParams.set("agent_id", agentId);
  if (apiBase) hostUrl.searchParams.set("api", apiBase);

  var accent = script.getAttribute("data-color") || "#E8536B"; // W Chats coral default
  var label = script.getAttribute("data-label") || "Chat";

  // ---- styles (scoped to our element ids; minimal footprint) ---------------
  var css =
    "#wchats-launcher{position:fixed;bottom:20px;right:20px;z-index:2147483000;" +
    "width:60px;height:60px;border:0;border-radius:50%;cursor:pointer;" +
    "background:" + accent + ";color:#fff;box-shadow:0 6px 24px rgba(0,0,0,.28);" +
    "display:flex;align-items:center;justify-content:center;transition:transform .15s ease}" +
    "#wchats-launcher:hover{transform:scale(1.06)}" +
    "#wchats-launcher svg{width:28px;height:28px;fill:#fff}" +
    "#wchats-frame-wrap{position:fixed;bottom:92px;right:20px;z-index:2147483000;" +
    "width:380px;height:600px;max-width:calc(100vw - 32px);max-height:calc(100vh - 120px);" +
    "border-radius:16px;overflow:hidden;box-shadow:0 12px 48px rgba(0,0,0,.32);" +
    "opacity:0;transform:translateY(12px);pointer-events:none;transition:opacity .18s ease,transform .18s ease}" +
    "#wchats-frame-wrap.open{opacity:1;transform:translateY(0);pointer-events:auto}" +
    "#wchats-frame{width:100%;height:100%;border:0;background:#fff}" +
    "@media(max-width:480px){#wchats-frame-wrap{bottom:0;right:0;width:100vw;height:100vh;" +
    "max-width:100vw;max-height:100vh;border-radius:0}}";
  var styleEl = document.createElement("style");
  styleEl.textContent = css;
  document.head.appendChild(styleEl);

  // ---- iframe (lazy: src set on first open) --------------------------------
  var wrap = document.createElement("div");
  wrap.id = "wchats-frame-wrap";
  var frame = document.createElement("iframe");
  frame.id = "wchats-frame";
  frame.title = "W Chats assistant";
  frame.setAttribute("loading", "lazy");
  frame.setAttribute("allow", "clipboard-write");
  wrap.appendChild(frame);
  document.body.appendChild(wrap);

  // ---- launcher ------------------------------------------------------------
  var btn = document.createElement("button");
  btn.id = "wchats-launcher";
  btn.setAttribute("aria-label", "Open " + label);
  var ICON_CHAT =
    '<svg viewBox="0 0 24 24"><path d="M12 3C6.5 3 2 6.6 2 11c0 2.1 1 4 2.7 5.4L4 21l4.9-1.6c1 .3 2 .4 3.1.4 5.5 0 10-3.6 10-8s-4.5-8-10-8z"/></svg>';
  var ICON_CLOSE =
    '<svg viewBox="0 0 24 24"><path d="M18.3 5.7 12 12l6.3 6.3-1.4 1.4L10.6 13.4 4.3 19.7 2.9 18.3 9.2 12 2.9 5.7 4.3 4.3l6.3 6.3L16.9 4.3z"/></svg>';
  btn.innerHTML = ICON_CHAT;

  var open = false;
  var mounted = false;
  btn.addEventListener("click", function () {
    open = !open;
    if (open && !mounted) {
      frame.src = hostUrl.toString(); // lazy-load the panel on first open
      mounted = true;
    }
    wrap.classList.toggle("open", open);
    btn.innerHTML = open ? ICON_CLOSE : ICON_CHAT;
    btn.setAttribute("aria-label", (open ? "Close " : "Open ") + label);
  });
  document.body.appendChild(btn);
})();
