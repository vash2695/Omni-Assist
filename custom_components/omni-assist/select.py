import logging
from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.components.assist_pipeline import (
    async_get_pipelines,
    # We don't import the listener function directly
    # async_get_pipeline is used to get the default pipeline
    async_get_pipeline,
    # Import the DATA_STORE constant to access the store
    DATA_STORE,
    PipelineStorageCollection, # Import the type hint for the store if needed
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

    # Check if the pipeline store is available
    if DATA_STORE not in hass.data:
        _LOGGER.error("Assist pipeline store not initialized.")
        # Optionally wait for it? Or just fail setup for select?
        # For now, let entity setup proceed, it might become available later
        # or log an error if needed during entity operation.
        pass # Allow entity creation even if store isn't ready yet

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
        # We will call _update_pipeline_options in async_added_to_hass
        # self._update_pipeline_options() # Initial population deferred

        _LOGGER.debug(f"Initialized OmniAssistPipelineSelect: {self.unique_id}")

    @callback
    def _update_pipeline_options(self) -> None:
        """Update the available pipeline options."""
        # Ensure hass is available before accessing pipelines
        if not self.hass:
            _LOGGER.warning(f"Hass object not available yet for {self.unique_id}, cannot update options.")
            return

        pipelines = async_get_pipelines(self.hass)
        new_pipelines_map = {p.id: p.name for p in pipelines}
        # Ensure default pipeline is included if available
        if default_pipeline := async_get_pipeline(self.hass, None):
             if default_pipeline.id not in new_pipelines_map:
                  new_pipelines_map[default_pipeline.id] = default_pipeline.name

        # Check if pipelines actually changed before updating state
        if self._pipelines == new_pipelines_map:
            _LOGGER.debug(f"Pipeline options unchanged for {self.unique_id}")
            return

        self._pipelines = new_pipelines_map

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
             invalid_name = f"Invalid: {current_id[:8]}..." # Show partial ID
             pipeline_names.append(invalid_name)
             # Store the invalid ID -> Name mapping too for reverse lookup
             self._pipelines[current_id] = invalid_name


        self._attr_options = sorted(pipeline_names) # Sort options alphabetically
        _LOGGER.debug(f"Updated pipeline options for {self.unique_id}: {self._attr_options}")

    @property
    def current_option(self) -> str | None:
        """Return the currently selected pipeline name."""
        # Ensure options are populated if not done yet
        if not self._pipelines and self.hass:
            self._update_pipeline_options()

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

        if selected_pipeline_id is None or option.startswith("Invalid:"):
            _LOGGER.error(f"Could not find valid pipeline ID for selected name: {option}")
            return

        _LOGGER.debug(f"Updating config entry options with pipeline_id: {selected_pipeline_id}")
        # Update the config entry options
        new_options = self._config_entry.options.copy()
        new_options[OPTION_PIPELINE] = selected_pipeline_id

        self.hass.config_entries.async_update_entry(
            self._config_entry, options=new_options
        )
        # Update state immediately after changing option
        self.async_write_ha_state()


    async def async_added_to_hass(self) -> None:
        """Run when entity about to be added to hass."""
        await super().async_added_to_hass()
        # Register a listener for pipeline changes using the store object
        if DATA_STORE in self.hass.data:
            store: PipelineStorageCollection = self.hass.data[DATA_STORE]
            self.async_on_remove(
                store.async_add_listener(self._handle_pipeline_update)
            )
            _LOGGER.debug(f"Added listener to pipeline store for {self.unique_id}")
        else:
            _LOGGER.warning(f"Pipeline store not found in hass.data when adding {self.unique_id}. Options may not update dynamically.")

        # Initial update after adding to hass and potentially connecting listener
        self._update_pipeline_options()

    @callback
    def _handle_pipeline_update(self) -> None:
        """Handle updates to the pipeline store."""
        _LOGGER.debug(f"Pipeline store updated signal received, refreshing options for {self.unique_id}")
        self._update_pipeline_options()
        self.async_write_ha_state() # Update HA state after options change