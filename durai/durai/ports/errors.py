"""Sample: Raise NotFound for a record that does not exist. Catch PortError to catch every port error."""


class PortError(Exception):
    pass


class NotFound(PortError):
    pass


class Conflict(PortError):
    pass


class DigestMismatch(PortError):
    pass


class DimensionMismatch(PortError):
    pass


class SchemaMismatch(PortError):
    pass


class InvalidPath(PortError):
    pass


class SignatureInvalid(PortError):
    pass


class LicenseInvalid(PortError):
    pass
