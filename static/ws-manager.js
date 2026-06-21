/**
 * Single persistent WebSocket for MAW workflow logs and status.
 * Uses /ws/maw with subscribe action — no reconnect on panel/task switch.
 */
class MawWebSocket {
  constructor({ onMessage, onOpen, onClose } = {}) {
    this.onMessage = onMessage;
    this.onOpen = onOpen;
    this.onClose = onClose;
    this.ws = null;
    this.subscribedTaskNum = null;
    this.reconnectDelay = 1000;
    this.maxReconnectDelay = 30000;
    this.heartbeatInterval = null;
    this.reconnectTimer = null;
    this.intentionalClose = false;
  }

  connect() {
    if (this.ws && (this.ws.readyState === WebSocket.OPEN || this.ws.readyState === WebSocket.CONNECTING)) {
      return;
    }
    this.intentionalClose = false;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    this.ws = new WebSocket(`${proto}://${location.host}/ws/maw`);
    this.ws.onopen = () => {
      this.reconnectDelay = 1000;
      this._startHeartbeat();
      if (this.subscribedTaskNum) {
        this._send({ action: 'subscribe', task_num: this.subscribedTaskNum });
      }
      if (this.onOpen) this.onOpen();
    };
    this.ws.onmessage = (ev) => {
      let data;
      try {
        data = JSON.parse(ev.data);
      } catch {
        return;
      }
      if (data.type === 'pong') return;
      if (this.onMessage) this.onMessage(data);
    };
    this.ws.onclose = () => {
      this._stopHeartbeat();
      if (this.onClose) this.onClose();
      if (!this.intentionalClose) this._scheduleReconnect();
    };
  }

  subscribe(taskNum) {
    this.subscribedTaskNum = taskNum;
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.connect();
      return;
    }
    this._send({ action: 'subscribe', task_num: taskNum });
  }

  _send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  close() {
    this.intentionalClose = true;
    this._stopHeartbeat();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) this.ws.close();
    this.ws = null;
  }

  _startHeartbeat() {
    this._stopHeartbeat();
    this.heartbeatInterval = setInterval(() => {
      this._send({ action: 'ping' });
    }, 25000);
  }

  _stopHeartbeat() {
    if (this.heartbeatInterval) {
      clearInterval(this.heartbeatInterval);
      this.heartbeatInterval = null;
    }
  }

  _scheduleReconnect() {
    if (this.reconnectTimer) return;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      this.connect();
      this.reconnectDelay = Math.min(this.reconnectDelay * 2, this.maxReconnectDelay);
    }, this.reconnectDelay);
  }
}