import React, { useEffect, useRef, useState } from 'react';

const SpaceMeetingRoom = ({ roomId, userToken }) => {
  const [participants, setParticipants] = useState([]);
  const [localStream, setLocalStream] = useState(null);
  const [remoteStreams, setRemoteStreams] = useState([]);
  const ws = useRef(null);

  useEffect(() => {
    // Initialize WebSocket connection
    ws.current = new WebSocket(`ws://localhost:8000/ws/video?token=${userToken}`);

    ws.current.onmessage = (event) => {
      const data = JSON.parse(event.data);
      handleWebSocketEvent(data);
    };

    ws.current.onopen = () => {
      ws.current.send(JSON.stringify({ event: 'join-room', payload: { room_id: roomId } }));
    };

    return () => {
      ws.current.close();
    };
  }, [roomId, userToken]);

  const handleWebSocketEvent = (data) => {
    switch (data.event) {
      case 'user-joined':
        setParticipants((prev) => [...prev, data.payload.user_id]);
        break;
      case 'user-left':
        setParticipants((prev) => prev.filter((id) => id !== data.payload.user_id));
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
    <div>
      <h2>Space Meeting Room</h2>
      <button onClick={startLocalStream}>Start Camera</button>
      <div>
        <h3>Participants</h3>
        <ul>
          {participants.map((participant) => (
            <li key={participant}>{participant}</li>
          ))}
        </ul>
      </div>
      <div>
        <video autoPlay playsInline muted ref={(video) => video && localStream && (video.srcObject = localStream)} />
      </div>
    </div>
  );
};

export default SpaceMeetingRoom;