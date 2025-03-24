"""Wyoming Virtual Satellite implementation for Omni-Assist."""
import logging
import socket
from typing import Optional

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)

class OmniAssistWyomingSatellite:
    """Wyoming protocol satellite for Omni-Assist."""
    
    def __init__(self, hass: HomeAssistant, device_id: str, config_entry: ConfigEntry):
        """Initialize the Wyoming satellite."""
        self.hass = hass
        self.device_id = device_id
        self.config_entry = config_entry
        self.server = None
        self.zeroconf = None
        self._server_task = None
        
    async def start(self, host: str = "0.0.0.0", port: int = 10700) -> bool:
        """Start the Wyoming satellite server."""
        from wyoming.server import AsyncServer
        
        # Check if port is already in use
        if self._port_in_use(host, port):
            _LOGGER.error(f"Port {port} is already in use, cannot start Wyoming satellite")
            return False
            
        # Initialize the server
        try:
            self.server = AsyncServer.from_uri(f"tcp://{host}:{port}")
            
            # Register the satellite with Zeroconf using the correct IP
            self._register_zeroconf(host, port)
            
            # Start the server in a background task
            self._server_task = self.hass.async_create_background_task(
                self.server.run(self._handle_connection), 
                "omni_assist_wyoming_server"
            )
            
            _LOGGER.info(f"Wyoming satellite server started on {host}:{port}")
            return True
        except Exception as e:
            _LOGGER.error(f"Failed to start Wyoming satellite server: {e}", exc_info=e)
            return False
    
    async def _handle_connection(self, connection):
        """Handle incoming Wyoming protocol connections with proper event loop."""
        from wyoming.satellite import RunSatellite, Ping, Pong
        
        _LOGGER.debug(f"New Wyoming connection established: {connection}")
        
        try:
            while True:
                event = await connection.read_event()
                
                if event is None:
                    _LOGGER.debug("Connection closed by client")
                    break
                    
                _LOGGER.debug(f"Received Wyoming event: {event.type}")
                
                if isinstance(event, RunSatellite):
                    # Extract pipeline parameters
                    pipeline_id = event.data.get("pipeline_id")
                    conversation_id = event.data.get("conversation_id")
                    extra_system_message = event.data.get("extra_system_message", "")
                    
                    _LOGGER.info(f"Running pipeline {pipeline_id} via Wyoming satellite")
                    
                    # Trigger Omni-Assist's internal pipeline mechanism
                    await self.hass.services.async_call(
                        "omni_assist", 
                        "run",
                        {
                            "pipeline_id": pipeline_id,
                            "conversation_id": conversation_id,
                            "extra_system_message": extra_system_message
                        },
                        blocking=False  # Non-blocking to prevent satellite connection issues
                    )
                elif isinstance(event, Ping):
                    # Respond to ping events to maintain connection
                    await connection.write_event(Pong())
                else:
                    _LOGGER.debug(f"Unhandled Wyoming event type: {event.type}")
        except Exception as e:
            _LOGGER.error("Error in Wyoming connection handler", exc_info=e)
        finally:
            _LOGGER.debug("Wyoming connection handler terminated")
    
    def _port_in_use(self, host: str, port: int) -> bool:
        """Check if the specified port is already in use."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((host, port)) == 0
    
    def _register_zeroconf(self, host: str, port: int) -> None:
        """Register the satellite service with Zeroconf using the actual IP address."""
        from zeroconf import ServiceInfo, Zeroconf
        
        # Get the actual IP address for Zeroconf advertisement
        # This ensures proper discovery even when binding to 0.0.0.0
        try:
            actual_ip = socket.gethostbyname(socket.gethostname())
            _LOGGER.debug(f"Using IP {actual_ip} for Zeroconf advertisement")
        except Exception as e:
            _LOGGER.warning(f"Failed to get hostname IP, using {host} instead: {e}")
            actual_ip = host if host != "0.0.0.0" else "127.0.0.1"
        
        info = ServiceInfo(
            "_wyoming._tcp.local.",
            f"omni_assist_{self.device_id}._wyoming._tcp.local.",
            addresses=[socket.inet_aton(actual_ip)],
            port=port,
            properties={
                "satellite": "true",
                "virtual": "true",
                "device_id": self.device_id,
                "manufacturer": "Omni-Assist",
                "model": "Virtual Satellite"
            }
        )
        
        self.zeroconf = Zeroconf()
        self.zeroconf.register_service(info)
        _LOGGER.info(f"Wyoming satellite registered via Zeroconf: omni_assist_{self.device_id}")
    
    async def stop(self) -> None:
        """Stop the Wyoming satellite server and clean up resources."""
        _LOGGER.info("Stopping Wyoming satellite server")
        
        # Unregister from Zeroconf
        if self.zeroconf:
            _LOGGER.debug("Unregistering Wyoming satellite from Zeroconf")
            self.zeroconf.unregister_all_services()
            self.zeroconf.close()
            self.zeroconf = None
        
        # Stop the server
        if self.server:
            _LOGGER.debug("Stopping Wyoming server")
            await self.server.stop()
            # Ensure server is fully closed
            if hasattr(self.server, "wait_closed"):
                await self.server.wait_closed()
            self.server = None 