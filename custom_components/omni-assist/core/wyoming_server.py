"""Wyoming Protocol server implementation for virtual satellites."""
import asyncio
import json
import logging
import socket
import time
from typing import Dict, Any, Optional, Callable, Awaitable, List, Set

import zeroconf
from homeassistant.components.assist_pipeline import PipelineEvent
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.network import get_url

_LOGGER = logging.getLogger(__name__)

# Wyoming Protocol constants
SAMPLE_RATE = 16000
SAMPLE_WIDTH = 2  # 16-bit
CHANNELS = 1  # mono

# Satellite Info
SATELLITE_INFO = {
    "name": "Omni-Assist Virtual Satellite",
    "description": "Home Assistant virtual satellite powered by Omni-Assist",
    "satellite": {
        "has_audio": True,
        "has_wake": True,
    }
}


class WyomingConnection:
    """Wyoming Protocol connection handler."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, server: "WyomingServer"):
        """Initialize the connection."""
        self.reader = reader
        self.writer = writer
        self.server = server
        self.addr = writer.get_extra_info("peername")
        self.closed = False
        self.audio_queue = asyncio.Queue()
        self.client_info = {}

    async def handle(self) -> None:
        """Handle the connection."""
        _LOGGER.debug(f"Client connected: {self.addr}")
        self.server.clients.add(self)
        
        try:
            # Process client messages
            while not self.closed:
                # Read header line
                line = await self.reader.readline()
                if not line:
                    # Connection closed
                    break
                    
                header = json.loads(line)
                
                # Read additional data if present
                data = {}
                if data_length := header.get("data_length", 0):
                    data_bytes = await self.reader.readexactly(data_length)
                    data = json.loads(data_bytes)
                
                # Read payload if present
                payload = b""
                if payload_length := header.get("payload_length", 0):
                    payload = await self.reader.readexactly(payload_length)
                
                # Handle the message
                await self._handle_message(header["type"], {**header.get("data", {}), **data}, payload)
        except asyncio.CancelledError:
            _LOGGER.debug(f"Connection handler cancelled for {self.addr}")
        except Exception as e:
            _LOGGER.error(f"Error handling Wyoming connection: {e}")
        finally:
            self.closed = True
            self.server.clients.remove(self)
            self.writer.close()
            await self.writer.wait_closed()
            _LOGGER.debug(f"Client disconnected: {self.addr}")
    
    async def _handle_message(self, msg_type: str, data: dict, payload: bytes) -> None:
        """Handle a Wyoming protocol message."""
        _LOGGER.debug(f"Received {msg_type} message: {data}")
        
        if msg_type == "connect":
            # Client is connecting, send info
            self.client_info = data
            
            # Send satellite info
            info = SATELLITE_INFO.copy()
            if room := self.server.room:
                info["name"] = f"{room} Satellite"
                
            await self.send_message("info", info)
        
        elif msg_type == "audio-start":
            # Client is starting to send audio
            await self.server._handle_event("audio-start", data)
        
        elif msg_type == "audio-chunk":
            # Audio data received from client
            await self.server._handle_event("audio-chunk", data, payload)
        
        elif msg_type == "audio-stop":
            # Client has stopped sending audio
            await self.server._handle_event("audio-stop", data)
        
        elif msg_type == "wake-detected":
            # Wake word detected
            await self.server._handle_event("wake-detected", data)
        
        else:
            _LOGGER.warning(f"Unknown Wyoming message type: {msg_type}")
    
    async def send_message(self, msg_type: str, data: dict = None, payload: bytes = None) -> None:
        """Send a Wyoming protocol message."""
        if self.closed:
            return
            
        if data is None:
            data = {}
            
        header = {
            "type": msg_type,
            "data": data,
        }
        
        # Add data length if we have additional data (unused currently)
        data_bytes = b""
        
        # Add payload length if we have a payload
        if payload:
            header["payload_length"] = len(payload)
        
        # Write header
        header_line = json.dumps(header).encode() + b"\n"
        self.writer.write(header_line)
        
        # Write data if present
        if data_bytes:
            self.writer.write(data_bytes)
            
        # Write payload if present
        if payload:
            self.writer.write(payload)
            
        await self.writer.drain()


class WyomingServer:
    """Wyoming Protocol server for virtual satellites."""

    def __init__(self, hass: HomeAssistant, entry_id: str, config: Dict[str, Any]):
        """Initialize the Wyoming server."""
        self.hass = hass
        self.entry_id = entry_id
        self.config = config
        self.server = None
        self.zeroconf_info = None
        self.port = config.get("satellite_port", 10600)
        self.room = config.get("satellite_room", "")
        self.pipeline_id = config.get("pipeline_id")
        self.noise_suppression_level = config.get("noise_suppression_level", 0)
        self.auto_gain_dbfs = config.get("auto_gain_dbfs", 0)
        self.volume_multiplier = config.get("volume_multiplier", 1.0)
        
        # State tracking
        self.is_running = False
        self.clients: Set[WyomingConnection] = set()
        self.tasks = []
        
        # Callback handlers
        self._event_callbacks = []
        
        # Audio processing state
        self.audio_queue = asyncio.Queue()
        self.stream_task = None
        self.detecting_wake = False
        self.wake_detected = False

    async def start(self) -> bool:
        """Start the Wyoming server."""
        _LOGGER.debug(f"Starting Wyoming server for entry {self.entry_id} on port {self.port}")
        
        # Start the server
        host = "0.0.0.0"  # Listen on all interfaces
        
        try:
            self.server = await asyncio.start_server(
                self._handle_connection,
                host,
                self.port,
            )
            
            _LOGGER.info(f"Wyoming server started on {host}:{self.port}")
            
            # Register with Zeroconf for discovery
            await self._register_zeroconf()
            
            # Start the server task
            server_task = asyncio.create_task(self.server.serve_forever())
            self.tasks.append(server_task)
            
            self.is_running = True
            return True
        except Exception as e:
            _LOGGER.error(f"Failed to start Wyoming server: {e}")
            await self._cleanup()
            return False
        
    async def stop(self) -> None:
        """Stop the Wyoming server."""
        _LOGGER.debug(f"Stopping Wyoming server for entry {self.entry_id}")
        await self._cleanup()
        
    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Unregister from Zeroconf
        await self._unregister_zeroconf()
        
        # Close all client connections
        for client in list(self.clients):
            if not client.closed:
                client.writer.close()
        
        # Cancel all tasks
        for task in self.tasks:
            if not task.done():
                task.cancel()
        
        # Stop the server
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        
        self.is_running = False
        _LOGGER.debug(f"Wyoming server stopped for entry {self.entry_id}")
        
    async def reconfigure(self, config: Dict[str, Any]) -> None:
        """Reconfigure the Wyoming server with new settings."""
        _LOGGER.debug(f"Reconfiguring Wyoming server for entry {self.entry_id}")
        
        port_changed = config.get("satellite_port", 10600) != self.port
        room_changed = config.get("satellite_room", "") != self.room
        
        self.config = config
        self.port = config.get("satellite_port", 10600)
        self.room = config.get("satellite_room", "")
        self.pipeline_id = config.get("pipeline_id")
        self.noise_suppression_level = config.get("noise_suppression_level", 0)
        self.auto_gain_dbfs = config.get("auto_gain_dbfs", 0)
        self.volume_multiplier = config.get("volume_multiplier", 1.0)
        
        # If the port or room changed, restart the server
        if port_changed or room_changed:
            _LOGGER.debug(f"Port or room changed, restarting Wyoming server")
            await self.stop()
            await asyncio.sleep(1)  # Short delay to ensure clean shutdown
            await self.start()
        
    async def _register_zeroconf(self) -> None:
        """Register the service with Zeroconf for discovery."""
        try:
            # Get hostname
            host_name = socket.gethostname().lower()
            if not host_name.endswith(".local."):
                host_name += ".local."
                
            # Get base URL for Home Assistant
            base_url = get_url(self.hass)
            
            # Create service info
            service_name = f"omni-assist-{self.entry_id[:7]}"
            if self.room:
                service_name = f"{self.room.lower().replace(' ', '-')}-satellite"
                
            service_name = f"{service_name}._wyoming._tcp.local."
            
            properties = {
                "name": self.room if self.room else f"Omni-Assist Satellite {self.entry_id[:7]}",
                "satellite.has_audio": "true",
                "satellite.has_wake": "true",
                "homeassistant.url": base_url,
            }
            
            # Add noise suppression if enabled
            if self.noise_suppression_level > 0:
                properties["asr.noise_suppression_level"] = str(self.noise_suppression_level)
                
            # Add auto gain if enabled
            if self.auto_gain_dbfs != 0:
                properties["asr.auto_gain_dbfs"] = str(self.auto_gain_dbfs)
                
            # Add volume multiplier if not default
            if self.volume_multiplier != 1.0:
                properties["asr.volume_multiplier"] = str(self.volume_multiplier)
                
            # Create Zeroconf service info
            self.zeroconf_info = zeroconf.ServiceInfo(
                "_wyoming._tcp.local.",
                service_name,
                addresses=[socket.inet_aton("127.0.0.1")],  # Use localhost
                port=self.port,
                properties=properties,
                server=host_name,
            )
            
            # Get Zeroconf instance
            zcf = self.hass.components.zeroconf.async_get_instance()
            
            # Register service
            await self.hass.async_add_executor_job(
                zcf.register_service, self.zeroconf_info
            )
            
            _LOGGER.debug(f"Registered Wyoming service {service_name} with Zeroconf")
        except Exception as e:
            _LOGGER.error(f"Failed to register Wyoming service with Zeroconf: {e}")
        
    async def _unregister_zeroconf(self) -> None:
        """Unregister the service from Zeroconf."""
        if self.zeroconf_info:
            try:
                # Get Zeroconf instance
                zcf = self.hass.components.zeroconf.async_get_instance()
                
                # Unregister service
                await self.hass.async_add_executor_job(
                    zcf.unregister_service, self.zeroconf_info
                )
                
                _LOGGER.debug(f"Unregistered Wyoming service from Zeroconf")
            except Exception as e:
                _LOGGER.error(f"Failed to unregister Wyoming service from Zeroconf: {e}")
                
            self.zeroconf_info = None
            
    def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Handle a new client connection."""
        connection = WyomingConnection(reader, writer, self)
        task = asyncio.create_task(connection.handle())
        self.tasks.append(task)
        
    @callback
    def register_event_callback(
        self, callback_func: Callable[[str, Dict[str, Any]], Awaitable[None]]
    ) -> Callable[[], None]:
        """Register a callback for Wyoming events."""
        self._event_callbacks.append(callback_func)
        
        def remove_callback():
            if callback_func in self._event_callbacks:
                self._event_callbacks.remove(callback_func)
                
        return remove_callback
        
    async def _handle_event(self, event_type: str, event_data: Dict[str, Any], payload: bytes = None) -> None:
        """Handle Wyoming protocol events and forward them to registered callbacks."""
        # Process the event
        if event_type == "audio-start":
            # Client is starting to send audio
            _LOGGER.debug("Audio stream started")
            self.detecting_wake = True
            self.wake_detected = False
            
        elif event_type == "audio-chunk" and payload:
            # Add audio to queue
            if not self.detecting_wake and not self.wake_detected:
                # Not in active listening mode, ignore audio
                return
            
            # Process audio data
            await self.audio_queue.put(payload)
            
        elif event_type == "audio-stop":
            # Client has stopped sending audio
            _LOGGER.debug("Audio stream stopped")
            self.detecting_wake = False
            await self.audio_queue.put(None)  # Signal end of stream
            
        elif event_type == "wake-detected":
            # Wake word detected, create pipeline event
            _LOGGER.debug("Wake word detected via Wyoming protocol")
            self.wake_detected = True
            
            # Create a wake word event to trigger the pipeline
            wake_event = PipelineEvent(
                "wake_word-detected",
                {
                    "wake_word_id": event_data.get("wake_word_id", "wyoming"),
                    "timestamp": time.time()
                }
            )
            
            # Forward to all callbacks
            for callback_func in self._event_callbacks:
                try:
                    await callback_func("wake-detected", event_data)
                except Exception as e:
                    _LOGGER.error(f"Error in Wyoming event callback: {e}")
        
    async def broadcast_event(self, event_type: str, event_data: Dict[str, Any], payload: bytes = None) -> None:
        """Broadcast an event to all connected clients."""
        for client in list(self.clients):
            try:
                await client.send_message(event_type, event_data, payload)
            except Exception as e:
                _LOGGER.error(f"Error sending event to client: {e}")
                
    async def send_audio(self, audio_data: bytes) -> None:
        """Send audio data to connected clients."""
        if not self.is_running or not self.clients:
            return
            
        payload_size = 1024  # Chunk size
        
        # Send audio in chunks
        for i in range(0, len(audio_data), payload_size):
            chunk = audio_data[i:i+payload_size]
            
            # Send the audio chunk to all clients
            await self.broadcast_event(
                "audio-chunk",
                {
                    "rate": SAMPLE_RATE,
                    "width": SAMPLE_WIDTH,
                    "channels": CHANNELS,
                },
                chunk
            )


async def setup_wyoming_server(
    hass: HomeAssistant, entry_id: str, config: Dict[str, Any]
) -> Optional[WyomingServer]:
    """Set up a Wyoming server for the given config entry."""
    server = WyomingServer(hass, entry_id, config)
    if await server.start():
        return server
    return None 