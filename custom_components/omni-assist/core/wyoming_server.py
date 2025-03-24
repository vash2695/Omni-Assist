"""Wyoming Protocol server implementation for virtual satellites."""
import asyncio
import json
import logging
import socket
import io
from typing import Dict, Any, Optional, Callable, Awaitable, Set

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

class WyomingEvent:
    """Wyoming Protocol event."""
    
    def __init__(self, type: str, data: Dict[str, Any] = None) -> None:
        """Initialize a Wyoming event."""
        self.type = type
        self.data = data or {}
    
    @staticmethod
    async def read_event(reader: asyncio.StreamReader) -> Optional["WyomingEvent"]:
        """Read a Wyoming event from a stream."""
        try:
            # Read header line
            line = await reader.readline()
            if not line:
                # Connection closed
                return None
            
            header = json.loads(line)
            
            # Read payload if present
            if data_length := header.get("data-length", 0):
                data_bytes = await reader.readexactly(data_length)
                data = json.loads(data_bytes)
            else:
                data = header.get("data", {})
            
            return WyomingEvent(header["type"], data)
        except json.JSONDecodeError as e:
            _LOGGER.warning(f"JSON decode error in Wyoming event: {e}")
            raise
        except Exception as e:
            _LOGGER.error(f"Error reading Wyoming event: {e}")
            raise
    
    async def write_event(self, writer: asyncio.StreamWriter) -> None:
        """Write a Wyoming event to a stream."""
        try:
            data_json = json.dumps(self.data).encode("utf-8")
            
            header = {
                "type": self.type,
                "data-length": len(data_json),
            }
            
            header_json = json.dumps(header).encode("utf-8") + b"\n"
            
            writer.write(header_json)
            writer.write(data_json)
            await writer.drain()
        except Exception as e:
            _LOGGER.error(f"Error writing Wyoming event: {e}")
            raise

class WyomingServer:
    """Wyoming protocol server - acts as a bridge between Wyoming and Omni-Assist."""
    
    def __init__(
        self,
        hass,
        entry_id: str,
        audio_callback: Optional[Callable[[bytes], Awaitable[None]]] = None,
        host: str = "0.0.0.0",
        port: int = 10600,
    ):
        """Initialize Wyoming server.
        
        Args:
            hass: Home Assistant instance
            entry_id: Config entry ID this server belongs to
            audio_callback: Callback to be called when audio is received
            host: Host to listen on
            port: Port to listen on
        """
        self.hass = hass
        self.entry_id = entry_id
        self.audio_callback = audio_callback
        self.host = host
        self.port = port
        self.server = None
        self.audio_stream = None
        self.zeroconf_info = None
        self._clients: Set[asyncio.Task] = set()
    
    async def start(self):
        """Start the Wyoming server."""
        self.server = await asyncio.start_server(
            self.handle_connection,
            self.host,
            self.port,
        )
        
        # Update port if dynamically assigned
        if self.port == 0:
            socket_info = self.server.sockets[0].getsockname()
            self.port = socket_info[1]
            
        _LOGGER.info(f"Wyoming server started on {self.host}:{self.port} for integration {self.entry_id}")
        
        # Register Zeroconf service
        await self._register_zeroconf()
    
    async def stop(self):
        """Stop the Wyoming server."""
        if self.server:
            # Unregister Zeroconf service
            await self._unregister_zeroconf()
            
            # Cancel all client tasks
            for client in self._clients:
                client.cancel()
            
            # Close server
            self.server.close()
            await self.server.wait_closed()
            self.server = None
            
            _LOGGER.info(f"Wyoming server stopped for integration {self.entry_id}")
    
    async def _register_zeroconf(self) -> None:
        """Register the service with Zeroconf for discovery."""
        try:
            # Get hostname
            host_name = socket.gethostname().lower()
            if not host_name.endswith(".local."):
                host_name += ".local."
                
            # Create service info
            service_name = f"omni-assist-{self.entry_id[:7]}._wyoming._tcp.local."
            
            properties = {
                "name": f"Omni-Assist Satellite {self.entry_id[:7]}",
                "satellite.has_audio": "true",
                "satellite.has_wake": "true",
            }
            
            # Import here to avoid hard dependency
            from homeassistant.components import zeroconf
            import zeroconf as zc
            
            # Create Zeroconf service info
            self.zeroconf_info = zc.ServiceInfo(
                "_wyoming._tcp.local.",
                service_name,
                addresses=[socket.inet_aton("127.0.0.1")],  # Use localhost
                port=self.port,
                properties=properties,
                server=host_name,
            )
            
            # Get Zeroconf instance - properly await this coroutine
            zcf = await self.hass.components.zeroconf.async_get_instance()
            
            # Register service - use lambda to wrap the call
            await self.hass.async_add_executor_job(
                lambda: zcf.register_service(self.zeroconf_info)
            )
            
            _LOGGER.debug(f"Registered Wyoming service {service_name} with Zeroconf")
        except Exception as e:
            _LOGGER.error(f"Failed to register Wyoming service with Zeroconf: {e}")
        
    async def _unregister_zeroconf(self) -> None:
        """Unregister the service from Zeroconf."""
        if self.zeroconf_info:
            try:
                # Get Zeroconf instance - properly await this coroutine
                zcf = await self.hass.components.zeroconf.async_get_instance()
                
                # Unregister service - use lambda to wrap the call
                await self.hass.async_add_executor_job(
                    lambda: zcf.unregister_service(self.zeroconf_info)
                )
                
                _LOGGER.debug(f"Unregistered Wyoming service from Zeroconf")
            except Exception as e:
                _LOGGER.error(f"Failed to unregister Wyoming service from Zeroconf: {e}")
                
            self.zeroconf_info = None
    
    async def handle_connection(self, reader, writer):
        """Handle a Wyoming connection."""
        client_task = asyncio.current_task()
        if client_task:
            self._clients.add(client_task)
            
        try:
            _LOGGER.debug("Handling Wyoming connection")
            client_addr = writer.get_extra_info("peername")
            _LOGGER.debug(f"Connection from {client_addr}")

            # First message should be a Wyoming detect request
            try:
                event = await WyomingEvent.read_event(reader)
                if not event or (event.type != "detect"):
                    _LOGGER.warning(f"Expected detect event, got: {event}")
                    return
            except json.JSONDecodeError as e:
                _LOGGER.warning(f"Malformed JSON in Wyoming connection: {e}")
                return
            except Exception as e:
                _LOGGER.warning(f"Error reading Wyoming event: {e}")
                return

            # Respond with our info - minimal information needed for Wyoming to identify us
            await WyomingEvent(
                type="detect",
                data={
                    "asr": [{
                        "name": "omni-assist",
                        "satellite": True
                    }]
                }
            ).write_event(writer)

            # Read audio chunks and stream to pipeline
            while True:
                try:
                    event = await WyomingEvent.read_event(reader)
                    if not event:
                        break

                    if event.type == "audio-frame":
                        if not self.audio_stream:
                            _LOGGER.debug("Starting new audio stream for incoming audio frame")
                            self.audio_stream = io.BytesIO()

                        audio_bytes = event.data["audio-frame"]["audio"]
                        # Process audio chunk
                        self.audio_stream.write(audio_bytes)
                    elif event.type == "audio-start":
                        # Start new audio stream
                        _LOGGER.debug("Received audio-start event")
                        self.audio_stream = io.BytesIO()
                    elif event.type == "audio-stop":
                        # Process complete audio
                        _LOGGER.debug("Received audio-stop event")
                        if self.audio_stream:
                            audio_data = self.audio_stream.getvalue()
                            self.audio_stream = None
                            
                            # Send to hass via callback
                            if self.audio_callback:
                                _LOGGER.debug(f"Calling audio callback with {len(audio_data)} bytes")
                                await self.audio_callback(audio_data)
                            else:
                                _LOGGER.warning("No audio callback registered")
                except json.JSONDecodeError as e:
                    _LOGGER.warning(f"Malformed JSON in Wyoming connection: {e}")
                    # Don't break the connection for a single malformed message
                    continue
                except Exception as e:
                    _LOGGER.error(f"Error handling Wyoming connection: {e}")
                    break
                    
        except Exception as e:
            _LOGGER.error(f"Error handling Wyoming connection: {e}")
        finally:
            _LOGGER.debug("Closing Wyoming connection")
            writer.close()
            try:
                await writer.wait_closed()
            except Exception as e:
                _LOGGER.debug(f"Error closing writer: {e}")
                
            # Remove client task
            if client_task and client_task in self._clients:
                self._clients.remove(client_task)


async def setup_wyoming_server(
    hass,
    entry_id: str,
    config: Dict[str, Any]
) -> Optional[WyomingServer]:
    """Set up a Wyoming server for the given config entry."""
    try:
        audio_callback = config.get("audio_callback")
        port = config.get("port", 10600)
        
        # Create and start the server
        server = WyomingServer(
            hass, 
            entry_id, 
            audio_callback=audio_callback,
            port=port
        )
        
        await server.start()
        return server
    except Exception as e:
        _LOGGER.error(f"Error setting up Wyoming server: {e}")
        return None 