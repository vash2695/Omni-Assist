import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.components.assist_pipeline import (
    async_get_pipelines,
    pipeline_store,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .core import DOMAIN # Import DOMAIN for device info

_LOGGER = logging.getLogger(__name__)

# Configuration key for the selected pipeline in options
OPTION_PIPELINE = "pipeline_id"

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Omni-Assist select entities from a config entry."""
    _LOGGER.debug(f"Setting up OmniAssist Select for entry {config_entry.entry_id}")
    select_entity = OmniAssistPipelineSelect(config_entry)
    async_add_entities([select_entity])

class OmniAssistPipelineSelect(SelectEntity):
    """Select entity for choosing the Assist pipeline."""

    _attr_has_entity_name = True
    _attr_should_poll = False
    _attr_icon = "mdi:robot-happy-outline" # Specific icon for pipeline selection

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the select entity."""
        self._config_entry = config_entry
        self._attr_unique_id = f"{config_entry.entry_id[:7]}-pipeline"
        self._attr_name = f"{config_entry.title} Pipeline"

        # Link to the same device as other entities
        self._attr_device_info = DeviceInfo(
            name=config_entry.title,
            identifiers={(DOMAIN, config_entry.entry_id[:7])},
            # entry_type=DeviceEntryType.SERVICE - Inherited from core DeviceInfo setup
        )

        # Store pipelines (id -> name)
        self._pipelines: dict[str, str] = {}
        self._update_pipeline_options() # Initial population

        _LOGGER.debug(f"Initialized OmniAssistPipelineSelect: {self.unique_id}")

    @callback
    def _update_pipeline_options(self) -> None:
        """Update the available pipeline options."""
        pipelines = async_get_pipelines(self.hass)
        self._pipelines = {p.id: p.name for p in pipelines}
        # Ensure default pipeline is included if available
        if default_pipeline := pipeline_store.async_get_pipeline(self.hass, None):
             if default_pipeline.id not in self._pipelines:
                  self._pipelines[default_pipeline.id] = default_pipeline.name

        # Get list of pipeline names for the options attribute
        pipeline_names = list(self._pipelines.values())

        # If the currently selected pipeline ID is no longer valid,
        # add its ID to the options temporarily to avoid errors,
        # but log a warning. The user should select a valid one.
        current_id = self._config_entry.options.get(OPTION_PIPELINE)
        if current_id and current_id not in self._pipelines:
             _LOGGER.warning(
                 f"Currently selected pipeline '{current_id}' for {self.entity_id} "
                 "is no longer available. Please select a valid pipeline."
             )
             # Add the invalid ID temporarily so it shows up until changed
             pipeline_names.append(f"Invalid: {current_id}")
             # Store the invalid ID -> Name mapping too for reverse lookup
             self._pipelines[current_id] = f"Invalid: {current_id}"


        self._attr_options = pipeline_names
        _LOGGER.debug(f"Updated pipeline options for {self.unique_id}: {self._attr_options}")

    @property
    def current_option(self) -> str | None:
        """Return the currently selected pipeline name."""
        pipeline_id = self._config_entry.options.get(OPTION_PIPELINE)
        # Use the stored mapping to get the name from the ID
        return self._pipelines.get(pipeline_id)

    async def async_select_option(self, option: str) -> None:
        """Handle the user selecting a new pipeline."""
        _LOGGER.debug(f"User selected pipeline option: {option} for {self.unique_id}")
        # Find the pipeline ID corresponding to the selected name
        selected_pipeline_id = None
        for pid, name in self._pipelines.items():
            if name == option:
                selected_pipeline_id = pid
                break

        if selected_pipeline_id is None:
            _LOGGER.error(f"Could not find pipeline ID for selected name: {option}")
            return

        _LOGGER.debug(f"Updating config entry options with pipeline_id: {selected_pipeline_id}")
        # Update the config entry options
        new_options = self._config_entry.options.copy()
        new_options[OPTION_PIPELINE] = selected_pipeline_id

        self.hass.config_entries.async_update_entry(
            self._config_entry, options=new_options
        )
        # No need to call schedule_update_ha_state, HA handles it for selects

    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        # Register a listener for pipeline changes to update options dynamically
        self.async_on_remove(
            pipeline_store.async_listen_for_updates(self.hass, self._handle_pipeline_update)
        )
        # Initial update after adding to hass
        self._update_pipeline_options()

    @callback
    def _handle_pipeline_update(self) -> None:
        """Handle updates to the pipeline store."""
        _LOGGER.debug(f"Pipeline store updated, refreshing options for {self.unique_id}")
        self._update_pipeline_options()
        self.async_write_ha_state()