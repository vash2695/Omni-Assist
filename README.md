[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

# Omni-Assist

# NOTE: I make changes to this frequently that may break things! Use at your own risk.

This rework is an attempt at implementing continued conversation and other features within Stream Assist.

Features added so far:
* Continued conversation: conversation_id is preserved between interactions for a given entry with a timeout of 300 seconds, though for now the timeout doesn't seem to work consistently
* STT End media: Now both the start and end of the STT phase have options for audio feedback. Also added a field for this in the config options
* STT Error media: When there is an error in the STT phase (like no-text-recognized), a distinct error sound can be played
* More initial config options: All available options are now in the initial config screen
* Added cancel phrases like: "nevermind", "never mind", "thank you", "cancel that", "cancel",
    "abort", "quit", "exit", "end", "forget it", "that's all", "that is all" so that you can cancel the continuous conversation feature
* Improved pipeline state tracking: Each stage of the pipeline is more accurately represented by the state sensors (wake, stt, intent, tts), with tts accurately representing the duration of playback instead of jumping immediately to 'end'

Future goals:
* Globally continued conversations: Add the option to pass conversation IDs across all integration entries
    * This would require the integration to also provide updated area and device information
* Wake Word skip: Allow the wake word phase to be skipped on follow-up interactions
* Expose the integration to automations - Attempting to do this via Virtual Satellite support


Work in progress:
## Virtual Satellite Support

Omni-Assist now supports virtual satellites via the Wyoming Protocol. This allows you to create multiple instances of the integration, with some configured as satellites for different rooms.

### Key Features:
- Configure any Omni-Assist instance as a satellite by enabling "Satellite Mode" in the integration options
- Each satellite exposes a Wyoming Protocol server that can be discovered by Home Assistant
- Status sensors for each satellite show the current state of wake word detection, speech processing, and more
- Omni-Assist acts as a bridge between Wyoming and Home Assistant's Assist pipeline

### Setting Up a Satellite
1. Create a new Omni-Assist integration in Home Assistant
2. In the integration options, enable "Satellite Mode"
3. Specify a room name and port number for the satellite
4. Select a pipeline to use for processing audio

Wyoming Protocol will handle all the complex details of managing the satellite, while Omni-Assist provides the bridge functionality to connect it to Home Assistant's pipeline.

### Common Issues and Solutions

If you encounter any of these issues with the virtual satellite implementation, here are the solutions:

#### Zeroconf Registration Issues
The integration now correctly awaits the Zeroconf instance acquisition and properly registers/unregisters services.

#### Thread Safety Violations 
State updates from different threads are now properly handled by using `hass.async_add_job` to schedule updates in the main event loop.

#### Wyoming Protocol Errors
Improved error handling for malformed JSON in the Wyoming Protocol communication, with better logging and error recovery.

#### Dispatcher Signal Handling
Thread-safe dispatcher signal handling ensures proper state updates across the integration.

## HACS Installation

1. Add the Omni-Assist repo to HACS as a custom repository
2. Install the "Omni-Assist" integration from HACS
3. Restart Home Assistant
4. Add the integration via the integrations page

## Installation

1. Using **HACS**. Add this repository as a custom repository
2. Add integration via Home Assistant interface: Configuration > Devices & Services > Add Integration > Omni-Assist

## Configuration

**RTSP camera and Media Player:**

<img src="https://github.com/AlexxIT/Telegram/assets/5478779/7cd9c3a8-3ccc-471b-9edb-7e5642de318e" width="600">

**Custom audio source and Pipeline:**

<img src="https://github.com/AlexxIT/Telegram/assets/5478779/99ae1e62-1be0-4da2-a4ad-d4fdc1d2fc5e" width="600">

**Satellite Mode Configuration:**

<img src="https://user-images.githubusercontent.com/5478779/283281537-f4d0fe3c-6bf9-4db7-971f-e31a6d2a4632.png" width="600">

**Entities:**

<img src="https://github.com/AlexxIT/Telegram/assets/5478779/0c051f3e-7307-4307-97cf-69f3a9b30ccc" width="600">

## Trademarks

Home Assistant is a trademark of the Home Assistant Foundation, a 501(c)(6) nonprofit corporation, registered in the United States. This project is not affiliated with the Home Assistant Foundation.
