from hotglue_singer_sdk.target_sdk.client import HotglueSink
from hotglue_singer_sdk.exceptions import FatalAPIError
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from urllib.parse import unquote
import xmltodict

# exact error messages observed from the DonorPerfect API
CREDENTIALS_ERROR_MESSAGES = ("invalid token.", "login failed", "user not authorized for this api call.")
PAYLOAD_ERROR_MESSAGES = ("sql statement not allowed",)

class DonorPerfectSink(HotglueSink):
    """DonorPerfect target sink class."""
    
    base_url = "https://www.donorperfect.net/prod/xmlrequest.asp"
    endpoint = ""

    def raise_classified_error(self, error_text: str, res_json: dict) -> None:
        """Raise the error class matching the API error message."""
        msg = f"Error in response: {error_text}. Response: {res_json}"
        lowered = error_text.strip().lower()
        if lowered in CREDENTIALS_ERROR_MESSAGES:
            raise InvalidCredentialsError(msg)
        if lowered in PAYLOAD_ERROR_MESSAGES:
            raise InvalidPayloadError(msg)
        raise FatalAPIError(msg)

    def validate_response(self, response) -> None:
        """Validate HTTP response, including errors DonorPerfect reports in the body."""
        super().validate_response(response)

        try:
            res_json = xmltodict.parse(response.text).get("result") or {}
        except Exception:
            return

        if not isinstance(res_json, dict):
            return

        error = res_json.get("error")
        if error:
            self.raise_classified_error(str(error), res_json)

        fields = res_json.get("field")
        if fields:
            if not isinstance(fields, list):
                fields = [fields]
            statuses = {f.get("@name"): str(f.get("@value", "")).lower() for f in fields}
            if statuses.get("success") == "false":
                reasons = [f["@reason"] for f in fields if f.get("@reason")]
                self.raise_classified_error("; ".join(reasons) or "unknown error", res_json)

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