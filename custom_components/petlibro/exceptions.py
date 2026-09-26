from homeassistant.exceptions import HomeAssistantError


class PetLibroAPIError(HomeAssistantError):
    "Basic API error"


class PetLibroAPIRejected(PetLibroAPIError):
    """The API definitively rejected a request with a nonzero code."""

    def __init__(self, code: int, message: str | None = None) -> None:
        self.code = code
        super().__init__(f"Code: {code}, Message: {message}")


class PetLibroCannotConnect(PetLibroAPIError):
    """Error to indicate we cannot connect."""


class PetLibroInvalidAuth(PetLibroAPIError):
    """Error to indicate there is invalid auth."""
