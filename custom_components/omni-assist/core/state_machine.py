"""State management module for Omni-Assist.

This module formalizes the state management of the assist pipeline processing stages,
making the transitions between states more predictable and manageable.
"""

import asyncio
import logging
from enum import Enum
from typing import Dict, Any, Optional, Callable

from homeassistant.components.assist_pipeline import (
    PipelineEvent,
    PipelineEventType,
    PipelineEventCallback,
)
from homeassistant.const import STATE_IDLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

_LOGGER = logging.getLogger(__name__)

# List of event types that the integration tracks
EVENTS = ["wake", "stt", "intent", "tts"]


class PipelineState(Enum):
    """Possible states for each stage of the pipeline."""
    IDLE = "idle"
    START = "start"
    RUNNING = "running"
    END = "end"
    ERROR = "error"


class OmniAssistStateMachine:
    """State machine for tracking pipeline states."""

    def __init__(self, hass: HomeAssistant, unique_id: str):
        """Initialize the state machine."""
        self.hass = hass
        self.unique_id = unique_id
        self.states = {event: PipelineState.IDLE for event in EVENTS}
        
        # Event type mapping for standard pipeline events
        self.event_type_mapping = {
            "wake_word-start": ("wake", PipelineState.START),
            "wake_word-end": ("wake", PipelineState.END),
            "stt-start": ("stt", PipelineState.START),
            "stt-end": ("stt", PipelineState.END),
            "intent-start": ("intent", PipelineState.START),
            "intent-end": ("intent", PipelineState.END),
            "tts-start": ("tts", PipelineState.START),
            "tts-end": ("tts", PipelineState.RUNNING),  # Special case for TTS
            "run-start": ("run", PipelineState.START),
            "run-end": ("run", PipelineState.END),
            "reset-after-tts": (None, None),  # Special event for resetting after TTS
        }
    
    def update_state(self, event_type: str, state: PipelineState, data: Optional[Dict[str, Any]] = None):
        """Update the state for a specific event type."""
        if event_type not in EVENTS and event_type != "run":
            _LOGGER.warning(f"Attempted to update unknown event type: {event_type}")
            return
            
        if event_type in EVENTS:
            self.states[event_type] = state
            
        # Dispatch state update to any listeners
        # Always send the enum value for consistent handling
        state_value = state.value if state != PipelineState.IDLE else STATE_IDLE
        self.hass.loop.call_soon_threadsafe(
            async_dispatcher_send, 
            self.hass, 
            f"{self.unique_id}-{event_type}",
            state_value, 
            data
        )
        
    def reset_states(self):
        """Reset all states to IDLE."""
        for event in EVENTS:
            self.update_state(event, PipelineState.IDLE)
            
    def reset_after_tts(self):
        """Reset states after TTS playback completes."""
        # Wake goes back to START state, ready for next command
        self.update_state("wake", PipelineState.START)
        
        # Other stages go back to IDLE
        self.update_state("stt", PipelineState.IDLE)
        self.update_state("intent", PipelineState.IDLE)
        self.update_state("tts", PipelineState.IDLE)
            
    def handle_pipeline_event(self, event: PipelineEvent):
        """Process a pipeline event and update states accordingly."""
        _LOGGER.debug(f"Processing pipeline event: {event.type}")
        
        # Handle reset-after-tts special event
        if event.type == "reset-after-tts":
            _LOGGER.debug("TTS playback complete, resetting states")
            self.reset_after_tts()
            return
            
        # Handle pipeline error events
        if event.type == PipelineEventType.ERROR:
            code = event.data.get("code", "error")
            
            # Determine which stage had the error
            if "wake_word" in code:
                stage = "wake"
            elif "stt" in code:
                stage = "stt" 
            elif "intent" in code:
                stage = "intent"
            elif "tts" in code:
                stage = "tts"
            else:
                stage = "error"
                
            _LOGGER.debug(f"Error in stage {stage}: {code}")
            self.update_state(stage, PipelineState.ERROR, event.data)
            
            # After an error, wake entity should return to "start" state
            if stage != "wake":  # Only if the error wasn't in the wake stage
                self.update_state("wake", PipelineState.START)
            return
        
        # Process standard pipeline events
        if event.type in self.event_type_mapping:
            stage, state = self.event_type_mapping[event.type]
            
            # Skip processing for special events that need custom handling
            if stage is None or state is None:
                return
                
            _LOGGER.debug(f"Updating state for {stage} to {state}")
            self.update_state(stage, state, event.data)
            return
            
        # Try to parse non-standard event types
        try:
            if "-" in event.type:
                raw_stage, state_name = event.type.split("-", 1)
                
                # Convert wake_word to wake
                stage = "wake" if raw_stage == "wake_word" else raw_stage
                
                # Map state name to PipelineState
                try:
                    state = PipelineState(state_name)
                except ValueError:
                    # If not a valid PipelineState, use appropriate default
                    state = PipelineState.START if state_name == "start" else PipelineState.END
                
                if stage in EVENTS:
                    _LOGGER.debug(f"Parsed custom event: {stage} to {state}")
                    self.update_state(stage, state, event.data)
            else:
                _LOGGER.warning(f"Unhandled event type: {event.type}")
        except Exception as e:
            _LOGGER.error(f"Error processing event {event.type}: {e}")


def create_event_callback(state_machine: OmniAssistStateMachine) -> PipelineEventCallback:
    """Create an event callback function that uses the state machine."""
    
    def event_callback(event: PipelineEvent):
        """Process pipeline events and update the state machine."""
        state_machine.handle_pipeline_event(event)
        
    return event_callback 