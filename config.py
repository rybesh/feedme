import os
import inspect
from dotenv import load_dotenv
from typing import get_type_hints

# load environment variables from .env file
load_dotenv()


class ConfigError(Exception):
    pass


class Config:
    APP_ID: str
    CERT_ID: str
    FEED_URL: str
    FEED_AUTHOR_NAME: str
    FEED_AUTHOR_EMAIL: str
    MAX_FEED_ENTRIES: int = 1000
    MAX_LISTING_AGE_DAYS: int = 84

    """
    Map environment variables to class fields according to these rules:
      - Field won't be parsed unless it has a type annotation
      - Field will be skipped if not in all caps
      - Class field and environment variable name are the same
    """

    def __init__(self, env):
        annotations = inspect.get_annotations(Config)
        for field in annotations:
            if not field.isupper():
                continue

            default_value = getattr(self, field, None)
            if default_value is None and env.get(field) is None:
                raise ConfigError(f"The {field} field is required")

            var_type = get_type_hints(Config)[field]
            raw_value = env.get(field, default_value)

            try:
                if var_type == str:
                    value = str(raw_value.strip("'"))
                else:
                    value = var_type(raw_value)

                setattr(self, field, value)

            except ValueError as e:
                raise ConfigError(
                    'Unable to cast value of "{}" to type "{}" for "{}" field'.format(
                        raw_value, var_type, field
                    )
                ) from e

    def __repr__(self):
        return str(self.__dict__)


config = Config(os.environ)
