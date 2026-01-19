# target-donorperfect

`target-donorperfect` is a Singer target for [DonorPerfect](https://www.donorperfect.com/), built with the Hotglue Singer SDK.

## Overview

This target allows you to sync data to DonorPerfect via their XML API. It currently supports the following streams:

- **Donors** - Create and update donor records
- **Contacts** - Create and update contact records

## Installation

```bash
pip install target-donorperfect
```

Or install from source:

```bash
git clone https://github.com/hotglue/target-donorperfect.git
cd target-donorperfect
pip install .
```

## Configuration

### Required Settings

| Setting   | Description          |
|-----------|----------------------|
| `api_key` | Your DonorPerfect API key |

Create a `config.json` file:

```json
{
  "api_key": "your-donorperfect-api-key"
}
```

### Environment Variables

You can also configure the target using environment variables. Set `TARGET_DONORPERFECT_API_KEY` to your API key.

## Usage

### Running the Target

```bash
# Display version
target-donorperfect --version

# Display help
target-donorperfect --help

# Run with a tap
tap-some-source | target-donorperfect --config config.json
```

## Development

### Setup

```bash
# Install poetry
pipx install poetry

# Install dependencies
poetry install
```

### Running Tests

```bash
poetry run pytest
```

### CLI Testing

```bash
poetry run target-donorperfect --help
```

## License

Apache 2.0
