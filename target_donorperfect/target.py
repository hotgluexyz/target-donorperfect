"""DonorPerfect target class."""

from hotglue_singer_sdk import typing as th
from hotglue_singer_sdk.helpers.capabilities import AlertingLevel
from hotglue_singer_sdk.target_sdk.target import TargetHotglue
from target_donorperfect.sinks import (
    DonorsSink,
    ContactsSink,
    GiftsSink,
    DonorFlagAssociationSink,
    DonorMembershipsSink,
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
        ),
        th.Property(
            "membership_udf_fields",
            th.ObjectType(
                th.Property(
                    "member_id",
                    th.StringType,
                    description="DP UDF field name for Amilia Member ID",
                ),
                th.Property(
                    "membership_description",
                    th.StringType,
                    description="DP UDF field name for Membership Level Description",
                ),
                th.Property(
                    "membership_expiration_date",
                    th.StringType,
                    description="DP UDF field name for Membership Expiration Date",
                ),
            ),
            description=(
                "DP UDF field-name mappings required when writing the "
                "donor_memberships stream"
            ),
        ),
    ).to_dict()

    SINK_TYPES = [
        DonorsSink,
        ContactsSink,
        GiftsSink,
        DonorFlagAssociationSink,
        DonorMembershipsSink,
    ]


if __name__ == "__main__":
    TargetDonorPerfect.cli()