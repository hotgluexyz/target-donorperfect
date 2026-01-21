"""DonorPerfect target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.target_sdk.target import TargetHotglue
from target_donorperfect.sinks import (
    DonorsSink,
    ContactsSink,
)


class TargetDonorPerfect(TargetHotglue):
    """Sample target for DonorPerfect."""

    name = "target-donorperfect"
    config_jsonschema = th.PropertiesList(
        th.Property(
            "api_token",
            th.StringType,
            description="The path to the target output file"
        )
    ).to_dict()

    SINK_TYPES = [
        DonorsSink,
        ContactsSink,
    ]


if __name__ == "__main__":
    TargetDonorPerfect.cli()