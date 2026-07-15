from hotglue_singer_sdk.target_sdk.client import HotglueSink
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from urllib.parse import unquote
import requests
import xmltodict

class DonorPerfectSink(HotglueSink):
    """DonorPerfect target sink class."""

    base_url = "https://www.donorperfect.net/prod/xmlrequest.asp"
    endpoint = ""

    # DonorPerfect reports auth failures inside the XML body of a 200 response,
    # e.g. reason='Invalid token.' or reason='login failed'
    credentials_error_patterns = (
        "apikey",
        "api key",
        "token",
        "login",
        "credential",
        "unauthorized",
        "not authorized",
        "authenticat",
        "password",
    )
    # Server-side/transient errors that should not be classified as payload errors
    transient_error_patterns = (
        "timeout",
        "deadlock",
        "temporarily",
        "try again",
    )

    def raise_classified_error(self, error_text: str, res_json: dict) -> None:
        """Raise the error class matching the API error message."""
        msg = f"Error in response: {error_text}. Response: {res_json}"
        lowered = error_text.lower()
        if any(pattern in lowered for pattern in self.credentials_error_patterns):
            raise InvalidCredentialsError(msg)
        if any(pattern in lowered for pattern in self.transient_error_patterns):
            raise Exception(msg)
        # DonorPerfect returns 200 when it rejects the request content
        # (field validation, SQL conversion, etc.)
        raise InvalidPayloadError(msg)

    def parse_xml_response(self, response: str) -> dict:
        """Parse the XML response."""
        res_json = xmltodict.parse(response).get("result") or {}
        record = res_json.get("record")
        error = res_json.get("error")

        if error:
            self.raise_classified_error(str(error), res_json)

        # fields are nested under <record> for selects, but sit directly under
        # <result> for save actions and auth/status failures
        fields = (record or res_json).get("field", [])
        if not fields:
            return {}
        if not isinstance(fields, list):
            fields = [fields]
        parsed = {field["@name"]: field.get("@value") for field in fields}

        # failures come back as <field name='success' value='false' reason='...'/>
        # directly under <result>, e.g. reason='Invalid token.' / 'login failed'
        if not record and str(parsed.get("success", "")).lower() == "false":
            reasons = [f["@reason"] for f in fields if f.get("@reason")]
            self.raise_classified_error("; ".join(reasons) or "unknown error", res_json)

        return parsed

    def request_api(self, http_method, endpoint=None, params={}, request_data=None, headers={}, verify=True):
        """Request records from REST endpoint(s), returning response records."""
        # add authentication
        api_token = self.config.get("api_token")
        if not api_token:
            raise InvalidCredentialsError("No api_token found in config.")
        params["apikey"] = unquote(api_token)
        return super().request_api(
            http_method, endpoint, params=params, request_data=request_data, headers=headers, verify=verify
        )

    def validate_response(self, response: requests.Response) -> None:
        """Validate HTTP response."""
        if response.status_code in [401, 403]:
            raise InvalidCredentialsError(self.response_error_message(response))
        if response.status_code in [400, 422]:
            raise InvalidPayloadError(response.text or self.response_error_message(response))
        super().validate_response(response)

    
    # Escape single quotes in string values to avoid breaking the params format
    def escape_single_quotes(self, value):
        if isinstance(value, str):
            return value.replace("'", "''")
        return value