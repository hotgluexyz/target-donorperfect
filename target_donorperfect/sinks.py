"""DonorPerfect target sink class, which handles writing streams."""


from target_donorperfect.client import DonorPerfectSink
from urllib.parse import unquote


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
            response = self.request_api("GET", params={"action": f"select *FROM dp WHERE donor_id='{record['donor_id']}'", "apikey": unquote(self.config.get("api_key"))})
            existing_record = self.parse_xml_response(response.text)
            # add donor_id to existing record for state, updates always return donor_id 0
            params["donor_id"] = existing_record.get("donor_id", 0)

        # fill empty values with existing values
        existing_record.update(record)
        # process data
        # process record fields
        fields = {
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
            "@narrative": existing_record.get("narrative", ""),
            "@donor_rcpt_type": existing_record.get("donor_rcpt_type", ""),
            "@user_id": existing_record.get("user_id", ""),
        }
        params["params"] = ",".join([f"{k}={v}" if not isinstance(v, str) else f"{k}='{v}'" for k, v in fields.items()])

        return params


class ContactsSink(DonorPerfectSink):
    """DonorPerfect target sink class."""

    name = "contacts"

    def preprocess_record(self, record: dict, context: dict) -> None:
        """Process the record."""
        
        # process data
        params = {}

        params["action"] = "dp_savecontact"
        # process record fields
        fields = {
            "@contact_id": record.get("contact_id", 0),
            "@donor_id": record.get("donor_id", None),
            "@activity_code": record.get("activity_code", None),
            "@mailing_code": record.get("middle_name", None),
            "@by_whom": record.get("by_whom", None),
            "@contact_date": record.get("contact_date", None),
            "@due_date": record.get("due_date", None),
            "@due_time": record.get("due_time", None),
            "@completed_date": record.get("completed_date", None),
            "@comment": record.get("comment", None),
            "@document_path": record.get("document_path", None),
            "@user_id": record.get("user_id", None)
        }
        params["params"] = ",".join([f"{k}={v}" if isinstance(v, str) else f"{k}='{v}'" for k, v in fields.items()])

        return params