from src.config import required_secret
from src.databases.influxdb.influxdb import InfluxDatabase


def create_databases(config):
    databases = []

    for database_config in config.databases:
        if not database_config.enabled:
            continue

        attributes = database_config.attributes

        if database_config.type == "influxdb":
            databases.append(
                InfluxDatabase(
                    url=str(attributes["url"]),
                    token=required_secret("INFLUXDB_TOKEN"),
                    org=str(attributes["org"]),
                    bucket=str(attributes["bucket"]),
                )
            )

        else:
            raise ValueError(f"Unknown database type: {database_config.type}")

    return databases
