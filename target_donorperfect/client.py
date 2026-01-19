from hotglue_singer_sdk.target_sdk.client import HotglueSink
from urllib.parse import unquote
import xmltodict

class DonorPerfectSink(HotglueSink):
    """DonorPerfect target sink class."""
    
    base_url = "https://www.donorperfect.net/prod/xmlrequest.asp"
    endpoint = ""


    def upsert_record(self, record: dict, context: dict) -> None:
        """Upsert the record."""
        method = "GET"
        # add authentication
        record["apikey"] = unquote(self.config.get("api_key"))
        donor_id = record.pop("donor_id", None)

        # send request
        response = self.request_api(method, params=record)
        res_json = self.parse_xml_response(response.text)
        id = donor_id or res_json.get("donor_id", None)
        return id, True, dict()

    def parse_xml_response(self, response: str) -> dict:
        """Parse the XML response."""
        res_json = xmltodict.parse(response).get("result", {}).get("record")
        fields = res_json.get("field", [])
        if isinstance(fields, list):
            return {field["@name"]: field["@value"] for field in fields}
        else:
            return {fields["@name"]: fields["@value"]}