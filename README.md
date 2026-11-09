# target-donorperfect

`target-donorperfect` is a Singer target for [DonorPerfect](https://www.donorperfect.com/), built with the Hotglue Singer SDK.

## Overview

This target allows you to sync data to DonorPerfect via their XML API. It currently supports the following streams:

- **Donors** - Create and update donor records
- **Contacts** - Create and update contact records
- **Gifts** - Create and update gift records
- **Donor Flag Association** - Associate existing FLAG codes with a donor (`dp_saveflag_xml`)
- **Donor Memberships** - Update membership UDFs on matched donors only (`dp_save_udf_xml`)

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
| `api_token` | Your DonorPerfect API key |
| `membership_udf_fields` | Optional. Required for `donor_memberships`: DP UDF field names for `member_id`, `membership_description`, and `membership_expiration_date` |

Create a `config.json` file:

```json
{
  "api_token": "your-donorperfect-api-key",
  "membership_udf_fields": {
    "member_id": "AMILIA_ID",
    "membership_description": "MEMB_LEVEL",
    "membership_expiration_date": "MEMB_EXP"
  }
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
