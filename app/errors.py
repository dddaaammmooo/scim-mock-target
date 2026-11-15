ERROR_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:Error"


class ScimError(Exception):
    def __init__(self, status: int, detail: str, scim_type: str | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.scim_type = scim_type


def error_body(status: int, detail: str, scim_type: str | None = None) -> dict:
    body = {"schemas": [ERROR_SCHEMA], "status": str(status)}
    if scim_type:
        body["scimType"] = scim_type
    body["detail"] = detail
    return body
