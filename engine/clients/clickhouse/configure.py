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
                                                    settings={"allow_experimental_usearch_index": "1"},
                                                             ** connection_params)
        self.engine = collection_params["engine"] if "engine" in collection_params else "MergeTree"
        self.index = "exact"
        self.index_params = {}
        if "index" in collection_params:
            if "type" in collection_params["index"]:
                self.index = collection_params["index"]["type"]
                self.index_params = collection_params["index"]["params"] if "params" in collection_params[
                    "index"] else {}

    def clean(self):
        self.client.command(
            f"DROP TABLE IF EXISTS {CLICKHOUSE_TABLE}")

    def recreate(self, dataset: Dataset, collection_params):
        if dataset.config.vector_size > 2048:
            # we limit
            raise IncompatibilityError
        columns = self._prepare_columns_config(dataset)
        order_by = ""
        if not self.engine == "Memory":
            order_by = "ORDER BY tuple()"
        index = ""
        if self.index.lower() == "hnsw":
            columns.append(f"INDEX hnsw_indx vector TYPE usearch('{DISTANCE_MAPPING[dataset.config.distance]}', 'f32')")
        command = f"CREATE TABLE IF NOT EXISTS {CLICKHOUSE_TABLE} (id UInt32, vector Array(Float32), {','.join(columns)}) ENGINE = {self.engine} {order_by} {index}"

        self.client.command(command)

    def _prepare_columns_config(self, dataset: Dataset):
        columns = []
        for field_name, field_type in dataset.config.schema.items():
            columns.append(f"{field_name} {self.DTYPE_MAPPING.get(field_type, field_type)}")
        return columns
