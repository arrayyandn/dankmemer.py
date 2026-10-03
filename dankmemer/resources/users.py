from dankmemer.errors import DankMemerResponseError
from dankmemer.http._routes import USER_BAN_STATUS

from ._base import Resource


class Users(Resource):
    """Access the official user ban-status lookup."""

    async def is_banned(self, user_id: int) -> bool:
        """Return whether a positive integer Discord user ID is banned.

        The API returns ``False`` for an unknown ID. It does not provide
        ban reasons, ban history, or any other user information.
        """
        USER_BAN_STATUS.target(user_id=user_id)
        self._check_access()
        value = await self._request(USER_BAN_STATUS, user_id=user_id)
        if type(value) is not bool:
            raise DankMemerResponseError(
                "expected a boolean", path=USER_BAN_STATUS.template
            )
        return value
