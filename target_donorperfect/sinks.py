"""DonorPerfect target sink class, which handles writing streams."""


from decimal import Decimal, InvalidOperation
from urllib.parse import unquote

from hotglue_etl_exceptions import InvalidPayloadError

from target_donorperfect.client import DonorPerfectSink


class DonorsSink(DonorPerfectSink):
    """DonorPerfect target sink class."""

    name = "donors"

    def preprocess_record(self, record: dict, context: dict) -> None:
        """Process the record."""

        params = {}
        params["action"] = "dp_savedonor"
        
        existing_record = {}
        # if donor_id, get current values, if empty values are sent the record will be updated with empty values
        if record.get("donor_id", None):
            response = self.request_api("GET", params={"action": f"select *FROM dp WHERE donor_id='{record['donor_id']}'", "apikey": unquote(self.config.get("api_token"))})
            existing_record = self.parse_xml_response(response.text)
            if not existing_record:
                raise InvalidPayloadError(f"Not able to update donor record, no existing record found for donor_id: {record['donor_id']}")

            # add donor_id to existing record for state, updates always return donor_id 0
            params["donor_id"] = existing_record.get("donor_id", 0)

            if record.get("email_status") != existing_record.get("email_status"):
                params["updated_email_status"] = record.get("email_status") or ""
                params["email_status_date"] = record.get("email_status_date") or ""

        # fill empty values with existing values
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
                "@country": existing_record.get("country", ""),
                "@address_type": existing_record.get("address_type", ""),
                "@home_phone": existing_record.get("home_phone", ""),
                "@business_phone": existing_record.get("business_phone", ""),
                "@fax_phone": existing_record.get("fax_phone", ""),
                "@mobile_phone": existing_record.get("mobile_phone", ""),
                "@email": existing_record.get("email", ""),
                "@org_rec": existing_record.get("org_rec", ""),
                "@donor_type": existing_record.get("donor_type", ""),
                "@nomail": existing_record.get("nomail", ""),
                "@nomail_reason": existing_record.get("nomail_reason", ""),
                "@email_status": existing_record.get("email_status", ""),
                "@email_status_date": existing_record.get("email_status_date", ""),
                "@narrative": existing_record.get("narrative", ""),
                "@donor_rcpt_type": existing_record.get("donor_rcpt_type", ""),
                "@user_id": existing_record.get("user_id", ""),
            }.items()
        }

        params["params"] = ",".join([f"{k}={v}" if not isinstance(v, str) else f"{k}='{v}'" for k, v in fields.items()])

        return params

    def upsert_record(self, record: dict, context: dict) -> None:
        """Upsert the record."""
        method = "GET"
        state_updates = dict()

        # get donor_id for updates
        donor_id = record.pop("donor_id", None)
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

        if record.get("contact_id", None):
            response = self.request_api("GET", params={"action": f"select * FROM dpcontact WHERE contact_id='{record['contact_id']}'", "apikey": unquote(self.config.get("api_token"))})
            existing_record = self.parse_xml_response(response.text)
            if not existing_record:
                self.logger.info(f"No existing record found for contact_id: {record['contact_id']}")
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
        params["params"] = ",".join([f"{k}={v}" if not isinstance(v, str) else f"{k}='{v}'" for k, v in fields.items()])

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
            "@class": record.get("class"),
        }

    def preprocess_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        params = {}
        existing_record = {}

        if record.get("gift_id"):
            response = self.request_api(
                "GET",
                params={
                    "action": f"select * FROM dpgift WHERE gift_id='{record['gift_id']}'",
                    "apikey": unquote(self.config.get("api_token")),
                },
            )
            existing_record = self.parse_xml_response(response.text)
            if not existing_record:
                raise InvalidPayloadError(
                    f"Not able to update gift record, no existing record found for gift_id: {record['gift_id']}"
                )
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

