"""Exercise the installed public Python client; credentials arrive only on stdin."""

import json
import sys

from spanlight import Spanlight

options = json.load(sys.stdin)
client = Spanlight(
    api_key=options["api_key"],
    host=options["host"],
    gzip=False,
    flush_interval=60,
    max_retries=1,
)
root = client.start_span("network-root", kind="chain")
first = client.start_span("network-first", kind="llm", parent=root)
first.set_model("gpt-4.1-mini", provider="openai")
first.set_usage(input_tokens=250_000, output_tokens=0, cached_tokens=0)
first.end()
child = client.start_span("network-child", kind="llm", parent=root)
child.set_model("gpt-4.1-mini", provider="openai")
child.set_usage(input_tokens=250_000, output_tokens=0, cached_tokens=0)
child.end()
root.end()
assert client.flush(15)
client.score(root.trace_id, "quality", 1)
assert client.flush(15)
exporter = client.exporter
assert exporter is not None
print(
    json.dumps(
        {
            "trace_id": root.trace_id,
            "sent": exporter.sent_count,
            "failed": exporter.failed_count,
            "dropped": exporter.dropped_count,
        }
    )
)
client.shutdown()
