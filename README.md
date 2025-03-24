[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

# Omni-Assist

# NOTE: I make changes to this frequently that may break things! Use at your own risk.

This fork is an attempt at implementing continued conversation and other features within Stream Assist.

Features added so far:
* Continued conversation: conversation_id is preserved between interactions for a given entry with a timeout of 300 seconds, though for now the timeout doesn't seem to work consistently
* Wake Word skip: The wake word phase can be automatically skipped on follow-up interactions
    * Still a work in progress, very buggy
* STT End media: Now both the start and end of the STT phase have options for audio feedback. Also added a field for this in the config options
* STT Error media: When there is an error in the STT phase (like no-text-recognized), a distinct error sound can be played
* More initial config options: All available options are now in the initial config screen
* Added cancel phrases like: "nevermind", "never mind", "thank you", "cancel that", "cancel",
    "abort", "quit", "exit", "end", "forget it", "that's all", "that is all" so that you can cancel the continuous conversation feature

Future goals:
* Globally continued conversations: Add the option to pass conversation IDs across all integration entries
    * This would require the integration to also provide updated area and device information
* Expose more of the integration to automations
    * Example: A service call that allows you to select an integration entry and trigger the assistant pipeline at the intent phase with predefined data

## Virtual Satellite Mode

Omni-Assist now includes full Wyoming Protocol support, allowing you to expose your integration instances as virtual satellites. This provides several benefits:

- **Multiple Pipeline Configurations**: Each satellite can have its own pipeline configuration
- **Room Organization**: Satellites can be organized by room for easier management
- **Custom Audio Processing**: Audio settings (noise suppression, auto gain, volume) can be customized per satellite
- **Native Integration**: Works with Home Assistant's built-in satellite infrastructure
- **Zeroconf Discovery**: Virtual satellites are automatically discovered by Home Assistant

### How It Works

The virtual satellite feature implements the Wyoming Protocol, which is used by Home Assistant for voice satellite communication. Each Omni-Assist instance running in satellite mode:

1. Opens a TCP server on the configured port
2. Registers with Zeroconf for automatic discovery
3. Handles Wyoming Protocol messages for audio input/output
4. Connects to the Home Assistant assist pipeline
5. Streams TTS responses back to connected clients

### Satellite Setup

1. Create a new Omni-Assist integration
2. Enable "Satellite Mode" toggle
3. Configure the satellite options:
   - **Satellite Room**: Name of the room where the satellite is located (displayed in Home Assistant UI)
   - **Satellite Port**: Port number for the Wyoming server (default: 10600)
   - **Audio Settings**:
     - **Noise Suppression Level**: 0-4 (0 = disabled)
     - **Auto Gain (dBFS)**: -31 to 0 (0 = disabled)
     - **Volume Multiplier**: 0.1 to 5.0 (1.0 = normal volume)

**Important**: Each satellite needs a unique port number.

### Using with Other Wyoming Clients

These virtual satellites are also compatible with other Wyoming Protocol clients:

- Wyoming Satellite devices (like ESP32-based satellites)
- Other Home Assistant instances
- Rhasspy voice assistants

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
