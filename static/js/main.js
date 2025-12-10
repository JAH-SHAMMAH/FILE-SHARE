// Rewritten clean file
// Track created blob URLs so we can clean them up on unload
const pdfBlobUrls = [];

// pdf.js worker configuration (shared across pages)
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
document.addEventListener("submit", function () {
  // keep default behavior; extend as needed
});

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
