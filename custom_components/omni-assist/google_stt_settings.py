"""
Functions for configuring Google Cloud STT settings to improve VAD behavior.
"""
import logging
from typing import Any, Dict, Optional

_LOGGER = logging.getLogger(__name__)

def get_google_stt_options() -> Dict[str, Any]:
    """
    Return optimized Google STT configuration options.
    These settings help prevent premature stream termination.
    """
    return {
        "singleUtterance": False,  # Crucial: Prevents early termination after a single utterance
        "interimResults": True,    # Useful: Keeps connection active with partial results
        "model": "default",        # Use default model 
        "enableWordTimeOffsets": False,
        "maxAlternatives": 1,
    }

def configure_google_stt_pipeline(pipeline_options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Configure the Google STT options for the pipeline.
    
    Args:
        pipeline_options: Existing pipeline options to update
        
    Returns:
        Updated pipeline options dictionary
    """
    if pipeline_options is None:
        pipeline_options = {}
    
    # Create or update the 'stt_options' key in the pipeline options
    if "stt_options" not in pipeline_options:
        pipeline_options["stt_options"] = {}
    
    # Add the Google STT specific configuration
    pipeline_options["stt_options"]["google_cloud_speech_to_text"] = get_google_stt_options()
    
    _LOGGER.debug("Configured Google STT options with improved VAD behavior")
    return pipeline_options 