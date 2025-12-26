(function(){
  let socket = null;
  let meId = null;
  let otherId = null;
  let otherUsername = null;

  function qs(sel, el) { return (el||document).querySelector(sel); }
  function qsa(sel, el) { return (el||document).querySelectorAll(sel); }

  function openModal(userId, username){
    otherId = userId || null;
    otherUsername = username || null;
    qs('#chat-username').textContent = username || 'User';
    qs('#chat-messages').innerHTML = '';
    // if recipient input exists, populate
    const rec = qs('#chat-recipient');
    if (rec){
      rec.value = otherId ? String(otherId) : '';
      rec.style.display = otherId ? 'none' : 'block';
    }
    qs('#chat-modal').style.display = 'block';
    connectSocket();
    loadHistory();
    qs('#chat-input').focus();
  }

  function closeModal(){
    qs('#chat-modal').style.display = 'none';
    disconnectSocket();
    otherId = null;
    otherUsername = null;
  }

  async function loadHistory(){
    if (!otherId) return;
    try{
      const res = await fetch('/api/messages/' + otherId);
      if (!res.ok) return;
      const msgs = await res.json();
      const container = qs('#chat-messages');
      container.innerHTML = '';
      msgs.reverse().forEach(m => appendMessage(m));
      container.scrollTop = container.scrollHeight;
    }catch(e){ console.warn('loadHistory error', e); }
  }

  function appendMessage(m){
    const container = qs('#chat-messages');
    const el = document.createElement('div');
    // decide if message is from me or other: compare to api /api/me if available
    const myIdEl = qs('[data-my-id]');
    let myId = null;
    if (myIdEl) myId = parseInt(myIdEl.getAttribute('data-my-id'));
    const isMe = myId ? (m.from === myId) : (m.from !== otherId);
    el.className = 'chat-msg ' + (isMe ? 'me' : 'other');
    const text = document.createElement('div');
    text.className = 'chat-msg__text';
    text.textContent = m.content;
    const meta = document.createElement('div');
    meta.className = 'chat-msg__meta';
    meta.textContent = new Date(m.created_at).toLocaleString();
    el.appendChild(text);
    el.appendChild(meta);
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
  }

  function connectSocket(){
    if (!otherId) return;
    // build WS url
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const url = proto + '://' + location.host + '/ws/chat/' + otherId;
    try{
      socket = new WebSocket(url);
    }catch(e){ console.warn('ws conn fail', e); socket = null; return; }
    socket.addEventListener('open', () => {
      console.log('chat socket open');
    });
    socket.addEventListener('message', (ev) => {
      let data;
      try{ data = JSON.parse(ev.data); }catch(e){ return; }
      if (data.type === 'message'){
        appendMessage({ id: data.id, from: data.from, to: data.to, content: data.content, created_at: data.created_at });
        // if the message is for otherId and from otherId, mark badge cleared
        if (data.to && data.to === parseInt(qs('[data-my-id]')?.getAttribute('data-my-id'))) {
          // do nothing
        }
      } else if (data.type === 'presence'){
        // optional: show presence
        updatePresenceDot(data.user_id, data.online);
      }
    });
    socket.addEventListener('close', ()=>{ console.log('chat socket closed'); socket = null; });
  }

  function disconnectSocket(){
    if (socket){ try{ socket.close(); }catch(e){} socket = null; }
  }

  document.addEventListener('click', function(ev){
    const btn = ev.target.closest('.btn-message');
    if (!btn) return;
    const uid = parseInt(btn.getAttribute('data-user-id'));
    const uname = btn.getAttribute('data-username') || '';
    openModal(uid, uname);
  }, false);

  // contact handling moved to main.js to present a dedicated contact modal

  // launcher button (global)
  document.addEventListener('click', function(ev){
    const launch = ev.target.closest('#chat-launcher');
    if (launch){
      openModal(null, '');
    }
  }, false);

  qs('#chat-close')?.addEventListener('click', closeModal);
  qs('#chat-modal')?.addEventListener('click', function(ev){ if (ev.target.classList.contains('chat-modal__backdrop')) closeModal(); });

  // signin modal close
  qs('#signin-close')?.addEventListener('click', function(){ qs('#signin-modal').style.display = 'none'; });
  qs('#signin-modal')?.addEventListener('click', function(ev){ if (ev.target.classList.contains('chat-modal__backdrop')) qs('#signin-modal').style.display = 'none'; });

  qs('#chat-form')?.addEventListener('submit', function(ev){
    ev.preventDefault();
    const input = qs('#chat-input');
    const val = input.value.trim();
    if (!val || !otherId) return;
    const payload = { action: 'message', to: otherId, content: val };
    if (socket && socket.readyState === WebSocket.OPEN){
      socket.send(JSON.stringify(payload));
    } else {
      // fallback: POST to API? For now, try to POST message to same endpoint as ws persistence
      fetch('/api/messages/' + otherId, { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({content: val}) }).then(()=>{ loadHistory(); });
    }
    input.value = '';
  });

  // support submitting when recipient input supplied and otherId not set
  qs('#chat-recipient')?.addEventListener('change', function(ev){
    const v = ev.target.value.trim();
    if (!v) return;
    // accept usernames or ids
    (async ()=>{
      const idCandidate = parseInt(v);
      if (!isNaN(idCandidate)){
        otherId = idCandidate;
        ev.target.style.display = 'none';
        qs('#chat-username').textContent = '';
        loadHistory();
        return;
      }
      // try resolve username
      try{
        const res = await fetch('/api/resolve-username?username=' + encodeURIComponent(v));
        if (!res.ok) return;
        const d = await res.json();
        otherId = d.id;
        ev.target.style.display = 'none';
        qs('#chat-username').textContent = d.username || '';
        loadHistory();
      }catch(e){ console.warn('resolve failed', e); }
    })();
  });

  // utility: update presence dot for user
  function updatePresenceDot(userId, online){
    qsa('.presence-dot[data-user-id="' + userId + '"]').forEach(el=>{ el.style.background = online ? '#10B981' : '#BDBDBD'; });
  }

  // fetch unread counts and presence for visible badges
  (async function pollBadges(){
    try{
      const me = await fetch('/api/me');
      if (me.ok){
        const meJson = await me.json();
        // attach my id on body for client-side
        document.body.setAttribute('data-my-id', meJson.id);
        // fetch unread counts
        const res = await fetch('/api/messages/unread_counts');
        if (res.ok){
          const counts = await res.json();
          Object.keys(counts).forEach(k=>{
            const id = k;
            qsa('.msg-badge[data-user-id="' + id + '"]').forEach(el=>{ el.textContent = counts[k] > 0 ? counts[k] : ''; el.style.fontWeight = '700'; el.style.color = counts[k] > 0 ? '#fff' : ''; el.style.background = counts[k] > 0 ? '#ff3b30' : 'transparent'; el.style.borderRadius = counts[k] > 0 ? '10px' : ''; el.style.padding = counts[k] > 0 ? '2px 6px' : ''; });
          });
        }
      }
    }catch(e){ /* ignore */ }
    // presence: check visible users
    qsa('.presence-dot').forEach(async el=>{
      const uid = el.getAttribute('data-user-id');
      try{
        const r = await fetch('/api/online/' + uid);
        if (r.ok){
          const j = await r.json();
          el.style.background = j.online ? '#10B981' : '#BDBDBD';
        }
      }catch(e){ }
    });
    setTimeout(pollBadges, 10000);
  })();

})();
