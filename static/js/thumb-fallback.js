document.addEventListener('DOMContentLoaded', function(){
  // Replace broken image thumbnails with placeholder images when available
  try{
    const imgs = Array.from(document.querySelectorAll('img[data-placeholder], img[data-cover]'));
    imgs.forEach(img => {
      // if src missing but data-cover present, use it
      if((!img.src || img.src==='') && img.dataset.cover){
        img.src = img.dataset.cover;
      }
      // attach onerror to swap to placeholder
      const placeholder = img.dataset.placeholder;
      img.addEventListener('error', function(){
        if(placeholder && img.src !== placeholder){
          img.src = placeholder;
        }
      }, { once: true });
    });

    // For presentation thumbs that may be loaded dynamically, use delegated error handling
    document.addEventListener('error', function(ev){
      const target = ev.target;
      if(target && target.tagName === 'IMG'){
        const pl = target.dataset && target.dataset.placeholder;
        if(pl && target.src !== pl){ target.src = pl; }
      }
    }, true);

    // If PDF iframes fail to load cover, replace with placeholder image element
    const iframes = Array.from(document.querySelectorAll('iframe.thumb-frame'));
    iframes.forEach(fr => {
      fr.addEventListener('error', function(){
        const parent = fr.parentElement;
        const placeholder = parent && parent.querySelector('img[data-placeholder]');
        if(placeholder){
          // replace iframe with img
          const img = document.createElement('img');
          img.src = placeholder.dataset.placeholder || (placeholder.dataset.cover || '');
          img.alt = placeholder.alt || '';
          img.className = 'thumb-frame-fallback';
          parent.replaceChild(img, fr);
        }
      }, { once: true });
    });
  }catch(e){console.warn('thumb-fallback init failed', e);} 
});
