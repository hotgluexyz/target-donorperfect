from hotglue_singer_sdk.target_sdk.client import HotglueSink
from hotglue_singer_sdk.exceptions import FatalAPIError
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from urllib.parse import unquote
import xmltodict
import re

CREDENTIALS_ERROR_MESSAGES = ("invalid token.", "login failed", "user not authorized for this api call.")
PAYLOAD_ERROR_MESSAGES = ("sql statement not allowed",)

class DonorPerfectSink(HotglueSink):
    """DonorPerfect target sink class."""
    
    base_url = "https://www.donorperfect.net/prod/xmlrequest.asp"
    endpoint = ""

    def raise_classified_error(self, error_messages: list, res_json: dict) -> None:
        """Raise the error class matching a known DonorPerfect error message."""
        lowered = [m.strip().lower() for m in error_messages]
        msg = f"Error in response: {'; '.join(error_messages) or 'unknown error'}. Response: {res_json}"
        if any(m in CREDENTIALS_ERROR_MESSAGES for m in lowered):
            raise InvalidCredentialsError(msg)
        if any(m in PAYLOAD_ERROR_MESSAGES for m in lowered):
            raise InvalidPayloadError(msg)
        raise FatalAPIError(msg)

    def validate_response(self, response) -> None:
        """Validate HTTP response, including errors DonorPerfect reports in the body."""
        super().validate_response(response)

        try:
            res_json = xmltodict.parse(response.text).get("result") or {}
        except Exception as exc:
            raise FatalAPIError(
                f"Failed to parse DonorPerfect XML response: {exc}. Response: {response.text}"
            ) from exc

        if not isinstance(res_json, dict):
            return

        error = res_json.get("error")
        if error:
            self.raise_classified_error([str(error)], res_json)

        fields = res_json.get("field")
        if fields:
            if not isinstance(fields, list):
                fields = [fields]
            statuses = {f.get("@name"): str(f.get("@value", "")).lower() for f in fields}
            if statuses.get("success") == "false":
                reasons = [f["@reason"] for f in fields if f.get("@reason")]
                self.raise_classified_error(reasons, res_json)

    def parse_xml_response(self, response: str) -> dict:
        """Parse the XML response."""
        res_json = xmltodict.parse(response).get("result") or {}
        result = res_json.get("record")

        if not result:
            return {}
        fields = result.get("field", [])
        if isinstance(fields, list):
            return {field["@name"]: field["@value"] for field in fields}
        else:
            return {fields["@name"]: fields["@value"]}

    def parse_xml_records(self, response: str) -> list:
        """Parse every record in the XML response into a list of {field name: value} dicts."""
        res_json = xmltodict.parse(response).get("result") or {}
        records = res_json.get("record") or []
        if not isinstance(records, list):
            records = [records]

        parsed = []
        for record in records:
            fields = record.get("field", [])
            if not isinstance(fields, list):
                fields = [fields]
            parsed.append({field["@name"]: field["@value"] for field in fields})
        return parsed

    def request_api(self, http_method, endpoint=None, params={}, request_data=None, headers={}, verify=True):
        """Request records from REST endpoint(s), returning response records."""
        # add authentication
        params["apikey"] = unquote(self.config.get("api_token"))
        return super().request_api(
            http_method, endpoint, params=params, request_data=request_data, headers=headers, verify=verify
        )

    
    # Escape single quotes in string values to avoid breaking the params format
    def escape_single_quotes(self, value):
        if isinstance(value, str):
            return value.replace("'", "''")
        return value

    def format_procedure_params(self, fields: dict) -> str:
        """Format stored-procedure params, quoting strings and using unquoted nulls."""
        parts = []
        for key, value in fields.items():
            value = self.escape_single_quotes(value)
            if value is None or value == "":
                parts.append(f"{key}=null")
            elif isinstance(value, str):
                parts.append(f"{key}='{value}'")
            else:
                parts.append(f"{key}={value}")
        return ",".join(parts)
    

    def clean_body(self, body: str) -> str:
        """Clean the body, replace  empty strings as null"""
        # Replace any field assignment of the form ='' (empty string) with =null
        body = re.sub(r"=\s*'\s*'(?=,|$)", "=null", body)
        return body

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
                    f"{len(candidates)} donors share an email and the incoming record has no {field}. "
                    "Cannot resolve donor"
                )
                return []
            narrowed = [c for c in candidates if normalize(c.get(field)) == value]
            if not narrowed:
                self.logger.info(
                    f"No donor matched {field} among {len(candidates)} donors sharing an email. "
                    "Cannot resolve donor"
                )
                return []
            candidates = narrowed
        return candidates

    def _pick_candidate(self, candidates: list, match_description: str):
        """Return the matched donor id, or None when the hierarchy cannot resolve the record.

        Exactly one candidate is a match. Zero candidates means the hierarchy could not
        resolve the record (for example a missing or non-matching tie-breaker). More than
        one candidate after every field has been applied means use the oldest donor.
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
        that matches nobody means the record cannot be resolved. If all four fields
        still leave more than one donor, the oldest is returned.

        A record with no email, or an email that matches nobody, returns None rather
        than matching on name and ZIP alone.
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
