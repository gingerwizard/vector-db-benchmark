import clickhouse_connect

from benchmark.dataset import Dataset
from engine.base_client import IncompatibilityError
from engine.base_client.configure import BaseConfigurator
from engine.clients.clickhouse.config import (
    CLICKHOUSE_TABLE,
    CLICKHOUSE_USER,
    CLICKHOUSE_PORT,
    CLICKHOUSE_PASSWORD, CLICKHOUSE_DATABASE, DISTANCE_MAPPING,
)
from clickhouse_connect import common

common.set_setting('autogenerate_session_id', False)


class ClickHouseConfigurator(BaseConfigurator):
    DTYPE_MAPPING = {
        "int": "Int64",
        "keyword": "String",
        "text": "String",
        "float": "Float32",
        "geo": "Point",
    }

    def __init__(self, host, collection_params: dict, connection_params: dict):
        super().__init__(host, collection_params, connection_params)
        self.client = clickhouse_connect.get_client(host=host, username=CLICKHOUSE_USER, password=CLICKHOUSE_PASSWORD,
                                                    database=CLICKHOUSE_DATABASE, port=CLICKHOUSE_PORT,
                                                    settings={"allow_experimental_vector_similarity_index": "1"},
                                                    **connection_params)
        self.engine = collection_params["engine"] if "engine" in collection_params else "MergeTree"
        self.index_type = "exact"
        self.index_params = {}
        if "index" in collection_params:
            self.index_type = collection_params["index"]["type"] if "type" in collection_params["index"] else "exact"
            self.index_params = collection_params["index"]["params"] if "params" in collection_params["index"] else {}
        self.settings = collection_params["settings"] if "settings" in collection_params else {}
        self.vector_compression = collection_params[
            "vector_compression"] if "vector_compression" in collection_params else ""
        self.use_simple_projections = collection_params[
            "use_simple_projections"] if "use_simple_projections" in collection_params else False
        self.use_projections = collection_params[
            "use_projections"] if "use_projections" in collection_params else False

    def clean(self):
        self.client.command(f"DROP TABLE IF EXISTS {CLICKHOUSE_TABLE}")
        self.client.command(f"DROP TABLE IF EXISTS {CLICKHOUSE_TABLE}_planes")
        self.client.command(f"DROP TABLE IF EXISTS {CLICKHOUSE_TABLE}_lsh")

    def recreate(self, dataset: Dataset, collection_params):
        if dataset.config.vector_size > 2048:
            # we limit
            raise IncompatibilityError
        columns = self._prepare_columns_config(dataset)
        order_by = ""
        if not self.engine == "Memory":
            order_by = "ORDER BY tuple()"
        if self.index_type.lower() == "hnsw":
            quantization = self.index_params["quantization"] if "quantization" in self.index_params else "bf16"
            m = self.index_params["m"] if "m" in self.index_params else 16
            ef_construction = self.index_params["ef_construction"] if "ef_construction" in self.index_params else 128
            ef_search = self.index_params["ef_search"] if "ef_search" in self.index_params else 64
            columns.append(f"INDEX hnsw_indx vector TYPE vector_similarity('hnsw','{DISTANCE_MAPPING[dataset.config.distance]}', '{quantization}', {m}, {ef_construction}, {ef_search})")
        settings = ""
        if self.settings:
            settings = f"SETTINGS {', '.join([f'{key}={value}' for key, value in self.settings.items()])}"
        self.client.command(f"CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE} (id UInt32, vector Array(Float32) "
                            f"{self.vector_compression}, {','.join(columns)}) ENGINE = {self.engine} {order_by} {settings}")
        if self.use_simple_projections:
            columns.append("bits UInt128")
            self.client.command(
                f"CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE}_planes (`projection` Array(Float32)) ENGINE = MergeTree "
                f"ORDER BY tuple()")
            self.settings["index_granularity"] = 128
            settings = f"SETTINGS {', '.join([f'{key}={value}' for key, value in self.settings.items()])}"
            self.client.command(
                f"CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE}_lsh (`id` UInt32, `vector` Array(Float32) "
                f"{self.vector_compression}, {','.join(columns)}) ENGINE = {self.engine} ORDER BY (bits) {settings}")
        elif self.use_projections:
            columns.append("bits UInt128")
            self.client.command(
                f"CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE}_planes "
                f"(`normal` Array(Float32), `offset` Array(Float32)) ENGINE = MergeTree ORDER BY tuple()")
            self.settings["index_granularity"] = 128
            settings = f"SETTINGS {', '.join([f'{key}={value}' for key, value in self.settings.items()])}"
            self.client.command(
                f"CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE}_lsh (`id` UInt32, `vector` Array(Float32) "
                f"{self.vector_compression}, {','.join(columns)}) ENGINE = {self.engine} ORDER BY (bits) {settings}")

    def _prepare_columns_config(self, dataset: Dataset):
        columns = []
        for field_name, field_type in dataset.config.schema.items():
            columns.append(f"{field_name} {self.DTYPE_MAPPING.get(field_type, field_type)}")
        return columns

    def delete_client(self):
        self.client.close()
