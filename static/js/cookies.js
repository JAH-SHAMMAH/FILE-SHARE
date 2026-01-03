(function(){
  // Cookie consent manager
  const NAME = 'cookie_consent';
  const DEFAULT = { necessary: true, analytics: false, marketing: false };

  function readConsent(){
    try{
      const c = document.cookie.split('; ').find(r=>r.startsWith(NAME+'='));
      if(!c) return null;
      const val = decodeURIComponent(c.split('=')[1]||'');
      return JSON.parse(val);
    }catch(e){return null}
  }
  function writeConsent(obj){
    const v = encodeURIComponent(JSON.stringify(obj));
    const year = 60*60*24*365; // seconds
    document.cookie = `${NAME}=${v}; path=/; max-age=${year};` + (location.protocol === 'https:' ? ' Secure; SameSite=Lax' : ' SameSite=Lax');
  }

  function createBanner(){
    const existing = document.getElementById('cookie-consent-banner');
    if(existing) return existing;
    const banner = document.createElement('div');
    banner.id = 'cookie-consent-banner';
    banner.style.position = 'fixed';
    banner.style.left = '12px';
    banner.style.right = '12px';
    banner.style.bottom = '12px';
    banner.style.zIndex = 1500;
    banner.style.background = 'white';
    banner.style.border = '1px solid rgba(0,0,0,0.06)';
    banner.style.padding = '12px';
    banner.style.borderRadius = '10px';
    banner.style.boxShadow = '0 8px 30px rgba(10,10,20,0.08)';

    banner.innerHTML = `
      <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap">
        <div style="flex:1;min-width:240px">
          <strong>We use cookies</strong>
          <div class="muted" style="font-size:0.95em;margin-top:4px">We use essential cookies for site functionality. We also use analytics and marketing cookies to improve the site. You can accept all or manage your preferences.</div>
        </div>
        <div style="display:flex;gap:8px;align-items:center">
          <button id="cookie-manage" class="btn btn--ghost">Manage</button>
          <button id="cookie-reject" class="btn btn--ghost">Reject non-essential</button>
          <button id="cookie-accept" class="btn">Accept all</button>
        </div>
      </div>
    `;
    document.body.appendChild(banner);
    return banner;
  }

  function createModal(){
    if(document.getElementById('cookie-consent-modal')) return;
    const modal = document.createElement('div');
    modal.id = 'cookie-consent-modal';
    modal.style.position = 'fixed';
    modal.style.left = '0'; modal.style.top='0'; modal.style.right='0'; modal.style.bottom='0';
    modal.style.zIndex = 1600;
    modal.style.display = 'flex'; modal.style.alignItems='center'; modal.style.justifyContent='center';

    const panel = document.createElement('div');
    panel.style.width = 'min(740px,96%)';
    panel.style.background='white'; panel.style.borderRadius='10px'; panel.style.padding='18px'; panel.style.boxShadow='0 20px 60px rgba(0,0,0,0.12)';

    panel.innerHTML = `
      <h3>Cookie preferences</h3>
      <p class="muted">Manage which types of cookies you consent to.</p>
      <div style="display:flex;flex-direction:column;gap:12px;margin-top:8px">
        <label><input type="checkbox" id="cookie-necessary" disabled checked/> Essential (required)</label>
        <label><input type="checkbox" id="cookie-analytics"/> Analytics (site usage)</label>
        <label><input type="checkbox" id="cookie-marketing"/> Marketing (ads and tracking)</label>
      </div>
      <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
        <button id="cookie-modal-cancel" class="btn btn--ghost">Cancel</button>
        <button id="cookie-modal-save" class="btn">Save preferences</button>
      </div>
    `;
    modal.appendChild(panel);
    document.body.appendChild(modal);

    modal.addEventListener('click', (e)=>{ if(e.target===modal) modal.style.display='none'; });
  }

  function showBannerIfNeeded(){
    const c = readConsent();
    if(!c){
      const b = createBanner();
      document.getElementById('cookie-accept').addEventListener('click', ()=>{
        writeConsent({ necessary:true, analytics:true, marketing:true });
        b.style.display='none';
        runPostConsent();
        // notify server for compliance
        try{ fetch('/api/cookie_consent', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({consent: { necessary:true, analytics:true, marketing:true }}) }); }catch(e){}
      });
      document.getElementById('cookie-reject').addEventListener('click', ()=>{
        writeConsent({ necessary:true, analytics:false, marketing:false });
        b.style.display='none';
        try{ fetch('/api/cookie_consent', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({consent: { necessary:true, analytics:false, marketing:false }}) }); }catch(e){}
      });
      document.getElementById('cookie-manage').addEventListener('click', ()=>{
        createModal();
        const modal = document.getElementById('cookie-consent-modal');
        modal.style.display='flex';
        const cboxA = document.getElementById('cookie-analytics');
        const cboxM = document.getElementById('cookie-marketing');
        const existing = readConsent() || DEFAULT;
        cboxA.checked = !!existing.analytics;
        cboxM.checked = !!existing.marketing;
        document.getElementById('cookie-modal-save').onclick = ()=>{
          const obj = { necessary:true, analytics: cboxA.checked, marketing: cboxM.checked };
          writeConsent(obj);
          modal.style.display='none';
          const b = document.getElementById('cookie-consent-banner'); if(b) b.style.display='none';
          runPostConsent();
          try{ fetch('/api/cookie_consent', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({consent: obj}) }); }catch(e){}
        };
        document.getElementById('cookie-modal-cancel').onclick = ()=>{ modal.style.display='none'; };
      });
    } else {
      // consent exists — run post-consent actions
      runPostConsent();
    }
  }

  function runPostConsent(){
    const c = readConsent() || DEFAULT;
    if(c.analytics){
      // initialize analytics here (deferred until consent)
      // e.g., load gtag or other analytics scripts
      if(!window.__slideshare_analytics_loaded){
        window.__slideshare_analytics_loaded = true;
        // example: load Google Analytics (commented out)
        // const s = document.createElement('script'); s.src = 'https://www.googletagmanager.com/gtag/js?id=G-XXXX'; s.async=true; document.head.appendChild(s);
        // window.dataLayer = window.dataLayer || []; function gtag(){dataLayer.push(arguments);} gtag('js', new Date()); gtag('config', 'G-XXXX');
      }
    }
    // marketing can enable other third-party scripts
    if(c.marketing){
      // load marketing scripts conditionally
    }
  }

  // Expose helper to other scripts
  window.CookieConsent = {
    read: readConsent,
    save: writeConsent,
    showBanner: showBannerIfNeeded
  };

  // run on DOM ready
  if(document.readyState === 'loading') document.addEventListener('DOMContentLoaded', showBannerIfNeeded);
  else showBannerIfNeeded();
})();
