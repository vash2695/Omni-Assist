import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.components import assist_pipeline
from homeassistant.components.camera import CameraEntityFeature
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import ConfigFlow, ConfigEntry, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import entity_registry
from homeassistant.data_entry_flow import FlowResult

from .core import DOMAIN

# Add translations for errors
ERRORS = {
    "invalid_port": "Port number must be between 1024 and 65535"
}

async def get_pipelines(hass):
    """Get available pipelines from Home Assistant."""
    pipelines = await hass.components.assist_pipeline.async_get_pipelines(hass)
    return [{"id": p.id, "name": p.name} for p in pipelines]

class ConfigFlowHandler(ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        if user_input:
            title = user_input.pop("name")
            return self.async_create_entry(title=title, data=user_input)

        reg = entity_registry.async_get(self.hass)
        cameras = [
            k
            for k, v in reg.entities.items()
            if v.domain == "camera"
            and v.supported_features & CameraEntityFeature.STREAM
        ]
        players = [
            k
            for k, v in reg.entities.items()
            if v.domain == "media_player"
            and v.supported_features & MediaPlayerEntityFeature.PLAY_MEDIA
        ]

        pipelines = {
            p.id: p.name for p in assist_pipeline.async_get_pipelines(self.hass)
        }
        
        return self.async_show_form(
            step_id="user",
            data_schema=vol_schema(
                {
                    vol.Required("name"): str,
                    vol.Exclusive("stream_source", "url"): str,
                    vol.Exclusive("camera_entity_id", "url"): vol.In(cameras),
                    vol.Optional("player_entity_id"): cv.multi_select(players),
                    vol.Optional("stt_start_media"): str,
                    vol.Optional("stt_end_media"): str,
                    vol.Optional("stt_error_media"): str,
                    vol.Optional("pipeline_id"): vol.In(pipelines),
                    vol.Optional("satellite_mode", default=False): bool,
                    vol.Optional("satellite_room"): str,
                },
                user_input,
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle a option flow for Omni-Assist."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        """Initialize options flow."""
        self.config_entry = config_entry
        self.options = dict(config_entry.options)

    async def async_step_init(self, user_input=None):
        """Handle options flow."""
        if user_input is not None:
            # Toggle satellite mode if needed
            if user_input.get("satellite_mode", False) != self.options.get("satellite_mode", False):
                # If enabling satellite mode, make sure we have required fields
                if user_input.get("satellite_mode", False):
                    return await self.async_step_satellite()
                
            # Update options and return
            return self.async_create_entry(title="", data=user_input)

        # Get list of pipeline ids
        pipelines = await get_pipelines(self.hass)
        pipeline_ids = [p["id"] for p in pipelines]
        
        all_options = {
            vol.Optional(
                "satellite_mode",
                default=self.options.get("satellite_mode", False),
            ): bool,
            vol.Optional(
                "pipeline_id",
                default=self.options.get("pipeline_id", ""),
            ): vol.In([""] + pipeline_ids),
        }
            
        return self.async_show_form(
            step_id="init", data_schema=vol.Schema(all_options)
        )
        
    async def async_step_satellite(self, user_input=None):
        """Configure satellite options."""
        errors = {}
        
        if user_input is not None:
            # Validate port
            try:
                port = int(user_input.get("satellite_port", 10600))
                
                # Check if port is valid
                if port < 1024 or port > 65535:
                    errors["satellite_port"] = "invalid_port"
                else:
                    # Merge options from previous step
                    merged_options = {**self.options, **user_input}
                    
                    # Make sure satellite_mode is enabled
                    merged_options["satellite_mode"] = True
                    
                    return self.async_create_entry(title="", data=merged_options)
            except ValueError:
                errors["satellite_port"] = "invalid_port"
                
        schema = vol.Schema({
            vol.Required(
                "satellite_room",
                default=self.options.get("satellite_room", ""),
            ): str,
            vol.Required(
                "satellite_port",
                default=self.options.get("satellite_port", 10600),
            ): int,
        })
        
        return self.async_show_form(
            step_id="satellite", 
            data_schema=schema,
            errors=errors,
        )


def vol_schema(schema: dict, defaults: dict) -> vol.Schema:
    schema = {k: v for k, v in schema.items() if not empty(v)}

    if defaults:
        for key in schema:
            if key.schema in defaults:
                key.default = vol.default_factory(defaults[key.schema])

    return vol.Schema(schema)


def empty(v) -> bool:
    if isinstance(v, vol.In):
        return len(v.container) == 0
    if isinstance(v, cv.multi_select):
        return len(v.options) == 0
    return False
