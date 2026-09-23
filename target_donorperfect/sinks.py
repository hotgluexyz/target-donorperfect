"""DonorPerfect target sink class, which handles writing streams."""


import re
from decimal import Decimal, InvalidOperation
from urllib.parse import unquote

from hotglue_etl_exceptions import InvalidPayloadError

from target_donorperfect.client import DonorPerfectSink


class DonorsSink(DonorPerfectSink):
    """DonorPerfect target sink class."""

    name = "donors"

    def _get_existing_donor(self, donor_id) -> dict:
        """GET donor by id, cached for the sink lifetime (multi-email payloads share a donor)."""        
        cache = getattr(self, "_donor_cache", None)
        if cache is None:
            cache = self._donor_cache = {}
        key = str(donor_id)
        if key not in cache:
            response = self.request_api("GET", params={"action": f"select * FROM dp WHERE donor_id='{donor_id}'", "apikey": unquote(self.config.get("api_token"))})
            cache[key] = self.parse_xml_response(response.text)
        return cache[key]

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
            existing_record = self._get_existing_donor(donor_id)
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

    @staticmethod
    def _normalize_text(value) -> str:
        """Normalize a name or email for matching: case-insensitive, extra whitespace ignored."""
        return " ".join(str(value).split()).casefold() if value is not None else ""

    @staticmethod
    def _normalize_zip(value) -> str:
        """Normalize a postal code for matching."""
        zip_code = re.sub(r"\s+", "", str(value)).casefold() if value is not None else ""
        # compare US ZIP+4 values by their 5-digit ZIP
        if re.fullmatch(r"\d{5}(-?\d{4})?", zip_code):
            return zip_code[:5]
        return zip_code

    def _query_donors(self, where: str) -> list:
        """Return donors matching a SQL WHERE clause, with only the fields used for matching."""
        response = self.request_api(
            "GET",
            params={"action": f"SELECT donor_id, first_name, last_name, zip, email FROM dp WHERE {where}"},
        )
        return self.parse_xml_records(response.text)

    def _narrow_candidates(self, record: dict, candidates: list) -> list:
        """Disambiguate donors that share an email, one field at a time.

        Extra fields are only applied while more than one donor still matches:
        last name, then ZIP, then first name. A single remaining donor is a match.
        If the incoming record is missing the next field, or that field matches
        none of the remaining donors, the record cannot be resolved.
        """
        for field, normalize in (
            ("last_name", self._normalize_text),
            ("zip", self._normalize_zip),
            ("first_name", self._normalize_text),
        ):
            if len(candidates) <= 1:
                break
            value = normalize(record.get(field))
            if not value:
                self.logger.info(
                    f"{len(candidates)} donors share an email and the incoming record has no {field}. Creating a new donor"
                )
                return []
            narrowed = [c for c in candidates if normalize(c.get(field)) == value]
            if not narrowed:
                self.logger.info(
                    f"No donor matched {field} among {len(candidates)} donors sharing an email. Creating a new donor"
                )
                return []
            candidates = narrowed
        return candidates

    def _pick_candidate(self, candidates: list, match_description: str):
        """Return the donor id to update, or None when matching should create a new donor.

        Exactly one candidate is a match. Zero candidates means the hierarchy could not
        resolve the record (for example a missing or non-matching tie-breaker). More than
        one candidate after every field has been applied means update the oldest donor.
        """
        if not candidates:
            return None
        candidates = sorted(
            candidates,
            key=lambda c: int(c["donor_id"]) if str(c.get("donor_id", "")).isdigit() else float("inf"),
        )
        donor_id = candidates[0].get("donor_id")
        if len(candidates) > 1:
            self.logger.warning(
                f"Multiple donors matched {match_description}: {[c.get('donor_id') for c in candidates]}. Using oldest donor_id {donor_id}"
            )
        else:
            self.logger.info(f"Matched existing donor {donor_id} by {match_description}")
        return donor_id

    def find_existing_donor_id(self, record: dict):
        """Find the existing DonorPerfect donor a source record refers to, if any.

        Used when a record has no donor id (for example before snapshots are populated)
        so the same person is updated instead of duplicated.

        Matching starts with email and stops as soon as one donor remains. While more
        than one donor shares the fields matched so far, the next field is added:
        last name, then ZIP, then first name. A missing tie-breaker or a tie-breaker
        that matches nobody means the record cannot be resolved and a new donor is
        created. If all four fields still leave more than one donor, the oldest is
        updated.

        A record with no email, or an email that matches nobody, is created rather
        than matched on name and ZIP.
        """
        email = (record.get("email") or "").strip()
        if not email:
            return None

        candidates = self._query_donors(f"email='{self.escape_single_quotes(email)}'")
        if not candidates:
            return None
        return self._pick_candidate(
            self._narrow_candidates(record, candidates),
            f"email '{email}'",
        )

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

