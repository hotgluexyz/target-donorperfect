from hotglue_singer_sdk.target_sdk.client import HotglueSink
from hotglue_etl_exceptions import InvalidCredentialsError, InvalidPayloadError
from urllib.parse import unquote
import requests
import xmltodict

# DonorPerfect reports auth failures inside the XML body of a 200 response,
# e.g. reason='Invalid token.' or reason='login failed'
CREDENTIALS_ERROR_PATTERNS = (
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
TRANSIENT_ERROR_PATTERNS = (
    "timeout",
    "deadlock",
    "temporarily",
    "try again",
)


class DonorPerfectSink(HotglueSink):
    """DonorPerfect target sink class."""

    base_url = "https://www.donorperfect.net/prod/xmlrequest.asp"
    endpoint = ""

    def raise_classified_error(self, error_text: str, res_json: dict) -> None:
        """Raise the error class matching the API error message."""
        msg = f"Error in response: {error_text}. Response: {res_json}"
        lowered = error_text.lower()
        if any(pattern in lowered for pattern in CREDENTIALS_ERROR_PATTERNS):
            raise InvalidCredentialsError(msg)
        if any(pattern in lowered for pattern in TRANSIENT_ERROR_PATTERNS):
            raise Exception(msg)
        # DonorPerfect returns 200 when it rejects the request content
        # (field validation, SQL conversion, etc.)
        raise InvalidPayloadError(msg)

    def validate_response(self, response: requests.Response) -> None:
        """Validate HTTP response, including errors DonorPerfect reports in the body."""
        if response.status_code in [401, 403]:
            raise InvalidCredentialsError(self.response_error_message(response))
        if response.status_code in [400, 422]:
            raise InvalidPayloadError(response.text or self.response_error_message(response))
        super().validate_response(response)

        # DonorPerfect returns 200 with the failure described in the XML body:
        # either an <error> element or <field name='success' value='false' reason='...'/>
        try:
            res_json = xmltodict.parse(response.text).get("result") or {}
        except Exception:
            return

        # the <error> element is classified on its own text: DonorPerfect pairs it
        # with a blanket reason ('user not authorized for this api call.') even for
        # plain bad-request errors, so that reason must not drive classification
        error = res_json.get("error")
        if error:
            self.raise_classified_error(str(error), res_json)

        # without an <error> element, a success=false field reports the failure in
        # its reason attr, e.g. reason='Invalid token.' / 'login failed'
        fields = res_json.get("field")
        if fields:
            if not isinstance(fields, list):
                fields = [fields]
            statuses = {f.get("@name"): str(f.get("@value", "")).lower() for f in fields}
            if statuses.get("success") == "false":
                reasons = [f["@reason"] for f in fields if f.get("@reason")]
                self.raise_classified_error("; ".join(reasons) or "unknown error", res_json)

    def parse_xml_response(self, response: str) -> dict:
        """Parse an XML response into a dict of field name/value pairs."""
        res_json = xmltodict.parse(response).get("result") or {}
        record = res_json.get("record")

        # fields are nested under <record> for selects, but sit directly under
        # <result> for save actions
        fields = (record or res_json).get("field") or []
        if not isinstance(fields, list):
            fields = [fields]
        return {field["@name"]: field.get("@value") for field in fields}

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
