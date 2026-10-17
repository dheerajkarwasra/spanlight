// instrumentation.ts (Next.js) or the first import of a Node service
import { NodeTracerProvider, BatchSpanProcessor } from '@opentelemetry/sdk-trace-node';
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http';
import { resourceFromAttributes } from '@opentelemetry/resources';
import { registerTelemetry } from 'ai';
import { OpenTelemetry } from '@ai-sdk/otel';

const provider = new NodeTracerProvider({
  resource: resourceFromAttributes({
    'service.name': 'support-bot',
    'service.version': process.env.RELEASE,
    'deployment.environment.name': process.env.NODE_ENV,
  }),
  spanProcessors: [
    new BatchSpanProcessor(
      new OTLPTraceExporter({
        url: `${process.env.SPANLIGHT_HOST}/v1/otlp/traces`,
        headers: { Authorization: `Bearer ${process.env.SPANLIGHT_API_KEY}` },
      }),
    ),
  ],
});
provider.register();
registerTelemetry(new OpenTelemetry({ tracer: provider.getTracer('spanlight') }));
