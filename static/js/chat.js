document.addEventListener('DOMContentLoaded', function(){
  let socket = null;
  let meId = null;
  let otherId = null;
  let otherUsername = null;
  const CHAT_THEME_KEY = 'slideshare_chat_theme';

  function qs(sel, el) { return (el||document).querySelector(sel); }
  function qsa(sel, el) { return (el||document).querySelectorAll(sel); }

  function openModal(userId, username){
    otherId = userId || null;
    otherUsername = username || null;
    const displayName = username || 'User';
    qs('#chat-username').textContent = displayName;
    // update avatar initials
    const avatar = qs('#chat-avatar');
    if (avatar){
      const initials = displayName.trim().slice(0, 2).toUpperCase();
      avatar.textContent = initials || '??';
    }
    qs('#chat-messages').innerHTML = '';
    // if recipient input exists, populate
    const rec = qs('#chat-recipient');
    if (rec){
      rec.value = otherId ? String(otherId) : '';
      rec.style.display = otherId ? 'none' : 'block';
    }
    // populate recipient selector + list for followees/mutuals when opening without a specific user
    const sel = qs('#chat-recipient-select');
    const mutualsOnly = qs('#chat-mutuals-only');
    const recList = qs('#chat-recipient-list');
    if (sel){
      sel.innerHTML = '';
      sel.style.display = otherId ? 'none' : 'block';
      if (recList){ recList.innerHTML = ''; }
      (async ()=>{
        try{
          const res = await fetch('/api/contacts/following');
          const following = res.ok ? await res.json() : [];
          let mutuals = [];
          try{ const r2 = await fetch('/api/contacts/mutuals'); mutuals = r2.ok ? await r2.json() : []; }catch(e){}
          const list = (mutualsOnly && mutualsOnly.checked) ? mutuals : following;
          const placeholder = document.createElement('option'); placeholder.value=''; placeholder.textContent='Select a user to message'; sel.appendChild(placeholder);
          list.forEach(u=>{ const o = document.createElement('option'); o.value = u.id; o.textContent = u.username + ' ('+u.id+')'; sel.appendChild(o); });

          // also render a modern clickable list of followees
          if (recList){
            recList.innerHTML = '';
            list.forEach(u => {
              const item = document.createElement('div');
              item.className = 'chat-recipient-item';
              item.dataset.userId = u.id;
              item.dataset.username = u.username || '';

              const meta = document.createElement('div');
              meta.className = 'chat-recipient-meta';
              const av = document.createElement('div');
              av.className = 'chat-recipient-avatar';
              const initials = (u.username || 'U').trim().slice(0,2).toUpperCase();
              av.textContent = initials || 'U';
              const name = document.createElement('div');
              name.className = 'chat-recipient-name';
              name.textContent = u.username || ('User #' + u.id);
              const idLabel = document.createElement('div');
              idLabel.className = 'chat-recipient-id';
              idLabel.textContent = '#' + u.id;
              meta.appendChild(av);
              const nameWrap = document.createElement('div');
              nameWrap.appendChild(name);
              nameWrap.appendChild(idLabel);
              meta.appendChild(nameWrap);
              item.appendChild(meta);

              item.addEventListener('click', () => {
                startDirectChat(u.id, u.username || ('User #' + u.id));
              });

              recList.appendChild(item);
            });
          }
        }catch(e){ console.warn('load contacts failed', e); }
      })();
    }
    // hide the recipient selector block entirely when we already know who to chat with
    const recBlock = qs('#chat-recipient-block');
    if (recBlock){
      recBlock.style.display = otherId ? 'none' : 'block';
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

  function startDirectChat(userId, usernameLabel){
    otherId = userId;
    const recInput = qs('#chat-recipient');
    const sel = qs('#chat-recipient-select');
    if (recInput) recInput.style.display = 'none';
    if (sel) sel.style.display = 'none';
    const headerName = qs('#chat-username');
    if (headerName) headerName.textContent = usernameLabel || ('User #' + userId);
    loadHistory();
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

    // header with avatar, name, and role badge
    const header = document.createElement('div');
    header.className = 'chat-msg__header';
    header.style.display = 'flex';
    header.style.alignItems = 'center';
    header.style.gap = '6px';
    header.style.marginBottom = '4px';

    const avatarWrap = document.createElement('div');
    avatarWrap.className = 'contact-avatar';
    avatarWrap.style.width = '28px';
    avatarWrap.style.height = '28px';
    avatarWrap.style.fontSize = '13px';

    const nameWrap = document.createElement('div');
    nameWrap.style.display = 'flex';
    nameWrap.style.alignItems = 'center';
    nameWrap.style.gap = '4px';

    // derive a display name and initials from payload
    const rawName = m.full_name || m.username || (isMe ? 'You' : (otherUsername || 'User'));
    const initials = (rawName || 'U').trim().slice(0, 2).toUpperCase() || 'U';

    // normalize role once so avatar + badge can share colors (default passerby)
    const role = (m.site_role || 'passerby').toString().toLowerCase();

    if (m.avatar){
      const img = document.createElement('img');
      img.src = '/download/' + m.avatar + '?inline=1';
      img.alt = rawName;
      img.style.width = '100%';
      img.style.height = '100%';
      img.style.borderRadius = '999px';
      img.onerror = function(){
        this.style.display = 'none';
        avatarWrap.textContent = initials;
        if (role === 'teacher') avatarWrap.style.color = '#1d4ed8';
        else if (role === 'student') avatarWrap.style.color = '#f59e0b';
        else if (role === 'individual') avatarWrap.style.color = '#dc2626';
        else if (role === 'passerby') avatarWrap.style.color = '#111827';
      };
      avatarWrap.appendChild(img);
    } else {
      // No uploaded avatar: use initials, and tint text to match role badge
      avatarWrap.textContent = initials;
      if (role === 'teacher') avatarWrap.style.color = '#1d4ed8';
      else if (role === 'student') avatarWrap.style.color = '#f59e0b';
      else if (role === 'individual') avatarWrap.style.color = '#dc2626';
      else if (role === 'passerby') avatarWrap.style.color = '#111827';
    }

    const nameLabel = document.createElement('span');
    nameLabel.textContent = rawName;
    nameLabel.style.fontWeight = '600';
    nameLabel.style.fontSize = '13px';
    nameWrap.appendChild(nameLabel);

    // role badge (teacher, student, individual, passerby) — always show
    const badge = document.createElement('span');
    badge.className = 'role-badge';
    if (role === 'teacher') badge.className += ' role-badge--teacher';
    else if (role === 'student') badge.className += ' role-badge--student';
    else if (role === 'individual') badge.className += ' role-badge--individual';
    else if (role === 'passerby') badge.className += ' role-badge--passerby';
    badge.textContent = '★';
    badge.title = role.charAt(0).toUpperCase() + role.slice(1);
    nameWrap.appendChild(badge);

    header.appendChild(avatarWrap);
    header.appendChild(nameWrap);

    const text = document.createElement('div');
    text.className = 'chat-msg__text';
    text.textContent = m.content;
    // if there's an attached file, show a link or preview (use thumbnail if available)
    if (m.file){
      const link = document.createElement('a');
      link.href = m.file;
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
      // show filename as link text
      try{
        const urlParts = m.file.split('/');
        link.textContent = decodeURIComponent(urlParts[urlParts.length-1]) || 'attachment';
      }catch(e){ link.textContent = 'attachment'; }
      // if thumbnail exists, show it; otherwise inline image preview when possible
      if (m.thumbnail){
        const img = document.createElement('img');
        img.src = m.thumbnail;
        img.style.maxWidth = '200px';
        img.style.display = 'block';
        text.appendChild(img);
      } else if (/\.(png$|jpe?g$|gif$|webp$)/i.test(link.textContent)){
        const img = document.createElement('img');
        img.src = m.file;
        img.style.maxWidth = '200px';
        img.style.display = 'block';
        text.appendChild(img);
      }
      text.appendChild(link);
    }
    el.appendChild(header);
    el.appendChild(text);
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
        appendMessage({
          id: data.id,
          from: data.from,
          to: data.to,
          content: data.content,
          file: data.file,
          thumbnail: data.thumbnail,
          created_at: data.created_at,
          full_name: data.full_name,
          username: data.username,
          avatar: data.avatar,
          site_role: data.site_role
        });
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

  // chat-only light/dark theme toggle
  function applyChatTheme(mode){
    const panel = qs('#chat-modal .chat-modal__panel');
    if (!panel) return;
    if (mode === 'dark') {
      panel.classList.add('chat-theme-dark');
    } else {
      panel.classList.remove('chat-theme-dark');
    }
    const btn = qs('#chat-theme-toggle');
    if (btn) btn.textContent = mode === 'dark' ? '☀️' : '🌙';
  }

  (function initChatTheme(){
    try{
      const saved = localStorage.getItem(CHAT_THEME_KEY);
      if (saved === 'dark' || saved === 'light') {
        applyChatTheme(saved);
      } else {
        applyChatTheme('light');
      }
    }catch(e){ applyChatTheme('light'); }
    const toggle = qs('#chat-theme-toggle');
    if (toggle){
      toggle.addEventListener('click', function(){
        const panel = qs('#chat-modal .chat-modal__panel');
        const isDark = panel && panel.classList.contains('chat-theme-dark');
        const next = isDark ? 'light' : 'dark';
        try{ localStorage.setItem(CHAT_THEME_KEY, next); }catch(e){}
        applyChatTheme(next);
      });
    }
  })();

  document.addEventListener('click', function(ev){
    const btn = ev.target.closest('.btn-message');
    if (!btn) return;
    const uid = parseInt(btn.getAttribute('data-user-id'));
    const uname = btn.getAttribute('data-username') || '';
    // propagate bypass flag (used for Contact Owner buttons) into the chat modal
    try{ const cm = qs('#chat-modal'); if (cm) cm.dataset.bypass = btn.getAttribute('data-bypass') || ''; }catch(e){}
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
    const fileInput = qs('#chat-file');
    const file = fileInput && fileInput.files && fileInput.files[0] ? fileInput.files[0] : null;
    const bypass = qs('#chat-modal') && qs('#chat-modal').dataset && qs('#chat-modal').dataset.bypass === '1';
    const apiUrl = '/api/messages/' + otherId + (bypass ? '?bypass=1' : '');

    // For contact-owner flows (bypass) always use the HTTP API so
    // messages are stored and notifications are created, even if the
    // WebSocket is connected.
    if (bypass) {
      const body = file ? (()=>{ const fd = new FormData(); fd.append('content', val); fd.append('file', file); return fd; })() : JSON.stringify({ content: val });
      const opts = file ? { method: 'POST', body } : { method: 'POST', headers: { 'Content-Type':'application/json' }, body };
      fetch(apiUrl, opts).then(async (r) => {
        if (r.ok) {
          await loadHistory();
          if (fileInput) fileInput.value = '';
          input.value = '';
        }
      });
      return;
    }

    if (file) {
      // send via multipart POST for regular chats as well
      const fd = new FormData();
      fd.append('content', val);
      fd.append('file', file);
      fetch(apiUrl, { method: 'POST', body: fd }).then(async (r) => { if (r.ok) { await loadHistory(); fileInput.value = ''; input.value = ''; } });
      return;
    }
    // For regular text-only chats, always use the HTTP API so
    // messages are stored consistently and notifications are created.
    fetch(apiUrl, {
      method: 'POST',
      headers: { 'Content-Type':'application/json' },
      body: JSON.stringify({ content: val })
    }).then(async (r) => {
      if (r.ok) {
        await loadHistory();
        input.value = '';
      }
    });
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

  // when a user picks from the select, set otherId and load history
  qs('#chat-recipient-select')?.addEventListener('change', function(ev){
    const v = ev.target.value;
    if (!v) return;
    const idNum = parseInt(v);
    const label = ev.target.options[ev.target.selectedIndex].textContent || '';
    startDirectChat(idNum, label);
  });

  // when mutuals checkbox changes, refresh the select
  qs('#chat-mutuals-only')?.addEventListener('change', function(){
    const sel2 = qs('#chat-recipient-select');
    const recList2 = qs('#chat-recipient-list');
    if (!sel2 || sel2.style.display === 'none') return;
    sel2.innerHTML = '';
    if (recList2) recList2.innerHTML = '';
    (async ()=>{
      try{
        const res = await fetch(this.checked ? '/api/contacts/mutuals' : '/api/contacts/following');
        const list = res.ok ? await res.json() : [];
        const placeholder = document.createElement('option'); placeholder.value=''; placeholder.textContent='Select a user to message'; sel2.appendChild(placeholder);
        list.forEach(u=>{ const o = document.createElement('option'); o.value = u.id; o.textContent = u.username + ' ('+u.id+')'; sel2.appendChild(o); });
        if (recList2){
          list.forEach(u => {
            const item = document.createElement('div');
            item.className = 'chat-recipient-item';
            item.dataset.userId = u.id;
            item.dataset.username = u.username || '';

            const meta = document.createElement('div');
            meta.className = 'chat-recipient-meta';
            const av = document.createElement('div');
            av.className = 'chat-recipient-avatar';
            const initials = (u.username || 'U').trim().slice(0,2).toUpperCase();
            av.textContent = initials || 'U';
            const name = document.createElement('div');
            name.className = 'chat-recipient-name';
            name.textContent = u.username || ('User #' + u.id);
            const idLabel = document.createElement('div');
            idLabel.className = 'chat-recipient-id';
            idLabel.textContent = '#' + u.id;
            meta.appendChild(av);
            const nameWrap = document.createElement('div');
            nameWrap.appendChild(name);
            nameWrap.appendChild(idLabel);
            meta.appendChild(nameWrap);
            item.appendChild(meta);

            item.addEventListener('click', () => {
              startDirectChat(u.id, u.username || ('User #' + u.id));
            });

            recList2.appendChild(item);
          });
        }
      }catch(e){ }
    })();
  });

  // utility: update presence dot for user
  function updatePresenceDot(userId, online){
    qsa('.presence-dot[data-user-id="' + userId + '"]').forEach(el=>{ el.style.background = online ? '#10B981' : '#BDBDBD'; });
  }

  // fetch unread counts, presence, and refresh open chat history
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
          let totalUnread = 0;
          Object.keys(counts).forEach(k=>{
            const id = k;
            const count = counts[k] || 0;
            totalUnread += count;
            qsa('.msg-badge[data-user-id="' + id + '"]').forEach(el=>{
              el.textContent = count > 0 ? count : '';
              el.style.fontWeight = '700';
              el.style.color = count > 0 ? '#fff' : '';
              el.style.background = count > 0 ? '#ff3b30' : 'transparent';
              el.style.borderRadius = count > 0 ? '10px' : '';
              el.style.padding = count > 0 ? '2px 6px' : '';
            });
          });
          const navBadge = document.getElementById('messages-badge');
          if (navBadge){
            if (totalUnread > 0){
              navBadge.style.display = 'inline-block';
              navBadge.textContent = String(totalUnread);
            } else {
              navBadge.style.display = 'none';
            }
          }
        }
      }
    }catch(e){ /* ignore */ }

    // if a chat is currently open with another user, periodically
    // refresh its history so new messages from the other side show up
    // even if a websocket push is missed.
    try{
      const modal = qs('#chat-modal');
      if (modal && modal.style.display === 'block' && otherId){
        await loadHistory();
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

});
