import time
import os
from .base import BaseAdapter
from shared.kafka_client import AuroraProducer
from shared.elastic_client import AuroraElasticClient

class ElasticsearchAdapter(BaseAdapter):
    def __init__(self, name="Elasticsearch"):
        super().__init__(name)
        self.es_hosts = [os.getenv("ELASTIC_HOST", "http://localhost:9200")]
        self.es_index = os.getenv("ELASTIC_INDEX", "mock-logs")
        self.kafka_brokers = [os.getenv("KAFKA_BROKERS", "192.168.1.6:29092")]
        self.kafka_topic = os.getenv("KAFKA_TOPIC", "logs.unfiltered")
        self.poll_interval = float(os.getenv("POLL_INTERVAL", "1.0"))

    def run(self):
        print(f"Ingestor: Starting {self.name} adapter...")
        
        es_client = AuroraElasticClient(self.es_hosts)
        producer = AuroraProducer(self.kafka_brokers)
        producer.ensure_topic(self.kafka_topic)
        
        last_timestamp = "1970-01-01T00:00:00.000Z"
        
        try:
            while True:
                hits = es_client.fetch_new_logs(self.es_index, last_timestamp)
                
                if hits:
                    for hit in hits:
                        log_data = hit['_source']
                        producer.send_log(self.kafka_topic, log_data)
                        last_timestamp = log_data['@timestamp']
                    
                    producer.flush()
                    print(f"  [{self.name}] Published {len(hits)} logs.")
                
                time.sleep(self.poll_interval)
        except Exception as e:
            print(f"  [{self.name}] Critical Error: {e}")
        finally:
            producer.close()
            es_client.close()
