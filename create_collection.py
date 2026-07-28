import os
from qdrant_client import QdrantClient
from qdrant_client.http import models

client = QdrantClient(url="http://localhost:6333")
try:
    client.create_collection(
        collection_name="insync_materials",
        vectors_config=models.VectorParams(size=3072, distance=models.Distance.COSINE),
    )
    print("Collection created successfully!")
except Exception as e:
    print("Collection might already exist or error:", e)
