"""DonorPerfect target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.helpers.capabilities import AlertingLevel
from hotglue_singer_sdk.target_sdk.target import TargetHotglue
from target_donorperfect.sinks import (
    DonorsSink,
    ContactsSink,
    GiftsSink,
)


class TargetDonorPerfect(TargetHotglue):
    """Sample target for DonorPerfect."""

    name = "target-donorperfect"
    alerting_level = AlertingLevel.WARNING
    config_jsonschema = th.PropertiesList(
        th.Property(
            "api_token",
            th.StringType,
            required=True,
            description="The DonorPerfect API key"
        )
    ).to_dict()

    SINK_TYPES = [
        DonorsSink,
        ContactsSink,
        GiftsSink,
    ]


if __name__ == "__main__":
    TargetDonorPerfect.cli()