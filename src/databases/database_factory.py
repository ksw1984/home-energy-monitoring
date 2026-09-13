from typing import TYPE_CHECKING

from src.config.config import required_secret
from src.databases.influxdb.influxdb import InfluxDatabase
from src.databases.text_file.textfiledb import TextFileDatabase

if TYPE_CHECKING:
    from src.databases.base_database import BaseDatabase


def create_databases(config):
    databases: list[BaseDatabase] = []

    for database_config in config.databases:
        attributes = database_config.attributes

        if database_config.type == "influxdb":
            databases.append(
                InfluxDatabase(
                    url=str(attributes["url"]),
                    token=required_secret("INFLUXDB_TOKEN"),
                    org=str(attributes["org"]),
                    bucket=str(attributes["bucket"]),
                    enabled=database_config.enabled,
                )
            )

        elif database_config.type == "text_file":
            databases.append(
                TextFileDatabase(
                    directory=str(attributes["directory"]),
                    enabled=database_config.enabled,
                )
            )

        else:
            raise ValueError(f"Unknown database type: {database_config.type}")

    return databases
