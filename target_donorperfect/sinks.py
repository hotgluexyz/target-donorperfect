"""DonorPerfect target sink class, which handles writing streams."""


from decimal import Decimal, InvalidOperation
from urllib.parse import unquote

from hotglue_etl_exceptions import InvalidPayloadError
from hotglue_singer_sdk.exceptions import FatalAPIError

from target_donorperfect.client import DonorPerfectSink


class DonorsSink(DonorPerfectSink):
    """DonorPerfect target sink class."""

    name = "donors"

    def preprocess_record(self, record: dict, context: dict) -> None:
        """Build the dp_savedonor request to create or update a donor.
        Updates if a donor ID is present or matched via find_existing_donor_id();
        otherwise creates a new donor. Matched updates preserve existing data by
        ignoring empty fields and only updating email_status when explicitly provided.
        Skips the whole record when payload email does not match the donor's primary email.
        """

        params = {}
        params["action"] = "dp_savedonor"

        donor_id = record.get("id", record.get("donor_id")) or 0

        # no id (e.g. no snapshot yet): try to match an existing donor to avoid creating duplicates
        matched_by_lookup = False
        if not donor_id:
            donor_id = self.find_existing_donor_id(record) or 0
            matched_by_lookup = bool(donor_id)

        existing_record = {}
        # if donor_id, get current values, if empty values are sent the record will be updated with empty values
        if donor_id:
            response = self.request_api("GET", params={"action": f"select * FROM dp WHERE donor_id='{donor_id}'", "apikey": unquote(self.config.get("api_token"))})
            existing_record = self.parse_xml_response(response.text)
            if not existing_record:
                raise InvalidPayloadError(f"Not able to update donor record, no existing record found for donor_id: {donor_id}")

            # ETL read flow emits one row per email (primary or alternate); only apply the primary-email row
            payload_email = record.get("email")
            primary_email = existing_record.get("email")
            if payload_email and primary_email and payload_email != primary_email:
                self.logger.info(f"Skipping donor {donor_id}: email {payload_email!r} does not match primary {primary_email!r}")
                return {"donor_id": existing_record.get("donor_id", donor_id), "_skip": True}

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


class DonorMembershipsSink(DonorPerfectSink):
    """Update membership UDFs on matched donors only via dp_save_udf_xml.

    Never creates donors or calls dp_savedonor. Unmatched records are skipped.
    """

    name = "donor_memberships"

    # ETL field -> expected DonorPerfect sd_field data_type
    _UDF_SPECS = (
        ("member_id", "C"),
        ("membership_description", "C"),
        ("membership_expiration_date", "D"),
    )

    @staticmethod
    def _format_udf_date(value):
        """Convert ISO dates to DP MM/DD/YYYY; pass through other non-empty values."""
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            return value
        if len(value) >= 10 and value[4] == "-" and value[7] == "-":
            year, month, day = value[:10].split("-")
            return f"{month}/{day}/{year}"
        return value

    def _membership_udf_config(self) -> dict:
        """Return the three required UDF field-name mappings from config."""
        mapping = self.config.get("membership_udf_fields")
        if not isinstance(mapping, dict):
            raise InvalidPayloadError(
                "membership_udf_fields config is required for donor_memberships "
                "(member_id, membership_description, membership_expiration_date)"
            )
        resolved = {}
        for key, _data_type in self._UDF_SPECS:
            field_name = mapping.get(key)
            if not field_name or not isinstance(field_name, str) or not field_name.strip():
                raise InvalidPayloadError(
                    f"membership_udf_fields.{key} must be a non-empty DP UDF field name"
                )
            resolved[key] = field_name.strip()
        return resolved

    def _load_udf_field_types(self, field_names: list) -> dict:
        """Return {field_name: data_type} from sd_field for the given UDF names."""
        quoted = ",".join(f"'{self.escape_single_quotes(name)}'" for name in field_names)
        response = self.request_api(
            "GET",
            params={
                "action": (
                    f"SELECT field_name, data_type FROM sd_field "
                    f"WHERE field_name IN ({quoted})"
                ),
            },
        )
        found = {}
        for row in self.parse_xml_records(response.text):
            name = row.get("field_name")
            if name:
                found[name] = (row.get("data_type") or "").strip().upper()
        return found

    def _validated_membership_udfs(self) -> dict:
        """Validate configured UDF names exist with compatible types (cached)."""
        cached = getattr(self, "_validated_udf_fields", None)
        if cached is not None:
            return cached

        mapping = self._membership_udf_config()
        found = self._load_udf_field_types(list(mapping.values()))
        for key, expected_type in self._UDF_SPECS:
            field_name = mapping[key]
            actual_type = found.get(field_name)
            if actual_type is None:
                raise InvalidPayloadError(
                    f"Configured UDF field {field_name!r} for {key} was not found in sd_field"
                )
            if actual_type != expected_type:
                raise InvalidPayloadError(
                    f"Configured UDF field {field_name!r} for {key} has data_type "
                    f"{actual_type!r}; expected {expected_type!r}"
                )
        self._validated_udf_fields = mapping
        return mapping

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
        return {
            "action": "dp_save_udf_xml",
            "params": self.format_procedure_params(fields),
        }

    def _read_udf_values(self, donor_id, field_names: list) -> dict:
        """SELECT the given DPUDF columns for a donor."""
        if not str(donor_id).isdigit():
            raise InvalidPayloadError(f"Invalid donor_id for UDF read: {donor_id!r}")
        columns = ", ".join(field_names)
        response = self.request_api(
            "GET",
            params={
                "action": f"SELECT {columns} FROM dpudf WHERE donor_id='{donor_id}'",
            },
        )
        return self.parse_xml_response(response.text)

    @staticmethod
    def _normalize_stored_udf_value(data_type: str, value):
        """Normalize a value for post-write comparison against DPUDF."""
        if value is None or value == "":
            return None
        if data_type == "D":
            text = str(value)
            # DP may return dates as MM/DD/YYYY or with a time component.
            if " " in text:
                text = text.split(" ", 1)[0]
            if len(text) >= 10 and text[4] == "-" and text[7] == "-":
                year, month, day = text[:10].split("-")
                return f"{month}/{day}/{year}"
            return text
        return str(value)

    def _verify_udf_values(self, donor_id, expected_udfs: list, saved: dict) -> None:
        """Raise when saved DPUDF values do not match what was written."""
        for field_name, data_type, expected_value in expected_udfs:
            expected_norm = self._normalize_stored_udf_value(data_type, expected_value)
            actual = self._normalize_stored_udf_value(data_type, saved.get(field_name))
            if actual != expected_norm:
                raise FatalAPIError(
                    f"dp_save_udf_xml verification failed for donor_id={donor_id!r}, "
                    f"field={field_name!r}: expected {expected_norm!r}, got {saved.get(field_name)!r}"
                )

    def preprocess_record(self, record: dict, context: dict) -> dict:
        """Resolve a matched donor and stage membership UDF updates (update-only)."""
        udf_fields = self._validated_membership_udfs()
        donor_id = record.get("id", record.get("donor_id")) or None
        if not donor_id:
            donor_id = self.find_existing_donor_id(record)

        if not donor_id:
            self.logger.info(
                f"Skipping unmatched donor_memberships record externalId={record.get('externalId')!r}"
            )
            return {"_skip": True}

        user_id = record.get("user_id") or "Hotglue"
        updates = []
        expected = []
        for key, data_type in self._UDF_SPECS:
            field_name = udf_fields[key]
            raw_value = record.get(key)
            if data_type == "C":
                write_value = None if raw_value is None else str(raw_value)
            elif data_type == "D":
                write_value = self._format_udf_date(raw_value)
            else:
                write_value = raw_value
            updates.append(self._udf_save_params(donor_id, field_name, data_type, write_value, user_id))
            expected.append((field_name, data_type, write_value))

        return {
            "donor_id": donor_id,
            "udf_updates": updates,
            "expected_udfs": expected,
            "udf_field_names": [field_name for field_name, _, _ in expected],
        }

    def upsert_record(self, record: dict, context: dict) -> None:
        """Write membership UDFs for a matched donor and verify saved values."""
        if record.pop("_skip", None):
            return None, True, {"existing": True}

        donor_id = record.pop("donor_id", None)
        updates = record.pop("udf_updates", [])
        expected = record.pop("expected_udfs", [])
        field_names = record.pop("udf_field_names", [])

        for update in updates:
            self.request_api("GET", params=update)

        saved = self._read_udf_values(donor_id, field_names)
        self._verify_udf_values(donor_id, expected, saved)
        return donor_id, True, {"is_updated": True}

