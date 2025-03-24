"""Wyoming Virtual Satellite implementation for Omni-Assist."""
import asyncio
import json
import logging
import socket
from typing import Any, Dict, Optional
import concurrent.futures

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

_LOGGER = logging.getLogger(__name__)

# Simple Wyoming protocol event types
WYOMING_EVENT_PING = "ping"
WYOMING_EVENT_PONG = "pong"
WYOMING_EVENT_RUN_SATELLITE = "run-satellite"

class OmniAssistWyomingSatellite:
    """Wyoming protocol satellite for Omni-Assist using minimal direct implementation."""
    
    def __init__(self, hass: HomeAssistant, device_id: str, config_entry: ConfigEntry):
        """Initialize the Wyoming satellite."""
        self.hass = hass
        self.device_id = device_id
        self.config_entry = config_entry
        self.server = None
        self.zeroconf = None
        self._server_task = None
        self._server_socket = None
        self._clients = set()
        self._running = False
        
    async def start(self, host: str = "0.0.0.0", port: int = 10700) -> bool:
        """Start the Wyoming satellite server."""
        try:
            # Check if port is already in use
            if self._port_in_use(host, port):
                _LOGGER.error(f"Port {port} is already in use, cannot start Wyoming satellite")
                return False
                
            # Create and start the server
            try:
                self._server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                self._server_socket.bind((host, port))
                self._server_socket.listen(5)
                self._server_socket.setblocking(False)
                
                # Register with Zeroconf
                self._register_zeroconf(host, port)
                
                # Start the server in a background task
                self._running = True
                self._server_task = self.hass.async_create_background_task(
                    self._run_server(),
                    "omni_assist_wyoming_server"
                )
                
                _LOGGER.info(f"Wyoming satellite server started on {host}:{port}")
                return True
            except Exception as e:
                _LOGGER.error(f"Failed to start Wyoming satellite server: {e}", exc_info=e)
                return False
        except Exception as e:
            _LOGGER.error(f"Error in Wyoming satellite startup: {e}", exc_info=e)
            return False
    
    async def _run_server(self):
        """Main server loop for accepting connections."""
        loop = asyncio.get_event_loop()
        
        while self._running:
            try:
                client_socket, addr = await loop.sock_accept(self._server_socket)
                client_socket.setblocking(False)
                _LOGGER.debug(f"New Wyoming connection from {addr}")
                
                # Start a task to handle this client
                client_task = asyncio.create_task(self._handle_client(client_socket, addr))
                self._clients.add(client_task)
                client_task.add_done_callback(self._clients.discard)
                
            except Exception as e:
                if self._running:  # Only log if we're supposed to be running
                    _LOGGER.error(f"Error accepting Wyoming connection: {e}")
                await asyncio.sleep(0.1)  # Prevent CPU spinning on errors
    
    async def _handle_client(self, client_socket, addr):
        """Handle a client connection."""
        loop = asyncio.get_event_loop()
        
        try:
            buffer = b""
            
            while self._running:
                # Read data
                chunk = await loop.sock_recv(client_socket, 4096)
                if not chunk:
                    _LOGGER.debug(f"Client disconnected: {addr}")
                    break
                
                buffer += chunk
                
                # Process complete Wyoming protocol messages from the buffer
                while b"\n" in buffer:
                    header_end = buffer.find(b"\n")
                    header_bytes = buffer[:header_end]
                    buffer = buffer[header_end + 1:]
                    
                    # Parse the header
                    try:
                        header = json.loads(header_bytes.decode("utf-8"))
                        event_type = header.get("type")
                        
                        # Extract payload if there is one
                        payload = None
                        payload_length = header.get("payload_length", 0)
                        if payload_length > 0:
                            # If we don't have enough data yet, put the header back and wait for more
                            if len(buffer) < payload_length:
                                buffer = header_bytes + b"\n" + buffer
                                break
                            
                            payload = buffer[:payload_length]
                            buffer = buffer[payload_length:]
                        
                        # Handle the event
                        await self._handle_event(client_socket, event_type, header, payload)
                        
                    except json.JSONDecodeError:
                        _LOGGER.error(f"Invalid Wyoming protocol message: {header_bytes}")
                        continue
                    except Exception as e:
                        _LOGGER.error(f"Error processing Wyoming message: {e}")
                        continue
                
                # Yield to allow other tasks to run
                await asyncio.sleep(0)
                
        except (ConnectionResetError, BrokenPipeError):
            _LOGGER.debug(f"Client connection closed: {addr}")
        except Exception as e:
            _LOGGER.error(f"Error handling Wyoming client: {e}", exc_info=e)
        finally:
            try:
                client_socket.close()
            except:
                pass
    
    async def _handle_event(self, client_socket, event_type, header, payload):
        """Handle a Wyoming protocol event."""
        loop = asyncio.get_event_loop()
        
        if event_type == WYOMING_EVENT_PING:
            # Respond with a pong
            pong = {"type": WYOMING_EVENT_PONG}
            await loop.sock_sendall(client_socket, json.dumps(pong).encode("utf-8") + b"\n")
            
        elif event_type == WYOMING_EVENT_RUN_SATELLITE:
            # Extract data from the event
            data = header.get("data", {})
            pipeline_id = data.get("pipeline_id")
            conversation_id = data.get("conversation_id")
            extra_system_message = data.get("extra_system_message", "")
            
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
        else:
            _LOGGER.debug(f"Received unhandled Wyoming event type: {event_type}")
    
    def _port_in_use(self, host: str, port: int) -> bool:
        """Check if the specified port is already in use."""
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            return s.connect_ex((host, port)) == 0
    
    def _register_zeroconf(self, host: str, port: int) -> None:
        """Register the satellite service with Zeroconf using the actual IP address."""
        try:
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
            
            # Run Zeroconf registration in a separate thread to avoid blocking the event loop
            def register_zeroconf():
                try:
                    _LOGGER.debug("Starting Zeroconf registration in thread")
                    zc = Zeroconf()
                    zc.register_service(info)
                    _LOGGER.info(f"Wyoming satellite registered via Zeroconf: omni_assist_{self.device_id}")
                    return zc
                except Exception as e:
                    _LOGGER.error(f"Failed to register Zeroconf in thread: {e}")
                    return None

            # Use a thread pool executor to run the registration
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(register_zeroconf)
                try:
                    self.zeroconf = future.result(timeout=5)  # 5 second timeout
                except concurrent.futures.TimeoutError:
                    _LOGGER.warning("Zeroconf registration timed out, continuing without Zeroconf")
                except Exception as e:
                    _LOGGER.error(f"Error in Zeroconf thread: {e}")
                
        except ImportError as err:
            _LOGGER.error(f"Zeroconf package not available: {err}")
            # Try to continue without Zeroconf - satellite will need to be manually configured
            _LOGGER.warning("Wyoming satellite will need to be manually configured as Zeroconf is unavailable")
        except Exception as e:
            _LOGGER.error(f"Error registering with Zeroconf: {e}", exc_info=e)
    
    async def stop(self) -> None:
        """Stop the Wyoming satellite server and clean up resources."""
        _LOGGER.info("Stopping Wyoming satellite server")
        
        # Stop accepting new connections
        self._running = False
        
        # Close the server socket
        if self._server_socket:
            try:
                self._server_socket.close()
            except Exception as e:
                _LOGGER.error(f"Error closing server socket: {e}")
            self._server_socket = None
        
        # Wait for all client connections to close
        if self._clients:
            wait_time = 2  # Wait up to 2 seconds for clients to disconnect
            done, pending = await asyncio.wait(self._clients, timeout=wait_time)
            if pending:
                _LOGGER.warning(f"{len(pending)} Wyoming client connections did not close gracefully")
        
        # Unregister from Zeroconf in a thread to avoid blocking
        if self.zeroconf:
            _LOGGER.debug("Unregistering Wyoming satellite from Zeroconf")
            try:
                # Run Zeroconf unregistration in a separate thread
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    def close_zeroconf():
                        try:
                            self.zeroconf.unregister_all_services()
                            self.zeroconf.close()
                        except Exception as e:
                            _LOGGER.debug(f"Error during Zeroconf cleanup: {e}")
                    
                    # Run with a timeout to prevent blocking
                    future = executor.submit(close_zeroconf)
                    try:
                        future.result(timeout=3)  # 3 second timeout
                    except concurrent.futures.TimeoutError:
                        _LOGGER.warning("Zeroconf unregistration timed out")
            except Exception as e:
                _LOGGER.error(f"Error during Zeroconf cleanup: {e}", exc_info=False)
            self.zeroconf = None 