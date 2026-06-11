from hotglue_singer_sdk.target_sdk.client import HotglueSink
from urllib.parse import unquote
import xmltodict

class DonorPerfectSink(HotglueSink):
    """DonorPerfect target sink class."""
    
    base_url = "https://www.donorperfect.net/prod/xmlrequest.asp"
    endpoint = ""

    def parse_xml_response(self, response: str) -> dict:
        """Parse the XML response."""
        res_json = xmltodict.parse(response).get("result", {}).get("record")
        fields = res_json.get("field", [])
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