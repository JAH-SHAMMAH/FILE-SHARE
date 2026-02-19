class PresentationViewer {
  constructor(options = {}) {
    const isArrayInput = Array.isArray(options);
    this.staticSlides = isArrayInput ? options : (options.slides || null);
    this.presentationId = isArrayInput ? null : options.presentationId;
    this.presentationFilename = isArrayInput ? '' : (options.presentationFilename || '');
    this.viewerUrl = isArrayInput ? '' : (options.viewerUrl || '');

    this.thumbsEl = document.getElementById('presentation-thumbs');
    this.mainEl = document.getElementById('presentation-main-viewer');
    this.placeholder = document.getElementById('viewer-placeholder');
    this.pageIndicator = document.getElementById('page-indicator');
    this.prevBtn = document.getElementById('prev-page');
    this.nextBtn = document.getElementById('next-page');
    this.zoomInBtn = document.getElementById('zoom-in');
    this.zoomOutBtn = document.getElementById('zoom-out');
    this.zoomLevelEl = document.getElementById('zoom-level');
    this.fullscreenBtn = document.getElementById('viewer-fullscreen');
    this.viewerFrame = document.getElementById('presentation-viewer-frame');
    this.thumbnailSidebar = this.thumbsEl;

    this.slides = [];
    this.currentIndex = 0;
    this.currentZoom = 1;
    this.minZoom = 0.5;
    this.maxZoom = 3;
    this.zoomStep = 0.1;
    this.currentImg = null;
    this.currentSlideIndex = 0;
    this.zoomStageEl = null;
    this.zoomContentEl = null;
    this.pdfDocPromise = null;
    this.pdfRenderToken = 0;
    this.pdfPagesRendered = false;
    this.isPdfFallbackMode = false;
    this.currentBaseWidth = 0;
    this.currentBaseHeight = 0;
    this.resizeObserver = null;
    this.pendingFitFrame = 0;
    this.initialized = false;
    this.isBound = false;
    this.zoomDebugEl = null;
    this.zoomDebugEnabled = false;
    this.zoomMutationObserver = null;
    this.pendingForceZoomFrame = 0;
  }

  forceApplyLiveZoom() {
    if (!this.mainEl) return;
    const liveContent = this.mainEl.querySelector('.presentation-zoom-content');
    if (!liveContent) return;

    this.zoomContentEl = liveContent;

    liveContent.style.setProperty('transform', `scale(${this.currentZoom})`, 'important');
    liveContent.style.setProperty('transform-origin', 'top center', 'important');
    liveContent.style.setProperty('transition', 'transform 0.2s ease', 'important');
    liveContent.style.setProperty('will-change', 'transform', 'important');

    const stack = liveContent.querySelector('.presentation-pdf-stack');
    if (stack) {
      stack.style.setProperty('transform', 'none', 'important');
      stack.style.setProperty('width', '100%', 'important');
    }

    this.mainEl.style.setProperty('overflow-x', 'scroll', 'important');
    this.mainEl.style.setProperty('overflow-y', 'scroll', 'important');
  }

  scheduleForceApplyLiveZoom() {
    if (this.pendingForceZoomFrame) {
      try { cancelAnimationFrame(this.pendingForceZoomFrame); } catch (_) {}
    }
    this.pendingForceZoomFrame = requestAnimationFrame(() => {
      this.pendingForceZoomFrame = 0;
      this.forceApplyLiveZoom();
    });
  }

  ensureZoomMutationObserver() {
    if (!this.mainEl || this.zoomMutationObserver || typeof MutationObserver === 'undefined') return;
    this.zoomMutationObserver = new MutationObserver(() => {
      this.scheduleForceApplyLiveZoom();
    });
    this.zoomMutationObserver.observe(this.mainEl, { childList: true, subtree: true });
  }

  ensureZoomDebugBadge() {
    if (!this.zoomDebugEnabled) return null;
    if (this.zoomDebugEl && document.body.contains(this.zoomDebugEl)) return this.zoomDebugEl;

    const badge = document.createElement('div');
    badge.id = 'presentation-zoom-debug';
    badge.style.position = 'fixed';
    badge.style.right = '14px';
    badge.style.bottom = '14px';
    badge.style.zIndex = '99999';
    badge.style.background = 'rgba(15, 23, 42, 0.88)';
    badge.style.color = '#fff';
    badge.style.padding = '8px 10px';
    badge.style.borderRadius = '8px';
    badge.style.fontSize = '12px';
    badge.style.fontFamily = 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif';
    badge.style.pointerEvents = 'none';
    badge.textContent = 'Zoom debug ready';
    document.body.appendChild(badge);
    this.zoomDebugEl = badge;
    return badge;
  }

  reportZoomDebug(source) {
    if (!this.zoomDebugEnabled) return;
    const badge = this.ensureZoomDebugBadge();
    const zoomLabel = `${Math.round(this.currentZoom * 100)}%`;
    const message = `Zoom ${zoomLabel} · ${source}`;
    if (badge) badge.textContent = message;
    try { console.debug('[presentation-viewer]', message); } catch (_) {}
  }

  getViewerInnerSize() {
    if (!this.mainEl) return { width: 0, height: 0 };
    const rect = this.mainEl.getBoundingClientRect();
    const measuredWidth = Math.floor((rect && rect.width) ? rect.width : this.mainEl.clientWidth);
    const measuredHeight = Math.floor((rect && rect.height) ? rect.height : this.mainEl.clientHeight);
    const fallbackWidth = Math.floor(window.innerWidth * 0.82);
    const fallbackHeight = Math.floor(window.innerHeight * 0.72);
    const width = Math.max(320, measuredWidth > 0 ? measuredWidth : fallbackWidth);
    const height = Math.max(260, measuredHeight > 0 ? measuredHeight : fallbackHeight);
    return { width, height };
  }

  getScaleMetrics(baseWidth, baseHeight) {
    const container = this.getViewerInnerSize();
    const scale = Math.max(this.minZoom, Math.min(this.maxZoom, this.currentZoom));
    const scaledWidth = Math.ceil(baseWidth * scale);
    const scaledHeight = Math.ceil(baseHeight * scale);
    return {
      scale,
      scaledWidth,
      scaledHeight,
      containerWidth: container.width,
      containerHeight: container.height,
    };
  }

  ensureZoomTargets() {
    if (!this.mainEl) return;

    if (this.zoomStageEl && !this.zoomStageEl.isConnected) {
      this.zoomStageEl = null;
    }
    if (this.zoomContentEl && !this.zoomContentEl.isConnected) {
      this.zoomContentEl = null;
    }

    if (!this.zoomStageEl || !this.zoomContentEl) {
      this.zoomStageEl = this.mainEl.querySelector('.presentation-zoom-stage');
      this.zoomContentEl = this.mainEl.querySelector('.presentation-zoom-content');
    }

    if (!this.zoomStageEl || !this.zoomContentEl) {
      const existing = this.mainEl.querySelector('.slide-image, img, canvas, object, iframe, video');
      this.prepareZoomLayer();
      if (existing && this.zoomContentEl && existing.parentElement !== this.zoomContentEl) {
        this.zoomContentEl.appendChild(existing);
      }
    }

    if (!this.currentImg && this.zoomContentEl) {
      this.currentImg = this.zoomContentEl.querySelector('.slide-image, img, canvas, object, iframe, video');
    }

    if (!this.currentImg) return;

    const rect = this.currentImg.getBoundingClientRect ? this.currentImg.getBoundingClientRect() : null;
    const naturalWidth = this.currentImg.naturalWidth || (rect && Math.floor(rect.width)) || this.currentBaseWidth || 1;
    const naturalHeight = this.currentImg.naturalHeight || (rect && Math.floor(rect.height)) || this.currentBaseHeight || 1;
    this.currentBaseWidth = Math.max(1, naturalWidth);
    this.currentBaseHeight = Math.max(1, naturalHeight);
  }

  applyElementScale(targetEl, scale) {
    if (!targetEl) return;
    targetEl.style.setProperty('transform', `scale(${scale})`, 'important');
    targetEl.style.setProperty('transform-origin', 'top center', 'important');
    targetEl.style.setProperty('transition', 'transform 0.2s ease', 'important');
    targetEl.style.setProperty('will-change', 'transform', 'important');
  }

  applyScaledLayout(baseWidth, baseHeight) {
    if (!this.mainEl || !this.zoomContentEl || !this.zoomStageEl) return;
    if (!baseWidth || !baseHeight) return;

    const metrics = this.getScaleMetrics(baseWidth, baseHeight);

    this.zoomStageEl.style.setProperty('display', 'flex', 'important');
    this.zoomStageEl.style.setProperty('justify-content', 'center', 'important');
    this.zoomStageEl.style.setProperty('align-items', 'flex-start', 'important');
    this.zoomStageEl.style.setProperty('width', `${Math.max(metrics.containerWidth, metrics.scaledWidth)}px`, 'important');
    this.zoomStageEl.style.setProperty('height', `${Math.max(metrics.containerHeight, metrics.scaledHeight)}px`, 'important');

    this.zoomContentEl.style.setProperty('width', `${Math.ceil(baseWidth)}px`, 'important');
    this.zoomContentEl.style.setProperty('height', `${Math.ceil(baseHeight)}px`, 'important');
    this.zoomContentEl.style.setProperty('transform', `scale(${metrics.scale})`, 'important');
    this.zoomContentEl.style.setProperty('transform-origin', 'top center', 'important');
    this.zoomContentEl.style.setProperty('will-change', 'transform', 'important');
    this.zoomContentEl.style.setProperty('transition', 'transform 160ms ease-out', 'important');
    this.zoomContentEl.style.setProperty('zoom', String(metrics.scale), 'important');

    this.mainEl.style.setProperty('overflow-x', 'scroll', 'important');
    this.mainEl.style.setProperty('overflow-y', 'scroll', 'important');
  }

  scheduleFitRerender() {
    if (this.pendingFitFrame) {
      try { cancelAnimationFrame(this.pendingFitFrame); } catch (_) {}
    }
    this.pendingFitFrame = requestAnimationFrame(() => {
      this.pendingFitFrame = 0;
      if (this.usesPdfDocumentMode()) {
        if (this.pdfPagesRendered && this.currentBaseWidth && this.currentBaseHeight) {
          this.applyPdfVisualScale();
        }
        return;
      }
      this.applyZoom();
    });
  }

  applyPdfVisualScale() {
    this.ensureZoomTargets();
    if (!this.mainEl || !this.zoomContentEl || !this.zoomStageEl) return;

    const liveContent = this.mainEl.querySelector('.presentation-zoom-content');
    if (liveContent && liveContent !== this.zoomContentEl) {
      this.zoomContentEl = liveContent;
    }

    const stack = this.zoomContentEl.querySelector('.presentation-pdf-stack');

    this.zoomContentEl.style.setProperty('transform', `scale(${this.currentZoom})`, 'important');
    this.zoomContentEl.style.setProperty('transform-origin', 'top center', 'important');
    this.zoomContentEl.style.setProperty('transition', 'transform 0.2s ease', 'important');
    this.zoomContentEl.style.setProperty('will-change', 'transform', 'important');
    this.mainEl.style.setProperty('--pdf-zoom-scale', String(this.currentZoom));
    this.mainEl.classList.add('pdf-zoom-active');

    if (stack) {
      stack.style.setProperty('transform', 'none', 'important');
      stack.style.setProperty('width', '100%', 'important');
    }

    this.mainEl.style.setProperty('overflow-x', 'scroll', 'important');
    this.mainEl.style.setProperty('overflow-y', 'scroll', 'important');
  }

  isPdfPresentation() {
    const filename = (this.presentationFilename || '').toLowerCase();
    return filename.endsWith('.pdf') && !!this.viewerUrl && !!window.pdfjsLib;
  }

  usesPdfDocumentMode() {
    return this.isPdfPresentation() && !this.isPdfFallbackMode;
  }

  getQualityFactor() {
    const dpr = window.devicePixelRatio || 1;
    const factor = dpr * Math.max(1, this.currentZoom);
    return Math.max(2, Math.min(8, Number(factor.toFixed(2))));
  }

  buildHdSlideUrl(index) {
    const quality = this.getQualityFactor();
    return `/presentations/${this.presentationId}/slide/${index}?hd=1&quality=${quality}`;
  }

  refreshCurrentImageQuality() {
    if (!this.currentImg || this.currentImg.tagName !== 'IMG') return;
    if (!this.presentationId) return;
    const index = Number(this.currentImg.dataset.slideIndex || this.currentSlideIndex || 0);
    const nextQuality = this.getQualityFactor();
    const loadedQuality = Number(this.currentImg.dataset.quality || 1);
    if (nextQuality <= loadedQuality + 0.05) return;

    this.currentImg.dataset.quality = String(nextQuality);
    this.currentImg.src = this.buildHdSlideUrl(index);
  }

  init() {
    if (!this.mainEl) {
      return;
    }

    if (this.initialized || this.mainEl.dataset.viewerInitialized === '1') {
      return;
    }
    this.initialized = true;
    this.mainEl.dataset.viewerInitialized = '1';

    try {
      window.__presentationViewer = this;
      window.__presentationZoomIn = () => this.zoomIn();
      window.__presentationZoomOut = () => this.zoomOut();
    } catch (_) {}

    this.bindEvents();
    this.ensureZoomMutationObserver();
    if (this.zoomLevelEl) {
      this.zoomLevelEl.textContent = `${Math.round(this.currentZoom * 100)}%`;
    }
    this.updateFullscreenUi();
    this.fetchThumbnails();
  }

  hasControls() {
    return !!(this.thumbsEl && this.pageIndicator && this.prevBtn && this.nextBtn);
  }

  setIndicator() {
    if (!this.hasControls()) return;
    const total = this.slides.length || 1;
    const current = Math.min(this.currentIndex + 1, total);
    this.pageIndicator.textContent = `Slide ${current} of ${total}`;
    this.prevBtn.disabled = this.currentIndex <= 0 || total <= 1;
    this.nextBtn.disabled = this.currentIndex >= (total - 1) || total <= 1;
  }

  applyZoom(delta, source = 'update') {
    if (typeof delta === 'number') {
      const nextZoom = this.currentZoom + delta;
      this.currentZoom = Math.max(this.minZoom, Math.min(this.maxZoom, Number(nextZoom.toFixed(2))));
    }
    if (this.zoomLevelEl) {
      this.zoomLevelEl.textContent = `${Math.round(this.currentZoom * 100)}%`;
    }
    console.log('Zoom:', this.currentZoom);
    this.reportZoomDebug(source);
    this.forceApplyLiveZoom();

    if (this.usesPdfDocumentMode()) {
      if (this.pdfPagesRendered) {
        this.applyPdfVisualScale();
        requestAnimationFrame(() => this.applyPdfVisualScale());
        this.scheduleForceApplyLiveZoom();
      } else {
        this.renderPdfPage(this.currentSlideIndex, true).catch(() => {});
      }
      return;
    }

    if (this.mainEl) {
      this.mainEl.classList.remove('pdf-zoom-active');
      this.mainEl.style.removeProperty('--pdf-zoom-scale');
    }

    this.ensureZoomTargets();

    if (!this.mainEl || !this.zoomContentEl || !this.zoomStageEl) return;

    const liveZoomContent = this.mainEl.querySelector('.presentation-zoom-content') || this.zoomContentEl;
    if (liveZoomContent && liveZoomContent !== this.zoomContentEl) {
      this.zoomContentEl = liveZoomContent;
    }

    if (!this.currentImg) {
      this.currentImg = this.zoomContentEl.querySelector('.slide-image, img, canvas, object, iframe, video');
    }

    if (!this.currentImg) {
      this.applyElementScale(this.zoomContentEl, this.currentZoom);
      const stack = this.zoomContentEl.querySelector('.presentation-pdf-stack');
      if (stack) {
        stack.style.setProperty('transform', `scale(${this.currentZoom})`, 'important');
        stack.style.setProperty('transform-origin', 'top center', 'important');
        stack.style.setProperty('transition', 'transform 0.2s ease', 'important');
        stack.style.setProperty('will-change', 'transform', 'important');
      }
      this.mainEl.style.setProperty('overflow-x', 'scroll', 'important');
      this.mainEl.style.setProperty('overflow-y', 'scroll', 'important');
      this.scheduleForceApplyLiveZoom();
      return;
    }

    this.currentImg.style.setProperty('display', 'block', 'important');
    this.currentImg.style.setProperty('margin', '0 auto', 'important');
    this.currentImg.style.setProperty('max-width', 'none', 'important');
    this.currentImg.style.setProperty('transform', 'none', 'important');
    this.currentImg.style.setProperty('transform-origin', 'top center', 'important');
    this.currentImg.style.setProperty('transition', 'none', 'important');

    this.applyElementScale(this.zoomContentEl, this.currentZoom);

    this.mainEl.style.setProperty('overflow-x', 'scroll', 'important');
    this.mainEl.style.setProperty('overflow-y', 'scroll', 'important');
    this.scheduleForceApplyLiveZoom();

    this.refreshCurrentImageQuality();
  }

  fitZoom() {
    this.currentZoom = 1;
    this.applyZoom();
  }

  renderCurrentSlide() {
    if (!this.slides.length && !this.staticSlides) return;
    if (this.usesPdfDocumentMode()) {
      if (this.pdfPagesRendered && this.currentBaseWidth && this.currentBaseHeight) {
        this.applyPdfVisualScale();
      } else {
        this.renderPdfPage(this.currentSlideIndex, true).catch(() => {});
      }
      return;
    }
    this.applyZoom();
  }

  scrollToPdfPage(index) {
    if (!this.zoomContentEl) return;
    const pageEl = this.zoomContentEl.querySelector(`[data-pdf-page-index="${index}"]`);
    if (!pageEl) return;
    try {
      pageEl.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } catch (_) {}
  }

  zoomIn() {
    this.applyZoom(this.zoomStep, 'zoom-in');
  }

  zoomOut() {
    this.applyZoom(-this.zoomStep, 'zoom-out');
  }

  prepareZoomLayer() {
    if (!this.mainEl) return;
    this.mainEl.innerHTML = '';

    const zoomStage = document.createElement('div');
    zoomStage.className = 'presentation-zoom-stage';

    const zoomContent = document.createElement('div');
    zoomContent.className = 'presentation-zoom-content slide-container';

    zoomStage.appendChild(zoomContent);
    this.mainEl.appendChild(zoomStage);

    this.zoomStageEl = zoomStage;
    this.zoomContentEl = zoomContent;
  }

  updateFullscreenUi() {
    if (!this.fullscreenBtn) return;
    const active = document.fullscreenElement === this.viewerFrame;
    this.fullscreenBtn.setAttribute('aria-label', active ? 'Exit fullscreen' : 'Enter fullscreen');
    this.fullscreenBtn.setAttribute('title', active ? 'Exit fullscreen' : 'Fullscreen');
    this.fullscreenBtn.classList.toggle('is-active', active);
  }

  async toggleFullscreen() {
    if (!this.viewerFrame || !document.fullscreenEnabled) return;
    try {
      if (document.fullscreenElement === this.viewerFrame) {
        await document.exitFullscreen();
      } else {
        await this.viewerFrame.requestFullscreen();
      }
    } catch (e) {
      return;
    }
  }

  async fetchThumbnails() {
    if (Array.isArray(this.staticSlides) && this.staticSlides.length) {
      this.slides = this.staticSlides.map((slide, index) => ({
        id: slide.id || `slide-${index + 1}`,
        imageUrl: slide.fullImageUrl || slide.imageUrl || this.buildHdSlideUrl(index),
        thumbnailUrl: slide.thumbnailUrl || slide.imageUrl || `/presentations/${this.presentationId}/slide/${index}`,
      }));
      this.generateThumbnails();
      await this.showPage(0);
      return;
    }

    try {
      const res = await fetch(`/presentations/${this.presentationId}/thumbnails`, { credentials: 'include' });
      if (!res.ok) throw new Error('no thumbs');
      const data = await res.json();
      const urls = data.thumbnails || [];
      this.slides = urls.map((url, index) => ({
        id: `slide-${index + 1}`,
        imageUrl: this.buildHdSlideUrl(index),
        thumbnailUrl: url,
      }));

      if (this.slides.length === 0) {
        const fallbackSrc = (document.getElementById('presentation-initial-image') || {}).src || `/media/thumbs/${this.presentationId}/slide_0.png`;
        this.slides = [{
          id: 'slide-1',
          imageUrl: fallbackSrc,
          thumbnailUrl: fallbackSrc,
        }];
      }

      this.generateThumbnails();
      await this.showPage(0);

      try { window.__presentationSlides = this.slides; } catch (_) {}

      if (data.status === 'queued') {
        await this.pollThumbnails();
      }
    } catch (e) {
      const fallbackSrc = (document.getElementById('presentation-initial-image') || {}).src || `/media/thumbs/${this.presentationId}/slide_0.png`;
      this.slides = [{
        id: 'slide-1',
        imageUrl: fallbackSrc,
        thumbnailUrl: fallbackSrc,
      }];
      this.generateThumbnails();
      await this.showPage(0);
    }
  }

  generateThumbnails() {
    if (!this.thumbnailSidebar) {
      return;
    }

    this.thumbnailSidebar.innerHTML = '';

    this.slides.forEach((slide, index) => {
      const wrap = document.createElement('div');
      wrap.className = 'presentation-thumb-wrap thumbnail-item';
      wrap.dataset.index = String(index);
      wrap.setAttribute('tabindex', '0');
      wrap.setAttribute('role', 'button');
      wrap.setAttribute('aria-label', `Go to slide ${index + 1}`);
      wrap.addEventListener('click', () => this.showPage(index));
      wrap.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' || event.key === ' ') {
          event.preventDefault();
          this.showPage(index);
        }
      });

      const img = document.createElement('img');
      img.className = 'presentation-thumb thumbnail-image';
  img.src = slide.thumbnailUrl || slide.imageUrl;
      img.alt = `Slide ${index + 1}`;
      img.dataset.index = String(index);
      img.loading = 'lazy';

      const badge = document.createElement('div');
      badge.className = 'presentation-thumb__badge thumbnail-number';
      badge.textContent = String(index + 1);

      wrap.appendChild(img);
      wrap.appendChild(badge);
      this.thumbnailSidebar.appendChild(wrap);
    });
  }

  renderThumbs() {
    this.generateThumbnails();
  }

  renderFallback() {
    if (this.mainEl && this.mainEl.querySelector('#presentation-initial-image')) {
      this.setIndicator();
      return;
    }

    this.mainEl.innerHTML = '';
    const wrap = document.createElement('div');
    wrap.style.display = 'flex';
    wrap.style.flexDirection = 'column';
    wrap.style.alignItems = 'center';
    wrap.style.justifyContent = 'center';
    wrap.style.minHeight = '240px';

    const msg = document.createElement('div');
    msg.className = 'muted';
    msg.style.marginBottom = '8px';
    msg.textContent = 'No slide previews yet. Use "Generate Previews" above, or open the original file.';
    wrap.appendChild(msg);

    const link = document.createElement('a');
    link.className = 'btn';
    const headerDownload = document.querySelector('.btn--download');
    link.href = (headerDownload && headerDownload.getAttribute('href')) ? headerDownload.getAttribute('href') : '#';
    link.textContent = headerDownload ? 'Download / Open original' : 'No file available';
    link.target = '_blank';
    wrap.appendChild(link);

    this.mainEl.appendChild(wrap);
    if (this.pageIndicator) {
      this.pageIndicator.textContent = 'Slide — of —';
    }
  }

  async renderPdfFallback() {
    try {
      if (!this.viewerUrl || !window.pdfjsLib) return false;
      const filename = this.presentationFilename.toLowerCase();
      if (!filename.endsWith('.pdf')) return false;
      this.isPdfFallbackMode = true;

      const cleanUrl = this.viewerUrl.split('#')[0];
      const canvas = document.getElementById('presentation-pdf-canvas') || document.createElement('canvas');
      canvas.id = 'presentation-pdf-canvas';
      canvas.style.setProperty('position', 'static', 'important');
      canvas.style.setProperty('inset', 'auto', 'important');
      canvas.style.setProperty('display', 'block', 'important');
      canvas.style.setProperty('margin', '0 auto', 'important');
      canvas.style.setProperty('width', '100%', 'important');
      canvas.style.setProperty('max-width', 'none', 'important');
      canvas.style.setProperty('height', 'auto', 'important');
      this.prepareZoomLayer();
      this.zoomContentEl.innerHTML = '';
      this.zoomContentEl.appendChild(canvas);

      let pdf = null;
      try {
        const loadingTask = window.pdfjsLib.getDocument({ url: cleanUrl });
        pdf = await loadingTask.promise;
      } catch (_) {
        const loadingTask = window.pdfjsLib.getDocument({ url: cleanUrl, disableWorker: true });
        pdf = await loadingTask.promise;
      }

      const page = await pdf.getPage(1);
      const dpr = window.devicePixelRatio || 1;
      const viewport = page.getViewport({ scale: 1 });
      const container = this.getViewerInnerSize();
      const targetCssWidth = Math.max(1, Math.floor(container.width));
      const fitScale = targetCssWidth / Math.max(1, viewport.width);
      const qualityBoost = 1.5;
      const renderScale = Math.max(1.5, Math.min(10, fitScale * dpr * qualityBoost));
      const scaledViewport = page.getViewport({ scale: renderScale });
      const ctx = canvas.getContext('2d');
      canvas.width = Math.floor(scaledViewport.width);
      canvas.height = Math.floor(scaledViewport.height);
      const cssWidth = Math.floor(scaledViewport.width / (dpr * qualityBoost));
      const cssHeight = Math.floor(scaledViewport.height / (dpr * qualityBoost));
      canvas.style.setProperty('width', `${cssWidth}px`, 'important');
      canvas.style.setProperty('height', `${cssHeight}px`, 'important');
      canvas.classList.add('slide-image');
      if (ctx) {
        ctx.setTransform(1, 0, 0, 1, 0, 0);
        await page.render({ canvasContext: ctx, viewport: scaledViewport }).promise;
      }

      this.currentImg = canvas;
      this.currentBaseWidth = Math.max(1, cssWidth || targetCssWidth);
      this.currentBaseHeight = Math.max(1, cssHeight || Math.floor(container.height));
      this.pdfPagesRendered = false;
      this.applyZoom(undefined, 'pdf-fallback');

      if (this.placeholder) this.placeholder.style.display = 'none';
      if (this.pageIndicator) this.pageIndicator.textContent = 'Slide 1 of 1';
      return true;
    } catch (_) {
      return false;
    }
  }

  async getPdfDocument() {
    if (this.pdfDocPromise) return this.pdfDocPromise;

    const cleanUrl = (this.viewerUrl || '').split('#')[0];
    this.pdfDocPromise = (async () => {
      try {
        const loadingTask = window.pdfjsLib.getDocument({ url: cleanUrl });
        return await loadingTask.promise;
      } catch (_) {
        const loadingTask = window.pdfjsLib.getDocument({ url: cleanUrl, disableWorker: true });
        return await loadingTask.promise;
      }
    })();

    return this.pdfDocPromise;
  }

  async renderPdfPage(index, force = false) {
    if (!this.mainEl || !this.zoomContentEl || !this.zoomStageEl) return;
    if (!this.isPdfPresentation()) return;
    this.isPdfFallbackMode = false;

    if (!force && this.pdfPagesRendered) {
      this.scrollToPdfPage(index);
      return;
    }

    const renderToken = ++this.pdfRenderToken;
    const pdf = await this.getPdfDocument();
    if (!pdf) return;
    const dpr = window.devicePixelRatio || 1;
    const { containerWidth, containerHeight } = this.getViewerInnerSize();
    const targetBaseWidth = Math.max(1, Math.floor(containerWidth));
    const qualityBoost = 1.5;
    const stack = document.createElement('div');
    stack.className = 'presentation-pdf-stack slide-container';
    stack.style.width = `${targetBaseWidth}px`;

    let firstCanvas = null;
    let firstWidth = 0;
    let totalHeight = 0;

    for (let pageNumber = 1; pageNumber <= (pdf.numPages || 1); pageNumber += 1) {
      if (renderToken !== this.pdfRenderToken) return;

      const page = await pdf.getPage(pageNumber);
      const baseViewport = page.getViewport({ scale: 1 });
      const fitScale = targetBaseWidth / Math.max(1, baseViewport.width);
      const renderScale = Math.max(1.5, Math.min(20, fitScale * dpr * qualityBoost));
      const viewport = page.getViewport({ scale: renderScale });
      const cssWidth = Math.floor(viewport.width / (dpr * qualityBoost));
      const cssHeight = Math.floor(viewport.height / (dpr * qualityBoost));

      const canvas = document.createElement('canvas');
      canvas.className = 'presentation-pdf-canvas slide-image';
      canvas.style.setProperty('position', 'static', 'important');
      canvas.style.setProperty('inset', 'auto', 'important');
      canvas.style.setProperty('display', 'block', 'important');
      canvas.style.setProperty('margin', '0 auto', 'important');
      canvas.style.setProperty('width', `${cssWidth}px`, 'important');
      canvas.style.setProperty('max-width', 'none', 'important');
      canvas.style.setProperty('height', `${cssHeight}px`, 'important');

      canvas.width = Math.floor(viewport.width);
      canvas.height = Math.floor(viewport.height);

      const ctx = canvas.getContext('2d');
      if (!ctx) continue;

      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = 'high';
      await page.render({ canvasContext: ctx, viewport }).promise;
      if (renderToken !== this.pdfRenderToken) return;

      const pageWrap = document.createElement('div');
      pageWrap.className = 'pdf-page-item';
      pageWrap.dataset.pdfPageIndex = String(pageNumber - 1);
      pageWrap.appendChild(canvas);
      stack.appendChild(pageWrap);

      if (!firstCanvas) {
        firstCanvas = canvas;
        firstWidth = cssWidth;
      }
      totalHeight += cssHeight;
    }

    this.zoomContentEl.innerHTML = '';
    this.zoomContentEl.appendChild(stack);
    this.currentImg = firstCanvas;
    this.currentBaseWidth = firstWidth || containerWidth;
    this.currentBaseHeight = Math.max(totalHeight, this.mainEl.clientHeight);
    this.pdfPagesRendered = true;

    this.zoomContentEl.style.width = `${targetBaseWidth}px`;
    this.zoomContentEl.style.height = 'auto';
    this.zoomContentEl.style.setProperty('zoom', '1', 'important');
    this.applyPdfVisualScale();
    this.scheduleForceApplyLiveZoom();
    this.scrollToPdfPage(index);
  }

  async showPage(index) {
    if (index < 0) return;
    this.currentIndex = index;
    this.currentSlideIndex = index;
    this.currentZoom = 1;
    if (this.zoomLevelEl) {
      this.zoomLevelEl.textContent = '100%';
    }
    try { window.currentIndex = this.currentIndex; } catch (_) {}

    if (this.thumbnailSidebar) {
      this.thumbnailSidebar.querySelectorAll('.thumbnail-item').forEach((el) => el.classList.remove('active'));
      const active = this.thumbnailSidebar.querySelector(`.thumbnail-item[data-index="${index}"]`);
      if (active) {
        active.classList.add('active');
        try { active.scrollIntoView({ behavior: 'smooth', block: 'nearest' }); } catch (_) {}
      }
    }

    this.prepareZoomLayer();
    if (this.mainEl) {
      this.mainEl.classList.remove('pdf-zoom-active');
      this.mainEl.style.removeProperty('--pdf-zoom-scale');
    }
    this.isPdfFallbackMode = false;
    this.pdfPagesRendered = false;
    const isPdf = this.isPdfPresentation();

    const staticSlide = Array.isArray(this.staticSlides) ? this.staticSlides[index] : null;
    const slide = this.slides[index] || null;
    const primaryImageUrl = staticSlide ? (staticSlide.imageUrl || (slide && slide.imageUrl)) : (slide && slide.imageUrl);

    if (isPdf) {
      try {
        await this.renderPdfPage(index);
      } catch (_) {
        const img = document.createElement('img');
        img.src = this.buildHdSlideUrl(index);
        img.alt = `Slide ${index + 1}`;
        img.dataset.slideIndex = String(index);
        img.dataset.quality = String(this.getQualityFactor());
        img.style.display = 'block';
        img.style.width = '100%';
        img.style.maxWidth = '100%';
        img.style.height = 'auto';
        img.style.margin = '0 auto';
        img.classList.add('slide-image');
        img.onerror = () => this.renderFallback();
        img.addEventListener('load', () => {
          this.currentBaseWidth = img.naturalWidth || img.width || 1;
          this.currentBaseHeight = img.naturalHeight || img.height || 1;
          this.applyZoom();
        });
        this.zoomContentEl.innerHTML = '';
        this.zoomContentEl.appendChild(img);
        this.currentImg = img;
      }
    } else if (primaryImageUrl) {
      const img = document.createElement('img');
      img.src = primaryImageUrl;
      img.alt = `Slide ${index + 1}`;
      img.dataset.slideIndex = String(index);
      img.dataset.quality = String(this.getQualityFactor());
      img.style.display = 'block';
      img.style.width = '100%';
      img.style.maxWidth = '100%';
      img.style.height = 'auto';
      img.style.margin = '0 auto';
      img.classList.add('slide-image');
      img.onerror = () => {
        if (img.dataset.fallbackUsed === '1') return;
        img.dataset.fallbackUsed = '1';
        img.src = this.buildHdSlideUrl(index);
      };
      img.addEventListener('load', () => {
        this.currentBaseWidth = img.naturalWidth || img.width || 1;
        this.currentBaseHeight = img.naturalHeight || img.height || 1;
        this.applyZoom();
      });
      this.zoomContentEl.appendChild(img);
      this.currentImg = img;
    } else {
      const img = document.createElement('img');
      img.src = this.buildHdSlideUrl(index);
      img.alt = `Slide ${index + 1}`;
      img.dataset.slideIndex = String(index);
      img.dataset.quality = String(this.getQualityFactor());
      img.style.display = 'block';
      img.style.width = '100%';
      img.style.maxWidth = '100%';
      img.style.height = 'auto';
      img.style.margin = '0 auto';
      img.classList.add('slide-image');
      img.onerror = () => this.renderFallback();
      img.addEventListener('load', () => {
        this.currentBaseWidth = img.naturalWidth || img.width || 1;
        this.currentBaseHeight = img.naturalHeight || img.height || 1;
        this.applyZoom();
      });
      this.zoomContentEl.appendChild(img);
      this.currentImg = img;
    }

    if (!isPdf) {
      this.applyZoom();
    }
    this.setIndicator();
  }

  async pollThumbnails() {
    const start = Date.now();
    const timeoutMs = 60000;
    if (this.placeholder) {
      this.placeholder.textContent = 'Generating previews…';
    }

    while (Date.now() - start < timeoutMs) {
      try {
        const resp = await fetch(`/presentations/${this.presentationId}/thumbnails`, { credentials: 'include' });
        if (resp.ok) {
          const json = await resp.json();
          if (json.thumbnails && json.thumbnails.length) {
            this.slides = json.thumbnails.map((url, index) => ({
              id: `slide-${index + 1}`,
              imageUrl: this.buildHdSlideUrl(index),
              thumbnailUrl: url,
            }));
            this.generateThumbnails();
            await this.showPage(0);
            return;
          }
        }
      } catch (_) {}
      await new Promise((resolve) => setTimeout(resolve, 2000));
    }

    const rendered = await this.renderPdfFallback();
    if (!rendered) this.renderFallback();
  }

  bindEvents() {
    if (this.isBound) return;
    this.isBound = true;

    if (this.prevBtn) {
      this.prevBtn.addEventListener('click', () => {
        if (this.currentIndex > 0) this.showPage(this.currentIndex - 1);
      });
    }

    if (this.nextBtn) {
      this.nextBtn.addEventListener('click', () => {
        if (this.currentIndex < this.slides.length - 1) this.showPage(this.currentIndex + 1);
      });
    }

    const viewer = this.mainEl;
    let touchStartX = 0;
    let touchEndX = 0;
    if (viewer) {
      viewer.addEventListener('touchstart', (event) => {
        if (!event.touches || !event.touches[0]) return;
        touchStartX = event.touches[0].clientX;
      }, { passive: true });

      viewer.addEventListener('touchmove', (event) => {
        if (!event.touches || !event.touches[0]) return;
        touchEndX = event.touches[0].clientX;
      }, { passive: true });

      viewer.addEventListener('touchcancel', () => { touchStartX = 0; touchEndX = 0; }, { passive: true });
      viewer.addEventListener('touchend', () => {
        const delta = touchEndX - touchStartX;
        if (Math.abs(delta) < 40) return;
        if (delta < 0 && this.currentIndex < this.slides.length - 1) this.showPage(this.currentIndex + 1);
        if (delta > 0 && this.currentIndex > 0) this.showPage(this.currentIndex - 1);
      });

      if (typeof ResizeObserver !== 'undefined') {
        this.resizeObserver = new ResizeObserver(() => {
          this.scheduleFitRerender();
        });
        this.resizeObserver.observe(viewer);
      }
    }

    if (this.zoomInBtn) {
      this.zoomInBtn.onclick = (event) => {
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        this.reportZoomDebug('zoom-in onclick');
        this.zoomIn();
      };
    }
    if (this.zoomOutBtn) {
      this.zoomOutBtn.onclick = (event) => {
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        this.reportZoomDebug('zoom-out onclick');
        this.zoomOut();
      };
    }

    if (this.fullscreenBtn) {
      if (!document.fullscreenEnabled) {
        this.fullscreenBtn.disabled = true;
        this.fullscreenBtn.style.opacity = '0.5';
        this.fullscreenBtn.style.cursor = 'not-allowed';
      }
      this.fullscreenBtn.addEventListener('click', () => this.toggleFullscreen());
    }

    document.addEventListener('fullscreenchange', () => this.updateFullscreenUi());
    window.addEventListener('resize', () => this.scheduleFitRerender());
    window.addEventListener('keydown', (event) => {
      const tag = (event.target && event.target.tagName) ? event.target.tagName.toLowerCase() : '';
      const isTyping = tag === 'input' || tag === 'textarea' || (event.target && event.target.isContentEditable);
      if (isTyping) return;

      if (event.key === 'ArrowLeft') {
        if (this.currentIndex > 0) this.showPage(this.currentIndex - 1);
      } else if (event.key === 'ArrowRight') {
        if (this.currentIndex < this.slides.length - 1) this.showPage(this.currentIndex + 1);
      } else if (event.key === 'Home') {
        this.showPage(0);
      } else if (event.key === 'End') {
        this.showPage(Math.max(0, this.slides.length - 1));
      } else if (event.key === 'Escape') {
        if (document.fullscreenElement === this.viewerFrame) {
          document.exitFullscreen().catch(() => {});
        }
      }
    });
  }
}

window.PresentationViewer = PresentationViewer;
