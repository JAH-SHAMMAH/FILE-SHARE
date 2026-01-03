document.addEventListener('DOMContentLoaded', function(){
  const launcher = document.getElementById('music-launcher');
  const panel = document.getElementById('music-panel');
  const closeBtn = document.getElementById('music-close');
  const loadBtn = document.getElementById('music-load');
  const clearBtn = document.getElementById('music-clear');
  const urlInput = document.getElementById('music-url');
  const embedHolder = document.getElementById('music-embed');
  const STORAGE_KEY = 'fileshare.music.url';

  // restore position and url from localStorage
  try{
    const savedUrl = localStorage.getItem(STORAGE_KEY);
    if(savedUrl){
      urlInput.value = savedUrl;
    }
  }catch(e){}

  function togglePanel(show){
    if(!panel) return;
    panel.style.display = show ? 'block' : 'none';
  }

  if(launcher){
    launcher.addEventListener('click', ()=> togglePanel(true));
  }
  if(closeBtn){
    closeBtn.addEventListener('click', ()=> togglePanel(false));
  }

  function makeSpotifyEmbed(url){
    // Accept track/playlist/album URLs and convert to embed
    try{
      const u = new URL(url);
      if(u.hostname.includes('spotify.com')){
        const parts = u.pathname.split('/').filter(Boolean);
        if(parts.length >= 2){
          const kind = parts[0];
          const id = parts[1];
          const src = `https://open.spotify.com/embed/${kind}/${id}`;
          return `<div style="display:flex;flex-direction:column;gap:8px;"><iframe src="${src}" width="100%" height="120" frameborder="0" allow="autoplay; encrypted-media; clipboard-write"></iframe><div class="muted" style="font-size:12px">Full playback requires Spotify login — <a href="${url}" target="_blank" rel="noopener">Open in Spotify</a></div></div>`;
        }
      }
    }catch(e){}
    return null;
  }

  function makeAppleEmbed(url){
    // Apple Music embed uses a player url pattern; for simplicity embed via music.apple.com iframe
    try{
      const u = new URL(url);
      if(u.hostname.includes('music.apple.com')){
        const src = url.replace('music.apple.com', 'embed.music.apple.com');
        return `<div style="display:flex;flex-direction:column;gap:8px;"><iframe src="${src}" width="100%" height="150" frameborder="0" allow="autoplay; encrypted-media"></iframe><div class="muted" style="font-size:12px">Full playback requires Apple Music subscription — <a href="${url}" target="_blank" rel="noopener">Open in Apple Music</a></div></div>`;
      }
    }catch(e){}
    return null;
  }

  function makeAudioEmbed(url){
    // direct audio files (mp3/m4a/ogg/wav) — use HTML5 audio element for full playback
    try{
      const audioExt = url.split('?')[0].split('.').pop().toLowerCase();
      if(['mp3','m4a','ogg','wav','flac'].includes(audioExt)){
        return `<audio controls style="width:100%;" src="${url}">Your browser does not support the audio element.</audio>`;
      }
    }catch(e){}
    return null;
  }

  function loadUrl(){
    const v = (urlInput.value || '').trim();
    if(!v){ embedHolder.innerHTML = ''; return; }
    // prefer direct audio embeds first for full playback, then Spotify/Apple
    let html = makeAudioEmbed(v) || makeSpotifyEmbed(v) || makeAppleEmbed(v);
    if(!html){
      // fallback: show link
      html = `<div class="muted">Unable to embed this URL. <a href="${v}" target="_blank" rel="noopener">Open in new tab</a></div>`;
    }
    embedHolder.innerHTML = html;
    togglePanel(true);
    try{ localStorage.setItem(STORAGE_KEY, v); }catch(e){}
  }

  if(loadBtn) loadBtn.addEventListener('click', loadUrl);
  if(clearBtn){
    clearBtn.addEventListener('click', ()=>{
      urlInput.value = '';
      embedHolder.innerHTML = '';
      try{ localStorage.removeItem(STORAGE_KEY); }catch(e){}
    });
  }

  // Make panel draggable
  (function makeDraggable(){
    if(!panel) return;
    panel.style.position = panel.style.position || 'fixed';
    panel.style.cursor = 'grab';
    let dragging = false;
    let startX = 0, startY = 0, origX = 0, origY = 0;

    panel.addEventListener('mousedown', function(e){
      // only start drag when clicking the header area
      const head = panel.querySelector('.music-panel__head');
      if(head && !head.contains(e.target)) return;
      dragging = true;
      panel.style.cursor = 'grabbing';
      startX = e.clientX;
      startY = e.clientY;
      const rect = panel.getBoundingClientRect();
      origX = rect.left;
      origY = rect.top;
      e.preventDefault();
    });

    document.addEventListener('mousemove', function(e){
      if(!dragging) return;
      const dx = e.clientX - startX;
      const dy = e.clientY - startY;
      let nx = origX + dx;
      let ny = origY + dy;
      // constrain to viewport
      const vw = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
      const vh = Math.max(document.documentElement.clientHeight || 0, window.innerHeight || 0);
      const rect = panel.getBoundingClientRect();
      nx = Math.min(Math.max(8, nx), vw - rect.width - 8);
      ny = Math.min(Math.max(8, ny), vh - rect.height - 8);
      panel.style.left = nx + 'px';
      panel.style.top = ny + 'px';
      panel.style.right = 'auto';
      panel.style.bottom = 'auto';
    });

    document.addEventListener('mouseup', function(){
      if(!dragging) return;
      dragging = false;
      panel.style.cursor = 'grab';
      // persist position
      try{
        const left = panel.style.left || '';
        const top = panel.style.top || '';
        localStorage.setItem('fileshare.music.pos', JSON.stringify({ left, top }));
      }catch(e){}
    });

    // restore position if present
    try{
      const pos = localStorage.getItem('fileshare.music.pos');
      if(pos){
        const p = JSON.parse(pos);
        if(p.left) panel.style.left = p.left;
        if(p.top) panel.style.top = p.top;
        panel.style.right = 'auto';
        panel.style.bottom = 'auto';
      }
    }catch(e){}
  })();
});

