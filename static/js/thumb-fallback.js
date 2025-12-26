// Probe slide thumbnails and gracefully fall back to cover or placeholder.
(function(){
  function isSlideUrl(url){
    return /\/presentations\/\d+\/slide\/0$/.test(url);
  }

  async function probe(url){
    try{
      // try HEAD first
      let res = await fetch(url, { method: 'HEAD' });
      if (res && res.ok) return true;
      // some servers don't support HEAD; try GET but only retrieve minimal
      res = await fetch(url, { method: 'GET' });
      return res && res.ok;
    }catch(e){
      return false;
    }
  }

  function replaceImgWithFallback(img, fallback){
    if (!fallback) fallback = img.getAttribute('data-placeholder') || '/static/slide-placeholder.svg';
    if (img.src === fallback) return;
    img.src = fallback;
  }

  function replaceIframeWithFallback(iframe, fallback){
    const wrapper = document.createElement('div');
    wrapper.className = 'thumb-fallback-wrap';
    const img = document.createElement('img');
    img.alt = iframe.getAttribute('title') || 'preview';
    img.style.width = '100%';
    img.style.height = '100%';
    img.style.objectFit = 'cover';
    img.src = fallback || '/static/slide-placeholder.svg';
    wrapper.appendChild(img);
    iframe.replaceWith(wrapper);
  }

  async function init(){
    // Images using slide thumbnails
    const imgs = Array.from(document.querySelectorAll('img[src*="/presentations/"]'));
    for (const img of imgs){
      const src = img.getAttribute('src') || '';
      if (!isSlideUrl(src)) continue;
      // if image already errored, skip (onerror handler may have run)
      const cover = img.getAttribute('data-cover') || img.getAttribute('data-cover-url');
      const placeholder = img.getAttribute('data-placeholder') || (window.SLIDE_PLACEHOLDER || '/static/slide-placeholder.svg');
      // probe
      const ok = await probe(src);
      if (!ok){
        if (cover){
          img.src = cover;
        }else{
          img.src = placeholder;
        }
      }
      // ensure we still have an onerror fallback
      img.onerror = function(){
        if (cover) this.src = cover; else this.src = placeholder;
      };
    }

    // Iframes (PDF previews) probe and fallback to cover/placeholder
    const iframes = Array.from(document.querySelectorAll('iframe.thumb-frame'));
    for (const iframe of iframes){
      const src = iframe.getAttribute('src') || iframe.getAttribute('data-pdf');
      if (!src) continue;
      const cover = iframe.getAttribute('data-cover') || iframe.getAttribute('data-cover-url');
      const ok = await probe(src);
      if (!ok){
        replaceIframeWithFallback(iframe, cover || (window.SLIDE_PLACEHOLDER || '/static/slide-placeholder.svg'));
      }
    }
  }

  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
