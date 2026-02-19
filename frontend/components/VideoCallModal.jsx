import React, { useEffect, useRef, useState } from 'react';

const VideoCallModal = ({ callerId, calleeId, userToken }) => {
  const [localStream, setLocalStream] = useState(null);
  const [remoteStream, setRemoteStream] = useState(null);
  const ws = useRef(null);

  useEffect(() => {
    // Initialize WebSocket connection
    ws.current = new WebSocket(`ws://localhost:8000/ws/video?token=${userToken}`);

    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleWebSocketEvent(data);
    };

    return () => {
      ws.current.close();
    };
  }, [userToken]);

  const handleWebSocketEvent = (data) => {
    switch (data.event) {
      case 'incoming-call':
        // Handle incoming call
        break;
      case 'offer':
        // Handle WebRTC offer
        break;
      case 'answer':
        // Handle WebRTC answer
        break;
      case 'ice-candidate':
        // Handle ICE candidate
        break;
      default:
        break;
    }
  };

  const startLocalStream = async () => {
    const stream = await navigator.mediaDevices.getUserMedia({ video: true, audio: true });
    setLocalStream(stream);
  };

  return (
    <div style={{ 
      position: 'fixed', 
      top: 0, 
      left: 0, 
      width: '100%', 
      height: '100%', 
      backgroundColor: 'rgba(0, 0, 0, 0.9)', 
      zIndex: 9999,
      display: 'flex',
      flexDirection: 'column',
      alignItems: 'center',
      justifyContent: 'center'
    }}>
      <h2 style={{ color: 'white', marginBottom: '20px' }}>Video Call</h2>
      <button 
        onClick={startLocalStream}
        style={{
          padding: '10px 20px',
          backgroundColor: '#4CAF50',
          color: 'white',
          border: 'none',
          borderRadius: '5px',
          cursor: 'pointer',
          marginBottom: '20px'
        }}
      >
        Start Camera
      </button>
      <div style={{ 
        display: 'grid', 
        gridTemplateColumns: '1fr 1fr', 
        gap: '20px',
        width: '90%',
        maxWidth: '1200px'
      }}>
        <video 
          autoPlay 
          playsInline 
          muted 
          ref={(video) => video && localStream && (video.srcObject = localStream)}
          style={{
            width: '100%',
            height: '400px',
            backgroundColor: '#000',
            borderRadius: '8px',
            objectFit: 'cover'
          }}
        />
        <video 
          autoPlay 
          playsInline 
          ref={(video) => video && remoteStream && (video.srcObject = remoteStream)}
          style={{
            width: '100%',
            height: '400px',
            backgroundColor: '#000',
            borderRadius: '8px',
            objectFit: 'cover'
          }}
        />
      </div>
    </div>
  );
};

export default VideoCallModal;