import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceResponse, SupportsResponse, ServiceCall
from homeassistant.helpers.device_registry import DeviceEntry
from homeassistant.helpers.typing import ConfigType

from .core import DOMAIN, get_stream_source, assist_run, stream_run
from .core.stream import Stream
from .wyoming_satellite import OmniAssistWyomingSatellite

_LOGGER = logging.getLogger(__name__)

PLATFORMS = (Platform.SENSOR, Platform.SWITCH)


async def async_setup(hass: HomeAssistant, config: ConfigType):
    async def run(call: ServiceCall) -> ServiceResponse:
        """Run the Omni-Assist pipeline with enhanced context handling."""
        # Get parameters
        pipeline_id = call.data.get("pipeline_id")
        conversation_id = call.data.get("conversation_id")
        extra_system_message = call.data.get("extra_system_message", "")
        
        _LOGGER.debug(f"Omni-Assist run called: pipeline={pipeline_id}, conversation_id={conversation_id}")
        
        # Create context with appropriate extra data
        context = call.context
        if not hasattr(context, "extra_data"):
            context.extra_data = {}
        
        # Add the extra system message to context
        if extra_system_message:
            _LOGGER.debug(f"Adding system message to pipeline context: {extra_system_message[:50]}...")
            context.extra_data["system_message"] = extra_system_message
        
        # Stream initialization
        stt_stream = Stream()
        
        try:
            # Run stream collection in background with explicit name
            coro = stream_run(hass, call.data, stt_stream=stt_stream)
            hass.async_create_background_task(coro, "omni_assist_stream_run")
            
            # Run the pipeline with the enhanced context
            return await assist_run(
                hass, 
                call.data,
                context=context,
                conversation_id=conversation_id,
                stt_stream=stt_stream
            )
        except Exception as e:
            _LOGGER.error("Error in omni_assist.run service", exc_info=e)
            return {"error": {"type": str(type(e)), "message": str(e)}}
        finally:
            stt_stream.close()

    hass.services.async_register(
        DOMAIN, "run", run, supports_response=SupportsResponse.OPTIONAL
    )

    return True


async def async_setup_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Set up Omni-Assist from a config entry."""
    # Convert data to options if needed
    if config_entry.data:
        hass.config_entries.async_update_entry(
            config_entry, data={}, options=config_entry.data
        )

    # Add update listener for config changes
    if not config_entry.update_listeners:
        config_entry.add_update_listener(async_update_options)

    # Initialize data dictionary
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][config_entry.entry_id] = {}
    
    # Set up Wyoming satellite if enabled
    if config_entry.options.get("enable_wyoming_satellite", True):
        try:
            # Try to import Wyoming to check if it's available
            import homeassistant.components.wyoming
            _LOGGER.debug("Wyoming integration is available")
            
            device_id = config_entry.entry_id[:7]
            port = config_entry.options.get("wyoming_port", 10700)
            
            _LOGGER.info(f"Initializing Wyoming virtual satellite on port {port}")
            
            # Create and start the satellite
            satellite = OmniAssistWyomingSatellite(hass, device_id, config_entry)
            success = await satellite.start(port=port)
            
            if success:
                # Store satellite reference for later use
                hass.data[DOMAIN][config_entry.entry_id]["satellite"] = satellite
            else:
                _LOGGER.warning("Failed to start Wyoming virtual satellite")
        except ImportError:
            _LOGGER.warning("Wyoming integration not installed - satellite functionality disabled")
        except Exception as e:
            _LOGGER.error(f"Error setting up Wyoming satellite: {e}", exc_info=e)
    
    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(config_entry, PLATFORMS)
    
    return True


async def async_unload_entry(hass: HomeAssistant, config_entry: ConfigEntry):
    """Unload a config entry."""
    # Stop the Wyoming satellite if it exists
    if DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]:
        satellite = hass.data[DOMAIN][config_entry.entry_id].get("satellite")
        if satellite:
            _LOGGER.info("Stopping Wyoming satellite during integration unload")
            await satellite.stop()
    
    # Unload platforms
    unload_ok = await hass.config_entries.async_unload_platforms(config_entry, PLATFORMS)
    
    # Clean up data
    if unload_ok and DOMAIN in hass.data and config_entry.entry_id in hass.data[DOMAIN]:
        hass.data[DOMAIN].pop(config_entry.entry_id)
        if not hass.data[DOMAIN]:
            hass.data.pop(DOMAIN)
    
    return unload_ok


async def async_update_options(hass: HomeAssistant, config_entry: ConfigEntry):
    await hass.config_entries.async_reload(config_entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, config_entry: ConfigEntry, device_entry: DeviceEntry
) -> bool:
    return True


async def async_remove_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> None:
    pass
