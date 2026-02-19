// Rewritten clean file
// Track created blob URLs so we can clean them up on unload
const pdfBlobUrls = [];

// Ensure global helpers exist to avoid ReferenceError from inline handlers
try {
  if (typeof window !== 'undefined') {
    window.thumbnails = window.thumbnails || [];
    window.currentIndex = typeof window.currentIndex !== 'undefined' ? window.currentIndex : 0;
  }
} catch (e) { /* ignore */ }

// Toast helper: non-blocking, non-disabling UI feedback
function showToast(message, type = 'info', timeout = 4000, action = null) {
  try {
    const container = document.getElementById('toast-container');
    if (!container) return;
    const el = document.createElement('div');
    el.className = 'toast toast--' + type;
    el.style.pointerEvents = 'auto';
    el.style.background = type === 'error' ? '#ffefef' : type === 'success' ? '#eefaf0' : '#fff';
    el.style.border = '1px solid ' + (type === 'error' ? '#f5c2c2' : type === 'success' ? '#cfe9d8' : '#ddd');
    el.style.color = '#111';
    el.style.padding = '10px 14px';
    el.style.borderRadius = '8px';
    el.style.boxShadow = '0 6px 18px rgba(20,20,30,0.06)';
    el.style.maxWidth = '360px';
    el.style.fontSize = '13px';
    el.style.opacity = '0';
    el.style.transition = 'opacity 180ms ease, transform 220ms ease';

    const content = document.createElement('div');
    content.style.display = 'flex';
    content.style.alignItems = 'center';
    content.style.gap = '10px';
    content.textContent = message;
    el.appendChild(content);

    if (action && (action.label || action.href)) {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn';
      btn.style.marginLeft = '8px';
      btn.style.padding = '6px 10px';
      btn.style.borderRadius = '8px';
      btn.style.border = '1px solid var(--border)';
      btn.style.cursor = 'pointer';
      btn.textContent = action.label || 'Action';
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        try {
          if (action.href) {
            // include next param if explicitly asked for convenience
            window.location.href = action.href;
            return;
          }
          if (typeof action.onClick === 'function') return action.onClick();
        } catch (ee) {}
      });
      el.appendChild(btn);
    }

    container.appendChild(el);
    // entrance
    requestAnimationFrame(() => { el.style.opacity = '1'; el.style.transform = 'translateY(0)'; });
    // auto remove
    setTimeout(() => {
      el.style.opacity = '0';
      el.style.transform = 'translateY(6px)';
      setTimeout(() => { try { container.removeChild(el); } catch (e) {} }, 220);
    }, timeout);
  } catch (e) { /* swallow */ }
}

// Minimal markdown renderer for AI text (bold/italic/newlines)
function renderAiMarkdown(text) {
  try {
    const raw = String(text || '');
    const esc = raw
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');
    // bold then italic
    let html = esc.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
    html = html.replace(/\*(.+?)\*/g, '<em>$1</em>');
    // line breaks
    html = html.replace(/\n/g, '<br>');
    return html;
  } catch (e) {
    return String(text || '');
  }
}
try { window.renderAiMarkdown = renderAiMarkdown; } catch(e) {}

// Global fetch wrapper: show sign-in toast on 401 responses
(function(){
  if (!window.fetch) return;
  const _fetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    try {
      // ensure credentials are included for same-origin requests unless explicitly set
      if (args.length >= 2 && typeof args[1] === 'object') {
        if (typeof args[1].credentials === 'undefined') args[1].credentials = 'include';
      } else if (args.length === 1) {
        args[1] = { credentials: 'include' };
      }
      // attach CSRF header for non-GET same-origin requests
      try {
        const init = args[1] || {};
        const method = (init.method || 'GET').toUpperCase();
        if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
          const cookieMatch = document.cookie && document.cookie.match(/(^|;)\s*csrf_token=([^;]+)/);
          const csrf = cookieMatch ? decodeURIComponent(cookieMatch[2]) : null;
          if (csrf) {
            init.headers = init.headers || {};
            // preserve existing headers
            if (init.headers instanceof Headers) {
              init.headers.set('X-CSRF-Token', csrf);
            } else if (Array.isArray(init.headers)) {
              init.headers.push(['X-CSRF-Token', csrf]);
            } else {
              init.headers['X-CSRF-Token'] = csrf;
            }
            args[1] = init;
          }
        }
      } catch (e) { /* swallow */ }

      const res = await _fetch(...args);
      if (res && res.status === 401) {
        // Friendly prompt to sign in for protected actions with action button
        try {
          const next = encodeURIComponent(window.location.pathname + window.location.search || '/');
          showToast('Please sign in to use this feature', 'info', 10000, { label: 'Sign in', href: '/login?next=' + next });
        } catch(e){}
      }
      return res;
    } catch (err) {
      throw err;
    }
  };
})();

// Defensive logo click handler: ensure brand link navigates even if overlay/CSS blocks the anchor
document.addEventListener('DOMContentLoaded', function () {
  try {
    const logo = document.querySelector('.brand--with-logo') || document.querySelector('.brand');
    if (!logo) return;
    // If the anchor is present but not functioning (e.g. overlay or pointer-events), force navigation
    logo.addEventListener('click', function (ev) {
      try {
        ev.preventDefault(); ev.stopPropagation();
      } catch (e) {}
      const href = logo.getAttribute && logo.getAttribute('href') || logo.href || '/';
      // small delay to let any other handlers run, then navigate
      setTimeout(function () { window.location.href = href; }, 10);
      return false;
    }, { passive: true });
  } catch (err) { /* ignore */ }
});

// Student menu toggle + outside-click close
document.addEventListener('DOMContentLoaded', function(){
  try {
    const studentBtn = document.getElementById('student-hamburger');
    const studentMenu = document.getElementById('student-menu');
    if (!studentBtn || !studentMenu) return;
    studentBtn.addEventListener('click', function(ev){ ev.stopPropagation(); studentMenu.style.display = (studentMenu.style.display === 'block') ? 'none' : 'block'; });
    document.addEventListener('click', function(ev){ if (!studentMenu.contains(ev.target) && ev.target !== studentBtn) studentMenu.style.display = 'none'; });
  } catch(e) { /* ignore */ }
});
// pdf.js worker configuration (shared across pages)
// Use the official CDN worker so we don't rely on a local file path.
if (window.pdfjsLib) {
  window.pdfjsLib.GlobalWorkerOptions.workerSrc = "https://unpkg.com/pdfjs-dist@2.16.105/build/pdf.worker.min.js";
  window.pdfjsLib.disableWorker = false;
}

// Theme toggle: remembers preference in localStorage
(() => {
  const key = "slideshare_theme";
  const root = document.documentElement;
  const btn = document.getElementById("theme-toggle");
  const apply = (mode) => {
    if (mode === "dark") root.setAttribute("data-theme", "dark");
    else root.removeAttribute("data-theme");
    if (btn) btn.textContent = mode === "dark" ? "Light mode" : "Dark mode";
  };
  const saved = localStorage.getItem(key);
  if (saved === "dark" || saved === "light") apply(saved);
  if (btn) {
    btn.addEventListener("click", () => {
      const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      localStorage.setItem(key, next);
      apply(next);
    });
  }
})();

// Placeholder hook for future interactions

// Horizontal scroll rows (presentation carousels)
document.addEventListener('DOMContentLoaded', function(){
  function initScrollRow(row){
    const track = row.querySelector('[data-scroll-track]');
    const prev = row.querySelector('[data-scroll-prev]');
    const next = row.querySelector('[data-scroll-next]');
    if (!track || !prev || !next) return;

    function update(){
      const max = track.scrollWidth - track.clientWidth;
      const hasOverflow = max > 4;
      prev.style.display = hasOverflow ? 'flex' : 'none';
      next.style.display = hasOverflow ? 'flex' : 'none';
      prev.disabled = track.scrollLeft <= 2;
      next.disabled = track.scrollLeft >= max - 2;
    }

    prev.addEventListener('click', function(){
      track.scrollBy({ left: -track.clientWidth * 0.8, behavior: 'smooth' });
    });
    next.addEventListener('click', function(){
      track.scrollBy({ left: track.clientWidth * 0.8, behavior: 'smooth' });
    });
    track.addEventListener('scroll', function(){
      requestAnimationFrame(update);
    });
    setTimeout(update, 60);
    window.addEventListener('resize', update);
  }

  document.querySelectorAll('[data-scroll-row]').forEach(initScrollRow);
});
document.addEventListener("submit", function () {
  // keep default behavior; extend as needed
});

// (promo slider removed) static cards handled by CSS

// Preview modal + notifications: initialize UI helpers on DOM ready
document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("preview-modal");

  // Preview modal: fetch PDF, render first page to canvas, fallback to object
  if (modal) {
    const canvas = document.getElementById("preview-canvas");
    const objectEl = document.getElementById("preview-object");
    const placeholder = document.getElementById("preview-placeholder");
    const titleEl = document.getElementById("preview-title");
    const statusEl = document.getElementById("preview-status");
    const messageEl = document.getElementById("preview-message");
    const openEl = document.getElementById("preview-open");
    const downloadEl = document.getElementById("preview-download");
    const thumbsEl = document.getElementById("preview-thumbs");
    const slideEl = document.getElementById("preview-slide");
    const prevBtn = document.getElementById("preview-prev");
    const nextBtn = document.getElementById("preview-next");
    const previewMain = modal.querySelector(".preview-main");

    let currentThumbs = [];
    let activeIndex = 0;

    const closeModal = () => {
      modal.classList.add("hidden");
      if (objectEl) objectEl.removeAttribute("data");
      if (canvas) {
        const ctx = canvas.getContext("2d");
        if (ctx) ctx.clearRect(0, 0, canvas.width || 0, canvas.height || 0);
        canvas.width = 0;
        canvas.height = 0;
        canvas.style.display = "none";
      }
      if (slideEl) {
        slideEl.src = "";
        slideEl.style.display = "none";
      }
      if (thumbsEl) thumbsEl.innerHTML = "";
      currentThumbs = [];
      activeIndex = 0;
    };

    modal.addEventListener("click", (e) => {
      if (e.target && e.target.hasAttribute("data-preview-close")) closeModal();
    });

    document.querySelectorAll(".preview-close").forEach((btn) => {
      btn.addEventListener("click", closeModal);
    });

    const renderPdfFirstPage = async (bytes) => {
      if (!canvas || !window.pdfjsLib) return false;
      try {
        const pdf = await window.pdfjsLib.getDocument({ data: bytes }).promise;
        const page = await pdf.getPage(1);
        const viewport = page.getViewport({ scale: 1 });
        const boxWidth = canvas.parentElement ? canvas.parentElement.clientWidth || viewport.width : viewport.width;
        const scale = Math.min(boxWidth / viewport.width, 1.6);
        const scaled = page.getViewport({ scale: scale > 0 ? scale : 1 });
        const ctx = canvas.getContext("2d");
        canvas.width = scaled.width;
        canvas.height = scaled.height;
        await page.render({ canvasContext: ctx, viewport: scaled }).promise;
        canvas.style.display = "block";
        if (placeholder) placeholder.style.display = "none";
        if (statusEl) statusEl.textContent = `Page 1 of ${pdf.numPages || 1}`;
        return true;
      } catch (e) {
        return false;
      }
    };

    const showObject = () => {
      if (objectEl) {
        objectEl.style.display = "block";
        if (placeholder) placeholder.style.display = "none";
      }
    };

    const setActiveSlide = (index) => {
      if (!currentThumbs.length || !slideEl) return;
      const clamped = Math.max(0, Math.min(index, currentThumbs.length - 1));
      activeIndex = clamped;
      slideEl.src = currentThumbs[clamped];
      slideEl.style.display = "block";
      if (thumbsEl) {
        thumbsEl.querySelectorAll("img").forEach((img, i) => {
          img.classList.toggle("active", i === clamped);
        });
      }
      if (statusEl) statusEl.textContent = `Slide ${clamped + 1} of ${currentThumbs.length}`;
      if (prevBtn) prevBtn.disabled = clamped === 0;
      if (nextBtn) nextBtn.disabled = clamped >= currentThumbs.length - 1;
    };

    const renderThumbnails = (thumbs) => {
      if (!thumbsEl || !slideEl) return false;
      currentThumbs = Array.isArray(thumbs) ? thumbs : [];
      thumbsEl.innerHTML = "";
      if (!currentThumbs.length) return false;
      currentThumbs.forEach((url, i) => {
        const img = document.createElement("img");
        img.src = url;
        img.alt = `Slide ${i + 1}`;
        img.loading = "lazy";
        img.addEventListener("click", () => setActiveSlide(i));
        thumbsEl.appendChild(img);
      });
      setActiveSlide(0);
      return true;
    };

    const handlePreview = async (id, titleHint = "") => {
      try {
        messageEl.classList.add("hidden");
        if (placeholder) placeholder.style.display = "block";
        if (objectEl) objectEl.style.display = "none";
        if (canvas) canvas.style.display = "none";
        if (slideEl) slideEl.style.display = "none";
        if (thumbsEl) thumbsEl.innerHTML = "";
        statusEl.textContent = "Loading preview…";
        titleEl.textContent = titleHint || "Preview";
        openEl.href = "#";
        downloadEl.href = "#";
        if (downloadEl) downloadEl.style.display = "none";

        const [thumbRes, previewRes] = await Promise.allSettled([
          fetch(`/presentations/${id}/thumbnails`),
          fetch(`/api/presentations/${id}/preview`)
        ]);

        let previewData = null;
        if (previewRes.status === "fulfilled" && previewRes.value.ok) {
          previewData = await previewRes.value.json();
        }

        let thumbsData = null;
        if (thumbRes.status === "fulfilled" && thumbRes.value.ok) {
          thumbsData = await thumbRes.value.json();
        }

        if (previewData) {
          titleEl.textContent = previewData.title || titleHint || "Preview";
          statusEl.textContent = previewData.conversion_status || "";
          if (previewData.original_url) {
            downloadEl.href = previewData.original_url;
            downloadEl.style.display = "inline-flex";
          }
          if (previewData.viewer_url) {
            const pageHint = previewData.viewer_url.includes("#") ? "" : "#page=1&view=FitH&toolbar=0&navpanes=0";
            openEl.href = `${previewData.viewer_url}${pageHint}`;
          } else {
            openEl.href = `/presentations/${id}`;
          }
        } else {
          openEl.href = `/presentations/${id}`;
        }

        const thumbs = thumbsData && Array.isArray(thumbsData.thumbnails) ? thumbsData.thumbnails : [];
        if (thumbs.length) {
          const rendered = renderThumbnails(thumbs);
          if (rendered) {
            if (placeholder) placeholder.style.display = "none";
            if (objectEl) objectEl.style.display = "none";
            if (canvas) canvas.style.display = "none";
            modal.classList.remove("hidden");
            return;
          }
        }
        if (previewData && previewData.viewer_url) {
          const pageHint = previewData.viewer_url.includes("#") ? "" : "#page=1&view=FitH&toolbar=0&navpanes=0";
          const clean = previewData.viewer_url.split("#")[0];
          try {
            const resPdf = await fetch(clean);
            if (!resPdf.ok) throw new Error("fetch failed");
            const buf = await resPdf.arrayBuffer();
            const blobUrl = URL.createObjectURL(new Blob([buf], { type: "application/pdf" }));
            pdfBlobUrls.push(blobUrl);

            if (objectEl) {
              objectEl.setAttribute("data", `${blobUrl}${pageHint}`);
              objectEl.style.display = "block";
            }
            openEl.href = `${blobUrl}${pageHint}`;

            const rendered = await renderPdfFirstPage(buf);
            if (rendered && objectEl) objectEl.style.display = "none";
            statusEl.textContent = rendered ? statusEl.textContent : "";
          } catch (err) {
            if (objectEl) {
              objectEl.setAttribute("data", `${previewData.viewer_url}${pageHint}`);
              showObject();
            }
            openEl.href = `${previewData.viewer_url}${pageHint}`;
          }
        } else {
          if (canvas) canvas.style.display = "none";
          if (objectEl) objectEl.style.display = "none";
          messageEl.textContent =
            previewData && previewData.conversion_status === "unsupported"
              ? "This file type cannot be previewed."
              : "Preview will be ready after conversion.";
          messageEl.classList.remove("hidden");
        }

        modal.classList.remove("hidden");
      } catch (err) {
        if (canvas) canvas.style.display = "none";
        if (objectEl) objectEl.style.display = "none";
        if (slideEl) slideEl.style.display = "none";
        messageEl.textContent = "Preview unavailable.";
        messageEl.classList.remove("hidden");
        modal.classList.remove("hidden");
      }
    };

    document.querySelectorAll(".preview-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        const id = btn.getAttribute("data-preview-id");
        const titleHint = btn.closest("[data-title]")?.getAttribute("data-title") || "";
        if (id) handlePreview(id, titleHint);
      });
    });

    prevBtn?.addEventListener("click", () => setActiveSlide(activeIndex - 1));
    nextBtn?.addEventListener("click", () => setActiveSlide(activeIndex + 1));

    if (previewMain) {
      let touchStartX = 0;
      previewMain.addEventListener("touchstart", (e) => {
        touchStartX = e.touches[0]?.clientX || 0;
      });
      previewMain.addEventListener("touchend", (e) => {
        if (!currentThumbs.length) return;
        const endX = e.changedTouches[0]?.clientX || 0;
        const delta = endX - touchStartX;
        if (Math.abs(delta) < 40) return;
        if (delta < 0) setActiveSlide(activeIndex + 1);
        else setActiveSlide(activeIndex - 1);
      });
    }
  }

  // Notifications: fetch and show badge + panel (should run on all pages)
  async function fetchNotifications(){
    try{
      const res = await fetch('/api/notifications');
      if (!res.ok) return [];
      const data = await res.json();
      return data;
    }catch(e){ return []; }
  }

  async function refreshNotifBadge(){
    const list = await fetchNotifications();
    const unread = list.filter(n=>!n.read).length;
    const badge = document.getElementById('notif-badge');
    if (!badge) return;
    if (unread > 0){ badge.style.display = 'inline-block'; badge.textContent = String(unread); }
    else { badge.style.display = 'none'; }
  }

  // Prefer direct listener, but also add delegated fallback in case element isn't present yet
  const openNotifPanel = async (ev)=>{
    try{ ev && ev.preventDefault(); }catch(e){}
    const panel = document.getElementById('notif-panel');
    if (!panel) return;
    panel.style.display = (panel.style.display === 'block') ? 'none' : 'block';
    // load list with optional filter from select
    const filterSel = document.getElementById('notif-filter');
    const filter = filterSel ? (filterSel.value || '') : '';
    const qs = filter ? ('?filter=' + encodeURIComponent(filter)) : '';
    const list = await (await fetch('/api/notifications' + qs)).json();
    const container = document.getElementById('notif-list');
    if (!container) return;
    container.innerHTML = '';
    if (!list.length){ container.innerHTML = '<div class="muted" style="padding:8px">No notifications</div>'; }
    list.forEach(n=>{
      const item = document.createElement('div');
      item.className = 'notif-item';
      item.style.padding = '10px';
      item.style.borderRadius = '10px';
      item.style.display = 'flex';
      item.style.alignItems = 'center';
      item.style.gap = '8px';
      item.style.background = n.read ? 'transparent' : 'linear-gradient(120deg,var(--brand-1),var(--brand-2))';
      item.style.color = n.read ? 'inherit' : '#07102a';

      // optional avatar
      if (n.actor_username){
        const avatarWrap = document.createElement('a');
        avatarWrap.href = '/users/' + n.actor_username;
        avatarWrap.style.display = 'inline-flex';
        avatarWrap.style.alignItems = 'center';
        avatarWrap.style.justifyContent = 'center';
        avatarWrap.style.width = '32px';
        avatarWrap.style.height = '32px';
        avatarWrap.style.borderRadius = '999px';
        avatarWrap.style.overflow = 'hidden';
        avatarWrap.style.background = 'linear-gradient(135deg,#eef1ff,#ffd9a8)';
        if (n.actor_avatar){
          const img = document.createElement('img');
          img.src = '/download/' + n.actor_avatar + '?inline=1';
          img.alt = n.actor_username + ' avatar';
          img.style.width = '100%';
          img.style.height = '100%';
          img.style.objectFit = 'cover';
          avatarWrap.appendChild(img);
        } else {
          avatarWrap.textContent = (n.actor_username[0] || 'U').toUpperCase();
          avatarWrap.style.fontWeight = '700';
          avatarWrap.style.color = '#1f1f3f';
        }
        item.appendChild(avatarWrap);
      }

      const body = document.createElement('div');
      body.style.flex = '1';
      const txt = document.createElement('div');
      // build human-friendly message using resolved actor_username and target_title when available
      let actor = n.actor_username || (n.actor_id ? ('User ' + n.actor_id) : 'Someone');
      let content = '';
      if (n.verb === 'follow') {
        content = `${actor} followed you`;
      } else if (n.verb === 'like') {
        if (n.target_title) content = `${actor} liked "${n.target_title}"`;
        else content = `${actor} liked your presentation`;
      } else if (n.verb === 'save') {
        if (n.target_title) content = `${actor} saved "${n.target_title}"`;
        else content = `${actor} saved your presentation`;
      } else {
        content = n.actor_username ? `${actor} ${n.verb}` : n.verb;
      }
      txt.textContent = content;
      const time = document.createElement('div');
      time.className = 'muted';
      time.style.fontSize = '12px';
      time.style.marginTop = '6px';
      time.textContent = new Date(n.created_at).toLocaleString();
      body.appendChild(txt);
      body.appendChild(time);
      item.appendChild(body);
      item.addEventListener('click', async (ev)=>{
        // If the user clicked directly on a profile link inside the notification,
        // let the browser follow it without overriding the navigation.
        const target = ev.target;
        if (target && target.tagName === 'A' && target.href) {
          return;
        }
        // mark read
        try{ await fetch('/api/notifications/' + n.id + '/read', { method: 'POST' }); }catch(e){}
        // optionally navigate to target
        if (n.target_type === 'presentation' && n.target_id){ location.href = '/presentations/' + n.target_id; }
        else if (n.target_type === 'user' && n.actor_username){ location.href = '/users/' + n.actor_username; }
        else { refreshNotifBadge(); }
      });
      container.appendChild(item);
    });
    refreshNotifBadge();
  };
  // When the bell is clicked, take user to the full notifications page.
  const notifLauncher = document.getElementById('notif-launcher');
  if (notifLauncher) {
    notifLauncher.addEventListener('click', (ev) => {
      ev.preventDefault();
      // go straight to the dedicated notifications page
      window.location.href = '/notifications';
    });
  }
  document.addEventListener('click', function(ev){
    if (ev.defaultPrevented) return;
    const btn = ev.target && ev.target.closest && ev.target.closest('#notif-launcher');
    if (btn) openNotifPanel(ev);
  }, false);

  document.getElementById('notif-close')?.addEventListener('click', ()=>{
    const panel = document.getElementById('notif-panel');
    if (panel) panel.style.display = 'none';
  });
  document.getElementById('notif-clear-all')?.addEventListener('click', async ()=>{
    try{
      const res = await fetch('/api/notifications/clear', { method: 'POST' });
      if(res.ok){
        const container = document.getElementById('notif-list');
        if (container) container.innerHTML = '<div class="muted" style="padding:8px">No notifications</div>';
        const badge = document.getElementById('notif-badge');
        if (badge){ badge.style.display = 'none'; }
      }
    }catch(e){}
  });
  document.getElementById('notif-mark-all')?.addEventListener('click', async ()=>{
    try{
      await fetch('/api/notifications/clear', { method: 'POST' });
    }catch(e){}
    refreshNotifBadge();
    const container = document.getElementById('notif-list');
    if (container) container.innerHTML = '<div class="muted" style="padding:8px">No notifications</div>';
  });

  const notifFilter = document.getElementById('notif-filter');
  if (notifFilter){
    notifFilter.addEventListener('change', function(){
      // reload panel with new filter
      openNotifPanel();
    });
  }

  // initial badge refresh
  setTimeout(refreshNotifBadge, 800);
});

// Download menu + collection modal + drag/drop upload
document.addEventListener("DOMContentLoaded", () => {
  // Language switcher: follow device language by default and allow manual selection
  (function(){
    const switcher = document.getElementById('lang-switcher');
    if (!switcher) return;
    const flagEl = document.getElementById('lang-flag');
    const codeEl = document.getElementById('lang-code');
    const dropdown = document.getElementById('lang-dropdown');
    const btn = switcher.querySelector('.lang-button');

    const getCookie = (name) => {
      const m = document.cookie.match('(?:^|; )' + name.replace(/([.$?*|{}\[\]\\\/\+^])/g, '\\$1') + '=([^;]*)');
      return m ? decodeURIComponent(m[1]) : '';
    };

    const supported = ['en','es','fr','pt','de','ar','hi','zh','ja'];
    const detectLang = () => {
      const raw = (navigator.language || 'en').toLowerCase();
      const base = raw.split('-')[0];
      return supported.includes(base) ? base : 'en';
    };
    const detectEnFlag = () => {
      const raw = (navigator.language || '').toLowerCase();
      return raw.startsWith('en-gb') || raw.startsWith('en-ie') ? '1f1ec-1f1e7' : '1f1fa-1f1f8';
    };
    const getFlagSvgForLang = (lang) => {
      const code = {
        en: detectEnFlag(),
        es: '1f1ea-1f1f8',
        fr: '1f1eb-1f1f7',
        pt: '1f1e7-1f1f7',
        de: '1f1e9-1f1ea',
        ar: '1f1f8-1f1e6',
        hi: '1f1ee-1f1f3',
        zh: '1f1e8-1f1f3',
        ja: '1f1ef-1f1f5',
      }[lang] || '1f310';
      return `https://twemoji.maxcdn.com/v/latest/svg/${code}.svg`;
    };

    const setIndicator = (lang) => {
      if (flagEl && flagEl.tagName && flagEl.tagName.toLowerCase() === 'img') {
        flagEl.setAttribute('src', getFlagSvgForLang(lang));
      }
      if (codeEl) codeEl.textContent = (lang || 'en').toUpperCase();
    };

    const setLang = (lang) => {
      const l = supported.includes(lang) ? lang : 'en';
      document.cookie = `ui_lang=${encodeURIComponent(l)}; path=/; max-age=${60 * 60 * 24 * 365}`;
      setIndicator(l);
      try { window.location.reload(); } catch (e) {}
    };

    // default to device language if no cookie set
    const existing = getCookie('ui_lang');
    if (!existing) {
      const autoLang = detectLang();
      setIndicator(autoLang);
      document.cookie = `ui_lang=${encodeURIComponent(autoLang)}; path=/; max-age=${60 * 60 * 24 * 365}`;
      try { window.location.reload(); } catch (e) {}
      return;
    }

    setIndicator(existing);

    if (btn && dropdown) {
      btn.addEventListener('click', (e) => {
        e.preventDefault();
        const open = switcher.classList.toggle('is-open');
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      document.addEventListener('click', (e) => {
        if (!switcher.contains(e.target)) {
          switcher.classList.remove('is-open');
          btn.setAttribute('aria-expanded', 'false');
        }
      });
    }

    if (dropdown) {
      dropdown.addEventListener('click', (e) => {
        const opt = e.target.closest('[data-lang]');
        if (!opt) return;
        e.preventDefault();
        const lang = opt.getAttribute('data-lang') || 'en';
        setLang(lang);
      });
    }
  })();

  // download dropdown
  document.querySelectorAll('[data-download-menu]').forEach((menu) => {
    const btn = menu.querySelector('button');
    if (!btn) return;
    btn.addEventListener('click', (e) => {
      e.preventDefault();
      menu.classList.toggle('open');
    });
  });
  document.addEventListener('click', (e) => {
    document.querySelectorAll('[data-download-menu].open').forEach((m) => {
      if (!m.contains(e.target)) m.classList.remove('open');
    });
  });

  // collections modal
  const modal = document.getElementById('collection-modal');
  const openBtn = document.getElementById('save-to-collection');
  const closeBtn = modal ? modal.querySelector('[data-collection-close]') : null;
  const listEl = document.getElementById('collection-list');
  const createBtn = document.getElementById('collection-create-btn');
  const nameInput = document.getElementById('collection-name');
  const presentationId = openBtn ? openBtn.getAttribute('data-presentation-id') : null;

  function openModal(){
    if (!modal) return;
    modal.classList.remove('hidden');
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
  }
  function closeModal(){
    if (!modal) return;
    modal.classList.remove('is-open');
    modal.classList.add('hidden');
    modal.setAttribute('aria-hidden', 'true');
  }
  if (openBtn) openBtn.addEventListener('click', (e) => { e.preventDefault(); openModal(); });
  if (closeBtn) closeBtn.addEventListener('click', (e) => { e.preventDefault(); closeModal(); });
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });

  async function addToCollection(collectionId){
    if (!presentationId) return;
    const res = await fetch(`/api/collections/${collectionId}/items`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ presentation_id: Number(presentationId) })
    });
    if (res.ok) { showToast('Saved to folder', 'success'); closeModal(); }
    else { showToast('Failed to save', 'error'); }
  }

  if (listEl) {
    listEl.addEventListener('click', (e) => {
      const btn = e.target.closest('.collection-item');
      if (!btn) return;
      const id = btn.getAttribute('data-collection-id');
      if (id) addToCollection(id);
    });
  }

  if (createBtn && nameInput) {
    createBtn.addEventListener('click', async (e) => {
      e.preventDefault();
      const name = nameInput.value.trim();
      if (!name) return;
      const res = await fetch('/api/collections', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name })
      });
      if (!res.ok) { showToast('Failed to create folder', 'error'); return; }
      const data = await res.json();
      const btn = document.createElement('button');
      btn.className = 'collection-item';
      btn.setAttribute('data-collection-id', data.id);
      btn.textContent = data.name;
      listEl.appendChild(btn);
      nameInput.value = '';
      addToCollection(data.id);
    });
  }

  // drag & drop upload validation
  const fileInput = document.getElementById('file-input');
  const dropzone = document.querySelector('.dropzone');
  const fileName = document.getElementById('file-name');
  const allowed = ['.pdf', '.ppt', '.pptx', '.pptm', '.mp4', '.mov', '.m4v', '.webm'];
  if (dropzone && fileInput) {
    const prevent = (e)=>{ e.preventDefault(); e.stopPropagation(); };
    ['dragenter','dragover','dragleave','drop'].forEach(evt => dropzone.addEventListener(evt, prevent));
    dropzone.addEventListener('drop', (e) => {
      const f = e.dataTransfer.files && e.dataTransfer.files[0];
      if (!f) return;
      const lower = f.name.toLowerCase();
      const ok = allowed.some(ext => lower.endsWith(ext));
      if (!ok) { showToast('Unsupported file type', 'error'); return; }
      fileInput.files = e.dataTransfer.files;
      if (fileName) fileName.textContent = f.name;
    });
    fileInput.addEventListener('change', () => {
      const f = fileInput.files && fileInput.files[0];
      if (f && fileName) fileName.textContent = f.name;
    });
  }
});

// (role selector modal removed; role is chosen on a dedicated page)

// Inline PDF thumbnails on cards: fetch, render first page via pdf.js, fallback to object blob
document.addEventListener("DOMContentLoaded", () => {
  const frames = document.querySelectorAll(".thumb-frame[data-pdf]");
  if (!frames.length) return;

  frames.forEach((wrap) => {
    const src = wrap.getAttribute("data-pdf") || "";
    if (!src) return;
    const canvas = wrap.querySelector(".thumb-canvas");
    const objectEl = wrap.querySelector(".thumb-object");
    const clean = src.split("#")[0];

    fetch(clean)
      .then((res) => {
        if (!res.ok) throw new Error("fetch failed");
        return res.arrayBuffer();
      })
      .then(async (buf) => {
        const blobUrl = URL.createObjectURL(new Blob([buf], { type: "application/pdf" }));
        pdfBlobUrls.push(blobUrl);
        if (objectEl) {
          objectEl.setAttribute("data", `${blobUrl}#toolbar=0&navpanes=0&view=FitH`);
          objectEl.style.display = "block";
        }

        if (canvas && window.pdfjsLib) {
          try {
            const pdf = await window.pdfjsLib.getDocument({ data: buf }).promise;
            const page = await pdf.getPage(1);
            const viewport = page.getViewport({ scale: 1 });
            const boxWidth = wrap.clientWidth || viewport.width;
            // Upscale a bit for sharper rendering when downsampled into the thumb box
            const scale = Math.min((boxWidth / viewport.width) * 1.6, 2.6);
            const scaled = page.getViewport({ scale: scale > 0 ? scale : 1 });
            const ctx = canvas.getContext("2d");
            canvas.width = scaled.width;
            canvas.height = scaled.height;
            await page.render({ canvasContext: ctx, viewport: scaled }).promise;
            canvas.style.display = "block";
            if (objectEl) objectEl.style.display = "none";
          } catch (err) {
            if (objectEl) objectEl.style.display = "block";
          }
        }
      })
      .catch(() => {
        if (objectEl) objectEl.style.display = "block";
      });
  });
});

// Cleanup all blob URLs we created
window.addEventListener("beforeunload", () => {
  pdfBlobUrls.forEach((url) => URL.revokeObjectURL(url));
});

// Bookmark toggle: fetch user's bookmarks and wire up bookmark buttons
document.addEventListener("DOMContentLoaded", () => {
  const markBookmarked = (ids) => {
    // mark both listing card bookmark buttons and the detail-page bookmark button
    document.querySelectorAll('.card__bookmark[data-id], .bookmark-btn[data-id]').forEach(el => {
      const id = el.getAttribute('data-id');
      if (!id) return;
      if (ids.includes(Number(id)) || ids.includes(id)) el.classList.add('bookmarked');
      else el.classList.remove('bookmarked');
    });
  };

  const fetchBookmarks = async () => {
    try {
      const res = await fetch('/api/bookmarks');
      if (!res.ok) return [];
      const data = await res.json();
      return data.bookmarks || [];
    } catch (e) { return []; }
  };

  // initial mark
  (async () => {
    const ids = await fetchBookmarks();
    markBookmarked(ids || []);
  })();

  // click handler (delegation)
  document.body.addEventListener('click', async (e) => {
    const bookmarkCardBtn = e.target.closest('.card__bookmark');
    const bookmarkDetailBtn = e.target.closest('.bookmark-btn');
    const likeBtn = e.target.closest('.like-btn');
    const btn = bookmarkCardBtn || bookmarkDetailBtn;
    if (!btn) return;
    e.preventDefault();
    const id = btn.getAttribute('data-id');
    if (!id) return;
    // optimistic UI
    const was = btn.classList.contains('bookmarked');
    btn.classList.toggle('bookmarked');
    try {
      const res = await fetch(`/api/presentations/${id}/bookmark`, { method: 'POST', headers: { 'Accept': 'application/json' } });
      if (!res.ok) {
        btn.classList.toggle('bookmarked', was);
        const err = await res.json().catch(()=>({detail:'error'}));
        showToast(err.detail || 'Bookmark failed', 'error');
        return;
      }
      const data = await res.json();
      btn.classList.toggle('bookmarked', !!data.bookmarked);
      // counts removed from UI; no badge updates
      showToast(data.bookmarked ? 'Saved' : 'Removed', data.bookmarked ? 'success' : 'info');
    } catch (err) {
      btn.classList.toggle('bookmarked', was);
      showToast('Network error', 'error');
    }
  });

    // Ensure "by <username>" doesn't wrap across lines in listings
    document.addEventListener('DOMContentLoaded', function () {
      try {
        const selectors = ['.muted', '.presentation-short-desc', '.card__meta', '.card__footer'];
        selectors.forEach(sel => {
          document.querySelectorAll(sel).forEach(el => {
            Array.from(el.childNodes).forEach(node => {
              if (node.nodeType !== Node.TEXT_NODE) return;
              const txt = node.nodeValue;
              if (!txt) return;
              if (/\bby\s*$/.test(txt) && node.nextSibling && node.nextSibling.nodeType === 1 && node.nextSibling.tagName.toLowerCase() === 'a') {
                node.nodeValue = txt.replace(/\bby\s*$/,'by\u00A0');
              }
            });
          });
        });
      } catch (e) { /* ignore */ }
    });
});

// Like button handling (delegated) — separate so it doesn't interfere with bookmarks
document.addEventListener('click', async (e) => {
  const like = e.target.closest('.like-btn');
  if (!like) return;
  e.preventDefault();
  const id = like.getAttribute('data-id');
  if (!id) return;
  const was = like.classList.contains('liked');
  like.classList.toggle('liked');
  try {
    const res = await fetch(`/presentations/${id}/like`, { method: 'POST', headers: { 'Accept': 'application/json' } });
    if (!res.ok) {
      like.classList.toggle('liked', was);
      const err = await res.json().catch(()=>({detail:'error'}));
      showToast(err.detail || 'Like failed', 'error');
      return;
    }
    const data = await res.json();
    like.classList.toggle('liked', !!data.liked);
    // update like count badge if present
    try {
      const lc = document.getElementById('like-count');
      if (lc) lc.textContent = String((data.count !== undefined) ? data.count : (was ? (Number(lc.textContent||0)-1) : (Number(lc.textContent||0)+1)));
    } catch (e) {}
    showToast(data.liked ? 'Liked' : 'Unliked', data.liked ? 'success' : 'info');
  } catch (err) {
    like.classList.toggle('liked', was);
    showToast('Network error', 'error');
  }
});

// Presentation AI panels: ensure they appear above overlays and get a brief highlight
document.addEventListener("DOMContentLoaded", () => {
  const mappings = [
    { btnId: 'ai-slide-btn', panelId: 'ai-slide-container' },
    { btnId: 'ai-summary-btn', panelId: 'ai-summary-container' },
    { btnId: 'ai-quiz-btn', panelId: 'ai-quiz-container' },
    { btnId: 'ai-flash-btn', panelId: 'ai-flash-container' },
  ];

  mappings.forEach(({ btnId, panelId }) => {
    const btn = document.getElementById(btnId);
    const panel = document.getElementById(panelId);
    if (!btn || !panel) return;

    btn.addEventListener('click', () => {
      try {
        panel.style.zIndex = 99999;
        panel.classList.add('ai-debug-highlight');
        // remove highlight after a short period to avoid permanent styling
        setTimeout(() => { panel.classList.remove('ai-debug-highlight'); }, 3000);
      } catch (e) { /* ignore */ }
    });
  });
});

// Profile dropdown toggle (global, class-based state)
document.addEventListener("DOMContentLoaded", () => {
  const triggers = document.querySelectorAll(".profile-trigger");
  if (!triggers.length) return;

  const closeAll = () => {
    document.querySelectorAll(".profile-menu").forEach((menu) => {
      menu.classList.remove("is-open");
      menu.hidden = false; // keep attribute off to rely on class
      menu.style.display = "none";
    });
    triggers.forEach((t) => t.setAttribute("aria-expanded", "false"));
  };

  triggers.forEach((trigger) => {
    const sibling = trigger.nextElementSibling;
    const menu = (sibling && sibling.classList && sibling.classList.contains("profile-menu"))
      ? sibling
      : document.querySelector(".profile-menu");
    if (!menu) return;

    const toggleMenu = (e) => {
      e.stopPropagation();
      const willOpen = !menu.classList.contains("is-open");
      closeAll();
      if (willOpen) {
        menu.classList.add("is-open");
        menu.style.display = "flex";
        trigger.setAttribute("aria-expanded", "true");
      }
    };

    trigger.addEventListener("click", toggleMenu);
  });

  document.addEventListener("click", (e) => {
    const anyMenuOpen = Array.from(document.querySelectorAll(".profile-menu")).some((m) => m.classList.contains("is-open"));
    if (!anyMenuOpen) return;
    const clickInside = Array.from(document.querySelectorAll(".profile-menu, .profile-trigger"))
      .some((el) => el.contains(e.target));
    if (!clickInside) closeAll();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAll();
  });
});

// Profile tabs: client-side switching with optional AJAX fallback
(function(){
  function setProfileTab(tabName, opts){
    const options = opts || {};
    const container = document.getElementById('profile-content');
    if (!container) return false;
    const sections = Array.from(container.querySelectorAll('[data-profile-tab]'));
    if (!sections.length) return false;

    sections.forEach((section) => {
      const isActive = section.getAttribute('data-profile-tab') === tabName;
      section.style.display = isActive ? '' : 'none';
    });

    document.querySelectorAll('.profile-tabs a').forEach(a=>a.classList.remove('active'));
    const activeLink = document.querySelector(`.profile-tabs a[href="#${tabName}"]`);
    if (activeLink) activeLink.classList.add('active');

    if (options.updateHash) {
      try { history.replaceState(null, '', `#${tabName}`); } catch (e) { /* ignore */ }
    }
    return true;
  }

  document.addEventListener('click', async (e) => {
    const tabLink = e.target.closest('.profile-tabs a');
    if (!tabLink) return;
    e.preventDefault();
    const tabHref = tabLink.getAttribute('href') || '#presentations';
    const tabName = tabHref.replace('#','');

    if (setProfileTab(tabName, { updateHash: true })) return;

    const header = document.querySelector('.profile-header[data-username]');
    if (!header) return;
    const username = header.getAttribute('data-username');
    const container = document.getElementById('profile-content');
    if (!container) return;

    // show a lightweight loading state
    const prev = container.innerHTML;
    container.innerHTML = '<div class="muted">Loading…</div>';

    try {
      // fetch the user's full profile page and extract #profile-content
      const res = await fetch(`/users/${encodeURIComponent(username)}?ajax=1`);
      if (!res.ok) throw new Error('failed');
      const text = await res.text();
      const doc = new DOMParser().parseFromString(text, 'text/html');
      const frag = doc.getElementById('profile-content');
      if (frag) {
        const requested = frag.querySelector('#' + tabName) || frag;
        container.innerHTML = requested.innerHTML || frag.innerHTML;
      } else {
        container.innerHTML = prev; // fallback
      }
    } catch (err) {
      container.innerHTML = '<div class="muted">Content not available.</div>';
    }
  });

  document.addEventListener('DOMContentLoaded', () => {
    const hash = (window.location.hash || '#presentations').replace('#','');
    setProfileTab(hash, { updateHash: false });
  });
})();

// Follow button: optimistic update
document.addEventListener('click', async (e) => {
  const btn = e.target.closest('.follow-btn');
  if (!btn) return;
  e.preventDefault();
  const username = btn.getAttribute('data-username');
  if (!username) return;
  const wasFollowing = btn.textContent.trim().toLowerCase().startsWith('un');
  // optimistic toggle
  btn.textContent = wasFollowing ? 'Follow' : 'Unfollow';
  try {
    const res = await fetch(`/follow/${encodeURIComponent(username)}`, { method: 'POST', headers: { 'Accept': 'application/json' } });
    if (!res.ok) {
      // revert
      btn.textContent = wasFollowing ? 'Unfollow' : 'Follow';
      return;
    }
    const data = await res.json();
    const countEl = document.getElementById('follower-count');
    if (countEl) {
      const cur = Number(countEl.textContent || 0);
      const next = data.following ? cur + 1 : Math.max(0, cur - 1);
      countEl.textContent = String(next);
    }
    btn.textContent = data.following ? 'Unfollow' : 'Follow';
    showToast(data.following ? 'Following' : 'Unfollowed', data.following ? 'success' : 'info');
  } catch (err) {
    // revert on error
    btn.textContent = wasFollowing ? 'Unfollow' : 'Follow';
    showToast('Network error', 'error');
  }
});

// Upload page helpers: file preview and premium AI actions
document.addEventListener("DOMContentLoaded", () => {
  const fileInput = document.getElementById("file-input");
  const frame = document.getElementById("file-preview-frame");
  const placeholder = document.getElementById("file-preview-placeholder");
  const nameEl = document.getElementById("file-name");
  const pageLabel = document.getElementById("preview-page-label");
  const canvas = document.getElementById("file-preview-canvas");
  const previewBox = document.getElementById("file-preview-box");
  let currentObjectUrl = null;

  const resetPreview = () => {
    if (currentObjectUrl) {
      URL.revokeObjectURL(currentObjectUrl);
      currentObjectUrl = null;
    }
    if (frame) {
      frame.removeAttribute("data");
      frame.style.display = "none";
    }
    if (canvas) {
      const ctx = canvas.getContext("2d");
      if (ctx) ctx.clearRect(0, 0, canvas.width || 0, canvas.height || 0);
      canvas.width = 0;
      canvas.height = 0;
      canvas.style.display = "none";
    }
    if (placeholder) placeholder.style.display = "flex";
    if (pageLabel) pageLabel.textContent = "1 of ?";
  };

  const renderPdfFirstPage = async (file) => {
    if (!canvas || !window.pdfjsLib) return null;
    try {
      const bytes = await file.arrayBuffer();
      const pdf = await window.pdfjsLib.getDocument({ data: bytes }).promise;
      const page = await pdf.getPage(1);
      const viewport = page.getViewport({ scale: 1 });
      const boxWidth = previewBox ? previewBox.clientWidth || viewport.width : viewport.width;
      const scale = Math.min(boxWidth / viewport.width, 1.6);
      const scaledViewport = page.getViewport({ scale: scale > 0 ? scale : 1 });
      const ctx = canvas.getContext("2d");
      if (!ctx) return null;
      canvas.width = scaledViewport.width;
      canvas.height = scaledViewport.height;
      canvas.style.display = "block";
      if (frame) frame.style.display = "none";
      if (placeholder) placeholder.style.display = "none";
      await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise;
      return pdf.numPages || 1;
    } catch (err) {
      console.error("pdf.js render failed", err);
      canvas.style.display = "none";
      return null;
    }
  };

  if (fileInput && frame && placeholder) {
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files && fileInput.files[0];
      resetPreview();
      if (!file) {
        if (nameEl) nameEl.textContent = "Drag & drop or browse your computer";
        return;
      }

      if (nameEl) nameEl.textContent = `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} MB`;
      const isPdf = file.type === "application/pdf" || (file.name || "").toLowerCase().endsWith(".pdf");
      if (!isPdf) {
        if (pageLabel) pageLabel.textContent = "Preview will be ready after conversion";
        return;
      }

      currentObjectUrl = URL.createObjectURL(file);
      // Always set the object data so the native PDF viewer can load immediately.
      frame.setAttribute("data", `${currentObjectUrl}#toolbar=1&navpanes=0&view=FitH`);
      frame.style.display = "block";
      if (placeholder) placeholder.style.display = "none";
      if (pageLabel) pageLabel.textContent = "Loading preview...";

      let pages = null;
      if (window.pdfjsLib && window.pdfjsLib.getDocument) {
        pages = await renderPdfFirstPage(file);
      }

      if (pages) {
        // Canvas render succeeded; hide the object to avoid double rendering
        if (frame) frame.style.display = "none";
        if (pageLabel) pageLabel.textContent = `1 of ${pages}`;
      } else {
        // Fallback: show the built-in viewer via object tag
        if (frame && currentObjectUrl) frame.style.display = "block";
        if (canvas) canvas.style.display = "none";
        if (placeholder) placeholder.style.display = "none";
        if (pageLabel) {
          // When pdf.js is unavailable, we still try to use the browser's
          // built-in PDF viewer instead of implying the preview is blocked.
          pageLabel.textContent = window.pdfjsLib ? "Preview unavailable" : "Using basic browser preview";
        }
      }
    });
  }

  // AI actions (premium)
  const aiButtons = document.querySelectorAll("[data-ai-mode]");
  const aiInput = document.getElementById("ai-input");
  const aiOutput = document.getElementById("ai-output");
  if (aiButtons && aiButtons.length && aiInput && aiOutput) {
    aiButtons.forEach((btn) => {
      btn.addEventListener("click", async () => {
        // If this AI button is intended to open the chat UI, trigger the
        // global chat launcher so chat.js shows the modal (keeps logic
        // centralized in chat.js). Use either data-ai-mode="chat" or
        // data-ai-open-chat="1" on the button.
        if (btn.getAttribute("data-ai-open-chat") === "1" || btn.getAttribute("data-ai-mode") === "chat") {
          const launch = document.querySelector('#chat-launcher');
          if (launch) { launch.click(); }
          return;
        }

        const mode = btn.getAttribute("data-ai-mode") || "rewrite";
        const content = aiInput.value.trim();
        if (!content) {
          aiOutput.textContent = "Add some text first.";
          return;
        }
        aiOutput.textContent = "Working...";
        try {
          const res = await fetch("/api/ai/rewrite", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content, mode }),
          });
          if (!res.ok) {
            const msg = res.status === 403 ? "Premium feature only." : "Request failed.";
            aiOutput.textContent = msg;
            return;
          }
          const data = await res.json();
          aiOutput.textContent = data.result || "";
          // Optionally drop into description if empty
          const desc = document.getElementById("desc-input");
          if (desc && (!desc.value || desc.value.trim().length < 4)) {
            desc.value = data.result || "";
          }
        } catch (e) {
          aiOutput.textContent = "Something went wrong.";
        }
      });
    });
  }
});

// Follow/unfollow AJAX handling and Contact modal handling
document.addEventListener("DOMContentLoaded", () => {
  // delegate submit on follow forms (non-blocking UI: show toasts instead of disabling)
  document.addEventListener("submit", async (e) => {
    const form = e.target.closest && e.target.closest('.follow-form') || (e.target.classList && e.target.classList.contains('follow-form') ? e.target : null);
    if (!form) return;
    e.preventDefault();
    const action = form.getAttribute('action');
    const ownerId = form.getAttribute('data-owner-id');
    const btn = form.querySelector('.btn-follow');
    try {
      showToast('Working…', 'info', 2000);
      const res = await fetch(action, { method: 'POST', credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } });
      if (!res.ok) throw new Error('Request failed');
      const data = await res.json();
      const followersEl = document.querySelector('.followers-count[data-owner-id="' + ownerId + '"]');
      if (data.following === true) {
        form.setAttribute('action', `/users/${ownerId}/unfollow`);
        if (btn) btn.textContent = 'Unsubscribe';
        showToast('Subscribed', 'success');
      } else if (data.following === false) {
        form.setAttribute('action', `/users/${ownerId}/follow`);
        if (btn) btn.textContent = 'Subscribe';
        showToast('Unsubscribed', 'success');
      }
      if (typeof data.followers_count !== 'undefined' && followersEl) {
        followersEl.textContent = data.followers_count + ' followers';
      }
    } catch (err) {
      console.error('Follow action failed', err);
      showToast('Action failed', 'error');
      // fallback: submit normally
      try { form.submit(); } catch (e) {}
    }
  });

  // Contact modal handling
  document.addEventListener('click', (ev) => {
    const btn = ev.target.closest && ev.target.closest('.btn-contact');
    if (!btn) return;

    const ownerId = btn.getAttribute('data-user-id');
    const ownerEmail = btn.getAttribute('data-email') || '';
    const ownerName = btn.getAttribute('data-username') || '';
    const current = document.getElementById('current-user');
    const isSignedIn = current && current.getAttribute('data-my-id');

    // If the user is not signed in, show the sign-in prompt and
    // prevent any other handlers (including chat.js) from running.
    if (!isSignedIn) {
      ev.preventDefault();
      ev.stopImmediatePropagation();
      const signin = document.getElementById('signin-modal');
      if (signin) {
        const mailto = document.getElementById('signin-mailto');
        if (mailto) mailto.setAttribute('href', ownerEmail ? ('mailto:' + encodeURIComponent(ownerEmail)) : '#');
        signin.style.display = 'block';
      }
      return;
    }

    // If this button is also a chat trigger (btn-message), let chat.js
    // handle opening the chat modal so we don't show two different
    // message boxes for the same action.
    if (btn.classList.contains('btn-message')) {
      ev.preventDefault();
      // Do NOT stop propagation here so the chat.js handler still runs.
      return;
    }

    // Signed-in and not a chat button: open the email-based contact modal.
    ev.preventDefault();
    const modal = document.getElementById('contact-modal');
    if (!modal) return;
    const nameInput = document.getElementById('contact-name');
    const emailInput = document.getElementById('contact-email');
    const ownerIdInput = document.getElementById('contact-owner-id');
    const msgInput = document.getElementById('contact-message');
    const avatarEl = document.getElementById('contact-owner-avatar');
    const titleEl = document.getElementById('contact-owner-title');
    const subtitleEl = document.getElementById('contact-owner-subtitle');
    const introOwner = document.getElementById('contact-owner-label');
    // prefill with current user info when available
    const cu = document.getElementById('current-user');
    const myId = cu ? cu.getAttribute('data-my-id') : null;
    // Try to prefill from server-rendered profile menu if present
    const profileEmail = document.querySelector('.profile-menu__email') ? document.querySelector('.profile-menu__email').textContent.trim() : '';
    const profileName = document.querySelector('.profile-menu__name') ? document.querySelector('.profile-menu__name').textContent.trim() : '';
    if (nameInput) nameInput.value = profileName || '';
    if (emailInput) emailInput.value = profileEmail || '';
    if (ownerIdInput) ownerIdInput.value = ownerId || '';
    if (msgInput) msgInput.value = '';

    // Fill header avatar + labels
    const displayName = ownerName || 'Owner';
    if (avatarEl) {
      const initials = displayName.trim().slice(0, 2).toUpperCase();
      avatarEl.textContent = initials || '??';
    }
    if (titleEl) titleEl.textContent = displayName ? `Message ${displayName}` : 'Contact owner';
    if (subtitleEl) subtitleEl.textContent = ownerEmail || '';
    if (introOwner) introOwner.textContent = displayName;
    modal.style.display = 'block';
  });

  // Inline slideshow / PDF-first rendering for cards
  (async function cardPreviews(){
    const cards = Array.from(document.querySelectorAll('.card[data-pid], .featured-card[data-pid]'));
    if (!cards.length) return;
    const intervals = [];

    for (const card of cards){
      const pid = card.getAttribute('data-pid');
      const thumbEl = card.querySelector('.card__thumb');
      if (!pid || !thumbEl) continue;

      try{
        const res = await fetch(`/presentations/${pid}/thumbnails`);
        const data = await res.json();
        const thumbs = data.thumbnails || [];
        if (thumbs && thumbs.length){
          // build inline slideshow
          thumbEl.innerHTML = '';
          const img = document.createElement('img');
          img.style.width = '100%';
          img.style.height = '100%';
          img.style.objectFit = 'cover';
          img.src = thumbs[0];
          thumbEl.appendChild(img);
          let idx = 0;
          const iv = setInterval(() => {
            idx = (idx + 1) % thumbs.length;
            img.src = thumbs[idx];
          }, 2800);
          intervals.push(iv);
          continue;
        }

        // No static thumbnails — try PDF viewer URL via preview API
        const pv = await fetch(`/api/presentations/${pid}/preview`);
        if (!pv.ok) continue;
        const pData = await pv.json();
        if (pData.viewer_url && window.pdfjsLib){
          // render first page into canvas inside thumbEl
          thumbEl.innerHTML = '';
          const canvas = document.createElement('canvas');
          canvas.style.width = '100%';
          canvas.style.height = '100%';
          canvas.style.display = 'block';
          thumbEl.appendChild(canvas);
          try{
            const loadingTask = window.pdfjsLib.getDocument({ url: pData.viewer_url.split('#')[0] });
            const pdf = await loadingTask.promise;
            const page = await pdf.getPage(1);
            const viewport = page.getViewport({ scale: 1 });
            const boxWidth = thumbEl.clientWidth || viewport.width;
            const scale = Math.min((boxWidth / viewport.width) * 1.4, 2.0);
            const scaled = page.getViewport({ scale: scale > 0 ? scale : 1 });
            const ctx = canvas.getContext('2d');
            canvas.width = scaled.width;
            canvas.height = scaled.height;
            await page.render({ canvasContext: ctx, viewport: scaled }).promise;
          }catch(e){
            // fallback: leave placeholder image if any
          }
        }
      }catch(e){
        // ignore per-card errors
      }
    }

    // cleanup intervals on unload
    window.addEventListener('beforeunload', () => { intervals.forEach(iv => clearInterval(iv)); });
  })();

  // Contact modal cancel/close
  document.getElementById('contact-close')?.addEventListener('click', () => { document.getElementById('contact-modal').style.display = 'none'; });
  document.getElementById('contact-cancel')?.addEventListener('click', () => { document.getElementById('contact-modal').style.display = 'none'; });

  // Submit contact form via AJAX
  const contactForm = document.getElementById('contact-form');
  if (contactForm) {
    contactForm.addEventListener('submit', async (e) => {
      e.preventDefault();
      const status = document.getElementById('contact-status');
      status.style.display = 'none';
      const msgInput = document.getElementById('contact-message');
      const messageText = msgInput ? msgInput.value.trim() : '';
      if (!messageText) {
        return;
      }
      const fd = new FormData(contactForm);
      try {
        showToast('Sending message…', 'info', 2500);
        const res = await fetch('/contact', { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } });
        if (!res.ok) throw new Error('Request failed');
        const data = await res.json();
        if (data && data.success) {
          // append the sent message into the mini chat thread so it
          // feels like a real conversation
          const thread = document.getElementById('contact-thread');
          if (thread) {
            const bubble = document.createElement('div');
            bubble.className = 'chat-msg me';
            const text = document.createElement('div');
            text.className = 'chat-msg__text';
            text.textContent = messageText;
            bubble.appendChild(text);
            thread.appendChild(bubble);
            thread.scrollTop = thread.scrollHeight;
          }
          if (msgInput) msgInput.value = '';
          status.textContent = 'Message sent to the owner.';
          status.style.display = 'block';
          showToast('Message sent', 'success');
        } else {
          status.textContent = 'Failed to send message.';
          status.style.display = 'block';
          showToast('Send failed', 'error');
        }
      } catch (err) {
        status.textContent = 'Failed to send message.';
        status.style.display = 'block';
        showToast('Send failed', 'error');
      }
    });
  }

});

// Simple carousel controls for elements with id 'featured-track'
document.addEventListener('DOMContentLoaded', function(){
  const track = document.getElementById('featured-track');
  const prev = document.getElementById('featured-prev');
  const next = document.getElementById('featured-next');
  if (!track) return;

  const cardWidth = (() => {
    const first = track.querySelector('.featured-card');
    if (!first) return 300;
    return Math.max(first.getBoundingClientRect().width, 260);
  })();

  prev?.addEventListener('click', function(e){
    e.preventDefault();
    track.scrollBy({ left: - (cardWidth + 12), behavior: 'smooth' });
  });
  next?.addEventListener('click', function(e){
    e.preventDefault();
    track.scrollBy({ left: (cardWidth + 12), behavior: 'smooth' });
  });

  // allow keyboard navigation when focused
  [prev, next].forEach(btn => btn && btn.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter' || ev.key === ' ') btn.click();
  }));
});

// Hamburger menu toggle for categories
document.addEventListener('DOMContentLoaded', function(){
  const btn = document.getElementById('hamburger');
  const menu = document.getElementById('hamburger-menu');
  if (!btn || !menu) return;
  btn.addEventListener('click', function(){
    const expanded = btn.getAttribute('aria-expanded') === 'true';
    btn.setAttribute('aria-expanded', String(!expanded));
    if (expanded) {
      menu.style.display = 'none';
      menu.setAttribute('aria-hidden', 'true');
    } else {
      menu.style.display = 'block';
      menu.setAttribute('aria-hidden', 'false');
    }
  });
  // close when clicking outside
  document.addEventListener('click', function(ev){
    if (!menu.contains(ev.target) && !btn.contains(ev.target)){
      menu.style.display = 'none';
      menu.setAttribute('aria-hidden','true');
      btn.setAttribute('aria-expanded','false');
    }
  });
});

// Hover preview: fetch up to 4 thumbnails and show a popup
document.addEventListener('DOMContentLoaded', function(){
  let previewPopup = null;
  function ensurePopup(){
    if (!previewPopup){
      previewPopup = document.createElement('div');
      previewPopup.className = 'preview-popup';
      previewPopup.style.position = 'absolute';
      previewPopup.style.display = 'none';
      previewPopup.style.zIndex = 1000;
      previewPopup.style.background = '#fff';
      previewPopup.style.border = '1px solid rgba(0,0,0,0.08)';
      previewPopup.style.borderRadius = '8px';
      previewPopup.style.boxShadow = '0 12px 30px rgba(16,24,40,0.12)';
      previewPopup.style.padding = '8px';
      previewPopup.style.maxWidth = '420px';
      previewPopup.style.pointerEvents = 'none';
      document.body.appendChild(previewPopup);
    }
  }

  async function showPreview(pid, rect){
    ensurePopup();
    previewPopup.innerHTML = '<div style="padding:6px;color:#666">Loading preview…</div>';
    previewPopup.style.display = 'block';
    previewPopup.style.left = (rect.right + 8) + 'px';
    previewPopup.style.top = (rect.top) + 'px';
    try{
      const res = await fetch(`/presentations/${pid}/thumbnails`);
      if (!res.ok) throw new Error('no thumbs');
      const data = await res.json();
      const urls = data.thumbnails || [];
      if (!urls.length){
        const status = (data && data.status) || '';
        if (status === 'queued') {
          previewPopup.innerHTML = '<div style="padding:8px;color:#666">Preview will be ready after conversion</div>';
        } else {
          previewPopup.innerHTML = '<div style="padding:8px;color:#666">Preview not available</div>';
        }
        return;
      }
      // show up to 4 thumbnails
      const items = urls.slice(0,4).map(u => `<img src="${u}" style="width:100px;height:72px;object-fit:cover;border-radius:6px;margin:4px;border:1px solid rgba(0,0,0,0.04)"/>`).join('');
      previewPopup.innerHTML = `<div style="display:flex;gap:6px;">${items}</div>`;
    }catch(e){
      previewPopup.innerHTML = '<div style="padding:8px;color:#666">Preview unavailable</div>';
    }
  }

  function hidePreview(){
    if (previewPopup) previewPopup.style.display = 'none';
  }

  document.querySelectorAll('.card[data-pid], .featured-card[data-pid]').forEach(el => {
    let timer = null;
    el.addEventListener('mouseenter', (ev) => {
      const pid = el.getAttribute('data-pid');
      if (!pid) return;
      const rect = el.getBoundingClientRect();
      timer = setTimeout(() => { showPreview(pid, rect); }, 350);
    });
    el.addEventListener('mouseleave', () => { if (timer) clearTimeout(timer); hidePreview(); });
  });

  // Hide when clicking anywhere
  document.addEventListener('scroll', hidePreview, true);
});

// Global share chooser: when `.btn-share` is clicked, prompt share destinations
(function(){
  function openWhatsApp(text){
    const url = 'https://wa.me/?text=' + encodeURIComponent(text);
    window.open(url, '_blank');
  }

  function openX(text, pageUrl){
    const url = 'https://x.com/intent/post?text=' + encodeURIComponent(text) + '&url=' + encodeURIComponent(pageUrl);
    window.open(url, '_blank');
  }

  function openLinkedIn(pageUrl){
    const url = 'https://www.linkedin.com/sharing/share-offsite/?url=' + encodeURIComponent(pageUrl);
    window.open(url, '_blank');
  }

  async function shareToInstagram(text, pageUrl){
    if (navigator.share){
      try { await navigator.share({ title: document.title, text: text, url: pageUrl }); return; } catch(e){}
    }
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(pageUrl);
        showToast('Link copied to clipboard — open Instagram to paste', 'success', 4000);
      }
    } catch (e) {}
    window.open('https://www.instagram.com/', '_blank');
  }

  function makeShareChooser(text, pageUrl){
    const existing = document.getElementById('share-chooser-modal');
    if (existing) existing.remove();
    const modal = document.createElement('div');
    modal.id = 'share-chooser-modal';
    Object.assign(modal.style, { position: 'fixed', left:0, top:0, width:'100%', height:'100%', display:'flex', alignItems:'center', justifyContent:'center', background:'rgba(0,0,0,0.35)', zIndex:2000 });

    const box = document.createElement('div');
    Object.assign(box.style, { background:'var(--surface, #fff)', padding:'14px', borderRadius:'10px', minWidth:'260px', boxShadow:'0 12px 40px rgba(16,24,40,0.25)'});

    const h = document.createElement('div'); h.textContent = 'Share via'; h.style.marginBottom = '8px'; h.style.fontWeight = '700'; box.appendChild(h);

    const wa = document.createElement('button'); wa.className = 'btn'; wa.textContent = 'WhatsApp'; wa.style.display='block'; wa.style.width='100%'; wa.style.marginBottom='8px'; wa.addEventListener('click', ()=>{ openWhatsApp(text); modal.remove(); }); box.appendChild(wa);

    const x = document.createElement('button'); x.className = 'btn btn--ghost'; x.textContent = 'X (Twitter)'; x.style.display='block'; x.style.width='100%'; x.style.marginBottom='8px'; x.addEventListener('click', ()=>{ openX(text, pageUrl); modal.remove(); }); box.appendChild(x);

    const li = document.createElement('button'); li.className = 'btn btn--ghost'; li.textContent = 'LinkedIn'; li.style.display='block'; li.style.width='100%'; li.style.marginBottom='8px'; li.addEventListener('click', ()=>{ openLinkedIn(pageUrl); modal.remove(); }); box.appendChild(li);

    const ig = document.createElement('button'); ig.className = 'btn'; ig.textContent = 'Instagram'; ig.style.display='block'; ig.style.width='100%'; ig.style.marginBottom='8px'; ig.addEventListener('click', async ()=>{ await shareToInstagram(text, pageUrl); modal.remove(); }); box.appendChild(ig);

    const cancel = document.createElement('button'); cancel.className = 'btn btn--ghost'; cancel.textContent = 'Cancel'; cancel.style.display='block'; cancel.style.width='100%'; cancel.addEventListener('click', ()=>modal.remove()); box.appendChild(cancel);

    modal.appendChild(box);
    document.body.appendChild(modal);
    modal.addEventListener('click', (e)=>{ if (e.target === modal) modal.remove(); });
  }

  document.addEventListener('click', function(ev){
    const btn = ev.target.closest && ev.target.closest('.btn-share');
    if (!btn) return;
    ev.preventDefault();
    const title = btn.getAttribute('data-title') || '';
    const id = btn.getAttribute('data-id');
    const filename = btn.getAttribute('data-filename');
    let pageUrl = '';
    if (id && id !== 'None') pageUrl = location.origin + '/presentations/' + id;
    else if (filename) pageUrl = location.origin + '/download/' + filename;
    else pageUrl = location.href;
    const text = title + '\n' + pageUrl;
    makeShareChooser(text, pageUrl);
  }, false);
})();
