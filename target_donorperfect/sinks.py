"""DonorPerfect target sink class, which handles writing streams."""


from decimal import Decimal, InvalidOperation
from urllib.parse import unquote

from hotglue_etl_exceptions import InvalidPayloadError
from hotglue_singer_sdk.exceptions import FatalAPIError

from target_donorperfect.client import DonorPerfectSink


class DonorsSink(DonorPerfectSink):
    """DonorPerfect target sink class."""

    name = "donors"

    @staticmethod
    def _format_udf_date(value):
        """Convert ISO dates to DonorPerfect format MM/DD/YYYY"""
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return value
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            year, month, day = value[:10].split("-")
            return f"{month}/{day}/{year}"
        return value

    def _load_udf_field_types(self, field_names: list) -> dict:
        """Return {field_name: data_type} from sd_field for the given UDF names."""
        if not field_names:
            return {}
        cache = getattr(self, "_udf_data_types_cache", {})
        missing_fields = [name for name in field_names if name not in cache]
        if missing_fields:
            quoted_field_names = ",".join(f"'{self.escape_single_quotes(name)}'" for name in missing_fields)
            response = self.request_api(
                "GET",
                params={
                    "action": (
                        f"SELECT field_name, data_type FROM sd_field "
                        f"WHERE field_name IN ({quoted_field_names})"
                    ),
                },
            )
            for row in self.parse_xml_records(response.text):
                name = row.get("field_name")
                if name:
                    cache[name] = (row.get("data_type") or "").strip().upper()
            self._udf_data_types_cache = cache
        return {name: cache[name] for name in field_names if name in cache}

    def _udf_save_params(self, donor_id, field_name: str, data_type: str, value, user_id: str) -> dict:
        """Build a single dp_save_udf_xml request body."""
        fields = {
            "@matching_id": donor_id,
            "@field_name": field_name,
            "@data_type": data_type,
            "@char_value": None,
            "@date_value": None,
            "@number_value": None,
            "@user_id": user_id,
        }
        if data_type == "C":
            fields["@char_value"] = "" if value is None else str(value)
        elif data_type == "D":
            fields["@date_value"] = self._format_udf_date(value)
        elif data_type == "N":
            fields["@number_value"] = value
        else:
            raise InvalidPayloadError(
                f"Unsupported UDF data_type {data_type!r} for field {field_name!r}"
            )
        return {
            "action": "dp_save_udf_xml",
            "params": self.format_procedure_params(fields),
        }

    def write_donor_udfs(self, donor_id, udfs: dict, user_id="Amilia"):
        """Write each entry in udfs via dp_save_udf_xml using sd_field data types."""
        if not udfs:
            return
        field_types = self._load_udf_field_types(list(udfs.keys()))
        for field_name, value in udfs.items():
            data_type = field_types.get(field_name)
            if data_type is None:
                raise InvalidPayloadError(
                    f"UDF field {field_name!r} was not found in sd_field"
                )
            self.request_api(
                "GET",
                params=self._udf_save_params(donor_id, field_name, data_type, value, user_id),
            )

    def _resolve_existing_donor(self, record: dict, *, require_existing: bool = False):
        """Resolve a donor by explicit id/donor_id or email lookup, then validate it.

        Prefers record id/donor_id and falls back to find_existing_donor_id only when
        neither is provided. When a donor is selected, loads it and skips when the
        payload email does not match the donor's primary email.

        """
        donor_id = record.get("id", record.get("donor_id")) or 0
        matched_by_lookup = False
        if not donor_id:
            # no id (e.g. no snapshot yet) - try to match an existing donor to avoid creating duplicates
            donor_id = self.find_existing_donor_id(record) or 0
            matched_by_lookup = bool(donor_id)

        if not donor_id:
            if require_existing:
                self.logger.info(
                    f"Skipping unmatched donors record"
                    f"email={record.get('email')!r} "
                    f"name={record.get('first_name')!r} {record.get('last_name')!r}"
                )
                return 0, {}, False, {"_skip": True}
            return 0, {}, False, None

        response = self.request_api(
            "GET",
            params={
                "action": f"select * FROM dp WHERE donor_id='{donor_id}'",
                "apikey": unquote(self.config.get("api_token")),
            },
        )
        existing_record = self.parse_xml_response(response.text)
        if not existing_record:
            raise InvalidPayloadError(
                f"Not able to update donor record, no existing record found for donor_id: {donor_id}"
            )

        # ETL read flow emits one row per email (primary or alternate); only apply the primary-email row
        payload_email = record.get("email")
        primary_email = existing_record.get("email")
        if payload_email and primary_email and payload_email != primary_email:
            self.logger.info(
                f"Skipping donor {donor_id}: email {payload_email!r} does not match primary {primary_email!r}"
            )
            return (
                donor_id,
                existing_record,
                matched_by_lookup,
                {"donor_id": existing_record.get("donor_id", donor_id), "_skip": True},
            )

        return donor_id, existing_record, matched_by_lookup, None

    def preprocess_record(self, record: dict, context: dict) -> None:
        """Build the dp_savedonor request to create or update a donor.
        Updates if a donor ID is present or matched via find_existing_donor_id();
        otherwise creates a new donor. Matched updates preserve existing data by
        ignoring empty fields and only updating email_status when explicitly provided.
        Skips the whole record when payload email does not match the donor's primary email.

        When udfs are present, match only and don't create a new donor.
        Example UDF object on a donor ETL record:
            {
                "udfs": {
                    "FAMILY_ID": "10200193",
                    "GC_ITEM_DESCR": "Adult (ages 31+)",
                    "FUND_DT": "2025-10-26", 
                }
            }
        """
        udfs = record.pop("udfs", None)
        if udfs is not None:
            donor_id, existing_record, _, skip = self._resolve_existing_donor(
                record, require_existing=True
            )
            if skip:
                return skip
            return {
                "donor_id": existing_record.get("donor_id", donor_id),
                "udfs": udfs,
                "user_id": "Amilia",
                "_udfs_only": True,
            }

        params = {}
        params["action"] = "dp_savedonor"

        donor_id, existing_record, matched_by_lookup, skip = self._resolve_existing_donor(record)
        if skip:
            return skip

        if donor_id:
            # add donor_id to existing record for state, updates always return donor_id 0
            params["donor_id"] = existing_record.get("donor_id", 0)

            email_status_sent = not matched_by_lookup or record.get("email_status")
            if email_status_sent and record.get("email_status") != existing_record.get("email_status"):
                params["updated_email_status"] = record.get("email_status") or ""
                params["email_status_date"] = record.get("email_status_date") or ""

        # fill empty values with existing values
        if matched_by_lookup:
            # the source didn't target this donor explicitly, don't wipe existing data with empty values
            existing_record.update({k: v for k, v in record.items() if v not in (None, "")})
        else:
            existing_record.update(record)
        # process data
        # process record fields

        fields = {
            k: self.escape_single_quotes(v) for k, v in {
                "@donor_id": existing_record.get("donor_id", 0),
                "@first_name": existing_record.get("first_name", ""),
                "@last_name": existing_record.get("last_name", ""),
                "@middle_name": existing_record.get("middle_name", ""),
                "@suffix": existing_record.get("suffix", ""),
                "@title": existing_record.get("title", ""),
                "@salutation": existing_record.get("salutation", ""),
                "@prof_title": existing_record.get("prof_title", ""),
                "@opt_line": existing_record.get("opt_line", ""),
                "@address": existing_record.get("address", ""),
                "@address2": existing_record.get("address2", ""),
                "@city": existing_record.get("city", ""),
                "@state": existing_record.get("state", ""),
                "@zip": existing_record.get("zip", ""),
                # DonorPerfect's country column is varchar(30); longer values are rejected
                "@country": (existing_record.get("country") or "")[:30],
                "@address_type": existing_record.get("address_type", ""),
                "@home_phone": existing_record.get("home_phone", ""),
                "@business_phone": existing_record.get("business_phone", ""),
                "@fax_phone": existing_record.get("fax_phone", ""),
                "@mobile_phone": existing_record.get("mobile_phone", ""),
                "@email": existing_record.get("email", ""),
                "@org_rec": existing_record.get("org_rec", ""),
                "@donor_type": existing_record.get("donor_type", ""),
                "@nomail": existing_record.get("nomail", "") if donor_id else existing_record.get("nomail", "N"),
                "@nomail_reason": existing_record.get("nomail_reason", ""),
                "@email_status": existing_record.get("email_status", ""),
                "@email_status_date": existing_record.get("email_status_date", ""),
                "@narrative": existing_record.get("narrative", ""),
                "@donor_rcpt_type": existing_record.get("donor_rcpt_type", ""),
                "@user_id": existing_record.get("user_id", ""),
            }.items()
        }

        body = ",".join([f"{k}={v}" if not isinstance(v, str) else f"{k}='{v}'" for k, v in fields.items()])
        body = self.clean_body(body)
        params["params"] = body
        return params

    def upsert_record(self, record: dict, context: dict) -> None:
        """Upsert the record."""
        method = "GET"
        state_updates = dict()

        # get donor_id for updates
        donor_id = record.pop("donor_id", None)
        if record.pop("_skip", None):
            return donor_id, True, {"existing": True}

        if record.pop("_udfs_only", None):
            udfs = record.pop("udfs", {}) or {}
            self.write_donor_udfs(donor_id, udfs, user_id="Amilia")
            return donor_id, True, {"is_updated": True}

        updated_email_status = record.pop("updated_email_status", None)
        email_status_date = record.pop("email_status_date", None)

        # send request
        response = self.request_api(method, params=record)
        res_json = self.parse_xml_response(response.text)
        
        if updated_email_status is not None:
            safe_status = self.escape_single_quotes(updated_email_status)
            safe_date = self.escape_single_quotes(email_status_date)
            update_query = f"UPDATE DPADDRESS SET email_status='{safe_status}', email_status_date='{safe_date}' WHERE donor_id='{donor_id}'"
            self.request_api("GET", params={"action": update_query})

        if donor_id:
            state_updates['is_updated'] = True
            return donor_id, True, state_updates

        id = res_json.get("", None)
        return id, True, state_updates


class ContactsSink(DonorPerfectSink):
    """DonorPerfect target sink class."""

    name = "dp_contacts"
    relation_fields = [
        {"field": "donor_id", "objectName": "donors"},
    ]

    def preprocess_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        params = {}
        existing_record = {}
        contact_id = record.get("id", record.get("contact_id")) or 0

        if contact_id:
            response = self.request_api("GET", params={"action": f"select * FROM dpcontact WHERE contact_id='{contact_id}'", "apikey": unquote(self.config.get("api_token"))})
            existing_record = self.parse_xml_response(response.text)
            if not existing_record:
                self.logger.info(f"No existing record found for contact_id: {contact_id}")
            # add contact_id to existing record for state, updates always return contact_id 0
            params["contact_id"] = existing_record.get("contact_id", 0)

        # fill empty values with existing values
        existing_record.update(record)

        params["action"] = "dp_savecontact"
        fields = {
            k: self.escape_single_quotes(v) for k, v in {
            "@contact_id": existing_record.get("contact_id", 0),
            "@donor_id": existing_record.get("donor_id", ""),
            "@activity_code": existing_record.get("activity_code", ""),
            "@mailing_code": existing_record.get("mailing_code", ""),
            "@by_whom": existing_record.get("by_whom", ""),
            "@contact_date": existing_record.get("contact_date", ""),
            "@due_date": existing_record.get("due_date", ""),
            "@due_time": existing_record.get("due_time", ""),
            "@completed_date": existing_record.get("completed_date", ""),
            "@comment": existing_record.get("comment", ""),
            "@document_path": existing_record.get("document_path", ""),
            "@user_id": existing_record.get("user_id", ""),
            "@contact_email": existing_record.get("contact_email", ""),
            "@em_campaign_status": existing_record.get("em_campaign_status", ""),
            "@em_campaign": existing_record.get("em_campaign", ""),
            "@em_event_status_date": existing_record.get("em_event_status_date", ""),
            "@em_bounce_reason": existing_record.get("em_bounce_reason", ""),
            "@contact_state": existing_record.get("contact_state", "")
            }.items()
        }
        body = ",".join([f"{k}={v}" if not isinstance(v, str) else f"{k}='{v}'" for k, v in fields.items()])
        body = self.clean_body(body)
        params["params"] = body

        return params

    def upsert_record(self, record: dict, context: dict) -> None:
        """Upsert the record."""
        method = "GET"
        state_updates = dict()
        contact_id = record.pop("contact_id", None)

        response = self.request_api(method, params=record)
        res_json = self.parse_xml_response(response.text)
        if contact_id:
            state_updates['is_updated'] = True
            return contact_id, True, state_updates

        id = res_json.get("", None)
        return id, True, state_updates


class GiftsSink(DonorPerfectSink):
    """DonorPerfect gifts sink."""

    name = "gifts"
    relation_fields = [
        {"field": "donor_id", "objectName": "donors"},
    ]

    def _coerce_number(self, value, default=None):
        if value is None or value == "":
            return default
        if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
            return value
        try:
            if isinstance(value, str) and "." in value:
                return Decimal(value)
            return int(value)
        except (TypeError, ValueError, InvalidOperation):
            return value

    def _format_gift_date(self, value):
        if not value or not isinstance(value, str):
            return value or None
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            year, month, day = value[:10].split("-")
            return f"{month}/{day}/{year}"
        return value

    def _gift_fields(self, record: dict) -> dict:
        return {
            "@gift_id": self._coerce_number(record.get("gift_id"), default=0),
            "@donor_id": self._coerce_number(record.get("donor_id")),
            "@record_type": record.get("record_type") or "G",
            "@gift_date": self._format_gift_date(record.get("gift_date")),
            "@amount": self._coerce_number(record.get("amount")),
            "@gl_code": record.get("gl_code"),
            "@solicit_code": record.get("solicit_code"),
            "@sub_solicit_code": record.get("sub_solicit_code"),
            "@campaign": record.get("campaign"),
            "@gift_type": record.get("gift_type"),
            "@split_gift": record.get("split_gift") or "N",
            "@pledge_payment": record.get("pledge_payment") or "N",
            "@reference": record.get("reference"),
            "@transaction_id": self._coerce_number(record.get("transaction_id")),
            "@memory_honor": record.get("memory_honor"),
            "@gfname": record.get("gfname"),
            "@glname": record.get("glname"),
            "@fmv": self._coerce_number(record.get("fmv")),
            "@batch_no": self._coerce_number(record.get("batch_no"), default=0),
            "@gift_narrative": record.get("gift_narrative"),
            "@ty_letter_no": record.get("ty_letter_no"),
            "@glink": self._coerce_number(record.get("glink")),
            "@plink": self._coerce_number(record.get("plink")),
            "@nocalc": record.get("nocalc") or "N",
            "@receipt": record.get("receipt") or "Y",
            "@old_amount": self._coerce_number(record.get("old_amount")),
            "@user_id": record.get("user_id") or "Hotglue",
            "@gift_aid_date": record.get("gift_aid_date"),
            "@gift_aid_amt": self._coerce_number(record.get("gift_aid_amt")),
            "@gift_aid_eligible_g": record.get("gift_aid_eligible_g"),
            "@currency": record.get("currency"),
        }

    def preprocess_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        params = {}
        existing_record = {}
        gift_id = record.get("id", record.get("gift_id")) or 0

        if gift_id:
            response = self.request_api(
                "GET",
                params={
                    "action": f"select * FROM dpgift WHERE gift_id='{gift_id}'",
                    "apikey": unquote(self.config.get("api_token")),
                },
            )
            existing_record = self.parse_xml_response(response.text)
            if not existing_record:
                # Snapshot may still reference a gift deleted in DP; recreate instead of failing
                self.logger.info(
                    f"No existing record found for gift_id: {gift_id}. Creating a new gift"
                )
                record["gift_id"] = 0
            else:
                params["gift_id"] = existing_record.get("gift_id", 0)

        existing_record.update(record)
        params["action"] = "dp_savegift"
        params["params"] = self.format_procedure_params(self._gift_fields(existing_record))
        return params

    def upsert_record(self, record: dict, context: dict) -> None:
        """Upsert the record."""
        method = "GET"
        state_updates = dict()
        gift_id = record.pop("gift_id", None)

        response = self.request_api(method, params=record)
        res_json = self.parse_xml_response(response.text)
        if gift_id:
            state_updates["is_updated"] = True
            return gift_id, True, state_updates

        id = res_json.get("", None)
        return id, True, state_updates


class DonorFlagAssociationSink(DonorPerfectSink):
    """Associate an existing DonorPerfect FLAG code with a donor via dp_saveflag_xml.

    Does not create FLAG codes — only links a code from Code Maintenance onto a donor.
    Re-associating a flag that is already set is treated as success (idempotent).
    """

    name = "donor_flag_association"
    relation_fields = [
        {"field": "donor_id", "objectName": "donors"},
    ]

    def _flag_already_set(self, donor_id, flag: str) -> bool:
        """Return True when dpflags already has this donor_id + flag pair."""
        if not str(donor_id).isdigit():
            raise InvalidPayloadError(f"Invalid donor_id for flag check: {donor_id!r}")
        safe_flag = self.escape_single_quotes(flag)
        response = self.request_api(
            "GET",
            params={
                "action": (
                    f"SELECT donor_id, flag FROM dpflags "
                    f"WHERE donor_id='{donor_id}' AND flag='{safe_flag}'"
                ),
            },
        )
        return bool(self.parse_xml_records(response.text))

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Build the dp_saveflag_xml request for a donor/flag association."""
        donor_id = record.get("donor_id")
        flag = record.get("flag")
        return {
            "action": "dp_saveflag_xml",
            "params": self.format_procedure_params(
                {
                    "@donor_id": donor_id,
                    "@flag": flag,
                    "@user_id": record.get("user_id") or "Hotglue",
                }
            ),
            # Carried into upsert_record for existence check but popped before the API call.
            "donor_id": donor_id,
            "flag": flag,
        }

    def upsert_record(self, record: dict, context: dict) -> None:
        """Upsert the flag association; duplicates are reported as existing."""
        donor_id = record.pop("donor_id", None)
        flag = record.pop("flag", None)
        remote_id = f"{donor_id}:{flag}"

        # DP raises a PK violation when the flag is already set; treat as success instead.
        if self._flag_already_set(donor_id, flag):
            return remote_id, True, {"existing": True}

        response = self.request_api("GET", params=record)
        # dp_saveflag_xml returns the donor_id in an unnamed field when the association is created.
        confirmed_donor_id = str(self.parse_xml_response(response.text).get("") or "").strip()
        if confirmed_donor_id != str(donor_id).strip():
            raise FatalAPIError(
                f"dp_saveflag_xml did not confirm flag association for "
                f"donor_id={donor_id!r}, flag={flag!r}. Response: {response.text}"
            )
        return remote_id, True, {}

