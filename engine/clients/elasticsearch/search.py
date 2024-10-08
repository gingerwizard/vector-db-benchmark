import multiprocessing as mp
import uuid
from typing import List, Tuple

from elasticsearch import Elasticsearch

from dataset_reader.base_reader import Query
from engine.base_client.distances import Distance
from engine.base_client.search import BaseSearcher
from engine.clients.elasticsearch.config import ELASTIC_INDEX, get_es_client
from engine.clients.elasticsearch.parser import ElasticConditionParser


class ClosableElastic(Elasticsearch):
    def __del__(self):
        self.close()


class ElasticSearcher(BaseSearcher):
    search_params = {}
    client: Elasticsearch = None
    parser = ElasticConditionParser()

    DISTANCE_SCRIPTS = {
        Distance.L2: "1 / (1 + l1norm(params.queryVector, 'vector'))",
        Distance.COSINE: "cosineSimilarity(params.query_vector, 'vector') + 1.0",
        Distance.DOT: "double value = dotProduct(params.query_vector, 'vector'); return sigmoid(1, Math.E, -value);",
    }

    @classmethod
    def get_mp_start_method(cls):
        return "forkserver" if "forkserver" in mp.get_all_start_methods() else "spawn"

    @classmethod
    def init_client(cls, host, _distance, connection_params: dict, search_params: dict):
        cls.client = get_es_client(host, connection_params)
        cls.search_params = search_params
        cls.distance = _distance

    @classmethod
    def search_one(cls, query: Query, top: int) -> List[Tuple[int, float]]:
        if "exact" in cls.search_params and cls.search_params["exact"]:
            res = cls.client.search(index=ELASTIC_INDEX, query={
                "script_score": {
                    "query": { "match_all": {} } if query.meta_conditions is None or not query.meta_conditions else query.meta_conditions,
                    "script": {
                        "source": cls.DISTANCE_SCRIPTS[cls.distance],
                        "params": {
                            "query_vector": query.vector
                        }
                    }
                }
            }, size=top)

        else:
            knn = {
                "field": "vector",
                "query_vector": query.vector,
                "k": top,
                **cls.search_params["config"],
            }
            meta_conditions = cls.parser.parse(query.meta_conditions)
            if meta_conditions:
                knn["filter"] = meta_conditions

            res = cls.client.search(
                index=ELASTIC_INDEX,
                knn=knn,
                size=top,
            )
        return [
            (uuid.UUID(hex=hit["_id"]).int, hit["_score"])
            for hit in res["hits"]["hits"]
        ]
