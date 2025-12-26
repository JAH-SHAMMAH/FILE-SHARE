// Rewritten clean file
// Track created blob URLs so we can clean them up on unload
const pdfBlobUrls = [];

// Toast helper: non-blocking, non-disabling UI feedback
function showToast(message, type = 'info', timeout = 4000) {
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
    el.style.maxWidth = '320px';
    el.style.fontSize = '13px';
    el.style.opacity = '0';
    el.style.transition = 'opacity 180ms ease, transform 220ms ease';
    el.textContent = message;
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

// pdf.js worker configuration (shared across pages)
if (window.pdfjsLib) {
  window.pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/js/pdf.worker.min.js";
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
document.addEventListener("submit", function () {
  // keep default behavior; extend as needed
});

// (promo slider removed) static cards handled by CSS

// Preview modal: fetch PDF, render first page to canvas, fallback to object, use blob URLs to avoid download prompts
document.addEventListener("DOMContentLoaded", () => {
  const modal = document.getElementById("preview-modal");
  if (!modal) return;

  const canvas = document.getElementById("preview-canvas");
  const objectEl = document.getElementById("preview-object");
  const placeholder = document.getElementById("preview-placeholder");
  const titleEl = document.getElementById("preview-title");
  const statusEl = document.getElementById("preview-status");
  const messageEl = document.getElementById("preview-message");
  const openEl = document.getElementById("preview-open");
  const downloadEl = document.getElementById("preview-download");

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

  const handlePreview = async (id) => {
    try {
      messageEl.classList.add("hidden");
      if (placeholder) placeholder.style.display = "block";
      if (objectEl) objectEl.style.display = "none";
      if (canvas) canvas.style.display = "none";
      statusEl.textContent = "Loading preview…";
      titleEl.textContent = "Preview";
      openEl.href = "#";
      downloadEl.href = "#";

      const res = await fetch(`/api/presentations/${id}/preview`);
      if (!res.ok) throw new Error("Preview not available");
      const data = await res.json();

      titleEl.textContent = data.title || "Preview";
      statusEl.textContent = data.conversion_status || "";

      if (data.viewer_url) {
        const pageHint = data.viewer_url.includes("#") ? "" : "#page=1&view=FitH&toolbar=0&navpanes=0";
        const clean = data.viewer_url.split("#")[0];
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
            objectEl.setAttribute("data", `${data.viewer_url}${pageHint}`);
            showObject();
          }
          openEl.href = `${data.viewer_url}${pageHint}`;
        }
      } else {
        if (canvas) canvas.style.display = "none";
        if (objectEl) objectEl.style.display = "none";
        messageEl.textContent =
          data.conversion_status === "unsupported"
            ? "This file type cannot be previewed."
            : "Preview will be ready after conversion.";
        messageEl.classList.remove("hidden");
      }

      if (data.original_url) {
        downloadEl.href = data.original_url;
        downloadEl.style.display = "inline-flex";
      } else {
        downloadEl.style.display = "none";
      }

      modal.classList.remove("hidden");
    } catch (err) {
      if (canvas) canvas.style.display = "none";
      if (objectEl) objectEl.style.display = "none";
      messageEl.textContent = "Preview unavailable.";
      messageEl.classList.remove("hidden");
      modal.classList.remove("hidden");
    }
  };

  document.querySelectorAll(".preview-btn").forEach((btn) => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      const id = btn.getAttribute("data-preview-id");
      if (id) handlePreview(id);
    });
  });
});

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
    document.querySelectorAll('.card__bookmark[data-id]').forEach(el => {
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
    const btn = e.target.closest('.card__bookmark');
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
      showToast(data.bookmarked ? 'Saved' : 'Removed', data.bookmarked ? 'success' : 'info');
    } catch (err) {
      btn.classList.toggle('bookmarked', was);
      showToast('Network error', 'error');
    }
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
        if (pageLabel) pageLabel.textContent = "Preview not available for this file type";
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
        if (pageLabel) pageLabel.textContent = window.pdfjsLib ? "Preview unavailable" : "Preview blocked (pdf.js)";
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
    ev.preventDefault();
    const ownerId = btn.getAttribute('data-user-id');
    const ownerEmail = btn.getAttribute('data-email') || '';
    const ownerName = btn.getAttribute('data-username') || '';
    const current = document.getElementById('current-user');
    const isSignedIn = current && current.getAttribute('data-my-id');
    if (!isSignedIn) {
      // show signin modal
      const signin = document.getElementById('signin-modal');
      if (signin) {
        const mailto = document.getElementById('signin-mailto');
        if (mailto) mailto.setAttribute('href', ownerEmail ? ('mailto:' + encodeURIComponent(ownerEmail)) : '#');
        signin.style.display = 'block';
      }
      return;
    }

    // signed-in: open contact modal
    const modal = document.getElementById('contact-modal');
    if (!modal) return;
    const nameInput = document.getElementById('contact-name');
    const emailInput = document.getElementById('contact-email');
    const ownerIdInput = document.getElementById('contact-owner-id');
    const msgInput = document.getElementById('contact-message');
    // prefill with current user info when available
    const cu = document.getElementById('current-user');
    const myId = cu ? cu.getAttribute('data-my-id') : null;
    // Try to prefill from server-rendered profile menu if present
    const profileEmail = document.querySelector('.profile-menu__email') ? document.querySelector('.profile-menu__email').textContent.trim() : '';
    const profileName = document.querySelector('.profile-menu__name') ? document.querySelector('.profile-menu__name').textContent.trim() : '';
    if (nameInput) nameInput.value = profileName || '';
    if (emailInput) emailInput.value = profileEmail || '';
    if (ownerIdInput) ownerIdInput.value = ownerId || '';
    if (msgInput) msgInput.value = `Hi ${ownerName || ''},\n\nI am interested in your presentation.`;
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
      const fd = new FormData(contactForm);
      try {
        showToast('Sending message…', 'info', 2500);
        const res = await fetch('/contact', { method: 'POST', body: fd, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' } });
        if (!res.ok) throw new Error('Request failed');
        const data = await res.json();
        if (data && data.success) {
          status.textContent = 'Message sent. Thank you.';
          status.style.display = 'block';
          showToast('Message sent', 'success');
          setTimeout(() => { document.getElementById('contact-modal').style.display = 'none'; }, 900);
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
        previewPopup.innerHTML = '<div style="padding:8px;color:#666">Preview not available</div>';
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
