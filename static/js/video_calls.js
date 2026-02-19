document.addEventListener('DOMContentLoaded', () => {
  const currentUserEl = document.getElementById('current-user');
  const myId = currentUserEl ? parseInt(currentUserEl.getAttribute('data-my-id') || '') : null;
  if (!myId) return;

  const toastContainer = document.getElementById('toast-container');
  const showToast = (msg) => {
    if (!toastContainer) return alert(msg);
    const item = document.createElement('div');
    item.textContent = msg;
    item.style.background = '#111827';
    item.style.color = '#fff';
    item.style.padding = '8px 12px';
    item.style.borderRadius = '10px';
    item.style.fontSize = '13px';
    item.style.boxShadow = '0 8px 16px rgba(0,0,0,0.2)';
    item.style.pointerEvents = 'auto';
    toastContainer.appendChild(item);
    setTimeout(() => item.remove(), 3200);
  };

  const wsProtocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const wsUrl = `${wsProtocol}://${location.host}/ws/video`;
  let iceServers = null;

  const fetchIceServers = async () => {
    try {
      const res = await fetch('/api/video/config');
      if (res.ok) {
        const data = await res.json();
        if (data && data.iceServers) return data.iceServers;
      }
    } catch (err) {
      console.warn('ICE fetch failed', err);
    }
    return [{ urls: ['stun:stun.l.google.com:19302'] }];
  };

  const signaling = {
    ws: null,
    openPromise: null,
    async connect() {
      if (this.ws && this.ws.readyState === 1) return;
      if (this.openPromise) return this.openPromise;
      this.openPromise = new Promise((resolve, reject) => {
        const ws = new WebSocket(wsUrl);
        this.ws = ws;
        ws.onopen = () => {
          resolve();
          this.openPromise = null;
        };
        ws.onerror = (err) => {
          reject(err);
          this.openPromise = null;
        };
        ws.onmessage = (event) => {
          let data = null;
          try { data = JSON.parse(event.data); } catch (e) { return; }
          handleSignal(data);
        };
        ws.onclose = () => {
          this.ws = null;
          this.openPromise = null;
        };
      });
      return this.openPromise;
    },
    async send(payload) {
      await this.connect();
      if (!this.ws || this.ws.readyState !== 1) return;
      this.ws.send(JSON.stringify(payload));
    }
  };

  const meeting = {
    spaceId: null,
    title: null,
    localStream: null,
    screenStream: null,
    peers: new Map(),
    remoteStreams: new Map(),
    active: false,
    host: false
  };

  const callState = {
    callId: null,
    targetId: null,
    media: 'video',
    pc: null,
    localStream: null,
    remoteStream: null,
    incomingFrom: null,
    startedAt: null
  };

  let lastCallLog = { callId: null, action: null, at: 0 };

  const formatDuration = (seconds) => {
    if (typeof seconds !== 'number' || Number.isNaN(seconds)) return '';
    const mins = Math.floor(seconds / 60);
    const secs = seconds % 60;
    return `${mins}:${String(secs).padStart(2, '0')}`;
  };

  const persistCallLog = async (detail) => {
    if (!detail || !detail.peerId) return;
    if (!detail.action) return;
    const now = Date.now();
    if (lastCallLog.callId === callState.callId && lastCallLog.action === detail.action && (now - lastCallLog.at) < 1200) {
      return;
    }
    lastCallLog = { callId: callState.callId, action: detail.action, at: now };

    if (!['started', 'ended', 'rejected', 'missed'].includes(detail.action)) return;

    const mediaLabel = detail.media === 'audio' ? 'Audio' : 'Video';
    let text = '';
    switch (detail.action) {
      case 'started':
        text = `📞 ${mediaLabel} call started`;
        break;
      case 'ended':
        text = `📞 ${mediaLabel} call ended`;
        break;
      case 'rejected':
        text = `📞 ${mediaLabel} call rejected`;
        break;
      case 'missed':
        text = `📞 Missed ${mediaLabel} call`;
        break;
      default:
        text = `📞 ${mediaLabel} call`;
    }
    if (detail.action === 'ended' && typeof detail.durationSeconds === 'number') {
      const duration = formatDuration(detail.durationSeconds);
      if (duration) text += ` · ${duration}`;
    }
    try {
      await fetch(`/api/messages/${detail.peerId}?bypass=1`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text })
      });
    } catch (err) { /* ignore */ }
  };

  const meetingModal = document.getElementById('space-meeting-modal');
  const meetingGrid = document.getElementById('space-video-grid');
  const meetingList = document.getElementById('space-participants-list');
  const meetingTitle = document.getElementById('space-meeting-title');
  const meetingClose = document.getElementById('space-meeting-close');
  const meetingMute = document.getElementById('meeting-toggle-mic');
  const meetingCamera = document.getElementById('meeting-toggle-camera');
  const meetingScreen = document.getElementById('meeting-share-screen');
  const meetingLeave = document.getElementById('meeting-leave');

  const callModal = document.getElementById('direct-call-modal');
  const callStatus = document.getElementById('direct-call-status');
  const callAccept = document.getElementById('direct-call-accept');
  const callReject = document.getElementById('direct-call-reject');
  const callEnd = document.getElementById('direct-call-end');
  const callLocalVideo = document.getElementById('direct-call-local');
  const callRemoteVideo = document.getElementById('direct-call-remote');
  const callClose = document.getElementById('direct-call-close');

  let activeChatUser = null;
  let activeChatUsername = null;

  const emitCallLog = (action, media, peerId, peerName, durationSeconds) => {
    try {
      const detail = {
        action,
        media: media || callState.media || 'video',
        peerId: peerId || activeChatUser || callState.targetId || null,
        peerName: peerName || activeChatUsername || callState.incomingFrom || ''
      };
      if (typeof durationSeconds === 'number') detail.durationSeconds = durationSeconds;
      if (!detail.peerId) return;
      document.dispatchEvent(new CustomEvent('chat:call_log', { detail }));
      persistCallLog(detail);
    } catch (err) { /* ignore */ }
  };

  document.addEventListener('chat:opened', (ev) => {
    activeChatUser = ev.detail?.otherId || null;
    activeChatUsername = ev.detail?.otherUsername || null;
  });
  document.addEventListener('chat:closed', () => {
    activeChatUser = null;
    activeChatUsername = null;
  });

  const openMeetingModal = (spaceName) => {
    if (!meetingModal) return;
    if (meetingTitle) meetingTitle.textContent = spaceName || 'Space meeting';
    meetingModal.style.display = 'flex';
  };

  const closeMeetingModal = () => {
    if (!meetingModal) return;
    meetingModal.style.display = 'none';
  };

  const openCallModal = () => {
    if (!callModal) return;
    callModal.style.display = 'flex';
  };

  const closeCallModal = () => {
    if (!callModal) return;
    callModal.style.display = 'none';
  };

  const clearMeetingUI = () => {
    if (meetingGrid) meetingGrid.innerHTML = '';
    if (meetingList) meetingList.innerHTML = '';
  };

  const addParticipantLabel = (userId, label) => {
    if (!meetingList) return;
    if (meetingList.querySelector(`[data-user-id="${userId}"]`)) return;
    const li = document.createElement('li');
    li.setAttribute('data-user-id', String(userId));
    li.textContent = label || `User #${userId}`;
    meetingList.appendChild(li);
  };

  const removeParticipantLabel = (userId) => {
    if (!meetingList) return;
    const el = meetingList.querySelector(`[data-user-id="${userId}"]`);
    if (el) el.remove();
  };

  const attachVideoTile = (userId, stream, isLocal = false) => {
    if (!meetingGrid || !stream) return;
    const existing = meetingGrid.querySelector(`[data-video-user="${userId}"]`);
    if (existing) {
      existing.srcObject = stream;
      return;
    }
    const wrap = document.createElement('div');
    wrap.className = 'space-video-tile';
    const video = document.createElement('video');
    video.autoplay = true;
    video.playsInline = true;
    video.muted = isLocal;
    video.setAttribute('data-video-user', String(userId));
    video.srcObject = stream;
    wrap.appendChild(video);
    meetingGrid.appendChild(wrap);
  };

  const removeVideoTile = (userId) => {
    if (!meetingGrid) return;
    const video = meetingGrid.querySelector(`[data-video-user="${userId}"]`);
    if (video && video.parentElement) video.parentElement.remove();
  };

  const ensureIceServers = async () => {
    if (!iceServers) iceServers = await fetchIceServers();
    return iceServers;
  };

  const getMeetingLocalStream = async () => {
    if (meeting.localStream) return meeting.localStream;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
      meeting.localStream = stream;
      attachVideoTile(myId, stream, true);
      addParticipantLabel(myId, 'You');
      return stream;
    } catch (err) {
      showToast('Camera/microphone permission denied.');
      throw err;
    }
  };

  const createMeetingPeer = async (peerId) => {
    if (meeting.peers.has(peerId)) return meeting.peers.get(peerId);
    const servers = await ensureIceServers();
    const pc = new RTCPeerConnection({ iceServers: servers });
    meeting.peers.set(peerId, pc);

    pc.onicecandidate = (event) => {
      if (event.candidate) {
        signaling.send({
          event: 'ice-candidate',
          payload: {
            target_id: peerId,
            room_id: meeting.spaceId,
            candidate: event.candidate
          }
        });
      }
    };

    pc.ontrack = (event) => {
      const [stream] = event.streams;
      if (stream) {
        meeting.remoteStreams.set(peerId, stream);
        attachVideoTile(peerId, stream, false);
      }
    };

    const stream = await getMeetingLocalStream();
    stream.getTracks().forEach((track) => pc.addTrack(track, stream));

    return pc;
  };

  const closeMeeting = async () => {
    if (!meeting.active) return;
    meeting.active = false;
    try {
      await signaling.send({ event: 'leave-room', payload: { room_id: meeting.spaceId } });
    } catch (err) { /* ignore */ }
    meeting.peers.forEach((pc) => pc.close());
    meeting.peers.clear();
    meeting.remoteStreams.clear();
    if (meeting.localStream) {
      meeting.localStream.getTracks().forEach((t) => t.stop());
    }
    if (meeting.screenStream) {
      meeting.screenStream.getTracks().forEach((t) => t.stop());
    }
    meeting.localStream = null;
    meeting.screenStream = null;
    meeting.spaceId = null;
    meeting.title = null;
    clearMeetingUI();
    closeMeetingModal();
  };

  const handleMeetingUsers = async (spaceId, users) => {
    if (!spaceId || !Array.isArray(users)) return;
    for (const uid of users) {
      addParticipantLabel(uid, `User #${uid}`);
      const pc = await createMeetingPeer(uid);
      const offer = await pc.createOffer();
      await pc.setLocalDescription(offer);
      signaling.send({
        event: 'offer',
        payload: {
          target_id: uid,
          room_id: spaceId,
          sdp: pc.localDescription
        }
      });
    }
  };

  const startMeeting = async (spaceId, spaceName) => {
    meeting.spaceId = spaceId;
    meeting.title = spaceName;
    meeting.active = true;
    openMeetingModal(spaceName);
    await getMeetingLocalStream();
    await signaling.send({ event: 'join-room', payload: { room_id: spaceId } });
  };

  const toggleMute = () => {
    if (!meeting.localStream) return;
    meeting.localStream.getAudioTracks().forEach((track) => {
      track.enabled = !track.enabled;
      if (meetingMute) meetingMute.textContent = track.enabled ? 'Mute' : 'Unmute';
    });
  };

  const toggleCamera = () => {
    if (!meeting.localStream) return;
    meeting.localStream.getVideoTracks().forEach((track) => {
      track.enabled = !track.enabled;
      if (meetingCamera) meetingCamera.textContent = track.enabled ? 'Camera off' : 'Camera on';
    });
  };

  const startScreenShare = async () => {
    if (!meeting.localStream) return;
    if (meeting.screenStream) {
      meeting.screenStream.getTracks().forEach((t) => t.stop());
      meeting.screenStream = null;
    }
    try {
      const screen = await navigator.mediaDevices.getDisplayMedia({ video: true });
      const screenTrack = screen.getVideoTracks()[0];
      meeting.screenStream = screen;
      meeting.peers.forEach((pc) => {
        const sender = pc.getSenders().find((s) => s.track && s.track.kind === 'video');
        if (sender) sender.replaceTrack(screenTrack);
      });
      attachVideoTile(myId, screen, true);
      screenTrack.onended = () => {
        const camTrack = meeting.localStream.getVideoTracks()[0];
        meeting.peers.forEach((pc) => {
          const sender = pc.getSenders().find((s) => s.track && s.track.kind === 'video');
          if (sender && camTrack) sender.replaceTrack(camTrack);
        });
        attachVideoTile(myId, meeting.localStream, true);
        meeting.screenStream = null;
      };
    } catch (err) {
      showToast('Screen sharing failed.');
    }
  };

  const startDirectCall = async (mediaType) => {
    if (!activeChatUser) {
      showToast('Select a chat first.');
      return;
    }
    callState.callId = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    callState.targetId = activeChatUser;
    callState.media = mediaType;
    callState.incomingFrom = null;
    openCallModal();
    if (callStatus) callStatus.textContent = `Calling ${activeChatUsername || ''}...`;
    if (callAccept) callAccept.style.display = 'none';
    if (callReject) callReject.style.display = 'none';
    if (callEnd) callEnd.style.display = '';

    emitCallLog('started', mediaType, activeChatUser, activeChatUsername || '');

    await ensureIceServers();
    await setupDirectCallPeer(false);

    await signaling.send({
      event: 'call-user',
      payload: {
        target_id: callState.targetId,
        call_id: callState.callId,
        media: mediaType
      }
    });
  };

  const setupDirectCallPeer = async (isCaller) => {
    if (callState.pc) return callState.pc;
    const servers = await ensureIceServers();
    const pc = new RTCPeerConnection({ iceServers: servers });
    callState.pc = pc;

    pc.onicecandidate = (event) => {
      if (event.candidate && callState.targetId) {
        signaling.send({
          event: 'ice-candidate',
          payload: {
            target_id: callState.targetId,
            call_id: callState.callId,
            candidate: event.candidate
          }
        });
      }
    };

    pc.ontrack = (event) => {
      const [stream] = event.streams;
      if (stream) {
        callState.remoteStream = stream;
        if (callRemoteVideo) callRemoteVideo.srcObject = stream;
      }
    };

    const media = callState.media === 'audio' ? { video: false, audio: true } : { video: true, audio: true };
    try {
      const stream = await navigator.mediaDevices.getUserMedia(media);
      callState.localStream = stream;
      stream.getTracks().forEach((track) => pc.addTrack(track, stream));
      if (callLocalVideo) callLocalVideo.srcObject = stream;
    } catch (err) {
      showToast('Unable to access microphone/camera.');
      throw err;
    }

    return pc;
  };

  const sendDirectOffer = async () => {
    if (!callState.pc || !callState.targetId) return;
    if (callState.pc.signalingState !== 'stable') return;
    const offer = await callState.pc.createOffer();
    await callState.pc.setLocalDescription(offer);
    await signaling.send({
      event: 'offer',
      payload: {
        target_id: callState.targetId,
        call_id: callState.callId,
        sdp: callState.pc.localDescription
      }
    });
  };

  const cleanupCall = () => {
    if (callState.pc) callState.pc.close();
    callState.pc = null;
    if (callState.localStream) {
      callState.localStream.getTracks().forEach((t) => t.stop());
    }
    callState.localStream = null;
    callState.remoteStream = null;
    callState.callId = null;
    callState.targetId = null;
    callState.incomingFrom = null;
    callState.startedAt = null;
    if (callLocalVideo) callLocalVideo.srcObject = null;
    if (callRemoteVideo) callRemoteVideo.srcObject = null;
    closeCallModal();
  };

  const handleSignal = async (data) => {
    const event = data.event;
    const payload = data.payload || {};

    if (event === 'room-users') {
      await handleMeetingUsers(payload.space_id, payload.users || []);
      return;
    }

    if (event === 'user-joined') {
      addParticipantLabel(payload.user_id, payload.username || `User #${payload.user_id}`);
      return;
    }

    if (event === 'user-left') {
      removeParticipantLabel(payload.user_id);
      removeVideoTile(payload.user_id);
      const pc = meeting.peers.get(payload.user_id);
      if (pc) pc.close();
      meeting.peers.delete(payload.user_id);
      return;
    }

    if (event === 'meeting-ended') {
      showToast('Meeting ended by host.');
      await closeMeeting();
      return;
    }

    if (event === 'meeting-inactive') {
      showToast('Meeting has not started yet.');
      return;
    }

    if (event === 'incoming-call') {
      callState.callId = payload.call_id;
      callState.targetId = payload.from_id;
      callState.media = payload.media || 'video';
      callState.incomingFrom = payload.from_username || `User #${payload.from_id}`;
      openCallModal();
      if (callStatus) callStatus.textContent = `Incoming ${callState.media} call from ${callState.incomingFrom}`;
      if (callAccept) callAccept.style.display = '';
      if (callReject) callReject.style.display = '';
      if (callEnd) callEnd.style.display = 'none';
      emitCallLog('incoming', callState.media, callState.targetId, callState.incomingFrom);
      return;
    }

    if (event === 'accept-call') {
      if (!callState.callId || payload.call_id !== callState.callId) return;
      if (callStatus) callStatus.textContent = 'Connecting...';
      await setupDirectCallPeer(true);
      await sendDirectOffer();
      if (callEnd) callEnd.style.display = '';
      callState.startedAt = Date.now();
      emitCallLog('connected', callState.media, callState.targetId, callState.incomingFrom || activeChatUsername || '');
      return;
    }

    if (event === 'reject-call') {
      if (callStatus) callStatus.textContent = 'Call rejected.';
      emitCallLog('rejected', callState.media, callState.targetId, callState.incomingFrom || activeChatUsername || '');
      setTimeout(cleanupCall, 1200);
      return;
    }

    if (event === 'end-call') {
      if (callStatus) callStatus.textContent = 'Call ended.';
      const durationSeconds = callState.startedAt ? Math.max(0, Math.round((Date.now() - callState.startedAt) / 1000)) : undefined;
      emitCallLog('ended', callState.media, callState.targetId, callState.incomingFrom || activeChatUsername || '', durationSeconds);
      setTimeout(cleanupCall, 800);
      return;
    }

    if (event === 'offer') {
      if (payload.room_id) {
        const peerId = payload.sender_id;
        const pc = await createMeetingPeer(peerId);
        await pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
        const answer = await pc.createAnswer();
        await pc.setLocalDescription(answer);
        signaling.send({
          event: 'answer',
          payload: {
            target_id: peerId,
            room_id: payload.room_id,
            sdp: pc.localDescription
          }
        });
      } else if (payload.call_id) {
        if (payload.call_id !== callState.callId) return;
        await setupDirectCallPeer(false);
        if (!callState.pc) return;
        await callState.pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
        const answer = await callState.pc.createAnswer();
        await callState.pc.setLocalDescription(answer);
        signaling.send({
          event: 'answer',
          payload: {
            target_id: callState.targetId,
            call_id: callState.callId,
            sdp: callState.pc.localDescription
          }
        });
        if (callStatus) callStatus.textContent = 'Connected';
        if (callEnd) callEnd.style.display = '';
      }
      return;
    }

    if (event === 'answer') {
      if (payload.room_id) {
        const peerId = payload.sender_id;
        const pc = meeting.peers.get(peerId);
        if (!pc) return;
        await pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
      } else if (payload.call_id && callState.pc) {
        if (payload.call_id !== callState.callId) return;
        await callState.pc.setRemoteDescription(new RTCSessionDescription(payload.sdp));
        if (callStatus) callStatus.textContent = 'Connected';
      }
      return;
    }

    if (event === 'ice-candidate') {
      if (payload.room_id) {
        const pc = meeting.peers.get(payload.sender_id);
        if (!pc) return;
        try { await pc.addIceCandidate(payload.candidate); } catch (err) {}
      } else if (payload.call_id && callState.pc) {
        try { await callState.pc.addIceCandidate(payload.candidate); } catch (err) {}
      }
      return;
    }
  };

  document.querySelectorAll('[data-space-meeting-start]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const spaceId = parseInt(btn.getAttribute('data-space-meeting-start'));
      const spaceName = btn.getAttribute('data-space-name') || 'Space meeting';
      if (!spaceId) return;
      await startMeeting(spaceId, spaceName);
    });
  });

  document.querySelectorAll('[data-space-meeting-join]').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const spaceId = parseInt(btn.getAttribute('data-space-meeting-join'));
      const spaceName = btn.getAttribute('data-space-name') || 'Space meeting';
      if (!spaceId) return;
      await startMeeting(spaceId, spaceName);
    });
  });

  meetingClose?.addEventListener('click', closeMeeting);
  meetingLeave?.addEventListener('click', closeMeeting);
  meetingMute?.addEventListener('click', toggleMute);
  meetingCamera?.addEventListener('click', toggleCamera);
  meetingScreen?.addEventListener('click', startScreenShare);

  callAccept?.addEventListener('click', async () => {
    if (!callState.callId || !callState.targetId) return;
    if (callStatus) callStatus.textContent = 'Connecting...';
    if (callAccept) callAccept.style.display = 'none';
    if (callReject) callReject.style.display = 'none';
    if (callEnd) callEnd.style.display = '';
    callState.startedAt = Date.now();
    emitCallLog('connected', callState.media, callState.targetId, callState.incomingFrom || activeChatUsername || '');
    await signaling.send({
      event: 'accept-call',
      payload: { target_id: callState.targetId, call_id: callState.callId }
    });
    await setupDirectCallPeer(false);
  });

  callReject?.addEventListener('click', async () => {
    if (callState.targetId) {
      await signaling.send({
        event: 'reject-call',
        payload: { target_id: callState.targetId, call_id: callState.callId }
      });
    }
    emitCallLog('rejected', callState.media, callState.targetId, callState.incomingFrom || activeChatUsername || '');
    cleanupCall();
  });

  callEnd?.addEventListener('click', async () => {
    if (callState.targetId) {
      await signaling.send({
        event: 'end-call',
        payload: { target_id: callState.targetId, call_id: callState.callId }
      });
    }
    const durationSeconds = callState.startedAt ? Math.max(0, Math.round((Date.now() - callState.startedAt) / 1000)) : undefined;
    emitCallLog('ended', callState.media, callState.targetId, callState.incomingFrom || activeChatUsername || '', durationSeconds);
    cleanupCall();
  });

  callClose?.addEventListener('click', cleanupCall);

  const videoCallBtn = document.getElementById('chat-video-call');
  const audioCallBtn = document.getElementById('chat-audio-call');
  videoCallBtn?.addEventListener('click', () => startDirectCall('video'));
  audioCallBtn?.addEventListener('click', () => startDirectCall('audio'));
});
