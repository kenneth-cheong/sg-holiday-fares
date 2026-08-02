# pyright: reportUnnecessaryTypeIgnoreComment=false

import os
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from ..querying import Query

try:
    import dotenv  # pyright: ignore [reportMissingImports]; pip install python-dotenv

    dotenv.load_dotenv()  # pyright: ignore [reportUnknownMemberType, reportUnusedCallResult]

except ModuleNotFoundError:
    pass


class FetchIntegration(ABC):
    """Represents an integration for `fetch()` operations."""

    @abstractmethod
    def fetch_html(self, q: Query | str, /) -> str:
        """Fetch the flights page HTML from a query.

        Args:
            q: The query.
        """
        raise NotImplementedError


T = TypeVar("T")


class DataSourceIntegration(ABC, Generic[T]):
    @abstractmethod
    def fetch(self, q: Query | str) -> T:
        """Fetch data using this data source provider.

        Args:
            q: The query.
        """
        raise NotImplementedError


def get_env(k: str, /) -> str:
    """(utility) Get environment variable.

    If nothing found, raises an error.

    Returns:
        str: The value.
    """
    try:
        return os.environ[k]
    except KeyError:
        raise OSError(
            f"could not find environment variable: {k!r}\n"
            + "if you're using .env files, install python-dotenv first:\n"
            + "  pip install python-dotenv"
        )
