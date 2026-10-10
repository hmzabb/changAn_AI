"""Debug: check retrieval scores for a few queries, write to file."""
import sys
sys.path.insert(0, ".")

from app.repositories.vector_store import get_store
from app.services.embedding import get_embedding_client
import json

out = []
queries = [
    "大雁塔门票多少钱",
    "从钟楼到大雁塔坐几号地铁",
    "壶口瀑布和华山哪个更值得去",
]

ec = get_embedding_client()
store = get_store()

for q in queries:
    try:
        qv = ec.embed_texts([q])[0]
        hits = store.query(qv, top_k=8)
        out.append({
            "query": q,
            "count": len(hits),
            "scores": [round(h["score"], 4) for h in hits[:4]],
            "docs": [h["metadata"].get("doc","")[:30] for h in hits[:4]]
        })
    except Exception as e:
        out.append({"query": q, "error": str(e)})

with open("scripts/_debug_out.json", "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)

print(f"OK, wrote {len(out)} results")