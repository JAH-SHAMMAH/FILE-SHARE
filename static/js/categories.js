// Lazy-load categories if /api/categories is available
(function(){
  async function loadCategories(){
    try{
      const res = await fetch('/api/categories');
      if(!res.ok) return;
      const data = await res.json();
      if(!Array.isArray(data)) return;
      const container = document.getElementById('category-scroll');
      if(!container) return;
      // preserve current category from URL
      const params = new URLSearchParams(window.location.search);
      const current = params.get('category') || '';
      container.innerHTML = '';
      data.forEach(cat => {
        const a = document.createElement('a');
        a.setAttribute('role','listitem');
        a.className = 'category-chip' + (cat === current ? ' active' : '');
        a.href = '/search?category=' + encodeURIComponent(cat);
        a.dataset.cat = cat;
        a.textContent = cat;
        container.appendChild(a);
      });
      attachControls(container);
      scrollActiveIntoView(container);
    }catch(e){
      // ignore - backend may not expose /api/categories
    }
  }

  function scrollActiveIntoView(container){
    // find active chip and center it
    const active = container.querySelector('.category-chip.active');
    if(active){
      try{
        active.scrollIntoView({ behavior: 'smooth', inline: 'center', block: 'nearest' });
      }catch(e){
        // fallback
        const left = active.offsetLeft - (container.clientWidth/2) + (active.clientWidth/2);
        container.scrollTo({ left, behavior: 'smooth' });
      }
    }
  }

  function attachControls(container){
    // don't attach twice
    if(container._controlsAttached) return;
    container._controlsAttached = true;

    // create prev/next buttons
    const prev = document.createElement('button');
    prev.className = 'category-scroll-btn prev';
    prev.type = 'button';
    prev.title = 'Scroll left';
    prev.innerHTML = '◀';

    const next = document.createElement('button');
    next.className = 'category-scroll-btn next';
    next.type = 'button';
    next.title = 'Scroll right';
    next.innerHTML = '▶';

    // place buttons before/after container
    container.parentNode.insertBefore(prev, container);
    container.parentNode.insertBefore(next, container.nextSibling);

    function scrollBy(amount){
      container.scrollBy({ left: amount, behavior: 'smooth' });
    }

    prev.addEventListener('click', ()=> scrollBy(-240));
    next.addEventListener('click', ()=> scrollBy(240));

    // keyboard support: left/right arrows when focus is inside page
    window.addEventListener('keydown', (ev)=>{
      if(document.activeElement && (document.activeElement.tagName === 'INPUT' || document.activeElement.tagName === 'TEXTAREA')) return;
      if(ev.key === 'ArrowLeft'){
        ev.preventDefault(); scrollBy(-240);
      } else if(ev.key === 'ArrowRight'){
        ev.preventDefault(); scrollBy(240);
      }
    });
  }

  // run after DOMContentLoaded
  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', loadCategories);
  } else {
    loadCategories();
  }
})();
