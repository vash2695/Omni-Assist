"""Pipeline management module for Omni-Assist.

This module handles interactions with Home Assistant's assist pipeline,
including pipeline configuration, execution, and event handling.
"""

import logging
import re
import time
from typing import Callable, Dict, List, Optional, Any

from homeassistant.components import assist_pipeline
from homeassistant.components.assist_pipeline import (
    Pipeline,
    PipelineEvent,
    PipelineEventCallback,
    PipelineInput,
    PipelineRun,
)
from homeassistant.core import HomeAssistant, Context

_LOGGER = logging.getLogger(__name__)

# Phrases that trigger cancellation of the current interaction
CANCELLATION_PHRASES = [
    r'\bnevermind\b', r'\bnever mind\b', r'\bthank you\b', r'\bcancel that\b', 
    r'\bcancel\b', r'\babort\b', r'\bquit\b', r'\bexit\b', r'\bend\b', r'\bforget it\b', 
    r'\bthat\'s all\b', r'\bthat is all\b'
]


async def get_pipeline(hass: HomeAssistant, pipeline_id: str = None) -> Pipeline:
    """Get a pipeline by ID or create a default one if not specified."""
    if pipeline_id:
        return assist_pipeline.async_get_pipeline(hass, pipeline_id)
    
    pipelines = assist_pipeline.async_get_pipelines(hass)
    
    # Use preferred pipeline if available
    for pipeline in pipelines:
        if pipeline.name == "Preferred":
            return pipeline
    
    # Otherwise, use the first available pipeline
    if pipelines:
        return pipelines[0]
    
    # Create a default pipeline if none exist
    raise ValueError("No assist pipelines available. Please create one in Home Assistant settings.")


def is_cancellation_phrase(text: str) -> bool:
    """Check if the provided text contains a cancellation phrase."""
    text = text.lower()
    for pattern in CANCELLATION_PHRASES:
        if re.search(pattern, text):
            return True
    return False


async def run_pipeline(
    hass: HomeAssistant,
    data: Dict[str, Any],
    context: Optional[Context] = None,
    event_callback: Optional[PipelineEventCallback] = None,
    stt_stream = None,
    conversation_id: Optional[str] = None
) -> Dict[str, Any]:
    """Run the assist pipeline with the given configuration."""
    _LOGGER.debug(f"run_pipeline called with conversation_id: {conversation_id}")
    
    # Process assist_pipeline settings
    assist_options = data.get("assist", {})

    if pipeline_id := data.get("pipeline_id"):
        # get pipeline from pipeline ID
        pipeline = await get_pipeline(hass, pipeline_id)
    else:
        # get default pipeline
        pipeline = await get_pipeline(hass)

    # Configure pipeline input
    pipeline_input = PipelineInput(
        conversation_id=conversation_id,
        device_id=assist_options.get("device_id"),
        # Pass STT stream if available
        stt_stream=stt_stream,
        # Set wake word metadata
        wake_word_settings={},
    )

    # Run the pipeline
    pipeline_run = await assist_pipeline.async_pipeline_from_audio_stream(
        hass,
        context=context,
        event_callback=event_callback,
        pipeline_id=pipeline.id,
        pipeline_input=pipeline_input,
    )

    # Process results
    result = {"conversation_id": pipeline_run.conversation_id or conversation_id}
    
    # Check if this was a cancellation phrase
    if (
        pipeline_run.stt_output is not None and 
        pipeline_run.stt_output.text and
        is_cancellation_phrase(pipeline_run.stt_output.text)
    ):
        _LOGGER.debug(f"Cancellation phrase detected: '{pipeline_run.stt_output.text}'")
        result["cancelled"] = True
    
    return result 